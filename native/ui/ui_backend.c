/* SPDX-License-Identifier: MIT. cammy-ui's real backend on the camera:
 *  - Wi-Fi through wpa_supplicant's control socket ($R/ctrl/wlan0), the runtime's own
 *    instance; joins persist to /etc/cammy/home.conf only after they succeed;
 *  - agent info from the local portal's /v1/state (bearer token from /etc/cammy);
 *  - volume in /etc/cammy/volume (the portal scales speaker PCM by it);
 *  - camera mode through the ui page;
 *  - the loading gate's boot records (ui_boot.h), read only while the gate holds. */
#include "ui_backend.h"
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <net/if.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}
static int read_small(const char *path, char *out, size_t n) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t k = read(fd, out, n - 1);
    close(fd);
    if (k < 0) return -1;
    out[k] = 0;
    return 0;
}
/* Writes path atomically (tmp + fsync + rename). */
static int write_atomic(const char *path, const char *data, size_t n, mode_t mode) {
    char tmp[256];
    snprintf(tmp, sizeof tmp, "%s.ui-new", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) return -1;
    size_t done = 0;
    while (done < n) {
        ssize_t w = write(fd, data + done, n - done);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) { close(fd); unlink(tmp); return -1; }
        done += (size_t)w;
    }
    if (fsync(fd) || close(fd)) { unlink(tmp); return -1; }
    if (rename(tmp, path)) { unlink(tmp); return -1; }
    return 0;
}

