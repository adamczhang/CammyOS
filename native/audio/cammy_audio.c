/* SPDX-License-Identifier: MIT */
#include "cammy_audio.h"
#include <limits.h>
#include <stddef.h>
#include <string.h>

static int nonzero(const uint8_t *p)
{
    unsigned i; int v = 0;
    for (i = 0; i < 16; ++i) v |= p[i];
    return v != 0;
}
static int same(const struct ca_binding *a, const struct ca_binding *b)
{
    return a && b && a->instance == b->instance && a->controller == b->controller &&
           a->generation == b->generation && !memcmp(a->boot, b->boot, 16);
}
static int time_ok(struct ca_core *c, uint64_t now)
{
    if (now < c->now || now > UINT64_MAX - CA_MAX_LEASE_MS) return 0;
    c->now = now;
    return 1;
}
static void finish(struct ca_core *c, enum ca_reason why)
{
    c->phase = CA_IDLE; c->reason = why; c->bytes = 0; c->job = 0;
    memset(c->pcm, 0, sizeof c->pcm);
}
static void halt(struct ca_core *c, enum ca_reason why)
{
    int r;
    if (c->phase == CA_IDLE) return;
    if (c->phase == CA_QUEUED) { finish(c, why); return; }
    if (c->phase == CA_STOPPING || c->phase == CA_UNCERTAIN) return;
    c->phase = CA_STOPPING; c->reason = why; c->stop_deadline = c->now + 200u;
    r = c->backend->stop(c->backend->ctx, c->job);
    if (r != 0) { c->phase = CA_UNCERTAIN; c->reason = CA_BACKEND_ERROR; }
}
static void advance(struct ca_core *c, uint64_t now)
{
    if (c->bound && now >= c->lease_until) {
        c->bound = 0; halt(c, CA_LEASE_LOST);
    }
    if (c->phase == CA_STOPPING && now >= c->stop_deadline) {
        c->phase = CA_UNCERTAIN; c->reason = CA_STOP_TIMEOUT;
    }
    if ((c->phase == CA_QUEUED || c->phase == CA_PLAYING) && now >= c->job_deadline)
        halt(c, CA_DEADLINE);
}
int ca_init(struct ca_core *c, const struct ca_binding *id, const struct ca_backend *be)
{
    if (!c || !id || !id->instance || !nonzero(id->boot) || id->controller || id->generation)
        return CA_BAD_REQUEST;
    memset(c, 0, sizeof *c); c->identity = *id;
    if (!be || be->abi != CA_BACKEND_ABI || be->struct_size != sizeof *be ||
        be->synthetic != 1 || !be->play || !be->stop) return CA_UNAVAILABLE;
    c->backend = be;
    return CA_OK;
}
int ca_bind(struct ca_core *c, const struct ca_binding *b, uint64_t now, uint64_t until)
{
    if (!c->backend) return CA_UNAVAILABLE;
    if (!b || !time_ok(c, now) || !b->controller || !b->generation ||
        b->instance != c->identity.instance || memcmp(b->boot, c->identity.boot, 16) ||
        until <= now || until - now > CA_MAX_LEASE_MS) return CA_BAD_REQUEST;
    advance(c, now);
    if (c->bound || c->phase != CA_IDLE) return CA_BUSY;
    if (b->generation <= c->last_generation) return CA_STALE;
    c->owner = *b; c->bound = 1; c->lease_until = until; c->last_request = 0;
    c->last_generation = b->generation;
    return CA_OK;
}
int ca_renew(struct ca_core *c, const struct ca_binding *b, uint64_t now, uint64_t until)
{
    if (!time_ok(c, now)) return CA_BAD_REQUEST;
    advance(c, now);
    if (!c->bound || !same(b, &c->owner)) return CA_STALE;
    if (until <= now || until - now > CA_MAX_LEASE_MS) return CA_BAD_REQUEST;
    c->lease_until = until;
    return CA_OK;
}
int ca_enqueue(struct ca_core *c, const struct ca_binding *b, uint64_t request,
               uint64_t now, uint64_t deadline, const uint8_t *pcm, uint32_t bytes)
{
    if (!time_ok(c, now)) return CA_BAD_REQUEST;
    advance(c, now);
    if (!c->bound || !same(b, &c->owner)) return CA_STALE;
    if (!request || request <= c->last_request) return CA_STALE;
    /* Consume authenticated request ids even for refusals; never replay later. */
    c->last_request = request;
    if (!pcm || !bytes || bytes > CA_MAX_BYTES || (bytes & 1u) ||
        deadline <= now || deadline - now > CA_MAX_LEASE_MS) return CA_BAD_REQUEST;
    if (c->phase != CA_IDLE) return CA_BUSY;
    if (c->sequence == UINT64_MAX) { c->exhausted = 1; return CA_EXHAUSTED; }
    c->job = ++c->sequence;
    memmove(c->pcm, pcm, bytes); c->bytes = bytes;
    c->job_deadline = deadline;
    c->reason = CA_NONE; c->phase = CA_QUEUED;
    return CA_OK;
}
int ca_cancel(struct ca_core *c, const struct ca_binding *b, uint64_t now)
{
    if (!time_ok(c, now)) return CA_BAD_REQUEST;
    advance(c, now);
    if (!c->bound || !same(b, &c->owner)) return CA_STALE;
    halt(c, CA_CANCELLED);
    return CA_OK;
}
void ca_owner_lost(struct ca_core *c, uint64_t now)
{
    if (!time_ok(c, now)) return;
    c->bound = 0;
    halt(c, CA_LEASE_LOST);
}
void ca_backend_fault(struct ca_core *c, uint64_t now)
{
    if (!time_ok(c, now)) return;
    advance(c, now); halt(c, CA_BACKEND_ERROR);
}
void ca_step(struct ca_core *c, uint64_t now)
{
    uint64_t play_until; int r;
    if (!time_ok(c, now)) return;
    advance(c, now);
    if (c->phase != CA_QUEUED) return;
    /* Revalidate immediately before dispatch, after lease/deadline cancellation. */
    if (!c->bound) { finish(c, CA_LEASE_LOST); return; }
    play_until = now + (c->bytes + 31u) / 32u + 250u;
    if (play_until < c->job_deadline) c->job_deadline = play_until;
    c->phase = CA_PLAYING;
    r = c->backend->play(c->backend->ctx, c->job, c->pcm, c->bytes);
    if (r == -1) finish(c, CA_SUBMIT_REFUSED);
    else if (r != 0) halt(c, CA_BACKEND_ERROR);
}
void ca_event(struct ca_core *c, uint64_t job, enum ca_event event)
{
    /* QUEUED has never reached the sink, so no completion can be valid there. */
    if (!job || job != c->job || c->phase == CA_QUEUED || c->phase == CA_IDLE ||
        (event != CA_FINISHED && event != CA_QUIESCENT && event != CA_FAILED_QUIESCENT) ||
        (event == CA_QUIESCENT && c->phase == CA_PLAYING)) {
        if (c->stale_events != UINT32_MAX) ++c->stale_events;
        return;
    }
    finish(c, event == CA_FAILED_QUIESCENT ? CA_BACKEND_ERROR :
              (c->phase == CA_PLAYING ? CA_COMPLETE : c->reason));
}
int ca_can_destroy(const struct ca_core *c) { return c->phase == CA_IDLE; }
int ca_physical_available(const struct ca_core *c) { (void)c; return 0; }
const struct ca_backend *ca_native_backend(void) { return NULL; }
