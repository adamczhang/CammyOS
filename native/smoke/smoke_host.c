/* SPDX-License-Identifier: MIT
 * Bounded SYNTHETIC success-path host; never sdvcam, never a hardware backend.
 * No signal/stress hooks, stdin commands, dlclose or pointer dispatch. The caller
 * owns the precreated private /tmp/cammy-bridge and exact-hash library staging.
 * Failure after attach always calls detach; abandoned state stays pinned to exit.
 */
#define _GNU_SOURCE
#include "../loader/cammy_native_host.h"
#include "../bridge/cammy_bridge_proto.h"
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

_Static_assert(sizeof(time_t) == 4 && sizeof(struct timespec) == 8, "time32 required");
_Static_assert(sizeof(struct stat) == 104, "saved-camera stat ABI required");
_Static_assert(sizeof(struct cammy_native_host_v1) == 48, "ARM32 host required");
#define SOCK "/tmp/cammy-bridge/bridge.sock"
#define MAXFD 1024
static const unsigned char source[32] = {
  0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e,
  0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec};
/* The only 64-bit divisors are nonzero constants; keep libgcc's unreachable
 * SIGFPE fallback out of the import table, as in the frozen status library. */
int __aeabi_idiv0(int value);
long long __aeabi_ldiv0(long long value);
int __aeabi_idiv0(int value) { return value; }
long long __aeabi_ldiv0(long long value) { return value; }
static uint64_t now_us(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) exit(90);
    return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}
