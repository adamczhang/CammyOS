/* SPDX-License-Identifier: MIT
 * Bridge-core self-test with deliberately misbehaving backend tables. These cases
 * cannot be reached through the daemon (its only native table is NULL), so they
 * run here against the real core and real sockets, on host and under qemu-arm.
 * Usage: bridge-selftest PRIVATE_EMPTY_DIR
 */
#define _GNU_SOURCE
#include "cammy_bridge.h"
#include "cammy_bridge_proto.h"

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static int failures, checks;
#define CHECK(c)                                                                 \
    do {                                                                         \
        checks++;                                                                \
        if (!(c)) {                                                              \
            failures++;                                                          \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);         \
        }                                                                        \
    } while (0)

struct tctx {
    int init_rc, status_rc, caps_rc, sleep_ms;
    int init_calls, fini_calls, status_calls, caps_calls;
    uint16_t st_state, st_reason;
    uint8_t cap_state, cap_reason;
};

static int t_init(void *c) { struct tctx *t = c; t->init_calls++; return t->init_rc; }
static void t_fini(void *c) { ((struct tctx *)c)->fini_calls++; }
static int t_status(void *c, struct cammy_bridge_backend_status *o) {
    struct tctx *t = c;
    t->status_calls++;
    if (t->sleep_ms) {
        struct timespec ts = {0, (long)t->sleep_ms * 1000000L};
        nanosleep(&ts, NULL);
    }
    o->state = t->st_state;
    o->reason = t->st_reason;
    return t->status_rc;
}
static int t_caps(void *c, struct cammy_bridge_cap *caps, unsigned n) {
    struct tctx *t = c;
    t->caps_calls++;
    for (unsigned i = 0; i < n; i++) { caps[i].state = t->cap_state; caps[i].reason = t->cap_reason; caps[i].id = 0; }
    return t->caps_rc;
}

static struct cammy_bridge_backend table(struct tctx *t, uint16_t kind) {
    struct cammy_bridge_backend b = {CAMMY_BRIDGE_BACKEND_ABI, sizeof(struct cammy_bridge_backend), kind, 0,
                                     "selftest", t, t_init, t_status, t_caps, t_fini};
    return b;
}

static char dir[96], sock_path[128], boot_path[128];
static const unsigned char boot_bytes[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
                                             0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xf0, 0x01};

static struct cammy_bridge *open_bridge(uint16_t kind, const struct cammy_bridge_backend *t,
                                        enum cammy_bridge_open_error *e) {
    struct cammy_bridge_config cfg = {sock_path, boot_path, kind, 2, 1000, geteuid()};
    return cammy_bridge_open(&cfg, t, e);
}

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

/* Sends one request and drives the bridge until the reply arrives (bounded). */
static ssize_t roundtrip(struct cammy_bridge *b, uint16_t cmd, uint32_t rid, uint64_t deadline,
                         unsigned char *resp, struct cb_response_header *h) {
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    struct sockaddr_un sa;
    memset(&sa, 0, sizeof sa);
    sa.sun_family = AF_UNIX;
    memcpy(sa.sun_path, sock_path, strlen(sock_path) + 1);
    if (fd < 0 || connect(fd, (struct sockaddr *)&sa, sizeof sa) != 0) { if (fd >= 0) close(fd); return -1; }
    struct cb_request r;
    memset(&r, 0, sizeof r);
    r.major = CB_PROTO_MAJOR;
    r.command = cmd;
    r.request_id = rid;
    r.deadline_mono_ms = deadline;
    if (deadline) memcpy(r.boot_id, boot_bytes, 16);
    unsigned char req[CB_REQUEST_SIZE];
    cb_encode_request(req, &r);
    ssize_t got = -1;
    if (send(fd, req, sizeof req, MSG_NOSIGNAL) == (ssize_t)sizeof req) {
        for (int i = 0; i < 50 && got < 0; i++) {
            cammy_bridge_step(b, 20);
            got = recv(fd, resp, CB_MAX_FRAME, MSG_DONTWAIT);
        }
    }
    close(fd);
    if (got < 0 || cb_decode_response_header(resp, (size_t)got, h) != 0) return -1;
    return got;
}

