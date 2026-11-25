/* SPDX-License-Identifier: MIT
 * WP3 owned-media supervision core (local component, 2026-11-14).
 *
 * This core owns *bookkeeping* for one owned video session: lease generations,
 * typed start/stop/status admission, a bounded work queue, dispatch
 * revalidation, asynchronous completion handling and cleanup accounting.
 * It never touches hardware. Work reaches a backend function table through
 * nonblocking submit calls on the thread that runs cm_step(); results come back
 * through cm_post(), a lock-free bounded mailbox safe to call from any thread.
 *
 * The default native backend is ABSENT (cm_native_media_backend() returns
 * NULL): WP3's static analysis found no existing native lock or thread that
 * owns every media entrypoint, so no native adapter is eligible. A fake
 * backend exists for tests only and can never make a physical capability
 * available. See docs/native-bridge/MEDIA-CONTRACT.md.
 */
#ifndef CAMMY_MEDIA_H
#define CAMMY_MEDIA_H

#include <stdatomic.h>
#include <stdint.h>

#define CM_BACKEND_ABI 1u
#define CM_MAX_QUEUE 8u
#define CM_RING_CAP 16u            /* power of two */
#define CM_CTL_SLOTS 8u
#define CM_MAX_DEADLINE_HORIZON_MS 60000u
#define CM_MIN_LEASE_MS 1000u
#define CM_MAX_LEASE_MS 60000u

/* Request kinds. */
enum cm_req_kind {
    CM_REQ_ACQUIRE = 1, CM_REQ_RENEW = 2, CM_REQ_RELEASE = 3,
    CM_REQ_START = 4, CM_REQ_STOP = 5, CM_REQ_STATUS = 6
};
#define CM_REQ_MAX 6u

/* Enumerated stream profiles; never a pointer, path or method name. */
enum cm_profile { CM_PROFILE_NONE = 0, CM_PROFILE_PREVIEW_MAIN = 1 };

/* Admission / reply status. 0..2 are success-class. */
enum cm_status_code {
    CM_ST_OK = 0,                 /* status/renew/acquire success */
    CM_ST_ACCEPTED = 1,           /* start admitted and queued (not dispatched) */
    CM_ST_STOP_QUEUED = 2,        /* stop acknowledged; cleanup not yet confirmed */
    CM_ST_STOP_NOT_STARTED = 3,   /* queued start cancelled before dispatch; nothing to clean */
    CM_ST_ALREADY_STARTED = 4,    /* idempotent: session exists for this owner; no new work */
    CM_ST_ALREADY_STOPPING = 5,
    CM_ST_NO_SESSION = 6,
    CM_ST_UNKNOWN_KIND = 16,
    CM_ST_BAD_FIELD = 17,
    CM_ST_STALE_INSTANCE = 18,
    CM_ST_BOOT_MISMATCH = 19,
    CM_ST_BAD_DEADLINE = 20,
    CM_ST_EXPIRED = 21,
    CM_ST_BAD_REQUEST_ID = 22,
    CM_ST_REPLAYED = 23,
    CM_ST_BACKEND_UNAVAILABLE = 24,
    CM_ST_OWNED_BY_OTHER = 25,
    CM_ST_STALE_GENERATION = 26,
    CM_ST_NOT_OWNER = 27,
    CM_ST_LEASE_EXPIRED = 28,
    CM_ST_CLEANUP_PENDING = 29,   /* previous session not confirmed clean: no new owner/start */
    CM_ST_FAULTED = 30,           /* cleanup failed past its budget; restart required */
    CM_ST_QUEUE_FULL = 31,
    CM_ST_BAD_PROFILE = 32,
    CM_ST_ALREADY_OWNER = 33
};

/* Offending field identifiers reported with a rejection. */
enum cm_field {
    CM_F_NONE = 0, CM_F_KIND, CM_F_PROFILE, CM_F_REQUEST_ID, CM_F_CONTROLLER,
    CM_F_INSTANCE, CM_F_BOOT, CM_F_GENERATION, CM_F_DEADLINE, CM_F_LEASE, CM_F_RESERVED
};

/* Session phases: each is a distinct, separately reported state. */
enum cm_phase {
    CM_PH_NONE = 0,
    CM_PH_QUEUED = 1,             /* accepted, waiting in the bounded queue */
    CM_PH_START_DISPATCHED = 2,   /* backend submit returned; completion pending */
    CM_PH_ACTIVE = 3,             /* start completed OK (readiness is separate flags) */
    CM_PH_STOP_QUEUED = 4,        /* cleanup required and queued */
    CM_PH_CLEANUP_DISPATCHED = 5,
    CM_PH_CLEANUP_UNCERTAIN = 6,  /* cleanup timed out or start unresolved: effects unknown */
    CM_PH_FAULTED = 7             /* cleanup failed past budget */
};

