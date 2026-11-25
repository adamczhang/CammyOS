/* SPDX-License-Identifier: MIT
 * CammyOS status bridge core (WP2): private SEQPACKET listener, fixed-layout
 * request validation, status-only dispatch and the protocol-1.0 capability clamp.
 * No shell, device, native-event or arbitrary-address surface exists here.
 */
#define _GNU_SOURCE
#include "cammy_bridge.h"
#include "cammy_bridge_proto.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CB_SUN_PATH_MAX (sizeof(((struct sockaddr_un *)0)->sun_path))
#define CB_DEFAULT_BOOT_ID_PATH "/proc/sys/kernel/random/boot_id"

struct cb_conn {
    int fd;
    uint32_t last_request_id;
    uint32_t errors;
    uint64_t last_activity_ms;
};

struct cb_counters {
    uint32_t accepted, rejected_busy, rejected_peer, requests_ok, requests_rejected;
    uint32_t framing_errors, idle_disconnects, slow_disconnects, peer_disconnects;
};

struct cammy_bridge {
    int listen_fd, lock_fd, wake_rd, wake_wr;
    char path[CB_SUN_PATH_MAX];
    dev_t sock_dev;
    ino_t sock_ino;
    int sock_bound;
    unsigned max_clients, idle_timeout_ms, active;
    uid_t allowed_uid;
    uint16_t kind;
    const struct cammy_bridge_backend *backend; /* set only when validated and initialised */
    uint16_t backend_reason;                    /* why backend is NULL */
    uint64_t instance, start_ms;
    unsigned char boot_id[16];
    int boot_ok;
    struct cb_counters n;
    struct cb_conn conns[CB_MAX_CLIENTS_LIMIT];
};

_Static_assert(sizeof CAMMY_BRIDGE_VERSION <= CB_BRIDGE_VERSION_LEN, "bridge version too long");

static const uint16_t cap_ids[CB_CAP_COUNT] = {
    CB_CAP_STATUS_IPC, CB_CAP_VIDEO_STREAM, CB_CAP_AUDIO_CAPTURE, CB_CAP_AUDIO_PLAYBACK,
    CB_CAP_GIMBAL, CB_CAP_DISPLAY, CB_CAP_LIFECYCLE_HANDOFF
};

static uint64_t mono_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Reads a canonical 36-character UUID from a regular file only. The lstat
 * precheck keeps a misconfigured path from opening a device node or FIFO. */
static int read_boot_id(const char *path, unsigned char out[16]) {
    struct stat st;
    char buf[64];
    if (lstat(path, &st) != 0 || !S_ISREG(st.st_mode)) return -1;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY);
    if (fd < 0) return -1;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) { close(fd); return -1; }
    ssize_t got = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (got < 36) return -1;
    size_t len = (size_t)got;
    while (len > 0 && buf[len - 1] == '\n') len--;
    if (len != 36) return -1;
    unsigned j = 0, any = 0;
    for (size_t i = 0; i < 36;) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (buf[i] != '-') return -1;
            i++;
            continue;
        }
        int hi = hexval(buf[i]), lo = hexval(buf[i + 1]);
        if (hi < 0 || lo < 0) return -1;
        out[j] = (unsigned char)((hi << 4) | lo);
        any |= out[j];
        j++;
        i += 2;
    }
    return (j == 16 && any) ? 0 : -1;
}

static uint64_t make_instance(void) {
    uint64_t v = 0;
    if (getrandom(&v, sizeof v, GRND_NONBLOCK) != (ssize_t)sizeof v) v = 0;
    if (v == 0) {
        struct timespec a = {0, 0}, r = {0, 0};
        clock_gettime(CLOCK_MONOTONIC, &a);
        clock_gettime(CLOCK_REALTIME, &r);
        v = ((uint64_t)a.tv_nsec << 32) ^ ((uint64_t)r.tv_sec * 1000003u) ^ (uint64_t)r.tv_nsec ^
            ((uint64_t)getpid() << 16);
    }
    return v ? v : 1;
}

