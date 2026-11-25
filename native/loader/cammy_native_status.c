/* SPDX-License-Identifier: MIT
 * Status-only in-process native bridge library (WP2A). Loaded by the offline
 * status candidate's stub at the 0x31DB0 bracket in sdvcam, or by a synthetic host.
 *
 * What it does: validates the stub's host record, opens the WP2 core with a
 * status-only native table, and serves it on one bridge-owned thread with every
 * blockable signal masked. It never dereferences main_module or any native
 * object, posts no native task, opens no device node and reports every physical
 * capability unavailable (the WP2 core clamps this again).
 *
 * Threads and ownership:
 *  - attach/detach run on the caller (the native main thread). attach is
 *    single-shot per process; detach is idempotent and a no-op unless attach
 *    succeeded.
 *  - The table's init runs inside cammy_bridge_open on the attaching thread before
 *    the bridge thread exists; query callbacks run only on the bridge thread; fini
 *    runs only from cammy_bridge_close, which is called either by detach after a
 *    completed join, by attach on its own failure path before any thread exists,
 *    or by the bridge thread itself after a fatal step error.
 *  - Abandon: if the bridge thread has not exited within the detach budget,
 *    detach returns without joining, closing, freeing or unmapping anything. The
 *    library is never dlclosed; all state is static or the leaked core handle, so
 *    everything reachable by a late-running bridge thread stays valid until death.
 *  - A fatal core step error (for example EMFILE on accept) marks the bridge
 *    FAILED, releases its descriptors from the bridge thread and stops serving.
 *    It never affects the native application or calls exit.
 */
#define _GNU_SOURCE
#include "cammy_native_host.h"
#include "../bridge/cammy_bridge.h"
#include "../bridge/cammy_bridge_proto.h"

#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CNS_EXPORT __attribute__((visibility("default")))

#ifndef CNS_SOCKET_PATH
#define CNS_SOCKET_PATH "/tmp/cammy-bridge/bridge.sock"
#endif
#define CNS_DETACH_BUDGET_MS 200u
#define CNS_STEP_WAIT_MS 1000
#define CNS_THREAD_STACK (64u * 1024u)
#define CNS_MAX_CLIENTS 4u
#define CNS_IDLE_TIMEOUT_MS 10000u

/* Exact stock sdvcam this build accepts (8771961f...62b0ec). */
static const uint8_t cns_source_sha256[32] = {
    0x87, 0x71, 0x96, 0x1f, 0xdb, 0xa8, 0x92, 0x80, 0x57, 0x8c, 0xe5, 0x48, 0x2a, 0x6c, 0xbe, 0x4e,
    0xc3, 0xf6, 0xec, 0x20, 0x50, 0x68, 0x94, 0x4b, 0x34, 0xf9, 0x44, 0x63, 0x7d, 0x62, 0xb0, 0xec};

#if defined(__arm__)
_Static_assert(sizeof(void *) == 4 && sizeof(long) == 4, "ARM32 ILP32 expected");
_Static_assert(sizeof(struct cammy_native_host_v1) == 48, "host record is 48 bytes on ARM32");
_Static_assert(offsetof(struct cammy_native_host_v1, source_sha256) == 12, "hash offset");
_Static_assert(offsetof(struct cammy_native_host_v1, main_module) == 44, "main_module offset");
#endif
#ifdef CNS_DEVICE_ABI
/* Device profile: musl 1.1.24 headers. A time64 header set fails here, not on the camera. */
_Static_assert(sizeof(time_t) == 4, "device musl 1.1.24 uses 32-bit time_t");
_Static_assert(sizeof(struct timespec) == 8, "32-bit timespec");
#endif

enum cns_phase {
    CNS_IDLE = 0,
    CNS_SERVING = 2,
    CNS_EXITING = 3,
    CNS_FAILED = 4,   /* bridge thread failure: unavailable, native app unaffected */
    CNS_REJECTED = 5  /* attach refused or could not start */
};

enum cns_detach_result { CNS_DETACH_NONE = 0, CNS_DETACH_JOINED = 1, CNS_DETACH_ABANDONED = 2 };

