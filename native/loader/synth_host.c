/* SPDX-License-Identifier: MIT
 * Synthetic host for the WP2A status library. NOT sdvcam: it mimics only the
 * 0x31DB0 bracket (attach on the main thread, a blocking fake run, detach) and
 * never touches devices. Built with the device profile (musl 1.1.24 headers,
 * linked against the SAVED camera libc, run under qemu-arm) and for the build
 * host with ASan/UBSan or TSan. The test driver sends one-line commands on stdin
 * at synchronisation points; results are JSON lines on stdout.
 *
 * usage: synth_host <library> <scenario> [probe-file]
 *   abi-probe | serve | reject-null | reject-abi | reject-size | reject-profile |
 *   reject-hash | detach-first | stall | emfile | kill-wait | exit-no-detach
 */
#define _GNU_SOURCE
#include "cammy_native_host.h"

#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

typedef int (*attach_fn)(const struct cammy_native_host_v1 *);
typedef void (*detach_fn)(uint32_t);
typedef void (*state_fn)(uint32_t out[10]);
typedef void (*stall_fn)(int);

static const uint8_t SRC_SHA[32] = {
    0x87, 0x71, 0x96, 0x1f, 0xdb, 0xa8, 0x92, 0x80, 0x57, 0x8c, 0xe5, 0x48, 0x2a, 0x6c, 0xbe, 0x4e,
    0xc3, 0xf6, 0xec, 0x20, 0x50, 0x68, 0x94, 0x4b, 0x34, 0xf9, 0x44, 0x63, 0x7d, 0x62, 0xb0, 0xec};

static volatile sig_atomic_t usr1_count;
static volatile long usr1_tid;
static attach_fn attach;
static detach_fn detach;
static state_fn get_state;
static stall_fn stall;

static void on_usr1(int sig) {
    (void)sig;
    usr1_count++;
    usr1_tid = syscall(SYS_gettid);
}

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

static void emit(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
}

static void sleep_ms(int ms) { poll(NULL, 0, ms); }

/* Visible in `qemu-arm -strace` as access("/cammy-marker/...") to bracket library calls. */
static void marker(const char *m) { (void)access(m, F_OK); }

/* Waits for one command line on stdin; exits 9 if it differs (driver protocol error). */
static void expect(const char *cmd) {
    char line[64];
    if (!fgets(line, sizeof line, stdin)) { emit("{\"event\":\"stdin-eof\",\"wanted\":\"%s\"}", cmd); exit(9); }
    line[strcspn(line, "\n")] = 0;
    if (strcmp(line, cmd) != 0) { emit("{\"event\":\"bad-command\",\"got\":\"%s\",\"wanted\":\"%s\"}", line, cmd); exit(9); }
}

#define MAXFD 1024
static void fd_snapshot(unsigned char set[MAXFD]) {
    memset(set, 0, MAXFD);
    DIR *d = opendir("/proc/self/fd");
    if (!d) return;
    int self = dirfd(d);
    struct dirent *e;
    while ((e = readdir(d))) {
        int fd = atoi(e->d_name);
        if (e->d_name[0] >= '0' && e->d_name[0] <= '9' && fd != self && fd >= 0 && fd < MAXFD) set[fd] = 1;
    }
    closedir(d);
}

/* New descriptors since `before`, and how many lack FD_CLOEXEC. */
static void fd_check(const unsigned char before[MAXFD], int *added, int *no_cloexec) {
    unsigned char now[MAXFD];
    fd_snapshot(now);
    *added = *no_cloexec = 0;
    for (int fd = 0; fd < MAXFD; fd++) {
        if (!now[fd] || before[fd]) continue;
        int fl = fcntl(fd, F_GETFD);
        if (fl < 0) continue; /* closed meanwhile */
        (*added)++;
        if (!(fl & FD_CLOEXEC)) (*no_cloexec)++;
    }
}

static void fill_host(struct cammy_native_host_v1 *h, void *mm) {
    memset(h, 0, sizeof *h);
    h->abi_version = CAMMY_NATIVE_HOST_ABI;
    h->struct_size = sizeof *h;
    h->profile = CAMMY_PROFILE_STATUS_ONLY;
    memcpy(h->source_sha256, SRC_SHA, 32);
    h->main_module = mm;
}

