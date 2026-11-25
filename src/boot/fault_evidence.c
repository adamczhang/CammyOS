/* SPDX-License-Identifier: MIT */
#define _GNU_SOURCE
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <stdint.h>
#include <signal.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <sys/vfs.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>
#include <unistd.h>
#include <sys/syscall.h>

#ifndef CARD
#define CARD "/mnt/extsd"
#endif
#ifndef PROC
#define PROC "/proc"
#endif
#ifndef RUN_DIR
#define RUN_DIR "/tmp/cammy-runtime"
#endif
#define OVERLAY "/overlay"
#define REPORTS 4
#define REPORT_BYTES (32L * 1024L)
#define RESERVED_BYTES (1024u * 1024u)
#define PROCESS_LIMIT 160
#define KERNEL_LINES 32
#define KERNEL_READ 65536
#define TRAIL_SLOTS 64u
#define TRAIL_SLOT_BYTES 512u
#define TRAIL_FILE_BYTES (TRAIL_SLOTS * TRAIL_SLOT_BYTES)
/* Seconds between unchanged trail samples: dense while startup is proving
 * health, sparse once committed. A phase change is always written at once. */
#define TRAIL_STARTUP_INTERVAL 8u
#define TRAIL_COMMITTED_INTERVAL 30u
#define CAP_MIN_BYTES 4096L
#define CAP_MAX_BYTES (1024L * 1024L)

/* Every TF/NOR write is bounded by alarm(4). The handler makes a stalled
 * write fail with EINTR (no SA_RESTART) instead of killing the process, so
 * one slow card write costs one sample, not the rest of the boot's trail. */
static volatile sig_atomic_t alarm_fired;
static void on_alarm(int sig) { (void)sig; alarm_fired = 1; }
static int bound_writes(void)
{
    struct sigaction sa = {.sa_handler = on_alarm};
    sigemptyset(&sa.sa_mask);
    return sigaction(SIGALRM, &sa, NULL);
}

static int word(const char *s, size_t max, const char *alphabet)
{
    size_t n = strlen(s);
    return n && n <= max && strspn(s, alphabet) == n;
}

static int card_present(void)
{
    FILE *in = fopen(PROC "/mounts", "re");
    if (!in) return 0;
    char line[1024], device[100], path[100], type[40];
    int found = 0;
    while (fgets(line, sizeof line, in)) {
        if (sscanf(line, "%99s %99s %39s", device, path, type) == 3 &&
            !strcmp(path, CARD)) {
            found = !strcmp(type, "vfat") &&
                    (!strcmp(device, "/dev/mmcblk0") || !strcmp(device, "/dev/mmcblk0p1"));
        }
    }
    fclose(in);
    return found;
}