enum cm_owner_state { CM_OWN_FREE = 0, CM_OWN_HELD = 1, CM_OWN_DRAINING = 2, CM_OWN_FAULTED = 3 };

/* Why the last session ended / why the owner is draining. */
enum cm_reason {
    CM_R_NONE = 0, CM_R_STOPPED, CM_R_STOPPED_BEFORE_DISPATCH, CM_R_EXPIRED_BEFORE_DISPATCH,
    CM_R_LEASE_EXPIRED, CM_R_CONTROLLER_LOST, CM_R_RELEASED, CM_R_START_FAILED,
    CM_R_START_NO_EFFECT, CM_R_START_TIMEOUT, CM_R_SUBMIT_REFUSED, CM_R_STALE_AT_DISPATCH,
    CM_R_CLEANUP_FAILED
};

/* Backend kinds and availability (mirrors WP2 honesty rules). */
enum cm_backend_kind { CM_BK_NONE = 0, CM_BK_FAKE = 1, CM_BK_NATIVE = 2 };
enum cm_backend_state { CM_BS_UNAVAILABLE = 0, CM_BS_READY = 1, CM_BS_ERROR = 2 };
enum cm_backend_reason {
    CM_BR_NONE = 0, CM_BR_FAKE_BACKEND = 1, CM_BR_NATIVE_BACKEND_ABSENT = 2,
    CM_BR_TABLE_REJECTED = 3, CM_BR_INIT_FAILED = 4, CM_BR_SERIALIZATION_UNPROVEN = 5
};

/* Completion results posted by a backend. */
enum cm_result { CM_RES_OK = 1, CM_RES_FAILED = 2, CM_RES_NO_EFFECT = 3 };
enum cm_comp_type { CM_COMP_START = 1, CM_COMP_CLEANUP = 2, CM_COMP_EVENT = 3 };
#define CM_EV_LISTENER 0x1u
#define CM_EV_SDP      0x2u
#define CM_EV_FRAME    0x4u
#define CM_COMP_SYNTHETIC 0x1u

/* Status flags. */
#define CM_SF_STOP_REQUESTED     0x0001u
#define CM_SF_START_UNRESOLVED   0x0002u
#define CM_SF_CLEANUP_UNRESOLVED 0x0004u
#define CM_SF_LISTENER_READY     0x0008u
#define CM_SF_SDP_READY          0x0010u
#define CM_SF_FRAME_FRESH        0x0020u
#define CM_SF_FRAMES_SYNTHETIC   0x0040u
#define CM_SF_LAST_CLEANUP_DONE  0x0080u  /* last ended session confirmed clean */
#define CM_SF_CLEANUP_OVERDUE    0x0100u
#define CM_SF_VIDEO_PHYSICAL     0x0200u  /* never set by this core version */

struct cm_request {
    uint16_t kind;
    uint16_t profile;              /* START only */
    uint32_t request_id;           /* non-zero, strictly increasing per controller */
    uint64_t controller_id;        /* assigned by the trusted transport, non-zero */
    uint64_t expect_instance;      /* mutating: must equal; STATUS: 0 = any */
    uint8_t boot_id[16];           /* mutating: required and equal */
    uint64_t owner_generation;     /* ACQUIRE: 0; others: current generation */
    uint64_t deadline_mono_ms;     /* mutating: required, absolute, same boot */
    uint32_t lease_ms;             /* ACQUIRE/RENEW */
    uint32_t reserved;             /* must be 0 */
};

struct cm_reply {
    uint16_t status;
    uint16_t field;
    uint16_t phase;
    uint16_t owner_state;
    uint64_t owner_generation;
    uint64_t session_id;
    uint64_t lease_expiry_ms;
};

struct cm_completion {
    uint16_t type;
    uint16_t result;
    uint32_t events;
    uint64_t token;                /* start op token (also the session token) or cleanup op token */
    uint64_t mono_ms;              /* event time in the core clock domain */
    uint32_t flags;
    uint32_t reserved;
};

struct cm_start_params { uint16_t profile; uint16_t reserved; uint32_t reserved2; uint64_t session_id; };

typedef uint64_t (*cm_clock_fn)(void *ctx);
struct cm_core;

/* Backend function table. Every submit_* must return immediately: it may only
 * enqueue work onto the backend's proven owning context. It must never run a
 * native media call on the caller's thread. Results arrive via cm_post(). */
struct cm_backend {
    uint32_t abi_version;          /* CM_BACKEND_ABI */
    uint32_t struct_size;          /* sizeof(struct cm_backend) */
    uint32_t kind;                 /* enum cm_backend_kind */
    uint32_t reserved;
    const char *name;
    void *ctx;
    int (*init)(void *ctx, struct cm_core *core);
    /* 0 = submitted (completion will follow); negative = refused with no effect. */
    int (*submit_start)(void *ctx, uint64_t token, const struct cm_start_params *p);
    int (*submit_cleanup)(void *ctx, uint64_t token, uint64_t session_token);
    void (*fini)(void *ctx);
};