static void print_state(const char *event) {
    if (!get_state) { emit("{\"event\":\"%s\",\"hooks\":false}", event); return; }
    uint32_t s[10];
    get_state(s);
    emit("{\"event\":\"%s\",\"phase\":%u,\"attached\":%u,\"detach_result\":%u,\"thread_done\":%u,"
         "\"attach_us\":%u,\"detach_us\":%u,\"step_failures\":%u,\"table_inited\":%u,\"detach_reason\":%u,"
         "\"handle_pinned\":%u}", event, s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9]);
}

static int wait_state(unsigned idx, uint32_t want, int timeout_ms) {
    uint32_t s[10];
    for (int t = 0; t <= timeout_ms; t += 10) {
        get_state(s);
        if (s[idx] == want) return 0;
        sleep_ms(10);
    }
    return -1;
}

/* Process-directed SIGUSR1 must never be taken by the bridge thread. */
static void signal_check(void) {
    sigset_t one, pend;
    sigemptyset(&one);
    sigaddset(&one, SIGUSR1);
    pthread_sigmask(SIG_BLOCK, &one, NULL);
    usr1_count = 0;
    kill(getpid(), SIGUSR1);
    sleep_ms(150);
    int while_blocked = usr1_count;
    sigpending(&pend);
    int pending = sigismember(&pend, SIGUSR1);
    pthread_sigmask(SIG_UNBLOCK, &one, NULL);
    sleep_ms(20);
    emit("{\"event\":\"signals\",\"handled_while_main_blocked\":%d,\"pending\":%d,\"handled_after\":%d,"
         "\"on_main_thread\":%d}", while_blocked, pending, (int)usr1_count, usr1_tid == (long)getpid());
}

static void heap_churn(void) {
    for (int i = 0; i < 256; i++) {
        void *p = malloc((size_t)(16 + i * 8));
        if (p) memset(p, 0xA5, (size_t)(16 + i * 8));
        free(p);
    }
}

#if defined(__arm__)
/* Kernel ARM struct stat64 offsets (arch/arm/include/uapi/asm/stat.h), written out
 * independently of libc headers so the comparison proves musl's layout. */
