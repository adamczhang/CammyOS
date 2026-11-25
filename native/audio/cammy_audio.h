/* SPDX-License-Identifier: MIT */
#ifndef CAMMY_AUDIO_H
#define CAMMY_AUDIO_H
#include <stdint.h>

/* Finite speaker jobs: 16 kHz, mono, signed 16-bit little-endian PCM.
 * One actor thread calls ALL APIs, non-reentrantly, including event delivery. A future native
 * callback must enqueue events to that actor; it must not call this API directly.
 * No allocation, device access, clock syscall, network or worker is used here. */
#define CA_MAX_BYTES 64000u /* two seconds; NOT an unbounded speech queue */
#define CA_MAX_LEASE_MS 60000u
#define CA_BACKEND_ABI 1u
enum ca_phase { CA_IDLE, CA_QUEUED, CA_PLAYING, CA_STOPPING, CA_UNCERTAIN };
enum ca_result { CA_OK, CA_BAD_REQUEST, CA_BUSY, CA_STALE, CA_UNAVAILABLE, CA_EXHAUSTED };
enum ca_event { CA_FINISHED = 1, CA_QUIESCENT = 2, CA_FAILED_QUIESCENT = 3 };
enum ca_reason { CA_NONE, CA_COMPLETE, CA_CANCELLED, CA_DEADLINE, CA_LEASE_LOST,
                 CA_SUBMIT_REFUSED, CA_BACKEND_ERROR, CA_STOP_TIMEOUT };
struct ca_binding {
    uint64_t instance, controller, generation;
    uint8_t boot[16];
};
struct ca_core;
struct ca_backend {
    uint32_t abi, struct_size;
    int synthetic; /* only synthetic backends are accepted in this version */
    void *ctx;
    /* Must return without waiting for playback. 0 accepts the immutable buffer
     * until FINISHED or QUIESCENT. Exactly -1 refuses with NO effects/references.
     * Any other result is uncertain: the buffer stays pinned pending cleanup. */
    int (*play)(void *, uint64_t job, const uint8_t *pcm, uint32_t bytes);
    /* 0 accepts cleanup; any other result leaves cleanup uncertain. Never a
     * completion. QUIESCENT means no remaining reference/effect from this job,
     * including a play that finished late; it must be serialized with play. */
    int (*stop)(void *, uint64_t job);
};
struct ca_core {
    struct ca_binding identity, owner;
    const struct ca_backend *backend;
    uint64_t now, lease_until, job_deadline, stop_deadline, sequence, job;
    uint64_t last_request, last_generation;
    uint32_t bytes, stale_events;
    enum ca_phase phase;
    enum ca_reason reason;
    int bound, exhausted;
    /* Never reuse/free this storage before ca_can_destroy(), including after
     * timeout or owner loss. The backend may still hold a pointer into it. */
    uint8_t pcm[CA_MAX_BYTES];
};
/* init accepts a nonzero instance and boot; controller/generation must be zero.
 * Native is intentionally unavailable. The fixed cleanup budget is 200 ms;
 * playback gets its actual duration plus 250 ms, bounded by request deadline. */
int ca_init(struct ca_core *, const struct ca_binding *, const struct ca_backend *);
/* Caller supplies an already authenticated portal lease. This is not a lease
 * authority or a wire protocol. No owner replacement until previous job drains.
 * Generations must increase globally within this instance (future integration
 * layer-issued; existing portal leases do not carry this generation). */
int ca_bind(struct ca_core *, const struct ca_binding *, uint64_t now, uint64_t until);
int ca_renew(struct ca_core *, const struct ca_binding *, uint64_t now, uint64_t until);
int ca_enqueue(struct ca_core *, const struct ca_binding *, uint64_t request,
               uint64_t now, uint64_t deadline, const uint8_t *, uint32_t bytes);
int ca_cancel(struct ca_core *, const struct ca_binding *, uint64_t now);
/* Trusted disconnect/global-stop path, distinct from an owner's cancel. */
void ca_owner_lost(struct ca_core *, uint64_t now);
/* Trusted adapter error; initiates cleanup, never proves it. */
void ca_backend_fault(struct ca_core *, uint64_t now);
void ca_step(struct ca_core *, uint64_t now);
void ca_event(struct ca_core *, uint64_t job, enum ca_event);
int ca_can_destroy(const struct ca_core *);
int ca_physical_available(const struct ca_core *); /* always false in v1 */
const struct ca_backend *ca_native_backend(void); /* NULL, no dispatch proof */
#endif