static uint16_t validate_backend(const struct cammy_bridge_backend *t, uint16_t kind) {
    if (!t) return kind == CB_BACKEND_NATIVE ? CB_BREASON_NATIVE_TABLE_ABSENT : CB_BREASON_NATIVE_TABLE_REJECTED;
    if (t->abi_version != CAMMY_BRIDGE_BACKEND_ABI || t->struct_size != sizeof *t || t->kind != kind ||
        t->reserved != 0 || !t->name || !t->init || !t->query_status || !t->query_capabilities || !t->fini)
        return CB_BREASON_NATIVE_TABLE_REJECTED;
    return CB_BREASON_NONE;
}

const char *cammy_bridge_open_error_name(enum cammy_bridge_open_error e) {
    switch (e) {
    case CB_OPEN_OK: return "ok";
    case CB_OPEN_BAD_CONFIG: return "bad_config";
    case CB_OPEN_UNSAFE_DIR: return "unsafe_directory";
    case CB_OPEN_ALREADY_RUNNING: return "already_running";
    case CB_OPEN_PATH_OCCUPIED: return "path_occupied";
    case CB_OPEN_SYSTEM: return "system_error";
    }
    return "unknown";
}

uint64_t cammy_bridge_instance(const struct cammy_bridge *b) { return b ? b->instance : 0; }

int cammy_bridge_wake(struct cammy_bridge *b) {
    if (!b || b->wake_wr < 0) return -1;
    int saved = errno;
    ssize_t w = write(b->wake_wr, "w", 1);
    int ok = w == 1 || (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)); /* already pending */
    errno = saved;
    return ok ? 0 : -1;
}

/* Removes a stale socket left by a dead bridge. Called with the instance lock held,
 * so no other bridge can own it. Anything still accepting, or not a socket, stays. */
static enum cammy_bridge_open_error clear_stale(const char *path) {
    struct stat st;
    if (lstat(path, &st) != 0) return errno == ENOENT ? CB_OPEN_OK : CB_OPEN_SYSTEM;
    if (!S_ISSOCK(st.st_mode) || st.st_uid != geteuid()) return CB_OPEN_PATH_OCCUPIED;
    int s = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (s < 0) return CB_OPEN_SYSTEM;
    struct sockaddr_un sa;
    memset(&sa, 0, sizeof sa);
    sa.sun_family = AF_UNIX;
    memcpy(sa.sun_path, path, strlen(path) + 1);
    int rc = connect(s, (struct sockaddr *)&sa, sizeof sa);
    int err = errno;
    close(s);
    if (rc == 0 || err != ECONNREFUSED) return CB_OPEN_PATH_OCCUPIED;
    if (unlink(path) != 0 && errno != ENOENT) return CB_OPEN_SYSTEM;
    return CB_OPEN_OK;
}

static enum cammy_bridge_open_error setup_listener(struct cammy_bridge *b) {
    size_t plen = strlen(b->path);
    char dir[CB_SUN_PATH_MAX], lockp[CB_SUN_PATH_MAX + 8];
    memcpy(dir, b->path, plen + 1);
    char *slash = strrchr(dir, '/');
    if (!slash || slash == dir) return CB_OPEN_BAD_CONFIG; /* never directly under "/" */
    *slash = '\0';
    if (mkdir(dir, 0700) != 0 && errno != EEXIST) return CB_OPEN_UNSAFE_DIR;
    struct stat st;
    if (lstat(dir, &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 0077) != 0)
        return CB_OPEN_UNSAFE_DIR;

    memcpy(lockp, b->path, plen);
    memcpy(lockp + plen, ".lock", 6);
    b->lock_fd = open(lockp, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY, 0600);
    if (b->lock_fd < 0) return errno == ELOOP ? CB_OPEN_UNSAFE_DIR : CB_OPEN_SYSTEM;
    if (fstat(b->lock_fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 0077) != 0)
        return CB_OPEN_UNSAFE_DIR;
    if (flock(b->lock_fd, LOCK_EX | LOCK_NB) != 0)
        return errno == EWOULDBLOCK ? CB_OPEN_ALREADY_RUNNING : CB_OPEN_SYSTEM;

    enum cammy_bridge_open_error e = clear_stale(b->path);
    if (e != CB_OPEN_OK) return e;