static void status_of(struct cammy_bridge *b, uint16_t *kind, uint16_t *state, uint16_t *reason) {
    unsigned char resp[CB_MAX_FRAME];
    struct cb_response_header h;
    *kind = *state = *reason = 0xFFFF;
    if (roundtrip(b, CB_CMD_STATUS, 1, 0, resp, &h) < 0 || h.status != CB_ST_OK) return;
    *kind = h.backend_kind;
    *state = cb_get16(resp + 64 + 2);
    *reason = cb_get16(resp + 64 + 4);
}

/* Returns the number of physical entries matching (UNAVAILABLE, reason); -1 on error. */
static int physical_unavailable(struct cammy_bridge *b, uint8_t reason) {
    unsigned char resp[CB_MAX_FRAME];
    struct cb_response_header h;
    if (roundtrip(b, CB_CMD_CAPABILITIES, 1, 0, resp, &h) < 0 || h.status != CB_ST_OK) return -1;
    const unsigned char *p = resp + 64;
    if (cb_get16(p) != CB_CAP_COUNT || h.payload_size != CB_CAPS_PAYLOAD_SIZE) return -1;
    int match = 0;
    for (unsigned i = 0; i < CB_CAP_COUNT; i++) {
        const unsigned char *e = p + 8 + i * CB_CAP_ENTRY_SIZE;
        uint16_t id = cb_get16(e);
        if (id == CB_CAP_STATUS_IPC) {
            if (e[2] != CB_CAP_AVAILABLE || e[4] != 0) return -1;
            continue;
        }
        if (!(e[4] & CB_CAP_FLAG_PHYSICAL)) return -1;
        if (e[2] == CB_CAP_UNAVAILABLE && e[3] == reason) match++;
    }
    return match;
}

static void case_lying_native(void) {
    struct tctx t = {0};
    t.st_state = CB_BSTATE_READY;
    t.cap_state = CB_CAP_AVAILABLE;
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    enum cammy_bridge_open_error e;
    struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, &tb, &e);
    CHECK(b != NULL);
    if (!b) return;
    uint16_t kind, state, reason;
    status_of(b, &kind, &state, &reason);
    CHECK(kind == CB_BACKEND_NATIVE && state == CB_BSTATE_READY);
    CHECK(physical_unavailable(b, CB_CREASON_NOT_IN_PROTOCOL_V1) == (int)CB_CAP_COUNT - 1);
    CHECK(t.caps_calls == 1);
    t.cap_state = CB_CAP_SIMULATED;
    CHECK(physical_unavailable(b, CB_CREASON_NOT_IN_PROTOCOL_V1) == (int)CB_CAP_COUNT - 1);
    t.cap_state = 9; /* out of range */
    CHECK(physical_unavailable(b, CB_CREASON_BACKEND_ERROR) == (int)CB_CAP_COUNT - 1);
    t.cap_state = CB_CAP_AVAILABLE;
    t.caps_rc = -1;
    CHECK(physical_unavailable(b, CB_CREASON_BACKEND_ERROR) == (int)CB_CAP_COUNT - 1);
    t.st_state = 7;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_ERROR && reason == CB_BREASON_QUERY_FAILED);
    t.st_state = CB_BSTATE_READY;
    t.status_rc = -1;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_ERROR && reason == CB_BREASON_QUERY_FAILED);
    cammy_bridge_close(b);
    CHECK(t.init_calls == 1 && t.fini_calls == 1);
}

static void case_lying_fake(void) {
    struct tctx t = {0};
    t.st_state = CB_BSTATE_READY;
    t.st_reason = CB_BREASON_NONE;
    t.cap_state = CB_CAP_AVAILABLE;
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_FAKE);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_FAKE, &tb, NULL);
    CHECK(b != NULL);
    if (!b) return;
    uint16_t kind, state, reason;
    status_of(b, &kind, &state, &reason);
    CHECK(kind == CB_BACKEND_FAKE && reason == CB_BREASON_FAKE_BACKEND);
    CHECK(physical_unavailable(b, CB_CREASON_FAKE_BACKEND) == (int)CB_CAP_COUNT - 1);
    CHECK(t.caps_calls == 0); /* fake capability claims are never consulted */
    cammy_bridge_close(b);
}