static int fd_snapshot(unsigned char out[MAXFD], int *no_cloexec) {
    memset(out, 0, MAXFD); *no_cloexec = 0;
    DIR *d = opendir("/proc/self/fd");
    if (!d) return -1;
    int count = 0, own = dirfd(d);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
        int fd = atoi(e->d_name);
        if (fd == own) continue;
        if (fd < 0 || fd >= MAXFD) { closedir(d); return -1; }
        out[fd] = 1; count++;
        if (fd > 2 && !(fcntl(fd, F_GETFD) & FD_CLOEXEC)) (*no_cloexec)++;
    }
    closedir(d); return count;
}
static int task_count(void) {
    DIR *d = opendir("/proc/self/task");
    if (!d) return -1;
    int n = 0; struct dirent *e;
    while ((e = readdir(d))) if (e->d_name[0] >= '0' && e->d_name[0] <= '9') n++;
    closedir(d); return n;
}
static void maps(void) {
    FILE *f = fopen("/proc/self/maps", "r"); char line[512];
    if (!f) return;
    while (fgets(line, sizeof line, f)) {
        if (!strstr(line, "libcammy_native_status.so")) continue;
        line[strcspn(line, "\n")] = 0;
        printf("{\"event\":\"map\",\"line\":\"%s\"}\n", line);
    }
    fclose(f);
}
static int request(int fd, unsigned cmd, struct cb_response_header *identity) {
    struct cb_request rq; memset(&rq, 0, sizeof rq);
    rq.major = 1; rq.command = (uint16_t)cmd; rq.request_id = cmd;
    if (cmd != CB_CMD_HELLO) {
        rq.expect_instance = identity->instance;
        memcpy(rq.boot_id, identity->boot_id, 16);
    }
    rq.deadline_mono_ms = cmd == CB_CMD_HELLO ? 0 : now_us() / 1000u + 1000u;
    unsigned char buf[CB_MAX_FRAME], packet[CB_REQUEST_SIZE];
    cb_encode_request(packet, &rq);
    if (send(fd, packet, sizeof packet, MSG_NOSIGNAL | MSG_DONTWAIT) != sizeof packet) return -1;
    struct pollfd p = {fd, POLLIN, 0};
    if (poll(&p, 1, 1000) != 1 || !(p.revents & POLLIN)) return -1;
    ssize_t len = recv(fd, buf, sizeof buf, MSG_DONTWAIT | MSG_TRUNC);
    if (len < 0 || len > (ssize_t)CB_MAX_FRAME) return -1;
    struct cb_response_header h;
    if (cb_decode_response_header(buf, (size_t)len, &h) || h.minor || h.status || h.backend_kind != 2 ||
        h.owner_generation || !h.instance || h.command != cmd || h.request_id != cmd) return -1;
    if (cmd != CB_CMD_HELLO && (h.instance != identity->instance || memcmp(h.boot_id, identity->boot_id, 16))) return -1;
    *identity = h;
    printf("{\"event\":\"response\",\"command\":%u,\"hex\":\"", cmd);
    for (ssize_t i = 0; i < len; i++) printf("%02x", buf[i]);
    puts("\"}");
    const unsigned char *v = buf + 64;
    if (cmd == 1 && (h.payload_size != 48 || cb_get32(v + 24) != 14)) return -1;
    if (cmd == 2 && (h.payload_size != 64 || cb_get16(v) != 2 || cb_get16(v + 2) != 1 ||
        cb_get16(v + 4) || cb_get16(v + 6) != 1)) return -1;
    if (cmd == 3) {
        if (h.payload_size != 64 || cb_get16(v) != 7 || cb_get16(v + 2) != 8 || cb_get32(v + 4)) return -1;
        for (unsigned i = 0; i < 7; i++) {
            const unsigned char *c = v + 8 + i * 8;
            if (cb_get16(c) != i + 1 || c[2] != (i == 0 ? 1 : 0) || c[3] != (i == 0 ? 0 : 3) ||
                c[4] != (i == 0 ? 0 : 1) || c[5] || c[6] || c[7]) return -1;
        }
    }
    return 0;
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    /* Give the launcher a bounded window to record this child's PID/starttime
     * for emergency identity-gated cleanup. Excluded from library timings. */
    (void)poll(NULL, 0, 100);
    if (argc != 2 && argc != 3) return 2;
    int reject = argc == 3 && !strcmp(argv[2], "reject-hash");
    if (argc == 3 && !reject) return 2; /* offline refusal control only */
    struct stat st;
    if (geteuid() != 0 || lstat("/tmp/cammy-bridge", &st) || !S_ISDIR(st.st_mode) ||
        st.st_uid || (st.st_mode & 0777) != 0700) return 3;
    if (!lstat(SOCK, &st) || errno != ENOENT || !lstat(SOCK ".lock", &st) || errno != ENOENT) return 3;
    unsigned char before[MAXFD], after[MAXFD], live[MAXFD]; int cloexec;
    int initial_fds = fd_snapshot(before, &cloexec), initial_tasks = task_count();
    if (initial_fds < 0 || initial_tasks < 1) return 4;
    uint64_t t = now_us();
    void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    uint64_t load_us = now_us() - t;
    if (!lib) { puts("{\"event\":\"dlopen-failed\"}"); return 5; }
    int (*attach)(const struct cammy_native_host_v1 *) = dlsym(lib, "cammy_native_attach_v1");
    void (*detach)(uint32_t) = dlsym(lib, "cammy_native_detach_v1");
    if (!attach || !detach) return 6;
    printf("{\"event\":\"identity\",\"caller\":\"SYNTHETIC\",\"pid\":%ld,\"time_t\":%u,\"stat_size\":%u,\"load_us\":%llu}\n",
           (long)getpid(), (unsigned)sizeof(time_t), (unsigned)sizeof(struct stat), (unsigned long long)load_us);
    maps();
    struct cammy_native_host_v1 host = {1, sizeof host, 1, {0}, NULL};
    memcpy(host.source_sha256, source, 32); if (reject) host.source_sha256[0] ^= 1;
    t = now_us(); int rc = attach(&host); uint64_t attach_us = now_us() - t;
    printf("{\"event\":\"attach\",\"rc\":%d,\"us\":%llu}\n", rc, (unsigned long long)attach_us);
    if (rc) {
        detach(1);
        int n = fd_snapshot(after, &cloexec);
        int absent = lstat(SOCK, &st) && errno == ENOENT;
        printf("{\"event\":\"refused\",\"clean\":%s}\n", n == initial_fds && !memcmp(before, after, MAXFD) && absent ? "true" : "false");
        return reject && rc == -EPROTO && n == initial_fds && !memcmp(before, after, MAXFD) && absent ? 0 : 7;
    }
    int ok = !reject;
    int live_fds = fd_snapshot(live, &cloexec), live_tasks = task_count();
    printf("{\"event\":\"serving\",\"fds\":%d,\"non_cloexec\":%d,\"tasks\":%d}\n",live_fds,cloexec,live_tasks);
    if (cloexec || live_fds <= initial_fds || live_tasks != initial_tasks + 1) ok = 0;
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
    memcpy(a.sun_path, SOCK, sizeof SOCK);
    if (fd < 0 || connect(fd, (const struct sockaddr *)&a, sizeof a)) ok = 0;
    else {
        struct cb_response_header identity; memset(&identity, 0, sizeof identity);
        for (unsigned cmd = 1; cmd <= 3; cmd++) if (request(fd, cmd, &identity)) { ok = 0; break; }
    }
    if (fd >= 0) close(fd);
    t = now_us(); detach(CAMMY_NATIVE_EXIT_QUEUE_CLOSED); uint64_t detach_us = now_us() - t;
    /* Never dlclose/free pinned state, even if the socket or descriptors survive. */
    int final_fds = fd_snapshot(after, &cloexec), final_tasks = task_count();
    int absent = lstat(SOCK, &st) && errno == ENOENT;
    int equal = initial_fds == final_fds && !memcmp(before, after, MAXFD);
    ok = ok && absent && equal && final_tasks == initial_tasks;
    printf("{\"event\":\"detach\",\"us\":%llu,\"socket_absent\":%s,\"fds_before\":%d,\"fds_after\":%d,\"fd_set_equal\":%s,\"tasks_before\":%d,\"tasks_after\":%d,\"pass\":%s}\n",
           (unsigned long long)detach_us, absent ? "true" : "false", initial_fds, final_fds, equal ? "true" : "false", initial_tasks, final_tasks, ok ? "true" : "false");
    return ok ? 0 : 8;
}