    b->listen_fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (b->listen_fd < 0) return CB_OPEN_SYSTEM;
    struct sockaddr_un sa;
    memset(&sa, 0, sizeof sa);
    sa.sun_family = AF_UNIX;
    memcpy(sa.sun_path, b->path, plen + 1);
    if (bind(b->listen_fd, (struct sockaddr *)&sa, sizeof sa) != 0)
        return errno == EADDRINUSE ? CB_OPEN_PATH_OCCUPIED : CB_OPEN_SYSTEM;
    /* The 0700 parent already excludes other users during the bind-to-chmod window. */
    if (chmod(b->path, 0600) != 0 || lstat(b->path, &st) != 0 || !S_ISSOCK(st.st_mode)) {
        unlink(b->path);
        return CB_OPEN_SYSTEM;
    }
    b->sock_dev = st.st_dev;
    b->sock_ino = st.st_ino;
    b->sock_bound = 1;
    if (listen(b->listen_fd, (int)b->max_clients) != 0) return CB_OPEN_SYSTEM;
    return CB_OPEN_OK;
}

struct cammy_bridge *cammy_bridge_open(const struct cammy_bridge_config *cfg,
                                       const struct cammy_bridge_backend *backend,
                                       enum cammy_bridge_open_error *error) {
    enum cammy_bridge_open_error dummy;
    if (!error) error = &dummy;
    *error = CB_OPEN_BAD_CONFIG;
    if (!cfg || !cfg->socket_path) return NULL;
    size_t plen = strlen(cfg->socket_path);
    if (cfg->socket_path[0] != '/' || plen < 3 || plen >= CB_SUN_PATH_MAX || cfg->socket_path[plen - 1] == '/')
        return NULL;
    if (cfg->backend_kind != CB_BACKEND_FAKE && cfg->backend_kind != CB_BACKEND_NATIVE) return NULL;
    if (cfg->max_clients < 1 || cfg->max_clients > CB_MAX_CLIENTS_LIMIT) return NULL;
    if (cfg->idle_timeout_ms < CB_MIN_IDLE_TIMEOUT_MS || cfg->idle_timeout_ms > CB_MAX_IDLE_TIMEOUT_MS) return NULL;

    struct cammy_bridge *b = calloc(1, sizeof *b);
    if (!b) { *error = CB_OPEN_SYSTEM; return NULL; }
    b->listen_fd = -1;
    b->lock_fd = -1;
    b->wake_rd = b->wake_wr = -1;
    for (unsigned i = 0; i < CB_MAX_CLIENTS_LIMIT; i++) b->conns[i].fd = -1;
    memcpy(b->path, cfg->socket_path, plen + 1);
    b->max_clients = cfg->max_clients;
    b->idle_timeout_ms = cfg->idle_timeout_ms;
    b->allowed_uid = cfg->allowed_uid;
    b->kind = cfg->backend_kind;
    b->instance = make_instance();
    b->start_ms = mono_ms();
    b->boot_ok = read_boot_id(cfg->boot_id_path ? cfg->boot_id_path : CB_DEFAULT_BOOT_ID_PATH, b->boot_id) == 0;
    if (!b->boot_ok) memset(b->boot_id, 0, sizeof b->boot_id);

    int wake[2];
    if (pipe2(wake, O_CLOEXEC | O_NONBLOCK) != 0) {
        *error = CB_OPEN_SYSTEM;
        cammy_bridge_close(b);
        return NULL;
    }
    b->wake_rd = wake[0];
    b->wake_wr = wake[1];
    *error = setup_listener(b);
    if (*error != CB_OPEN_OK) {
        cammy_bridge_close(b);
        return NULL;
    }
    /* Backend last: a refused listener never initialises a backend. */
    b->backend_reason = validate_backend(backend, b->kind);
    if (b->backend_reason == CB_BREASON_NONE) {
        if (backend->init(backend->ctx) == 0) b->backend = backend;
        else b->backend_reason = CB_BREASON_INIT_FAILED;
    }
    return b;
}

static void drop(struct cammy_bridge *b, struct cb_conn *c, uint32_t *counter) {
    if (c->fd < 0) return;
    close(c->fd);
    c->fd = -1;
    if (b->active) b->active--;
    if (counter) (*counter)++;
}