static void case_rejected_tables(void) {
    for (int variant = 0; variant < 7; variant++) {
        struct tctx t = {0};
        t.st_state = CB_BSTATE_READY;
        t.cap_state = CB_CAP_AVAILABLE;
        struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
        switch (variant) {
        case 0: tb.abi_version = CAMMY_BRIDGE_BACKEND_ABI + 1; break;
        case 1: tb.struct_size = (uint32_t)sizeof tb - 4; break;
        case 2: tb.kind = CB_BACKEND_FAKE; break; /* a table may not relabel itself */
        case 3: tb.query_capabilities = NULL; break;
        case 4: tb.fini = NULL; break;
        case 5: tb.reserved = 1; break;
        default: tb.name = NULL; break;
        }
        struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, &tb, NULL);
        CHECK(b != NULL);
        if (!b) continue;
        uint16_t kind, state, reason;
        status_of(b, &kind, &state, &reason);
        CHECK(kind == CB_BACKEND_NATIVE && state == CB_BSTATE_UNAVAILABLE &&
              reason == CB_BREASON_NATIVE_TABLE_REJECTED);
        CHECK(physical_unavailable(b, CB_CREASON_NATIVE_TABLE_REJECTED) == (int)CB_CAP_COUNT - 1);
        cammy_bridge_close(b);
        CHECK(t.init_calls == 0 && t.status_calls == 0 && t.caps_calls == 0 && t.fini_calls == 0);
    }
    /* A native table offered to a fake-kind bridge is rejected, not relabeled. */
    struct tctx t = {0};
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_FAKE, &tb, NULL);
    CHECK(b != NULL);
    if (b) {
        uint16_t kind, state, reason;
        status_of(b, &kind, &state, &reason);
        CHECK(kind == CB_BACKEND_FAKE && state == CB_BSTATE_UNAVAILABLE);
        cammy_bridge_close(b);
    }
    CHECK(t.init_calls == 0);
}

static void case_absent_and_init_failure(void) {
    CHECK(cammy_bridge_native_backend() == NULL);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, cammy_bridge_native_backend(), NULL);
    CHECK(b != NULL);
    if (b) {
        uint16_t kind, state, reason;
        status_of(b, &kind, &state, &reason);
        CHECK(kind == CB_BACKEND_NATIVE && state == CB_BSTATE_UNAVAILABLE &&
              reason == CB_BREASON_NATIVE_TABLE_ABSENT);
        CHECK(physical_unavailable(b, CB_CREASON_NATIVE_TABLE_ABSENT) == (int)CB_CAP_COUNT - 1);
        cammy_bridge_close(b);
    }
    struct tctx t = {0};
    t.init_rc = -1;
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    b = open_bridge(CB_BACKEND_NATIVE, &tb, NULL);
    CHECK(b != NULL);
    if (b) {
        uint16_t kind, state, reason;
        status_of(b, &kind, &state, &reason);
        CHECK(state == CB_BSTATE_UNAVAILABLE && reason == CB_BREASON_INIT_FAILED);
        CHECK(physical_unavailable(b, CB_CREASON_BACKEND_ERROR) == (int)CB_CAP_COUNT - 1);
        cammy_bridge_close(b);
    }
    CHECK(t.init_calls == 1 && t.status_calls == 0 && t.caps_calls == 0 && t.fini_calls == 0);
}