/* ---- wpa_supplicant control socket ---- */
static int wpa(struct ui_real *r, const char *cmd, char *reply, size_t n) {
    if (r->wpa_fd < 0) {
        int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
        if (fd < 0) return -1;
        struct sockaddr_un local = {.sun_family = AF_UNIX}, peer = {.sun_family = AF_UNIX};
        snprintf(local.sun_path, sizeof local.sun_path, "%s/ctrl/cammy-ui-%d", r->run_dir, (int)getpid());
        unlink(local.sun_path);
        snprintf(peer.sun_path, sizeof peer.sun_path, "%s/ctrl/wlan0", r->run_dir);
        if (bind(fd, (struct sockaddr *)&local, sizeof local) || connect(fd, (struct sockaddr *)&peer, sizeof peer)) {
            unlink(local.sun_path); close(fd); return -1;
        }
        r->wpa_fd = fd;
    }
    char junk[64];
    while (recv(r->wpa_fd, junk, sizeof junk, MSG_DONTWAIT) > 0) {} /* stale replies */
    if (send(r->wpa_fd, cmd, strlen(cmd), 0) < 0) goto reset;
    struct pollfd p = {r->wpa_fd, POLLIN, 0};
    if (poll(&p, 1, 1500) != 1) goto reset;
    ssize_t k = recv(r->wpa_fd, reply, n - 1, 0);
    if (k < 0) goto reset;
    reply[k] = 0;
    return 0;
reset: {
        struct sockaddr_un local = {.sun_family = AF_UNIX};
        snprintf(local.sun_path, sizeof local.sun_path, "%s/ctrl/cammy-ui-%d", r->run_dir, (int)getpid());
        unlink(local.sun_path);
        close(r->wpa_fd); r->wpa_fd = -1;
        return -1;
    }
}
static int wpa_ok(struct ui_real *r, const char *cmd) {
    char reply[64];
    return wpa(r, cmd, reply, sizeof reply) || strncmp(reply, "OK", 2) ? -1 : 0;
}
static const char *field(const char *text, const char *key, char *out, size_t n) {
    size_t k = strlen(key);
    for (const char *p = text; p && *p; p = strchr(p, '\n') ? strchr(p, '\n') + 1 : NULL)
        if (!strncmp(p, key, k) && p[k] == '=') {
            const char *v = p + k + 1, *e = strchr(v, '\n');
            size_t len = e ? (size_t)(e - v) : strlen(v);
            if (len >= n) len = n - 1;
            memcpy(out, v, len); out[len] = 0;
            return out;
        }
    out[0] = 0;
    return NULL;
}
/* wpa_supplicant prints SSIDs with printf_encode escapes; undo them. */
static size_t ssid_decode(const char *in, char *out, size_t n) {
    size_t k = 0;
    while (*in && k + 1 < n) {
        if (*in != '\\') { out[k++] = *in++; continue; }
        in++;
        if (*in == 'x' && in[1] && in[2]) {
            char hex[3] = {in[1], in[2], 0};
            out[k++] = (char)strtoul(hex, NULL, 16); in += 3;
        } else if (*in == 'n') { out[k++] = '\n'; in++; }
        else if (*in == 'r') { out[k++] = '\r'; in++; }
        else if (*in == 't') { out[k++] = '\t'; in++; }
        else if (*in == 'e') { out[k++] = 27; in++; }
        else if (*in) out[k++] = *in++;
    }
    out[k] = 0;
    return k;
}
static int bars(int rssi) { return rssi >= -60 ? 3 : rssi >= -72 ? 2 : 1; }
static void own_ip(char *out, size_t n) {
    out[0] = 0;
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return;
    struct ifreq q;
    memset(&q, 0, sizeof q);
    snprintf(q.ifr_name, sizeof q.ifr_name, "wlan0");
    if (!ioctl(fd, SIOCGIFADDR, &q)) {
        struct sockaddr_in a;
        memcpy(&a, &q.ifr_addr, sizeof a);
        (void)inet_ntop(AF_INET, &a.sin_addr, out, (socklen_t)n);
    }
    close(fd);
}
static int lease_ip(struct ui_real *r, char *out, size_t n) {
    char path[256], text[256];
    snprintf(path, sizeof path, "%s/lease.txt", r->run_dir);
    out[0] = 0;
    if (read_small(path, text, sizeof text)) return -1;
    return field(text, "ip", out, n) ? 0 : -1;
}
static void renew_dhcp(struct ui_real *r) {
    char path[256], text[32], comm[64];
    snprintf(path, sizeof path, "%s/dhcp.pid", r->run_dir);
    if (read_small(path, text, sizeof text)) return;
    long pid = strtol(text, NULL, 10);
    if (pid <= 1) return;
    snprintf(path, sizeof path, "/proc/%ld/comm", pid);
    if (read_small(path, comm, sizeof comm) || (strncmp(comm, "busybox", 7) && strncmp(comm, "udhcpc", 6))) return;
    (void)kill((pid_t)pid, SIGUSR2); /* release the old network's lease */
    (void)kill((pid_t)pid, SIGUSR1); /* and ask the new one */
}
static int r_status(void *c, struct wifi_status *w) {
    struct ui_real *r = c;
    char path[256], state[32], text[2048], v[64];
    memset(w, 0, sizeof *w);
    w->signal = -1;
    snprintf(path, sizeof path, "%s/state", r->run_dir);
    if (!read_small(path, state, sizeof state) && !strncmp(state, "AP_", 3)) { w->mode = WIFI_HOTSPOT; return 0; }
    w->mode = WIFI_HOME;
    if (wpa(r, "STATUS", text, sizeof text)) { snprintf(w->state, sizeof w->state, "Wi-Fi service down"); return 0; }
    field(text, "wpa_state", w->state, sizeof w->state);
    w->connected = !strcmp(w->state, "COMPLETED");
    if (field(text, "ssid", v, sizeof v)) (void)ssid_decode(v, w->ssid, sizeof w->ssid);
    if (w->connected && r->pending_id >= 0 && !r->renewed && !strcmp(w->ssid, r->pending_ssid)) {
        renew_dhcp(r); r->renewed = 1; r->renew_at = now_ms();
    }
    /* Address only from a lease taken after a join's renew (the old one may linger). */
    if (w->connected && (!r->renewed || now_ms() - r->renew_at > 1500u)) (void)lease_ip(r, w->ip, sizeof w->ip);
    if (w->connected && !wpa(r, "SIGNAL_POLL", text, sizeof text) && field(text, "RSSI", v, sizeof v)) w->signal = bars(atoi(v));
    if (!w->connected && !strcmp(w->state, "SCANNING")) snprintf(w->state, sizeof w->state, "Searching...");
    else if (!w->connected && w->state[0]) snprintf(w->state, sizeof w->state, "Not connected");
    return 0;
}
static int r_scan(void *c) {
    struct ui_real *r = c;
    char reply[64];
    if (wpa(r, "SCAN", reply, sizeof reply)) return -1;
    if (strncmp(reply, "OK", 2) && strncmp(reply, "FAIL-BUSY", 9)) return -1;
    r->scan_at = now_ms();
    return 0;
}
static int r_results(void *c, struct wifi_net *nets, int max) {
    struct ui_real *r = c;
    if (now_ms() - r->scan_at < 3500u) return -1;
    static char text[16384];
    if (wpa(r, "SCAN_RESULTS", text, sizeof text)) return -1;
    int n = 0, rssi[UI_NETS];
    char *save = NULL;
    for (char *line = strtok_r(text, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *col[5] = {0};
        int k = 0;
        for (char *p = line; k < 5; k++) { col[k] = p; char *t = strchr(p, '\t'); if (!t) { k++; break; } *t = 0; p = t + 1; }
        if (k < 5 || !col[4] || !col[4][0] || !strcmp(col[0], "bssid")) continue;
        struct wifi_net net;
        memset(&net, 0, sizeof net);
        if (!ssid_decode(col[4], net.ssid, sizeof net.ssid)) continue;
        int level = atoi(col[2]);
        net.secured = strstr(col[3], "WPA") || strstr(col[3], "WEP") || strstr(col[3], "SAE") || strstr(col[3], "RSN");
        net.bars = bars(level);
        int at = -1;
        for (int i = 0; i < n; i++) if (!strcmp(nets[i].ssid, net.ssid)) at = i;
        if (at >= 0) { if (level > rssi[at]) { rssi[at] = level; nets[at].bars = net.bars; } continue; }
        if (n < max && n < UI_NETS) { nets[n] = net; rssi[n++] = level; }
        else {
            int weakest = 0;
            for (int i = 1; i < n; i++) if (rssi[i] < rssi[weakest]) weakest = i;
            if (n && level > rssi[weakest]) { nets[weakest] = net; rssi[weakest] = level; }
        }
    }
    for (int i = 1; i < n; i++) /* strongest first */
        for (int j = i; j > 0 && rssi[j] > rssi[j - 1]; j--) {
            struct wifi_net t = nets[j]; nets[j] = nets[j - 1]; nets[j - 1] = t;
            int x = rssi[j]; rssi[j] = rssi[j - 1]; rssi[j - 1] = x;
        }
    return n;
}
static void hex(const char *s, char *out) {
    static const char d[] = "0123456789abcdef";
    for (; *s; s++) { *out++ = d[(unsigned char)*s >> 4]; *out++ = d[(unsigned char)*s & 15]; }
    *out = 0;
}
static int r_join(void *c, const char *ssid, const char *psk) {
    struct ui_real *r = c;
    char reply[64], cmd[256], hx[2 * UI_SSID_MAX + 1];
    if (psk) {
        size_t n = strlen(psk);
        if (n < 8 || n > 63) return -1;
        for (size_t i = 0; i < n; i++) if (psk[i] < 32 || psk[i] > 126) return -1;
    }
    if (wpa(r, "ADD_NETWORK", reply, sizeof reply)) return -1;
    char *end;
    long id = strtol(reply, &end, 10);
    if (end == reply || id < 0) return -1;
    hex(ssid, hx);
    int bad = 0;
    snprintf(cmd, sizeof cmd, "SET_NETWORK %ld ssid %s", id, hx); bad |= wpa_ok(r, cmd);
    if (psk) { snprintf(cmd, sizeof cmd, "SET_NETWORK %ld psk \"%s\"", id, psk); bad |= wpa_ok(r, cmd); }
    else { snprintf(cmd, sizeof cmd, "SET_NETWORK %ld key_mgmt NONE", id); bad |= wpa_ok(r, cmd); }
    snprintf(cmd, sizeof cmd, "SET_NETWORK %ld priority 10", id); bad |= wpa_ok(r, cmd);
    if (!bad) { snprintf(cmd, sizeof cmd, "SELECT_NETWORK %ld", id); bad |= wpa_ok(r, cmd); }
    if (bad) { snprintf(cmd, sizeof cmd, "REMOVE_NETWORK %ld", id); (void)wpa_ok(r, cmd); (void)wpa_ok(r, "ENABLE_NETWORK all"); return -1; }
    r->pending_id = (int)id; r->renewed = 0;
    snprintf(r->pending_ssid, sizeof r->pending_ssid, "%s", ssid);
    snprintf(r->pending_psk, sizeof r->pending_psk, "%s", psk ? psk : "");
    return 0;
}
static int r_reconnect(void *c) { return wpa_ok(c, "RECONNECT"); }
/* Rewrites home.conf: the original text, minus an earlier cammy-ui block for the same
 * SSID, plus the new block. Keeps one copy of the pre-menu file. */
static int persist(struct ui_real *r) {
    static char old[16384], out[20480];
    char marker[128], hx[2 * UI_SSID_MAX + 1], backup[256];
    if (read_small(r->home_conf, old, sizeof old)) return -1;
    if (strlen(old) > sizeof old - 2) return -1;
    snprintf(backup, sizeof backup, "%s.before-menu", r->home_conf);
    if (access(backup, F_OK) && write_atomic(backup, old, strlen(old), 0600)) return -1;
    hex(r->pending_ssid, hx);
    snprintf(marker, sizeof marker, "# cammy-ui ssid=%s\n", hx);
    size_t k = 0;
    for (const char *p = old; *p;) {
        if (!strncmp(p, marker, strlen(marker))) {
            const char *close = strstr(p, "\n}");
            p = close ? close + 2 : p + strlen(p);
            if (*p == '\n') p++;
            continue;
        }
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p + 1) : strlen(p);
        memcpy(out + k, p, len); k += len; p += len;
    }
    if (k && out[k - 1] != '\n') out[k++] = '\n';
    int w = r->pending_psk[0]
        ? snprintf(out + k, sizeof out - k, "%snetwork={\n\tssid=%s\n\tpsk=\"%s\"\n\tpriority=10\n}\n", marker, hx, r->pending_psk)
        : snprintf(out + k, sizeof out - k, "%snetwork={\n\tssid=%s\n\tkey_mgmt=NONE\n\tpriority=10\n}\n", marker, hx);
    if (w < 0 || (size_t)w >= sizeof out - k) return -1;
    return write_atomic(r->home_conf, out, k + (size_t)w, 0600);
}
static int r_commit(void *c) {
    struct ui_real *r = c;
    (void)wpa_ok(r, "ENABLE_NETWORK all"); /* SELECT disabled the others for this session */
    int rc = persist(r);
    r->pending_id = -1;
    memset(r->pending_psk, 0, sizeof r->pending_psk);
    return rc;
}
static int r_abort(void *c) {
    struct ui_real *r = c;
    char cmd[64];
    if (r->pending_id >= 0) { snprintf(cmd, sizeof cmd, "REMOVE_NETWORK %d", r->pending_id); (void)wpa_ok(r, cmd); }
    r->pending_id = -1;
    memset(r->pending_psk, 0, sizeof r->pending_psk);
    (void)wpa_ok(r, "ENABLE_NETWORK all");
    return wpa_ok(r, "REASSOCIATE");
}