void cammy_bridge_close(struct cammy_bridge *b) {
    if (!b) return;
    struct stat st;
    if (b->sock_bound && lstat(b->path, &st) == 0 && S_ISSOCK(st.st_mode) && st.st_dev == b->sock_dev &&
        st.st_ino == b->sock_ino)
        unlink(b->path); /* never remove a socket that replaced ours */
    if (b->listen_fd >= 0) close(b->listen_fd);
    for (unsigned i = 0; i < CB_MAX_CLIENTS_LIMIT; i++) drop(b, &b->conns[i], NULL);
    if (b->lock_fd >= 0) close(b->lock_fd); /* releases flock; lock file is kept on purpose */
    if (b->wake_rd >= 0) close(b->wake_rd);
    if (b->wake_wr >= 0) close(b->wake_wr);
    if (b->backend) b->backend->fini(b->backend->ctx);
    free(b);
}

static void put_header(const struct cammy_bridge *b, unsigned char *out, uint16_t command, uint32_t rid,
                       uint16_t status, uint32_t payload_size, int withhold_identity) {
    memset(out, 0, CB_RESPONSE_HEADER_SIZE);
    memcpy(out + CB_RS_MAGIC, CB_RESPONSE_MAGIC, 4);
    cb_put16(out + CB_RS_MAJOR, CB_PROTO_MAJOR);
    cb_put16(out + CB_RS_MINOR, CB_PROTO_MINOR);
    cb_put16(out + CB_RS_HEADER_SIZE, CB_RESPONSE_HEADER_SIZE);
    cb_put16(out + CB_RS_COMMAND, command);
    cb_put32(out + CB_RS_REQUEST_ID, rid);
    cb_put16(out + CB_RS_STATUS, status);
    cb_put32(out + CB_RS_PAYLOAD_SIZE, payload_size);
    if (withhold_identity) return;
    cb_put16(out + CB_RS_BACKEND_KIND, b->kind);
    cb_put64(out + CB_RS_INSTANCE, b->instance);
    memcpy(out + CB_RS_BOOT_ID, b->boot_id, 16);
    cb_put64(out + CB_RS_MONO_NOW, mono_ms());
    cb_put64(out + CB_RS_OWNER_GENERATION, 0); /* no owner exists in protocol 1.0 */
}

static size_t put_error(const struct cammy_bridge *b, unsigned char *out, uint16_t command, uint32_t rid,
                        uint16_t status, uint16_t field, int closing, uint32_t remaining, int withhold) {
    put_header(b, out, command, rid, status, CB_ERROR_PAYLOAD_SIZE, withhold);
    unsigned char *p = out + CB_RESPONSE_HEADER_SIZE;
    cb_put16(p + 0, CB_PROTO_MAJOR);
    cb_put16(p + 2, CB_PROTO_MINOR);
    cb_put16(p + 4, field);
    cb_put16(p + 6, closing ? CB_DISPOSITION_CLOSING : CB_DISPOSITION_KEEP);
    cb_put32(p + 8, CB_REQUEST_SIZE);
    cb_put32(p + 12, closing ? 0 : remaining);
    return CB_RESPONSE_HEADER_SIZE + CB_ERROR_PAYLOAD_SIZE;
}

static void backend_status(const struct cammy_bridge *b, uint16_t *state, uint16_t *reason) {
    if (!b->backend) {
        *state = CB_BSTATE_UNAVAILABLE;
        *reason = b->backend_reason;
        return;
    }
    struct cammy_bridge_backend_status s = {CB_BSTATE_ERROR, CB_BREASON_QUERY_FAILED};
    /* Out-of-range values, and a native READY that carries a reason, are inconsistent. */
    if (b->backend->query_status(b->backend->ctx, &s) != 0 || s.state > CB_BSTATE_ERROR ||
        s.reason > CB_BREASON_MAX ||
        (b->kind == CB_BACKEND_NATIVE && s.state == CB_BSTATE_READY && s.reason != CB_BREASON_NONE)) {
        *state = CB_BSTATE_ERROR;
        *reason = CB_BREASON_QUERY_FAILED;
        return;
    }
    *state = s.state;
    *reason = b->kind == CB_BACKEND_FAKE ? (uint16_t)CB_BREASON_FAKE_BACKEND : s.reason;
}