static void case_late_result_not_success(void) {
    struct tctx t = {0};
    t.st_state = CB_BSTATE_READY;
    t.sleep_ms = 150; /* violates the non-blocking contract on purpose */
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, &tb, NULL);
    CHECK(b != NULL);
    if (!b) return;
    unsigned char resp[CB_MAX_FRAME];
    struct cb_response_header h;
    CHECK(roundtrip(b, CB_CMD_STATUS, 1, now_ms() + 50, resp, &h) > 0);
    CHECK(h.status == CB_ST_EXPIRED && h.payload_size == CB_ERROR_PAYLOAD_SIZE);
    CHECK(t.status_calls == 1); /* dispatched in time, finished late: post-check rejected it */
    CHECK(cb_get16(resp + 64 + 4) == CB_RQ_DEADLINE);
    t.sleep_ms = 0;
    CHECK(roundtrip(b, CB_CMD_STATUS, 1, now_ms() + 2000, resp, &h) > 0 && h.status == CB_ST_OK);
    CHECK(memcmp(h.boot_id, boot_bytes, 16) == 0);
    cammy_bridge_close(b);
}

static void case_refused_listener_never_inits(void) {
    struct tctx t = {0};
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    enum cammy_bridge_open_error e = CB_OPEN_OK;
    CHECK(chmod(dir, 0750) == 0);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, &tb, &e);
    CHECK(b == NULL && e == CB_OPEN_UNSAFE_DIR && t.init_calls == 0);
    if (b) cammy_bridge_close(b);
    CHECK(chmod(dir, 0700) == 0);
}

static void case_native_reasons(void) {
    struct tctx t = {0};
    struct cammy_bridge_backend tb = table(&t, CB_BACKEND_NATIVE);
    struct cammy_bridge *b = open_bridge(CB_BACKEND_NATIVE, &tb, NULL);
    CHECK(b != NULL);
    if (!b) return;
    uint16_t kind, state, reason;
    t.st_state = CB_BSTATE_UNAVAILABLE;
    t.st_reason = CB_BREASON_NATIVE_EXITING;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_UNAVAILABLE && reason == CB_BREASON_NATIVE_EXITING);
    t.st_state = CB_BSTATE_ERROR;
    t.st_reason = CB_BREASON_NATIVE_IDENTITY_MISMATCH;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_ERROR && reason == CB_BREASON_NATIVE_IDENTITY_MISMATCH);
    t.st_state = CB_BSTATE_READY; /* READY must not carry a reason */
    t.st_reason = CB_BREASON_NATIVE_EXITING;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_ERROR && reason == CB_BREASON_QUERY_FAILED);
    t.st_state = CB_BSTATE_UNAVAILABLE;
    t.st_reason = CB_BREASON_MAX + 1;
    status_of(b, &kind, &state, &reason);
    CHECK(state == CB_BSTATE_ERROR && reason == CB_BREASON_QUERY_FAILED);
    cammy_bridge_close(b);
}

struct waker { struct cammy_bridge *b; uint64_t elapsed; int rc; };
static void *step_long(void *arg) {
    struct waker *w = arg;
    uint64_t t0 = now_ms();
    w->rc = cammy_bridge_step(w->b, 5000);
    w->elapsed = now_ms() - t0;
    return NULL;
}

static void case_wake_bounds_blocked_step(void) {
    struct cammy_bridge *b = open_bridge(CB_BACKEND_FAKE, cammy_bridge_fake_backend(), NULL);
    CHECK(b != NULL);
    if (!b) return;
    struct waker w = {b, 0, -1};
    pthread_t th;
    CHECK(pthread_create(&th, NULL, step_long, &w) == 0);
    struct timespec ts = {0, 50 * 1000000L};
    nanosleep(&ts, NULL);
    CHECK(cammy_bridge_wake(b) == 0);
    pthread_join(th, NULL);
    CHECK(w.rc == 0 && w.elapsed < 1000); /* a detach join needs this bound, not the poll wait */
    int woke = 0;
    for (int i = 0; i < 100000; i++) woke += cammy_bridge_wake(b) == 0; /* fills the pipe: coalesces, never blocks */
    CHECK(woke == 100000);
    uint64_t t0 = now_ms();
    CHECK(cammy_bridge_step(b, 5000) == 0 && now_ms() - t0 < 1000);
    cammy_bridge_close(b);
}