/* ---- portal ---- */
static int json_str(const char *j, const char *key, char *out, size_t n) {
    char pat[48];
    snprintf(pat, sizeof pat, "\"%s\":\"", key);
    const char *p = strstr(j, pat);
    out[0] = 0;
    if (!p) return -1;
    p += strlen(pat);
    size_t k = 0;
    while (*p && *p != '"' && k + 1 < n) out[k++] = *p++;
    out[k] = 0;
    return 0;
}
static long json_num(const char *j, const char *key) {
    char pat[48];
    snprintf(pat, sizeof pat, "\"%s\":", key);
    const char *p = strstr(j, pat);
    return p ? strtol(p + strlen(pat), NULL, 10) : -1;
}
static int r_agent(void *c, struct agent_info *g) {
    struct ui_real *r = c;
    memset(g, 0, sizeof *g);
    own_ip(g->cammy_ip, sizeof g->cammy_ip);
    char token[160];
    if (read_small(r->token_file, token, sizeof token)) return 0;
    token[strcspn(token, "\r\n")] = 0;
    int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return 0;
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)r->portal_port)};
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    struct timeval tv = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    static char buf[4096];
    size_t got = 0;
    if (!connect(fd, (struct sockaddr *)&a, sizeof a)) {
        int n = snprintf(buf, sizeof buf, "GET /v1/state HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n", token);
        if (n > 0 && send(fd, buf, (size_t)n, MSG_NOSIGNAL) == n)
            for (ssize_t k; got + 1 < sizeof buf && (k = recv(fd, buf + got, sizeof buf - 1 - got, 0)) > 0;) got += (size_t)k;
    }
    close(fd);
    memset(token, 0, sizeof token);
    buf[got] = 0;
    if (strncmp(buf, "HTTP/1.", 7) || strncmp(buf + 8, " 200", 4)) return 0;
    const char *body = strstr(buf, "\r\n\r\n");
    if (!body) return 0;
    g->reachable = 1;
    (void)json_str(body, "owner", g->owner, sizeof g->owner);
    (void)json_str(body, "owner_label", g->label, sizeof g->label);
    (void)json_str(body, "owner_address", g->address, sizeof g->address);
    long s = json_num(body, "owner_since_seconds");
    g->since_s = s > 0 ? (unsigned)s : 0u;
    return 0;
}
static int r_volume(void *c, int v) {
    struct ui_real *r = c;
    char text[8];
    int n = snprintf(text, sizeof text, "%d\n", v);
    return write_atomic(r->volume_file, text, (size_t)n, 0644);
}
/* Face pack: the ID goes to /etc/cammy/face-pack (boot choice, and the portal reports it
 * to the agent); the page tells the running face service at once. */