/* Protocol-1.0 clamp: no physical capability is AVAILABLE, whatever a table says. */
static void build_caps(const struct cammy_bridge *b, struct cammy_bridge_cap out[CB_CAP_COUNT]) {
    uint8_t reason;
    if (!b->backend) {
        reason = b->backend_reason == CB_BREASON_NATIVE_TABLE_ABSENT ? CB_CREASON_NATIVE_TABLE_ABSENT
               : b->backend_reason == CB_BREASON_NATIVE_TABLE_REJECTED ? CB_CREASON_NATIVE_TABLE_REJECTED
               : CB_CREASON_BACKEND_ERROR;
    } else if (b->kind == CB_BACKEND_FAKE) {
        reason = CB_CREASON_FAKE_BACKEND; /* fake claims are never consulted */
    } else {
        reason = CB_CREASON_NOT_IN_PROTOCOL_V1;
    }
    for (unsigned i = 0; i < CB_CAP_COUNT; i++) {
        out[i].id = cap_ids[i];
        out[i].state = CB_CAP_UNAVAILABLE;
        out[i].reason = reason;
    }
    if (b->backend && b->kind == CB_BACKEND_NATIVE) {
        struct cammy_bridge_cap q[CB_CAP_COUNT - 1];
        for (unsigned i = 0; i < CB_CAP_COUNT - 1; i++) q[i] = (struct cammy_bridge_cap){cap_ids[i + 1], 0, 0};
        int rc = b->backend->query_capabilities(b->backend->ctx, q, CB_CAP_COUNT - 1);
        for (unsigned i = 0; i < CB_CAP_COUNT - 1; i++)
            if (rc != 0 || q[i].state > CB_CAP_SIMULATED || q[i].reason == CB_CREASON_BACKEND_ERROR)
                out[i + 1].reason = CB_CREASON_BACKEND_ERROR;
    }
    out[0].state = CB_CAP_AVAILABLE; /* answering this request is the status IPC */
    out[0].reason = CB_CREASON_NONE;
}

static size_t build_ok(const struct cammy_bridge *b, unsigned char *out, uint16_t cmd, uint32_t rid) {
    unsigned char *p = out + CB_RESPONSE_HEADER_SIZE;
    uint32_t size = 0;
    if (cmd == CB_CMD_HELLO) {
        size = CB_HELLO_PAYLOAD_SIZE;
        memset(p, 0, size);
        cb_put16(p + 0, CB_PROTO_MAJOR);
        cb_put16(p + 2, CB_PROTO_MINOR);
        cb_put16(p + 4, (uint16_t)b->max_clients);
        cb_put16(p + 6, CB_MAX_BURST);
        cb_put32(p + 8, CB_REQUEST_SIZE);
        cb_put32(p + 12, CB_MAX_FRAME);
        cb_put32(p + 16, b->idle_timeout_ms);
        cb_put32(p + 20, CB_MAX_DEADLINE_HORIZON_MS);
        cb_put32(p + 24, CB_COMMAND_MASK_V1);
        cb_put32(p + 28, CB_ERROR_BUDGET);
        memcpy(p + 32, CAMMY_BRIDGE_VERSION, sizeof CAMMY_BRIDGE_VERSION);
    } else if (cmd == CB_CMD_STATUS) {
        uint16_t state, reason;
        backend_status(b, &state, &reason);
        size = CB_STATUS_PAYLOAD_SIZE;
        uint64_t now = mono_ms();
        cb_put16(p + 0, b->kind);
        cb_put16(p + 2, state);
        cb_put16(p + 4, reason);
        cb_put16(p + 6, b->boot_ok ? CB_STATUS_FLAG_BOOT_ID_AVAILABLE : 0);
        cb_put64(p + 8, b->start_ms);
        cb_put64(p + 16, now >= b->start_ms ? now - b->start_ms : 0);
        cb_put32(p + 24, b->active);
        cb_put32(p + 28, b->n.accepted);
        cb_put32(p + 32, b->n.rejected_busy);
        cb_put32(p + 36, b->n.rejected_peer);
        cb_put32(p + 40, b->n.requests_ok);
        cb_put32(p + 44, b->n.requests_rejected);
        cb_put32(p + 48, b->n.framing_errors);
        cb_put32(p + 52, b->n.idle_disconnects);
        cb_put32(p + 56, b->n.slow_disconnects);
        cb_put32(p + 60, b->n.peer_disconnects);
    } else { /* CB_CMD_CAPABILITIES */
        struct cammy_bridge_cap caps[CB_CAP_COUNT];
        build_caps(b, caps);
        size = CB_CAPS_PAYLOAD_SIZE;
        memset(p, 0, size);
        cb_put16(p + 0, CB_CAP_COUNT);
        cb_put16(p + 2, CB_CAP_ENTRY_SIZE);
        for (unsigned i = 0; i < CB_CAP_COUNT; i++) {
            unsigned char *e = p + 8 + i * CB_CAP_ENTRY_SIZE;
            cb_put16(e, caps[i].id);
            e[2] = caps[i].state;
            e[3] = caps[i].reason;
            e[4] = (unsigned char)(caps[i].id == CB_CAP_STATUS_IPC ? 0u : CB_CAP_FLAG_PHYSICAL);
        }
    }
    put_header(b, out, cmd, rid, CB_ST_OK, size, 0);
    return CB_RESPONSE_HEADER_SIZE + size;
}