static void case_client_decoder(void) {
    unsigned char f[CB_MAX_FRAME + 1];
    struct cb_response_header h;
    memset(f, 0, sizeof f);
    memcpy(f, CB_RESPONSE_MAGIC, 4);
    cb_put16(f + CB_RS_MAJOR, 1);
    cb_put16(f + CB_RS_HEADER_SIZE, 64);
    cb_put32(f + CB_RS_PAYLOAD_SIZE, 16);
    CHECK(cb_decode_response_header(f, 80, &h) == 0);
    CHECK(cb_decode_response_header(f, 79, &h) != 0);  /* payload shorter than declared */
    CHECK(cb_decode_response_header(f, 63, &h) != 0);  /* short header */
    CHECK(cb_decode_response_header(f, CB_MAX_FRAME + 1, &h) != 0);
    cb_put16(f + CB_RS_MAJOR, 2);
    CHECK(cb_decode_response_header(f, 80, &h) != 0);
    cb_put16(f + CB_RS_MAJOR, 1);
    f[0] = 'X';
    CHECK(cb_decode_response_header(f, 80, &h) != 0);
}

static void case_accept_resource_exhaustion(void) {
    struct cammy_bridge *b = open_bridge(CB_BACKEND_FAKE, cammy_bridge_fake_backend(), NULL);
    CHECK(b != NULL);
    if (!b) return;
    int client = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    CHECK(client >= 0);
    if (client < 0) { cammy_bridge_close(b); return; }
    struct sockaddr_un sa;
    memset(&sa, 0, sizeof sa);
    sa.sun_family = AF_UNIX;
    memcpy(sa.sun_path, sock_path, strlen(sock_path) + 1);
    int connected = connect(client, (struct sockaddr *)&sa, sizeof sa);
    CHECK(connected == 0);
    if (connected != 0) { close(client); cammy_bridge_close(b); return; }
    struct rlimit original;
    int got_limit = getrlimit(RLIMIT_NOFILE, &original);
    CHECK(got_limit == 0);
    if (got_limit != 0) { close(client); cammy_bridge_close(b); return; }
    struct rlimit limited = original;
    if (limited.rlim_cur > 64) limited.rlim_cur = 64;
    int set_limit = setrlimit(RLIMIT_NOFILE, &limited);
    CHECK(set_limit == 0);
    if (set_limit != 0) { close(client); cammy_bridge_close(b); return; }
    int held[128];
    unsigned count = 0;
    while (count < 128) {
        int fd = dup(client);
        if (fd < 0) break;
        held[count++] = fd;
    }
    CHECK(count < 128 && errno == EMFILE);
    /* A real queued connection plus EMFILE must fail the instance, not keep
     * returning success against an always-readable listener. */
    int step_rc = cammy_bridge_step(b, 0);
    CHECK(step_rc == -1 && errno == EMFILE);
    CHECK(setrlimit(RLIMIT_NOFILE, &original) == 0);
    while (count) close(held[--count]);
    close(client);
    cammy_bridge_close(b);
}

int main(int argc, char **argv) {
    if (argc != 2 || strlen(argv[1]) > 80) {
        fputs("usage: bridge-selftest PRIVATE_EMPTY_DIR (<=80 chars)\n", stderr);
        return 2;
    }
    snprintf(dir, sizeof dir, "%s", argv[1]);
    snprintf(sock_path, sizeof sock_path, "%s/bridge.sock", dir);
    snprintf(boot_path, sizeof boot_path, "%s/boot_id", dir);
    if (chmod(dir, 0700) != 0) { perror("chmod"); return 2; }
    FILE *bf = fopen(boot_path, "w");
    if (!bf) { perror("boot_id"); return 2; }
    fputs("11223344-5566-7788-99aa-bbccddeef001\n", bf);
    fclose(bf);
    case_lying_native();
    case_lying_fake();
    case_rejected_tables();
    case_absent_and_init_failure();
    case_late_result_not_success();
    case_refused_listener_never_inits();
    case_client_decoder();
    case_native_reasons();
    case_wake_bounds_blocked_step();
    case_accept_resource_exhaustion();
    printf("bridge-selftest: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