static int r_pack(void *c, int i) {
    struct ui_real *r = c;
    const char *id = i >= 0 ? cui_pack_id((uint32_t)i) : NULL;
    if (!id) return -1;
    char text[40];
    int n = snprintf(text, sizeof text, "%s\n", id);
    if (write_atomic(r->pack_file, text, (size_t)n, 0644)) return -1;
    if (r->page) {
        atomic_store_explicit(&r->page->face_pack, (uint32_t)i + 1u, memory_order_release);
        atomic_fetch_add_explicit(&r->page->pack_seq, 1u, memory_order_release); /* a physical pick */
    }
    return 0;
}
int ui_real_pack(const struct ui_real *r) {
    char text[40];
    if (read_small(r->pack_file, text, sizeof text)) return 0;
    size_t n = strlen(text);
    while (n && (text[n - 1] == '\n' || text[n - 1] == ' ' || text[n - 1] == '\r' || text[n - 1] == '\t')) n--;
    int i = cui_pack_index(text, (unsigned)n);
    return i < 0 ? 0 : i;
}
static void r_camera(void *c) {
    struct ui_real *r = c;
    if (!r->page) return;
    atomic_store_explicit(&r->page->show, 0u, memory_order_release);
    atomic_store_explicit(&r->page->camera_mode, 1u, memory_order_release);
}
int ui_real_volume(const struct ui_real *r) {
    char text[16];
    if (read_small(r->volume_file, text, sizeof text)) return 100;
    long v = strtol(text, NULL, 10);
    return v < 0 ? 0 : v > 100 ? 100 : (int)v;
}
/* A small boot record: 1 absent, 0 read, -1 anything else (a link, not a regular file,
 * unreadable or too long): never trusted, so it counts as stale. */