/* Semantic checks for a well-framed protocol-1.0 request, in documented order. */
static uint16_t check_request(const struct cammy_bridge *b, struct cb_conn *c, const unsigned char *in,
                              uint16_t *field, uint64_t *deadline) {
    uint32_t rid = cb_get32(in + CB_RQ_REQUEST_ID);
    uint16_t cmd = cb_get16(in + CB_RQ_COMMAND);
    *deadline = 0;
    *field = CB_RQ_REQUEST_ID;
    if (rid == 0 || rid <= c->last_request_id) return CB_ST_BAD_REQUEST_ID;
    c->last_request_id = rid;
    *field = CB_RQ_FLAGS;
    if (cb_get32(in + CB_RQ_FLAGS) != 0) return CB_ST_UNKNOWN_FIELD;
    *field = CB_RQ_RESERVED;
    if (cb_get32(in + CB_RQ_RESERVED) != 0) return CB_ST_UNKNOWN_FIELD;
    *field = CB_RQ_COMMAND;
    if (cmd >= CB_CMD_MUTATING_FIRST && cmd <= CB_CMD_MUTATING_LAST) return CB_ST_MUTATION_DISABLED;
    if (cmd != CB_CMD_HELLO && cmd != CB_CMD_STATUS && cmd != CB_CMD_CAPABILITIES) return CB_ST_UNKNOWN_COMMAND;
    *field = CB_RQ_OWNER_GENERATION;
    if (cb_get64(in + CB_RQ_OWNER_GENERATION) != 0) return CB_ST_OWNER_UNSUPPORTED;
    *field = CB_RQ_EXPECT_INSTANCE;
    uint64_t expect = cb_get64(in + CB_RQ_EXPECT_INSTANCE);
    if (expect != 0 && expect != b->instance) return CB_ST_STALE_INSTANCE;
    unsigned char zero[16] = {0};
    int has_boot = memcmp(in + CB_RQ_BOOT_ID, zero, 16) != 0;
    *field = CB_RQ_BOOT_ID;
    if (has_boot && (!b->boot_ok || memcmp(in + CB_RQ_BOOT_ID, b->boot_id, 16) != 0)) return CB_ST_BOOT_MISMATCH;
    uint64_t dl = cb_get64(in + CB_RQ_DEADLINE);
    *field = CB_RQ_DEADLINE;
    if (dl != 0) {
        if (!has_boot || !b->boot_ok) return CB_ST_CLOCK_DOMAIN;
        uint64_t now = mono_ms();
        if (now >= dl) return CB_ST_EXPIRED;
        if (dl - now > CB_MAX_DEADLINE_HORIZON_MS) return CB_ST_BAD_DEADLINE;
    }
    *deadline = dl;
    *field = CB_FIELD_NONE;
    return CB_ST_OK;
}