static void meminfo(FILE *out)
{
    FILE *in = fopen(PROC "/meminfo", "re");
    if (!in) return;
    char line[160], name[64];
    unsigned long amount;
    fputs("[meminfo]\n", out);
    for (unsigned i = 0; i < 96 && fgets(line, sizeof line, in); ++i) {
        if (sscanf(line, "%63[^:]: %lu", name, &amount) == 2 &&
            word(name, 63, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_()"))
            fprintf(out, "%s: %lu%s\n", name, amount, strstr(line, " kB") ? " kB" : "");
    }
    fclose(in);
}

static void processes(FILE *out)
{
    DIR *dir = opendir(PROC);
    if (!dir) return;
    const char *known[] = {"sdvcam", "cammy-portal", "cammy-ui", "speaker-guardia",
                           "wpa_supplicant", "busybox", "sh", "hostapd", "udhcpd",
                           "GUIMainWindowTh", "face-status", "fault-evidence"};
    fputs("[processes] pid comm state start_ticks rss_pages\n", out);
    struct dirent *ent;
    unsigned count = 0;
    while (count < PROCESS_LIMIT && (ent = readdir(dir))) {
        if (!word(ent->d_name, 10, "0123456789")) continue;
        char file[64], line[1024];
        snprintf(file, sizeof file, PROC "/%.10s/stat", ent->d_name);
        FILE *in = fopen(file, "re");
        if (!in) continue;
        char *got = fgets(line, sizeof line, in);
        fclose(in);
        if (!got) continue;
        char *comm = strchr(line, '('), *end = strrchr(line, ')');
        if (!comm || !end || end <= comm || strlen(end) < 4 || end[1] != ' ' || end[3] != ' ') continue;
        *end = 0;
        const char *name = "other";
        for (size_t i = 0; i < sizeof known / sizeof known[0]; ++i)
            if (!strcmp(comm + 1, known[i])) name = known[i];
        char state = end[2];
        if (!strchr("RSDZTtXxKWPI", state)) continue;
        char *field = end + 4;
        /* State is field 3. Skip fields 4..21, read start time, vsize, RSS. */
        for (unsigned f = 4; field && f < 22; ++f) {
            field = strchr(field, ' ');
            if (field) ++field;
        }
        unsigned long long start, unused;
        long rss;
        if (field && sscanf(field, "%llu %llu %ld", &start, &unused, &rss) == 3) {
            fprintf(out, "%s %s %c %llu %ld\n", ent->d_name, name, state, start, rss);
            ++count;
        }
    }
    closedir(dir);
}

static int kmsg_class(const char *message)
{
    const char *oom[] = {"Out of memory:", "oom-kill:", "Killed process ", "Memory cgroup out of memory:"};
    for (size_t i = 0; i < sizeof oom / sizeof oom[0]; ++i)
        if (!strncmp(message, oom[i], strlen(oom[i]))) return 0;
    if (!strncmp(message, "Unable to handle kernel", 23) ||
        !strncmp(message, "Internal error:", 15)) return 1;
    if (!strncmp(message, "watchdog:", 9)) return 2;
    if (!strncmp(message, "mmc0:", 5) || !strncmp(message, "FAT-fs ", 7)) return 3;
    if ((!strncmp(message, "sdvcam[", 7) && strstr(message, "segfault")) ||
        (!strncmp(message, "sdvcam:", 7) && strstr(message, "unhandled"))) return 4;
    return -1;
}

static void kernel_tail(FILE *out)
{
    static char raw[KERNEL_READ + 1];
    static const char *const classes[] = {"oom", "kernel-fault", "watchdog", "tf", "sdvcam-segfault"};
    struct entry { unsigned long sec, usec; int kind; } tail[KERNEL_LINES];
    int len = (int)syscall(SYS_syslog, 3, raw, KERNEL_READ); /* read last bytes, do not clear */
    unsigned n = 0, at = 0;
    if (len <= 0 || len > KERNEL_READ) {
        fputs("[kernel tail] unavailable\n", out);
        return;
    }
    raw[len] = 0;
    fputs("[kernel tail] filtered event categories only\n", out);
    char *save = NULL;
    for (char *line = strtok_r(raw, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        unsigned level;
        unsigned long sec, usec;
        int cut = 0;
        if (sscanf(line, "<%u>[%lu.%lu] %n", &level, &sec, &usec, &cut) != 3 || !cut) continue;
        int kind = kmsg_class(line + cut);
        if (kind < 0) continue;
        tail[at] = (struct entry){sec, usec, kind};
        at = (at + 1) % KERNEL_LINES;
        if (n < KERNEL_LINES) ++n;
    }
    for (unsigned i = 0; i < n; ++i) {
        struct entry e = tail[(at + KERNEL_LINES - n + i) % KERNEL_LINES];
        fprintf(out, "%lu.%06lu %s\n", e.sec, e.usec, classes[e.kind]);
    }
}

static int flush_file(FILE *out)
{
    int failed = fflush(out) || fsync(fileno(out));
    return fclose(out) || failed;
}

static int open_card(void)
{
    if (!card_present()) return -1;
    int root = open(CARD, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (root < 0) return -1;
    struct statfs fs;
    if (fstatfs(root, &fs) || fs.f_type != MSDOS_SUPER_MAGIC ||
        (uint64_t)fs.f_bavail * (uint64_t)fs.f_bsize < RESERVED_BYTES) {
        close(root);
        return -1;
    }
    return root;
}

/* Both callers exit after publication, apart from the trail writer that
 * needs to release every descriptor before polling again. */
static int publish_report(int root, char **args, const char *previous)
{
    if (mkdirat(root, "CammyOS-faults", 0700) && errno != EEXIST) return 1;
    int dir = openat(root, "CammyOS-faults", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return 1;
    struct stat st, root_st;
    if (fstat(dir, &st) || fstat(root, &root_st) || st.st_dev != root_st.st_dev) goto failed;
    unsigned oldest = 0;
    time_t mtime = 0;
    char target[24];
    for (unsigned i = 0; !previous && i < REPORTS; ++i) {
        snprintf(target, sizeof target, "fault-%u.txt", i);
        if (fstatat(dir, target, &st, AT_SYMLINK_NOFOLLOW)) {
            if (errno != ENOENT) goto failed;
            oldest = i;
            break;
        }
        if (!S_ISREG(st.st_mode)) goto failed;
        if (!i || st.st_mtime < mtime) { mtime = st.st_mtime; oldest = i; }
    }
    if (previous) snprintf(target, sizeof target, "previous-boot.txt");
    else snprintf(target, sizeof target, "fault-%u.txt", oldest);
    const char *temp = previous ? ".previous-pending" : ".pending";
    if (!fstatat(dir, temp, &st, AT_SYMLINK_NOFOLLOW) &&
        (!S_ISREG(st.st_mode) || unlinkat(dir, temp, 0))) goto failed;
    int fd = openat(dir, temp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) goto failed;
    FILE *out = fdopen(fd, "w");
    if (!out) { close(fd); goto failed; }
    fprintf(out, "format=cammy-first-fault/1\nboot=%s\nrelease=%s\nreason=%s\nphase=%s\nuptime_s=%s\n",
            args[0], args[1], args[2], args[3], args[4]);
    if (previous) fprintf(out, "[previous boot breadcrumb]\n%s", previous);
    else {
        meminfo(out);
        processes(out);
        kernel_tail(out);
    }
    long bytes = ftell(out);
    int invalid = bytes <= 0 || bytes > REPORT_BYTES || ferror(out);
    if (flush_file(out) || invalid || renameat(dir, temp, dir, target) || fsync(dir)) goto failed;
    close(dir);
    return 0;
failed:
    close(dir);
    return 1;
}

static int snapshot(char **args)
{
    const char *hex = "0123456789abcdef";
    const char *id = "0123456789abcdef-";
    const char *label = "abcdefghijklmnopqrstuvwxyz0123456789-";
    if (strlen(args[0]) != 36 || !word(args[0], 36, id) || strlen(args[1]) != 24 ||
        !word(args[1], 24, hex) || !word(args[2], 64, label) ||
        !word(args[3], 64, "abcdefghijklmnopqrstuvwxyz_-") || !word(args[4], 12, "0123456789")) return 1;
    /* One-shot: the default SIGALRM action ends the process, which aborts
     * its only write and leaves just the .pending file. */
    alarm(4);
    int root = open_card();
    if (root < 0) return 1;
    int rc = publish_report(root, args, NULL);
    close(root);
    return rc;
}

struct trail_slot {
    uint32_t sequence;
    uint32_t checksum;
    char text[TRAIL_SLOT_BYTES - 8u];
};
_Static_assert(sizeof(struct trail_slot) == TRAIL_SLOT_BYTES, "trail slot size");

static uint32_t trail_checksum(const struct trail_slot *slot)
{
    uint32_t hash = slot->sequence;
    for (size_t i = 0; i < sizeof slot->text; i++)
        hash = (hash ^ (unsigned char)slot->text[i]) * 16777619u;
    return hash;
}

static int trail_open(int root)
{
    int fd = openat(root, "CammyOS-trail", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) ||
        (st.st_size != TRAIL_FILE_BYTES &&
         (st.st_size || ftruncate(fd, TRAIL_FILE_BYTES) || fsync(fd) || fsync(root)))) {
        close(fd);
        return -1;
    }
    return fd;
}

static struct trail_slot trail_latest(int fd)
{
    struct trail_slot latest = {0}, sample;
    for (unsigned i = 0; i < TRAIL_SLOTS; i++) {
        if (pread(fd, &sample, sizeof sample, (off_t)(i * sizeof sample)) != sizeof sample ||
            !sample.sequence || sample.sequence % TRAIL_SLOTS != i ||
            !memchr(sample.text, 0, sizeof sample.text) || sample.checksum != trail_checksum(&sample))
            continue;
        if (!latest.sequence || (int32_t)(sample.sequence - latest.sequence) > 0) latest = sample;
    }
    return latest;
}

static int request_kind(const char *kind)
{
    return !strcmp(kind, "lease") || !strcmp(kind, "media-prepare") ||
           !strcmp(kind, "speaker") || !strcmp(kind, "capture-mode");
}

static void copy_previous(int root, const struct trail_slot *latest, const char *boot)
{
    char prior[37], release[25], uptime[13], phase[32], request[32];
    unsigned pid;
    unsigned long available, cached, slab, rss, threads;
    if (!latest->sequence ||
        sscanf(latest->text, "boot=%36[0-9a-f-]\nrelease=%24[0-9a-f]\nuptime_s=%12[0-9]\nphase=%31[a-z_]\n"
               "last_request=%31[a-z-]\nMemAvailable_kB=%lu\nCached_kB=%lu\nSlab_kB=%lu\n"
               "sdvcam_pid=%u\nsdvcam_RSS_kB=%lu\nsdvcam_threads=%lu",
               prior, release, uptime, phase, request, &available, &cached, &slab, &pid, &rss, &threads) != 11 ||
        strlen(prior) != 36 || strlen(release) != 24 || !strcmp(prior, boot) ||
        !strcmp(phase, "shutdown")) return;
    char fields[256];
    snprintf(fields, sizeof fields, "last_request=%s\nMemAvailable_kB=%lu\nCached_kB=%lu\nSlab_kB=%lu\n"
             "sdvcam_pid=%u\nsdvcam_RSS_kB=%lu\nsdvcam_threads=%lu\n",
             request_kind(request) ? request : "none", available, cached, slab, pid, rss, threads);
    char *reason = !strcmp(phase, "committed") ? "abrupt-after-commit" : "interrupted-boot";
    char *args[] = {prior, release, reason, phase, uptime};
    publish_report(root, args, fields);
}

static int line_at(const char *path, char *line, size_t size)
{
    FILE *in = fopen(path, "re");
    if (!in) return 0;
    int got = fgets(line, (int)size, in) != NULL;
    fclose(in);
    if (got) line[strcspn(line, "\r\n")] = 0;
    return got;
}

/* Shared capture's independent guardian keeps its one-second ownership
 * check without starting a sleep process on every pass. The shell still
 * owns epoch validation and restoration. */
static int watch_shared(const char *pid, const char *start_tick, const char *epoch)
{
    if (!word(pid, 10, "0123456789") || !word(start_tick, 20, "0123456789") ||
        !word(epoch, 40, "0123456789-")) return 1;
    pid_t parent = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) || parent != getppid()) return 1;
    char path[96], current[64], line[1024];
    snprintf(path, sizeof path, PROC "/%s/stat", pid);
    while (line_at(RUN_DIR "/shared-capture/epoch", current, sizeof current) &&
           !strcmp(current, epoch) && line_at(path, line, sizeof line)) {
        const char *end = strrchr(line, ')');
        if (!end || end[1] != ' ' || !end[2] || end[2] == 'Z' || end[3] != ' ') break;
        const char *field = end + 4;
        for (unsigned n = 4; field && n < 22; n++) {
            field = strchr(field, ' ');
            if (field) field++;
        }
        if (!field || strcspn(field, " ") != strlen(start_tick) ||
            strncmp(field, start_tick, strlen(start_tick))) break;
        const struct timespec delay = {.tv_sec = 1};
        nanosleep(&delay, NULL);
    }
    return 0;
}

static unsigned stock_pid(unsigned cached)
{
    char path[96], name[32];
    snprintf(path, sizeof path, PROC "/%u/comm", cached);
    if (cached && line_at(path, name, sizeof name) && !strcmp(name, "sdvcam")) return cached;
    DIR *dir = opendir(PROC);
    if (!dir) return 0;
    struct dirent *ent;
    unsigned pid = 0;
    while ((ent = readdir(dir))) {
        if (!word(ent->d_name, 9, "0123456789")) continue;
        snprintf(path, sizeof path, PROC "/%.9s/comm", ent->d_name);
        if (line_at(path, name, sizeof name) && !strcmp(name, "sdvcam")) {
            pid = (unsigned)strtoul(ent->d_name, NULL, 10);
            break;
        }
    }
    closedir(dir);
    return pid;
}

static void trail_stats(char *out, size_t size, unsigned *pid)
{
    char path[96], line[160];
    unsigned long available = 0, cached = 0, slab = 0, rss = 0, threads = 0;
    FILE *in = fopen(PROC "/meminfo", "re");
    if (in) {
        while (fgets(line, sizeof line, in)) {
            if (sscanf(line, "MemAvailable: %lu", &available) == 1) continue;
            if (sscanf(line, "Cached: %lu", &cached) == 1) continue;
            if (sscanf(line, "Slab: %lu", &slab) == 1) break;
        }
        fclose(in);
    }
    *pid = stock_pid(*pid);
    snprintf(path, sizeof path, PROC "/%u/status", *pid);
    in = *pid ? fopen(path, "re") : NULL;
    if (in) {
        while (fgets(line, sizeof line, in)) {
            if (sscanf(line, "VmRSS: %lu", &rss) == 1) continue;
            if (sscanf(line, "Threads: %lu", &threads) == 1) break;
        }
        fclose(in);
    }
    snprintf(out, size, "MemAvailable_kB=%lu\nCached_kB=%lu\nSlab_kB=%lu\nsdvcam_pid=%u\n"
             "sdvcam_RSS_kB=%lu\nsdvcam_threads=%lu\n", available, cached, slab, *pid, rss, threads);
}

/* Kernel taint, read in one read(): a sysctl file returns EOF to any read at
 * a non-zero offset, so the shell's byte-wise `read` only ever sees "4". */
static unsigned long read_taint(void)
{
    char text[32];
    int fd = open(PROC "/sys/kernel/tainted", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t got = read(fd, text, sizeof text - 1);
    close(fd);
    if (got <= 0) return 0;
    text[got] = 0;
    return strtoul(text, NULL, 10);
}

/* The resident trail is also startup's oops watch: when taint bit 7
 * (TAINT_DIE, 128) appears after the trail started, it leaves a RAM marker
 * the supervisor tests with a shell builtin, and writes a sample at once. */
static int mark_oops(unsigned long taint, unsigned long seconds)
{
    int fd = open(RUN_DIR "/kernel-oops", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return errno == EEXIST;
    char text[64];
    int n = snprintf(text, sizeof text, "taint=%lu\nuptime_s=%lu\n", taint, seconds);
    int ok = n > 0 && write(fd, text, (size_t)n) == n;
    return !close(fd) && ok;
}

static volatile sig_atomic_t trail_stop;
static void stop_trail(int sig) { (void)sig; trail_stop = 1; }

static int trail(const char *boot, const char *release)
{
    if (strlen(boot) != 36 || !word(boot, 36, "0123456789abcdef-") ||
        strlen(release) != 24 || !word(release, 24, "0123456789abcdef")) return 1;
    struct sigaction sa = {.sa_handler = stop_trail};
    sigemptyset(&sa.sa_mask);
    pid_t parent = getppid();
    if (sigaction(SIGTERM, &sa, NULL) || bound_writes() || prctl(PR_SET_PDEATHSIG, SIGTERM) ||
        parent != getppid()) return 1;
    alarm(4);
    int root = open_card();
    if (root < 0) return 1;
    int fd = trail_open(root);
    if (fd < 0) { close(root); return 1; }
    struct trail_slot latest = trail_latest(fd);
    copy_previous(root, &latest, boot);
    close(root);
    uint32_t sequence = latest.sequence;
    unsigned pid = 0;
    unsigned long last_sample = 0;
    char last_phase[32] = "";
    unsigned long taint_base = read_taint();
    int oops_seen = 0;
    alarm(0);
    for (;;) {
        int stopping = trail_stop;
        const char *phase = "unknown";
        char guard[64], stage[32], request[32], uptime[64], stats[256];
        if (stopping && access(RUN_DIR "/trail-shutdown", F_OK)) break;
        if (stopping) phase = "shutdown";
        else if (line_at("/etc/cammy/startup.pending", guard, sizeof guard) &&
                 (!strcmp(guard, "phase=media_prepare") || !strcmp(guard, "phase=runtime_health_fault") ||
                  !strcmp(guard, "phase=post_healthy_fault") || !strcmp(guard, "phase=radio_fault"))) phase = guard + 6;
        else if (!access(RUN_DIR "/release-health/committed", F_OK)) phase = "committed";
        else if (line_at(RUN_DIR "/startup.phase", stage, sizeof stage) &&
                 word(stage, 31, "abcdefghijklmnopqrstuvwxyz_")) phase = stage;
        unsigned long seconds = 0;
        if (line_at(PROC "/uptime", uptime, sizeof uptime)) seconds = strtoul(uptime, NULL, 10);
        unsigned long interval = strcmp(phase, "committed") ? TRAIL_STARTUP_INTERVAL : TRAIL_COMMITTED_INTERVAL;
        int oops_now = 0;
        if (!oops_seen && !(taint_base & 128u) && (read_taint() & 128u)) oops_seen = oops_now = mark_oops(read_taint(), seconds);
        if (!stopping && !oops_now && !strcmp(phase, last_phase) && seconds >= last_sample && seconds-last_sample < interval) {
            const struct timespec delay = {.tv_sec = 2};
            nanosleep(&delay, NULL);
            continue;
        }
        last_sample = seconds;
        snprintf(last_phase, sizeof last_phase, "%.31s", phase);
        if (!line_at(RUN_DIR "/last-request", request, sizeof request) ||
            !request_kind(request)) strcpy(request, "none");
        trail_stats(stats, sizeof stats, &pid);
        if (++sequence == 0) sequence++;
        struct trail_slot slot = {.sequence = sequence};
        snprintf(slot.text, sizeof slot.text, "boot=%s\nrelease=%s\nuptime_s=%lu\nphase=%s\n"
                 "last_request=%s\n%s", boot, release, seconds, phase, request, stats);
        slot.checksum = trail_checksum(&slot);
        alarm_fired = 0;
        alarm(4);
        off_t offset = (off_t)((sequence % TRAIL_SLOTS) * sizeof slot);
        int written = pwrite(fd, &slot, sizeof slot, offset) == sizeof slot && !fdatasync(fd);
        alarm(0);
        /* A write the alarm cut short (EINTR) is skipped; a torn slot fails its
         * checksum and the reader keeps the previous one. Any other error ends
         * the trail as before. */
        if (!written && !alarm_fired) break;
        if (stopping) { close(fd); return written ? 0 : 1; }
    }
    close(fd);
    return 1;
}

/* BusyBox on the camera has no mkfifo/mknod; the supervisor's wake FIFO. */
static int make_fifo(const char *path)
{
    if (strncmp(path, RUN_DIR "/", sizeof RUN_DIR) || strstr(path, "/..")) return 1;
    return mkfifo(path, 0600) ? 1 : 0;
}

/* Keep each RAM log at most `limit` bytes without a per-line fork: an
 * oversized file keeps its newest half in place. Writers use O_APPEND, so
 * they continue at the new end after the truncate. */
static int cap_logs(const char *limit_text, char **files, int count)
{
    if (!word(limit_text, 7, "0123456789")) return 1;
    long limit = strtol(limit_text, NULL, 10);
    if (limit < CAP_MIN_BYTES || limit > CAP_MAX_BYTES) return 1;
    static char keep[CAP_MAX_BYTES / 2];
    static const char mark[] = "[earlier lines dropped: log cap]\n";
    int failed = 0;
    for (int i = 0; i < count; ++i) {
        if (strncmp(files[i], RUN_DIR "/", sizeof RUN_DIR) && strcmp(files[i], "/tmp/cammy-startup.log")) { failed = 1; continue; }
        int fd = open(files[i], O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue; /* an unmatched glob or a log not yet created */
        struct stat st;
        if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size <= limit) { close(fd); continue; }
        size_t want = (size_t)(limit / 2) - sizeof mark;
        ssize_t got = pread(fd, keep, want, st.st_size - (off_t)want);
        if (got < 0) { failed = 1; close(fd); continue; }
        /* Start the kept part at a line boundary. */
        char *start = memchr(keep, '\n', (size_t)got);
        start = start ? start + 1 : keep;
        size_t len = (size_t)(keep + got - start);
        if (ftruncate(fd, 0) || pwrite(fd, mark, sizeof mark - 1, 0) != (ssize_t)(sizeof mark - 1) ||
            pwrite(fd, start, len, (off_t)(sizeof mark - 1)) != (ssize_t)len) failed = 1;
        close(fd);
    }
    return failed;
}

/* One wpa_supplicant control request, bounded, without wpa_cli and grep.
 * STATUS exits 0 only for wpa_state=COMPLETED (the line is printed);
 * any other command exits 0 when the reply begins with OK. */
static int wpa_request(const char *ctrl_dir, const char *iface, const char *command)
{
    if (strncmp(ctrl_dir, RUN_DIR "/", sizeof RUN_DIR) || !word(iface, 15, "abcdefghijklmnopqrstuvwxyz0123456789") ||
        (strcmp(command, "STATUS") && strcmp(command, "REASSOCIATE"))) return 2;
    alarm(3); /* default action: a wedged socket can never hold the caller */
    struct sockaddr_un peer = {.sun_family = AF_UNIX};
    int n = snprintf(peer.sun_path, sizeof peer.sun_path, "%s/%s", ctrl_dir, iface);
    if (n < 0 || (size_t)n >= sizeof peer.sun_path) return 2;
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return 1;
    /* Autobind to an abstract address so the reply has a destination and
     * nothing is left in the control directory. */
    struct sockaddr_un local = {.sun_family = AF_UNIX};
    char reply[2048];
    if (bind(fd, (struct sockaddr *)&local, sizeof(sa_family_t)) ||
        connect(fd, (struct sockaddr *)&peer, sizeof peer) ||
        send(fd, command, strlen(command), 0) < 0) { close(fd); return 1; }
    struct pollfd p = {fd, POLLIN, 0};
    ssize_t got = poll(&p, 1, 2000) == 1 ? recv(fd, reply, sizeof reply - 1, 0) : -1;
    close(fd);
    if (got <= 0) return 1;
    reply[got] = 0;
    if (strcmp(command, "STATUS")) return strncmp(reply, "OK", 2) ? 1 : 0;
    for (char *line = reply; line && *line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : NULL) {
        if (strncmp(line, "wpa_state=", 10)) continue;
        size_t len = strcspn(line, "\n");
        printf("%.*s\n", (int)len, line);
        return len == 19 && !strncmp(line, "wpa_state=COMPLETED", 19) ? 0 : 1;
    }
    return 1;
}

int main(int argc, char **argv)
{
    if (argc == 5 && !strcmp(argv[1], "watch-shared")) return watch_shared(argv[2], argv[3], argv[4]);
    if (argc == 4 && !strcmp(argv[1], "trail")) return trail(argv[2], argv[3]);
    if (argc == 7 && !strcmp(argv[1], "snapshot")) return snapshot(argv + 2);
    if (argc == 5 && !strcmp(argv[1], "wpa")) return wpa_request(argv[2], argv[3], argv[4]);
    if (argc == 3 && !strcmp(argv[1], "mkfifo")) return make_fifo(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "cap")) return cap_logs(argv[2], argv + 3, argc - 3);
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "sync")) {
        int fd = open(OVERLAY, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        return fd < 0 || syscall(SYS_syncfs, fd) ? 1 : 0;
    }
    if (!strcmp(argv[1], "space")) {
        struct statfs fs;
        if (statfs(OVERLAY, &fs) || fs.f_type != JFFS2_SUPER_MAGIC || fs.f_bsize <= 0) return 1;
        printf("%llu\n", (unsigned long long)fs.f_bavail * (unsigned long long)fs.f_bsize);
        return 0;
    }
    return 2;
}