static int read_record(const char *path, char *out, size_t n) {
    out[0] = 0;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return errno == ENOENT ? 1 : -1;
    struct stat st;
    ssize_t k = -1;
    if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size >= 0 && (size_t)st.st_size < n) k = read(fd, out, n - 1);
    close(fd);
    if (k < 0 || memchr(out, 0, (size_t)k)) { out[0] = 0; return -1; }
    out[k] = 0;
    return 0;
}
/* First word of a one-word record, or empty. */
static void word(const char *path, char *out, size_t n) {
    char text[64];
    out[0] = 0;
    if (read_record(path, text, sizeof text)) return;
    size_t m = 0;
    for (const char *p = text; *p && *p != '\n' && *p != ' ' && *p != '\r' && m + 1 < n; p++) out[m++] = *p;
    out[m] = 0;
}
/* Health's fallback check (pidof hostapd and udhcpd): one /proc scan, made only while the
 * gate holds and the runtime reports the access point. */
static int ap_running(void) {
    DIR *d = opendir("/proc");
    if (!d) return 0;
    int found = 0;
    for (struct dirent *e; found != 3 && (e = readdir(d));) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9' || strspn(e->d_name, "0123456789") != strlen(e->d_name)) continue;
        char path[300], comm[32];
        snprintf(path, sizeof path, "/proc/%.256s/comm", e->d_name);
        word(path, comm, sizeof comm);
        if (!strcmp(comm, "hostapd")) found |= 1;
        else if (!strcmp(comm, "udhcpd")) found |= 2;
    }
    closedir(d);
    return found == 3;
}
void ui_real_boot(struct ui_real *r, struct ub_input *in) {
    char path[192], text[512], v[UB_BOOT_ID], release[25];
    const char *cfg = r->cfg_dir ? r->cfg_dir : "/etc/cammy";
    memset(in, 0, sizeof *in);
    in->supervisor = -1;
    if (!r->boot_id[0] && !read_record("/proc/sys/kernel/random/boot_id", text, sizeof text)) {
        size_t m = strcspn(text, "\n");
        if (m && m < sizeof r->boot_id) { memcpy(r->boot_id, text, m); r->boot_id[m] = 0; }
    }
    snprintf(path, sizeof path, "%s/startup.pending", cfg);
    int g = read_record(path, text, sizeof text);
    if (g > 0) in->guard = UBG_ABSENT;
    else if (g < 0) in->guard = UBG_STALE;
    else ub_guard(text, r->boot_id, in);
    /* Only the durable launch latch permits this boot's RAM progress hint. */
    if (in->guard == UBG_CURRENT && !strcmp(in->phase, "launch")) {
        char phase[sizeof in->phase];
        snprintf(path, sizeof path, "%s/startup.phase", r->run_dir);
        word(path, phase, sizeof phase);
        const char *steps[] = {"payload", "stock_settle", "radio_load", "station_setup",
                               "dhcp", "ap_fallback", "portal", "menu"};
        for (size_t i = 0; i < sizeof steps / sizeof steps[0]; i++)
            if (!strcmp(phase, steps[i])) {
                strcpy(in->phase, phase);
                break;
            }
    }
    snprintf(path, sizeof path, "%s/runtime-health-fault", cfg);
    if (!read_record(path, text, sizeof text) && !ub_field(text, "boot_id", v, sizeof v) && r->boot_id[0] && !strcmp(v, r->boot_id)) {
        in->fault = 1;
        if (ub_field(text, "reason", in->fault_reason, sizeof in->fault_reason)) in->fault_reason[0] = 0;
    }
    snprintf(path, sizeof path, "%s/state", r->run_dir);
    word(path, in->net, sizeof in->net);
    if (!strcmp(in->net, "AP_CammyOS")) in->ap = ap_running();
    snprintf(path, sizeof path, "%s/media-prepare/state", r->run_dir);
    word(path, in->media, sizeof in->media);
    char pid[24];
    snprintf(path, sizeof path, "%s/portal.pid", r->run_dir);
    word(path, pid, sizeof pid);
    if (pid[0] && strspn(pid, "0123456789") == strlen(pid) && strlen(pid) < 10 && atoi(pid) > 1) {
        char comm[32];
        snprintf(path, sizeof path, "/proc/%s/comm", pid);
        word(path, comm, sizeof comm);
        in->portal = !strcmp(comm, "cammy-portal");
    }
    snprintf(path, sizeof path, "%s/native-release.conf", r->run_dir);
    if (!read_record(path, text, sizeof text) && !ub_field(text, "release_id", release, sizeof release)) {
        snprintf(path, sizeof path, "%s/release-health/display-ready", r->run_dir);
        if (!read_record(path, text, sizeof text)) in->services_ready = ub_display_ready(text, r->boot_id, release, in->net);
    }
}
void ui_real_close(struct ui_real *r) {
    if (r->wpa_fd < 0) return;
    struct sockaddr_un local = {.sun_family = AF_UNIX};
    snprintf(local.sun_path, sizeof local.sun_path, "%s/ctrl/cammy-ui-%d", r->run_dir, (int)getpid());
    unlink(local.sun_path);
    close(r->wpa_fd); r->wpa_fd = -1;
}
void ui_real_backend(struct ui_real *r, struct ui_backend *be) {
    *be = (struct ui_backend){r, r_status, r_scan, r_results, r_join, r_reconnect, r_commit, r_abort, r_agent, r_volume, r_camera, r_pack};
}