/* Builds the reply for one received packet. Returns the reply length; *closing
 * is set when the connection must close after the reply is sent. */
static size_t process(struct cammy_bridge *b, struct cb_conn *c, const unsigned char *in, size_t len,
                      int truncated, int ctrunc, unsigned char *out, int *closing) {
    uint16_t cmd = 0, field = CB_FIELD_NONE, st;
    uint32_t rid = 0;
    uint64_t deadline = 0;
    int framing = 0;
    int magic_ok = len >= 4 && memcmp(in, CB_REQUEST_MAGIC, 4) == 0;
    if (magic_ok && len >= 16) {
        cmd = cb_get16(in + CB_RQ_COMMAND);
        rid = cb_get32(in + CB_RQ_REQUEST_ID);
    }
    if (ctrunc) { st = CB_ST_BAD_ANCILLARY; framing = 1; }
    else if (truncated || len > CB_MAX_FRAME) { st = CB_ST_TOO_LARGE; framing = 1; }
    else if (len < 8) { st = CB_ST_BAD_LENGTH; framing = 1; }
    else if (!magic_ok) { st = CB_ST_BAD_MAGIC; framing = 1; field = CB_RQ_MAGIC; }
    else if (cb_get16(in + CB_RQ_MAJOR) != CB_PROTO_MAJOR) { st = CB_ST_UNSUPPORTED_VERSION; field = CB_RQ_MAJOR; }
    else if (cb_get16(in + CB_RQ_MINOR) > CB_PROTO_MINOR) { st = CB_ST_UNSUPPORTED_VERSION; field = CB_RQ_MINOR; }
    else if (len != CB_REQUEST_SIZE) { st = CB_ST_BAD_LENGTH; framing = 1; }
    else if (cb_get16(in + CB_RQ_HEADER_SIZE) != CB_REQUEST_SIZE) { st = CB_ST_BAD_LENGTH; framing = 1; field = CB_RQ_HEADER_SIZE; }
    else st = check_request(b, c, in, &field, &deadline);

    if (st == CB_ST_OK) {
        size_t n = build_ok(b, out, cmd, rid);
        /* A result produced after its deadline is never delivered as success. */
        if (deadline == 0 || mono_ms() < deadline) {
            b->n.requests_ok++;
            *closing = 0;
            return n;
        }
        st = CB_ST_EXPIRED;
        field = CB_RQ_DEADLINE;
    }
    if (framing) {
        b->n.framing_errors++;
        *closing = 1;
    } else {
        b->n.requests_rejected++;
        c->errors++;
        *closing = c->errors >= CB_ERROR_BUDGET;
    }
    uint32_t remaining = c->errors >= CB_ERROR_BUDGET ? 0 : CB_ERROR_BUDGET - c->errors;
    return put_error(b, out, cmd, rid, st, field, *closing, remaining, 0);
}