/* All library state is static: nothing here is freed, so a thread abandoned by
 * detach can keep running against it until process death. */
static struct {
    uint32_t attach_claimed;  /* first attach call wins */
    uint32_t attached;        /* attach completed successfully */
    uint32_t detach_claimed;  /* first detach after a successful attach wins */
    uint32_t phase;           /* enum cns_phase */
    uint32_t table_inited;
    uint32_t thread_done;     /* bridge thread left its loop */
    uint32_t detach_result;   /* enum cns_detach_result */
    uint32_t detach_reason;
    uint32_t step_failures;
    uint32_t attach_us, detach_us;
    pthread_t thread;
    pthread_mutex_t handle_lock;     /* guards handle against wake-after-close */
    struct cammy_bridge *handle;     /* leaked, never closed, on abandon */
#ifdef CNS_TEST_HOOKS
    int stall_fd;                    /* test build only: stall one status query */
#endif
} g = {
    .handle_lock = PTHREAD_MUTEX_INITIALIZER,
#ifdef CNS_TEST_HOOKS
    .stall_fd = -1,
#endif
};

static uint32_t ld(const uint32_t *p) { return __atomic_load_n(p, __ATOMIC_SEQ_CST); }
static void st(uint32_t *p, uint32_t v) { __atomic_store_n(p, v, __ATOMIC_SEQ_CST); }
static int cas(uint32_t *p, uint32_t from, uint32_t to) {
    return __atomic_compare_exchange_n(p, &from, to, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

static uint64_t mono_us(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

#if defined(__arm__)
/* ARM RTABI lets the platform supply the division-by-zero hooks. libgcc's default
 * raises SIGFPE; inside sdvcam we must never raise a signal. All 64-bit divisors in
 * this library (the WP2 core's clock conversion) are non-zero constants, so these
 * are unreachable; defining them keeps `raise` out of the import table. */
int __aeabi_idiv0(int value);
long long __aeabi_ldiv0(long long value);
int __aeabi_idiv0(int value) { return value; }
long long __aeabi_ldiv0(long long value) { return value; }
#endif

/* ---- status-only native table (WP2 backend ABI 1) ---- */

static int t_init(void *ctx) {
    (void)ctx;
    st(&g.table_inited, 1);
    return 0;
}

static int t_query_status(void *ctx, struct cammy_bridge_backend_status *out) {
    (void)ctx;
#ifdef CNS_TEST_HOOKS
    /* Fixture-only: stall mid-step, inside the core, to force the detach abandon
     * path and then prove the pinned handle and peers still work afterwards. */
    int sfd = __atomic_exchange_n(&g.stall_fd, -1, __ATOMIC_SEQ_CST);
    if (sfd >= 0) {
        char c;
        while (read(sfd, &c, 1) < 0 && errno == EINTR) {}
    }
#endif
    if (!ld(&g.table_inited)) return -1;
    switch (ld(&g.phase)) {
    case CNS_SERVING:
        out->state = CB_BSTATE_READY;
        out->reason = CB_BREASON_NONE;
        return 0;
    case CNS_EXITING:
        out->state = CB_BSTATE_UNAVAILABLE;
        out->reason = CB_BREASON_NATIVE_EXITING;
        return 0;
    default:
        return -1; /* core reports error/query_failed */
    }
}

static int t_query_capabilities(void *ctx, struct cammy_bridge_cap *caps, unsigned count) {
    (void)ctx;
    for (unsigned i = 0; i < count; i++) {
        caps[i].state = CB_CAP_UNAVAILABLE;
        caps[i].reason = CB_CREASON_NOT_IN_PROTOCOL_V1;
    }
    return 0;
}

static void t_fini(void *ctx) {
    (void)ctx;
    st(&g.table_inited, 0);
}

static const struct cammy_bridge_backend native_status_table = {
    .abi_version = CAMMY_BRIDGE_BACKEND_ABI,
    .struct_size = sizeof(struct cammy_bridge_backend),
    .kind = CB_BACKEND_NATIVE,
    .reserved = 0,
    .name = "native-status-only",
    .ctx = NULL,
    .init = t_init,
    .query_status = t_query_status,
    .query_capabilities = t_query_capabilities,
    .fini = t_fini,
};

/* ---- bridge thread ---- */

static void *serve_main(void *arg) {
    (void)arg;
    struct cammy_bridge *b = g.handle; /* published before pthread_create */
    while (ld(&g.phase) == CNS_SERVING) {
        if (cammy_bridge_step(b, CNS_STEP_WAIT_MS) != 0) {
            __atomic_add_fetch(&g.step_failures, 1, __ATOMIC_SEQ_CST);
            /* Only the winner of SERVING->FAILED may release the handle. If detach
             * already moved SERVING->EXITING, detach owns it (join+close or pin). */
            if (cas(&g.phase, CNS_SERVING, CNS_FAILED)) {
                pthread_mutex_lock(&g.handle_lock);
                g.handle = NULL; /* detach can no longer wake a closed handle */
                pthread_mutex_unlock(&g.handle_lock);
                cammy_bridge_close(b); /* this thread was the only stepper */
            }
            break;
        }
    }
    st(&g.thread_done, 1);
    return NULL;
}

static int open_error_to_errno(enum cammy_bridge_open_error e) {
    switch (e) {
    case CB_OPEN_BAD_CONFIG: return -EINVAL;
    case CB_OPEN_UNSAFE_DIR: return -EACCES;
    case CB_OPEN_ALREADY_RUNNING: return -EBUSY;
    case CB_OPEN_PATH_OCCUPIED: return -EADDRINUSE;
    default: return -EIO;
    }
}

static int validate_host(const struct cammy_native_host_v1 *host) {
    if (!host) return -EINVAL;
    /* Read the two leading words first: they are present in every version. */
    if (host->abi_version != CAMMY_NATIVE_HOST_ABI) return -EPROTO;
    if (host->struct_size != sizeof(struct cammy_native_host_v1)) return -EPROTO;
    if (host->profile != CAMMY_PROFILE_STATUS_ONLY) return -EPROTO;
    if (memcmp(host->source_sha256, cns_source_sha256, sizeof cns_source_sha256) != 0) return -EPROTO;
    /* main_module is deliberately neither read nor stored. */
    return 0;
}

CNS_EXPORT int cammy_native_attach_v1(const struct cammy_native_host_v1 *host) {
    if (!cas(&g.attach_claimed, 0, 1)) return -EALREADY;
    uint64_t t0 = mono_us();
    int rc = validate_host(host);
    if (rc != 0) {
        st(&g.phase, CNS_REJECTED);
        return rc;
    }

    struct cammy_bridge_config cfg = {
        .socket_path = CNS_SOCKET_PATH,
        .boot_id_path = NULL,
        .backend_kind = CB_BACKEND_NATIVE,
        .max_clients = CNS_MAX_CLIENTS,
        .idle_timeout_ms = CNS_IDLE_TIMEOUT_MS,
        .allowed_uid = geteuid(),
    };
    enum cammy_bridge_open_error oe = CB_OPEN_SYSTEM;
    struct cammy_bridge *b = cammy_bridge_open(&cfg, &native_status_table, &oe);
    if (!b) {
        st(&g.phase, CNS_REJECTED);
        return open_error_to_errno(oe);
    }
    if (!ld(&g.table_inited)) { /* core refused or failed our table: do not serve */
        cammy_bridge_close(b);
        st(&g.phase, CNS_REJECTED);
        return -EPROTO;
    }

    pthread_attr_t attr;
    if (pthread_attr_init(&attr) != 0) {
        cammy_bridge_close(b);
        st(&g.phase, CNS_REJECTED);
        return -ENOMEM;
    }
    int arc = pthread_attr_setstacksize(&attr, CNS_THREAD_STACK);
    sigset_t all, old;
    sigfillset(&all);
    int mrc = arc == 0 ? pthread_sigmask(SIG_BLOCK, &all, &old) : arc;
    if (mrc != 0) {
        pthread_attr_destroy(&attr);
        cammy_bridge_close(b);
        st(&g.phase, CNS_REJECTED);
        return -mrc;
    }
    g.handle = b;
    st(&g.phase, CNS_SERVING);
    /* The new thread inherits the all-blocked mask; the caller's mask is restored. */
    int crc = pthread_create(&g.thread, &attr, serve_main, NULL);
    pthread_sigmask(SIG_SETMASK, &old, NULL);
    pthread_attr_destroy(&attr);
    if (crc != 0) {
        g.handle = NULL;
        st(&g.phase, CNS_REJECTED);
        cammy_bridge_close(b); /* no thread exists: safe */
        return -crc;
    }
    st(&g.attach_us, (uint32_t)(mono_us() - t0));
    st(&g.attached, 1);
    return 0;
}

CNS_EXPORT void cammy_native_detach_v1(uint32_t reason) {
    if (!ld(&g.attached)) return;              /* before/without a successful attach */
    if (!cas(&g.detach_claimed, 0, 1)) return; /* idempotent */
    uint64_t t0 = mono_us();
    uint64_t deadline = t0 + (uint64_t)CNS_DETACH_BUDGET_MS * 1000u;
    st(&g.detach_reason, reason);
    cas(&g.phase, CNS_SERVING, CNS_EXITING); /* a FAILED bridge stays FAILED */

    /* Wake the stepping thread. The lock is held by the bridge thread only for a
     * pointer swap; still, never wait on it past the deadline. */
    for (;;) {
        if (pthread_mutex_trylock(&g.handle_lock) == 0) {
            if (g.handle) cammy_bridge_wake(g.handle);
            pthread_mutex_unlock(&g.handle_lock);
            break;
        }
        if (mono_us() >= deadline) break;
        poll(NULL, 0, 1);
    }

    /* Bounded join: CLOCK_MONOTONIC polling, no CLOCK_REALTIME absolute timeout. */
    int joined = 0;
    for (;;) {
        if (pthread_tryjoin_np(g.thread, NULL) == 0) { joined = 1; break; }
        if (mono_us() >= deadline) break;
        poll(NULL, 0, 1);
    }
    if (joined) {
        pthread_mutex_lock(&g.handle_lock); /* uncontended: the only other user has exited */
        struct cammy_bridge *b = g.handle;
        g.handle = NULL;
        pthread_mutex_unlock(&g.handle_lock);
        if (b) cammy_bridge_close(b); /* unlinks our socket, releases lock/fds, fini */
        st(&g.detach_result, CNS_DETACH_JOINED);
    } else {
        /* Abandon: no join, close, free, unlink or dlclose. The thread, the core
         * handle and this library stay valid until process death. */
        st(&g.detach_result, CNS_DETACH_ABANDONED);
    }
    st(&g.detach_us, (uint32_t)(mono_us() - t0));
}

#ifdef CNS_TEST_HOOKS
/* Fixture-only exports; the release build exports exactly attach and detach. */
CNS_EXPORT void cammy_native_test_stall(int fd);
CNS_EXPORT void cammy_native_test_state(uint32_t out[10]);

/* The next status query on the bridge thread blocks until fd is readable. */
CNS_EXPORT void cammy_native_test_stall(int fd) { __atomic_store_n(&g.stall_fd, fd, __ATOMIC_SEQ_CST); }

CNS_EXPORT void cammy_native_test_state(uint32_t out[10]) {
    out[0] = ld(&g.phase);
    out[1] = ld(&g.attached);
    out[2] = ld(&g.detach_result);
    out[3] = ld(&g.thread_done);
    out[4] = ld(&g.attach_us);
    out[5] = ld(&g.detach_us);
    out[6] = ld(&g.step_failures);
    out[7] = ld(&g.table_inited);
    out[8] = ld(&g.detach_reason);
    pthread_mutex_lock(&g.handle_lock);
    out[9] = g.handle != NULL;
    pthread_mutex_unlock(&g.handle_lock);
}
#endif