#define K_MODE 16
#define K_UID 24
#define K_SIZE 48
#define K_MTIME 80
#define K_INO 96
#define K_STAT64_SIZE 104
static void abi_probe(const char *file) {
    int fails = 0;
    unsigned char ts_buf[sizeof(struct timespec) + 16];
    memset(ts_buf, 0xEE, sizeof ts_buf);
    struct timespec *ts = (struct timespec *)(void *)ts_buf;
    int32_t raw[2] = {0, 0};
    long r1 = clock_gettime(CLOCK_MONOTONIC, ts);
    long r2 = syscall(SYS_clock_gettime, CLOCK_MONOTONIC, raw); /* 32-bit kernel timespec */
    for (size_t i = sizeof(struct timespec); i < sizeof ts_buf; i++) fails += ts_buf[i] != 0xEE;
    long long dsec = (long long)ts->tv_sec - raw[0];
    if (r1 || r2 || dsec < -1 || dsec > 1) fails++;

    int fd = open(file, O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0640);
    if (fd < 0 || ftruncate(fd, 12345) != 0) fails++;
    if (fd >= 0) close(fd);
    unsigned char st_buf[sizeof(struct stat) + 16];
    memset(st_buf, 0xEE, sizeof st_buf);
    struct stat *st = (struct stat *)(void *)st_buf;
    unsigned char k[K_STAT64_SIZE + 16];
    memset(k, 0, sizeof k);
    long s1 = lstat(file, st);
    long s2 = syscall(SYS_fstatat64, AT_FDCWD, file, k, AT_SYMLINK_NOFOLLOW);
    for (size_t i = sizeof(struct stat); i < sizeof st_buf; i++) fails += st_buf[i] != 0xEE;
    uint32_t kmode, kuid, kmtime;
    int64_t ksize;
    uint64_t kino;
    memcpy(&kmode, k + K_MODE, 4);
    memcpy(&kuid, k + K_UID, 4);
    memcpy(&ksize, k + K_SIZE, 8);
    memcpy(&kmtime, k + K_MTIME, 4);
    memcpy(&kino, k + K_INO, 8);
    if (s1 || s2 || st->st_mode != kmode || st->st_uid != kuid || st->st_size != ksize || st->st_size != 12345 ||
        (uint32_t)st->st_mtime != kmtime || (uint64_t)st->st_ino != kino)
        fails++;
    unlink(file);
    emit("{\"event\":\"abi-probe\",\"fails\":%d,\"sizeof_time_t\":%u,\"sizeof_timespec\":%u,\"sizeof_stat\":%u,"
         "\"off_st_mode\":%u,\"off_st_size\":%u,\"off_st_mtim\":%u,\"off_st_ino\":%u,\"sizeof_sigset\":%u,"
         "\"sizeof_pthread_attr\":%u,\"sizeof_pthread_mutex\":%u,\"sizeof_pollfd\":%u,\"sizeof_msghdr\":%u,"
         "\"sizeof_cmsghdr\":%u,\"sizeof_ucred\":%u,\"sizeof_sockaddr_un\":%u}",
         fails, (unsigned)sizeof(time_t), (unsigned)sizeof(struct timespec), (unsigned)sizeof(struct stat),
         (unsigned)offsetof(struct stat, st_mode), (unsigned)offsetof(struct stat, st_size),
         (unsigned)offsetof(struct stat, st_mtim), (unsigned)offsetof(struct stat, st_ino),
         (unsigned)sizeof(sigset_t), (unsigned)sizeof(pthread_attr_t), (unsigned)sizeof(pthread_mutex_t),
         (unsigned)sizeof(struct pollfd), (unsigned)sizeof(struct msghdr), (unsigned)sizeof(struct cmsghdr),
         (unsigned)sizeof(struct ucred), (unsigned)sizeof(struct sockaddr_un));
}
#else
static void abi_probe(const char *file) {
    (void)file;
    emit("{\"event\":\"abi-probe\",\"fails\":0,\"skipped\":\"not ARM\"}");
}
#endif

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    const char *scenario = argv[2];
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (!strcmp(scenario, "abi-probe")) {
        abi_probe(argc > 3 ? argv[3] : "abi-probe.tmp");
        return 0;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_usr1;
    sigaction(SIGUSR1, &sa, NULL);
    signal(SIGPIPE, SIG_IGN); /* as live sdvcam */
    sigset_t base, after;
    pthread_sigmask(SIG_SETMASK, NULL, &base);
    /* An inaccessible page stands in for MainModule: any dereference faults. */
    void *mm = mmap(NULL, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mm == MAP_FAILED) return 3;

    uint64_t t = now_us();
    void *h = dlopen(argv[1], RTLD_NOW);
    uint64_t dl_us = now_us() - t;
    if (!h) {
        const char *e = dlerror();
        emit("{\"event\":\"dlopen\",\"ok\":false,\"error\":\"%s\"}", e ? e : "");
        return 4;
    }
    attach = (attach_fn)(uintptr_t)dlsym(h, "cammy_native_attach_v1");
    detach = (detach_fn)(uintptr_t)dlsym(h, "cammy_native_detach_v1");
    get_state = (state_fn)(uintptr_t)dlsym(h, "cammy_native_test_state");
    stall = (stall_fn)(uintptr_t)dlsym(h, "cammy_native_test_stall");
    void *bogus = dlsym(h, "cammy_bridge_open"); /* core symbols must stay hidden */
    emit("{\"event\":\"dlopen\",\"ok\":true,\"us\":%llu,\"attach\":%d,\"detach\":%d,\"hooks\":%d,\"core_exported\":%d}",
         (unsigned long long)dl_us, attach != NULL, detach != NULL, get_state != NULL, bogus != NULL);
    if (!attach || !detach) return 5;

    unsigned char fds_before[MAXFD];
    fd_snapshot(fds_before);
    struct cammy_native_host_v1 host;
    fill_host(&host, mm);
    int rc;

    if (!strncmp(scenario, "reject-", 7)) {
        const char *what = scenario + 7;
        if (!strcmp(what, "abi")) host.abi_version = 2;
        else if (!strcmp(what, "size")) host.struct_size = 44;
        else if (!strcmp(what, "profile")) host.profile = 2;
        else if (!strcmp(what, "hash")) host.source_sha256[31] ^= 1;
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED); /* before attach: no-op */
        rc = attach(!strcmp(what, "null") ? NULL : &host);
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED); /* after failed attach: no-op */
        struct cammy_native_host_v1 good;
        fill_host(&good, mm);
        int rc2 = attach(&good);
        int added, noexec;
        fd_check(fds_before, &added, &noexec);
        emit("{\"event\":\"rejected\",\"rc\":%d,\"retry_rc\":%d,\"new_fds\":%d}", rc, rc2, added);
        print_state("state");
        return 0;
    }

    if (!strcmp(scenario, "detach-first")) {
        t = now_us();
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        uint64_t noop_us = now_us() - t;
        rc = attach(&host);
        emit("{\"event\":\"attached\",\"rc\":%d,\"noop_detach_us\":%llu}", rc, (unsigned long long)noop_us);
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        print_state("detached");
        return 0;
    }

    t = now_us();
    marker("/cammy-marker/attach-begin");
    rc = attach(&host);
    marker("/cammy-marker/attach-end");
    uint64_t attach_us = now_us() - t;
    pthread_sigmask(SIG_SETMASK, NULL, &after);
    int added, noexec;
    fd_check(fds_before, &added, &noexec);
    emit("{\"event\":\"attached\",\"rc\":%d,\"us\":%llu,\"mask_restored\":%d,\"new_fds\":%d,\"new_fds_without_cloexec\":%d}",
         rc, (unsigned long long)attach_us, memcmp(&base, &after, sizeof base) == 0, added, noexec);
    if (rc != 0) return 6;

    if (!strcmp(scenario, "serve")) {
        expect("check-fds"); /* a client is connected now */
        fd_check(fds_before, &added, &noexec);
        emit("{\"event\":\"fds\",\"new_fds\":%d,\"new_fds_without_cloexec\":%d}", added, noexec);
        signal_check();
        rc = attach(&host);
        emit("{\"event\":\"second-attach\",\"rc\":%d}", rc);
        expect("close"); /* the fake queue run returns */
        t = now_us();
        marker("/cammy-marker/detach-begin");
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        marker("/cammy-marker/detach-end");
        uint64_t d1 = now_us() - t;
        t = now_us();
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        uint64_t d2 = now_us() - t;
        fd_check(fds_before, &added, &noexec);
        emit("{\"event\":\"detached\",\"us\":%llu,\"second_us\":%llu,\"fds_left\":%d}", (unsigned long long)d1,
             (unsigned long long)d2, added);
        print_state("state");
        munmap(mm, 4096); /* MainModule deletion analogue */
        heap_churn();
        return 0;
    }

    if (!strcmp(scenario, "stall")) {
        if (!stall || !get_state) return 7;
        int sp[2];
        if (pipe2(sp, O_CLOEXEC) != 0) return 7;
        stall(sp[0]);
        emit("{\"event\":\"stall-armed\"}");
        expect("detach"); /* a status request is now blocked inside the bridge */
        t = now_us();
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        uint64_t d1 = now_us() - t;
        emit("{\"event\":\"detach-returned\",\"us\":%llu}", (unsigned long long)d1);
        print_state("after-detach");
        munmap(mm, 4096);
        heap_churn();
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED); /* idempotent even after abandon */
        expect("release");
        if (write(sp[1], "x", 1) != 1) return 7;
        int done = wait_state(3, 1, 3000);
        emit("{\"event\":\"released\",\"thread_done_seen\":%d}", done == 0);
        print_state("final");
        expect("exit");
        return 0;
    }

    if (!strcmp(scenario, "emfile")) {
        if (!get_state) return 7;
        struct rlimit old, lim;
        getrlimit(RLIMIT_NOFILE, &old);
        int lowest = 0;
        while (fcntl(lowest, F_GETFD) >= 0) lowest++;
        lim = old;
        lim.rlim_cur = (rlim_t)lowest; /* the next descriptor (an accepted peer) fails EMFILE */
        int lr = setrlimit(RLIMIT_NOFILE, &lim);
        emit("{\"event\":\"emfile-armed\",\"setrlimit\":%d,\"limit\":%d}", lr, lowest);
        expect("check");
        int failed = wait_state(3, 1, 3000); /* thread finished its failure handling */
        setrlimit(RLIMIT_NOFILE, &old);
        print_state("after-failure");
        sleep_ms(200);
        emit("{\"event\":\"still-running\",\"failed_seen\":%d}", failed == 0);
        expect("close");
        detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED);
        fd_check(fds_before, &added, &noexec);
        print_state("detached");
        emit("{\"event\":\"fds\",\"fds_left\":%d}", added);
        return 0;
    }

    if (!strcmp(scenario, "kill-wait")) {
        for (;;) pause(); /* killed by the driver while serving */
    }

    if (!strcmp(scenario, "exit-no-detach")) {
        expect("exit");
        exit(0); /* native exit() path: atexit + DSO fini while the bridge thread runs */
    }
    return 8;
}