static void serve_conn(struct cammy_bridge *b, struct cb_conn *c, short revents) {
    if (revents & POLLNVAL) { drop(b, c, &b->n.peer_disconnects); return; }
    for (unsigned burst = 0; burst < CB_MAX_BURST && c->fd >= 0; burst++) {
        unsigned char in[CB_MAX_FRAME], out[CB_MAX_FRAME];
        struct iovec iov = {in, sizeof in};
        struct msghdr mh;
        memset(&mh, 0, sizeof mh);
        mh.msg_iov = &iov;
        mh.msg_iovlen = 1; /* no control buffer: passed descriptors are discarded by the kernel */
        ssize_t r = recvmsg(c->fd, &mh, MSG_DONTWAIT);
        if (r < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return;
            drop(b, c, &b->n.peer_disconnects);
            return;
        }
        if (r == 0) { drop(b, c, &b->n.peer_disconnects); return; } /* EOF or empty packet */
        c->last_activity_ms = mono_ms();
        int closing = 0;
        size_t n = process(b, c, in, (size_t)r, (mh.msg_flags & MSG_TRUNC) != 0, (mh.msg_flags & MSG_CTRUNC) != 0,
                           out, &closing);
        ssize_t s = send(c->fd, out, n, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (s != (ssize_t)n) {
            int slow = s < 0 && (errno == EAGAIN || errno == EWOULDBLOCK);
            drop(b, c, slow ? &b->n.slow_disconnects : &b->n.peer_disconnects);
            return;
        }
        if (closing) { drop(b, c, NULL); return; }
    }
}

static void reject_new(struct cammy_bridge *b, int fd, uint16_t status, int withhold) {
    unsigned char out[CB_MAX_FRAME];
    size_t n = put_error(b, out, 0, 0, status, CB_FIELD_NONE, 1, 0, withhold);
    ssize_t s = send(fd, out, n, MSG_DONTWAIT | MSG_NOSIGNAL);
    (void)s; /* best effort; the connection closes either way */
    close(fd);
}

static int accept_pending(struct cammy_bridge *b) {
    for (unsigned i = 0; i < b->max_clients + 2; i++) {
        int fd = accept4(b->listen_fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd < 0) {
            if (errno == ECONNABORTED || errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
            /* Resource exhaustion leaves the listener readable. Continuing the
             * step loop would busy-spin inside the native hardware owner.
             * Fail this bridge instance; never terminate its containing app. */
            return -1;
        }
        struct ucred cr;
        socklen_t cl = sizeof cr;
        if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &cl) != 0 || cl != sizeof cr || cr.uid != b->allowed_uid) {
            b->n.rejected_peer++;
            reject_new(b, fd, CB_ST_PEER_DENIED, 1);
            continue;
        }
        struct cb_conn *slot = NULL;
        for (unsigned k = 0; k < b->max_clients; k++)
            if (b->conns[k].fd < 0) { slot = &b->conns[k]; break; }
        if (!slot) {
            b->n.rejected_busy++;
            reject_new(b, fd, CB_ST_BUSY, 0);
            continue;
        }
        slot->fd = fd;
        slot->last_request_id = 0;
        slot->errors = 0;
        slot->last_activity_ms = mono_ms();
        b->active++;
        b->n.accepted++;
    }
    return 0;
}

int cammy_bridge_step(struct cammy_bridge *b, int max_wait_ms) {
    if (!b || b->listen_fd < 0) return -1;
    struct pollfd pfd[2 + CB_MAX_CLIENTS_LIMIT];
    unsigned idx[CB_MAX_CLIENTS_LIMIT];
    nfds_t n = 2;
    uint64_t now = mono_ms();
    int wait = max_wait_ms < 0 ? 0 : max_wait_ms;
    pfd[0].fd = b->listen_fd;
    pfd[0].events = POLLIN;
    pfd[0].revents = 0;
    pfd[1].fd = b->wake_rd;
    pfd[1].events = POLLIN;
    pfd[1].revents = 0;
    for (unsigned i = 0; i < b->max_clients; i++) {
        if (b->conns[i].fd < 0) continue;
        pfd[n].fd = b->conns[i].fd;
        pfd[n].events = POLLIN;
        pfd[n].revents = 0;
        idx[n - 2] = i;
        n++;
        uint64_t due = b->conns[i].last_activity_ms + b->idle_timeout_ms;
        uint64_t left = due > now ? due - now : 0;
        if (left < (uint64_t)wait) wait = (int)left;
    }
    int rc = poll(pfd, n, wait);
    if (rc < 0) return errno == EINTR ? 0 : -1;
    if (pfd[0].revents & (POLLERR | POLLNVAL)) return -1;
    if (pfd[1].revents & POLLIN) {
        char drain[16];
        while (read(b->wake_rd, drain, sizeof drain) > 0) {}
    }
    for (nfds_t k = 2; k < n; k++)
        if (pfd[k].revents) serve_conn(b, &b->conns[idx[k - 2]], pfd[k].revents);
    if ((pfd[0].revents & POLLIN) && accept_pending(b) != 0) return -1;
    now = mono_ms();
    for (unsigned i = 0; i < b->max_clients; i++)
        if (b->conns[i].fd >= 0 && now - b->conns[i].last_activity_ms >= b->idle_timeout_ms)
            drop(b, &b->conns[i], &b->n.idle_disconnects);
    return 0;
}