struct cm_config {
    uint64_t instance;             /* non-zero */
    uint8_t boot_id[16];           /* non-zero */
    uint32_t queue_cap;            /* 1..CM_MAX_QUEUE */
    uint32_t start_timeout_ms;     /* cap from dispatch to start completion */
    uint32_t cleanup_timeout_ms;
    uint32_t cleanup_retry_ms;
    uint32_t cleanup_max_attempts; /* >= 1 */
    uint32_t frame_fresh_ms;
    uint32_t requested_kind;       /* operator choice: CM_BK_FAKE or CM_BK_NATIVE */
    uint32_t reserved;
};

struct cm_counters {
    uint32_t admitted, rejected, replays, stale_generation;
    uint32_t dispatched_starts, cancelled_before_dispatch, stale_at_dispatch;
    uint32_t start_ok, start_failed, start_timeouts, late_completions, stale_completions;
    uint32_t cleanups_dispatched, cleanups_ok, cleanup_failures, cleanup_timeouts;
    uint32_t events_applied, events_ignored, ring_overflows, owners_granted, owners_lost;
};

struct cm_status {
    uint16_t backend_kind, backend_state, backend_reason;
    uint16_t video_state, video_reason;  /* video_state is always unavailable in core v1 */
    uint16_t owner_state, owner_reason, phase, last_end_reason;
    uint16_t flags;
    uint32_t cleanup_attempts;
    uint64_t instance, owner_generation, owner_controller, lease_expiry_ms;
    uint64_t session_id, last_session_id, last_frame_ms, now_ms;
    struct cm_counters counters;
};

struct cm_ring_cell { _Atomic uint32_t seq; struct cm_completion msg; };

struct cm_item { uint16_t type; uint16_t pad; uint32_t pad2; uint64_t session_id; uint64_t gen; uint64_t deadline_ms; };

struct cm_ctl_slot { uint64_t controller_id; uint32_t last_request_id; uint32_t pad; uint64_t touched_ms; };

struct cm_core {
    struct cm_config cfg;
    const struct cm_backend *be;   /* NULL when unavailable */
    uint16_t bstate, breason;
    cm_clock_fn clock; void *clock_ctx;
    uint64_t now, op_seq, gen_seq, session_seq;
    /* owner */
    uint16_t owner_state, owner_reason;
    uint64_t owner_gen, owner_ctl, lease_expiry;
    /* session (at most one) */
    uint16_t phase, last_end_reason;
    uint32_t sflags;
    uint64_t session_id, last_session_id, start_token, cleanup_token;
    uint64_t start_deadline, cleanup_deadline, retry_at, last_frame_ms;
    uint32_t cleanup_attempts;
    uint16_t profile, stop_reason;
    /* bounded queue */
    struct cm_item q[CM_MAX_QUEUE];
    uint32_t q_head, q_len;
    struct cm_ctl_slot ctl[CM_CTL_SLOTS];
    struct cm_counters c;
    /* mailbox (multi-producer, consumed only by cm_step) */
    _Atomic uint32_t ring_enq, ring_deq;
    _Atomic uint32_t ring_overflow;
    struct cm_ring_cell ring[CM_RING_CAP];
};

/* Core API. The caller owns storage; the core never allocates. All functions
 * except cm_post() must be called from one thread (the stepping thread). */
int cm_core_init(struct cm_core *c, const struct cm_config *cfg, const struct cm_backend *be,
                 cm_clock_fn clock, void *clock_ctx);
/* Quiesce/join all completion producers before fini or freeing caller storage.
 * fini is local teardown, not confirmation that physical cleanup completed. */
void cm_core_fini(struct cm_core *c);
void cm_submit(struct cm_core *c, const struct cm_request *rq, struct cm_reply *rp);
void cm_controller_lost(struct cm_core *c, uint64_t controller_id);
void cm_step(struct cm_core *c);
void cm_get_status(struct cm_core *c, struct cm_status *st);
/* Any thread, lock-free (not wait-free), no allocation. No per-caller latency
 * bound under contention. 0 on success, -1 if the mailbox is
 * full (counted; the affected op then resolves only by timeout, i.e. stays
 * uncertain until a later completion or cleanup). */
int cm_post(struct cm_core *c, const struct cm_completion *m);

uint64_t cm_monotonic_ms(void *unused);
const struct cm_backend *cm_native_media_backend(void);
const char *cm_status_name(unsigned v);
const char *cm_phase_name(unsigned v);
const char *cm_reason_name(unsigned v);
const char *cm_owner_name(unsigned v);

#endif
