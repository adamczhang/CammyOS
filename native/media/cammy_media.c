/* SPDX-License-Identifier: MIT
 * WP3 owned-media supervision core. See cammy_media.h and
 * docs/native-bridge/MEDIA-CONTRACT.md. No hardware access, no allocation,
 * no stdio, no signals; one stepping thread plus lock-free cm_post().
 */
#include "cammy_media.h"

#include <string.h>

#define SESSION_FLAGS (CM_SF_STOP_REQUESTED | CM_SF_START_UNRESOLVED | CM_SF_CLEANUP_UNRESOLVED | \
                       CM_SF_LISTENER_READY | CM_SF_SDP_READY | CM_SF_FRAMES_SYNTHETIC | CM_SF_CLEANUP_OVERDUE)

static int zero16(const uint8_t *b)
{
    uint8_t acc = 0;
    for (unsigned i = 0; i < 16; i++) acc = (uint8_t)(acc | b[i]);
    return acc == 0;
}

/* Monotonic clamp: a clock that steps backwards never re-opens a deadline. */
static uint64_t tick(struct cm_core *c)
{
    uint64_t t = c->clock(c->clock_ctx);
    if (t > c->now) c->now = t;
    return c->now;
}

/* ---- bounded start queue ------------------------------------------------ */

static int q_push(struct cm_core *c, const struct cm_item *it)
{
    if (c->q_len >= c->cfg.queue_cap) return -1;
    c->q[(c->q_head + c->q_len) % CM_MAX_QUEUE] = *it;
    c->q_len++;
    return 0;
}

static const struct cm_item *q_peek(const struct cm_core *c)
{
    return c->q_len ? &c->q[c->q_head] : NULL;
}

static void q_pop(struct cm_core *c)
{
    if (!c->q_len) return;
    c->q_head = (c->q_head + 1) % CM_MAX_QUEUE;
    c->q_len--;
}

/* Remove every queued start for a session; returns how many were removed. */
static uint32_t q_cancel_session(struct cm_core *c, uint64_t sid)
{
    struct cm_item keep[CM_MAX_QUEUE];
    uint32_t n = 0, removed = 0;
    for (uint32_t i = 0; i < c->q_len; i++) {
        const struct cm_item *it = &c->q[(c->q_head + i) % CM_MAX_QUEUE];
        if (it->session_id == sid) removed++;
        else keep[n++] = *it;
    }
    c->q_head = 0;
    c->q_len = n;
    for (uint32_t i = 0; i < n; i++) c->q[i] = keep[i];
    return removed;
}

/* ---- session / owner transitions ----------------------------------------- */

static void session_end(struct cm_core *c, uint16_t reason, int confirmed_clean)
{
    c->last_session_id = c->session_id;
    c->last_end_reason = reason;
    c->sflags &= ~(uint32_t)(SESSION_FLAGS | CM_SF_LAST_CLEANUP_DONE);
    if (confirmed_clean) c->sflags |= CM_SF_LAST_CLEANUP_DONE;
    c->session_id = 0;
    c->phase = CM_PH_NONE;
    c->start_token = c->cleanup_token = 0;
    c->start_deadline = c->cleanup_deadline = c->retry_at = c->last_frame_ms = 0;
    c->cleanup_attempts = 0;
    c->profile = 0;
    c->stop_reason = CM_R_NONE;
    if (c->owner_state == CM_OWN_DRAINING) {
        c->owner_state = CM_OWN_FREE;
        c->owner_gen = c->owner_ctl = c->lease_expiry = 0;
    }
}

/* Begin stopping the current session. Never reports cleanup as done unless the
 * start was provably never dispatched. */
static uint16_t stop_session(struct cm_core *c, uint16_t reason)
{
    switch (c->phase) {
    case CM_PH_NONE:
        return CM_ST_NO_SESSION;
    case CM_PH_QUEUED:
        c->c.cancelled_before_dispatch += q_cancel_session(c, c->session_id);
        session_end(c, reason == CM_R_STOPPED ? CM_R_STOPPED_BEFORE_DISPATCH : reason, 1);
        return CM_ST_STOP_NOT_STARTED;
    case CM_PH_START_DISPATCHED:
        if (c->sflags & CM_SF_STOP_REQUESTED) return CM_ST_ALREADY_STOPPING;
        c->sflags |= CM_SF_STOP_REQUESTED;
        c->stop_reason = reason;
        return CM_ST_STOP_QUEUED;         /* cleanup follows start resolution or timeout */
    case CM_PH_ACTIVE:
        c->sflags |= CM_SF_STOP_REQUESTED;
        c->stop_reason = reason;
        c->phase = CM_PH_STOP_QUEUED;
        c->retry_at = c->now;
        return CM_ST_STOP_QUEUED;
    default:
        return CM_ST_ALREADY_STOPPING;    /* cleanup already pending / uncertain / faulted */
    }
}

static void owner_lost(struct cm_core *c, uint16_t reason)
{
    if (c->owner_state != CM_OWN_HELD) return;
    c->owner_state = CM_OWN_DRAINING;
    c->owner_reason = reason;
    c->c.owners_lost++;
    (void)stop_session(c, reason);
    if (c->phase == CM_PH_NONE && c->owner_state == CM_OWN_DRAINING) {
        c->owner_state = CM_OWN_FREE;
        c->owner_gen = c->owner_ctl = c->lease_expiry = 0;
    }
}

static void check_lease(struct cm_core *c, uint64_t now)
{
    if (c->owner_state == CM_OWN_HELD && now >= c->lease_expiry) owner_lost(c, CM_R_LEASE_EXPIRED);
}

static void fault(struct cm_core *c)
{
    c->phase = CM_PH_FAULTED;
    c->owner_state = CM_OWN_FAULTED;
    c->owner_reason = CM_R_CLEANUP_FAILED;
}

/* ---- completions ---------------------------------------------------------- */

static void cleanup_failed(struct cm_core *c)
{
    c->c.cleanup_failures++;
    if (c->cleanup_attempts >= c->cfg.cleanup_max_attempts) {
        fault(c);
        return;
    }
    c->phase = CM_PH_STOP_QUEUED;
    c->retry_at = c->now + c->cfg.cleanup_retry_ms;
}

static void apply_start(struct cm_core *c, const struct cm_completion *m)
{
    if (!c->start_token || m->token != c->start_token) { c->c.stale_completions++; return; }
    uint16_t res = m->result;
    if (res != CM_RES_OK && res != CM_RES_NO_EFFECT) res = CM_RES_FAILED;   /* unknown = failed */
    if (c->phase == CM_PH_START_DISPATCHED) {
        if (res == CM_RES_OK) {
            c->c.start_ok++;
            if (c->sflags & CM_SF_STOP_REQUESTED) { c->phase = CM_PH_STOP_QUEUED; c->retry_at = c->now; }
            else c->phase = CM_PH_ACTIVE;
        } else if (res == CM_RES_FAILED) {
            /* A failed start may have left partial effects: cleanup is required. */
            c->c.start_failed++;
            if (!(c->sflags & CM_SF_STOP_REQUESTED)) c->stop_reason = CM_R_START_FAILED;
            c->sflags |= CM_SF_STOP_REQUESTED;
            c->phase = CM_PH_STOP_QUEUED;
            c->retry_at = c->now;
        } else {
            c->c.start_failed++;
            session_end(c, (c->sflags & CM_SF_STOP_REQUESTED) ? c->stop_reason : CM_R_START_NO_EFFECT, 1);
        }
        return;
    }
    if (c->sflags & CM_SF_START_UNRESOLVED) {
        /* Late completion of a timed-out start. Dropping it cannot undo an effect. */
        c->c.late_completions++;
        c->sflags &= ~(uint32_t)CM_SF_START_UNRESOLVED;
        int cleanup_already_acked = (c->phase == CM_PH_CLEANUP_UNCERTAIN && c->cleanup_token == 0);
        if (!cleanup_already_acked) return;   /* queued/in-flight cleanup is ordered after start */
        if (res == CM_RES_NO_EFFECT) {
            session_end(c, c->stop_reason, 1);
        } else {
            c->phase = CM_PH_STOP_QUEUED;      /* effect may post-date the acked cleanup */
            c->retry_at = c->now;
        }
        return;
    }
    c->c.stale_completions++;                  /* duplicate for a resolved start */
}

static void apply_cleanup(struct cm_core *c, const struct cm_completion *m)
{
    if (!c->cleanup_token || m->token != c->cleanup_token ||
        (c->phase != CM_PH_CLEANUP_DISPATCHED && c->phase != CM_PH_CLEANUP_UNCERTAIN)) {
        c->c.stale_completions++;
        return;
    }
    if (c->phase == CM_PH_CLEANUP_UNCERTAIN) c->c.late_completions++;
    c->cleanup_token = 0;
    c->sflags &= ~(uint32_t)(CM_SF_CLEANUP_UNRESOLVED | CM_SF_CLEANUP_OVERDUE);
    if (m->result == CM_RES_OK || m->result == CM_RES_NO_EFFECT) {
        c->c.cleanups_ok++;
        if (c->sflags & CM_SF_START_UNRESOLVED) {
            c->phase = CM_PH_CLEANUP_UNCERTAIN;  /* not final until the start resolves */
            return;
        }
        session_end(c, c->stop_reason, 1);
        return;
    }
    cleanup_failed(c);
}

static void apply_event(struct cm_core *c, const struct cm_completion *m)
{
    if (!c->start_token || m->token != c->start_token || (c->sflags & CM_SF_STOP_REQUESTED) ||
        (c->phase != CM_PH_START_DISPATCHED && c->phase != CM_PH_ACTIVE)) {
        c->c.events_ignored++;
        return;
    }
    c->c.events_applied++;
    if (m->events & CM_EV_LISTENER) c->sflags |= CM_SF_LISTENER_READY;
    if (m->events & CM_EV_SDP) c->sflags |= CM_SF_SDP_READY;
    if (m->events & CM_EV_FRAME) {
        c->last_frame_ms = m->mono_ms > c->now ? c->now : m->mono_ms;
        /* The core, not the backend, decides labeling: fake frames are synthetic. */
        if ((m->flags & CM_COMP_SYNTHETIC) || c->be->kind == CM_BK_FAKE) c->sflags |= CM_SF_FRAMES_SYNTHETIC;
    }
}

static int ring_pop(struct cm_core *c, struct cm_completion *out)
{
    uint32_t pos = atomic_load_explicit(&c->ring_deq, memory_order_relaxed);
    struct cm_ring_cell *cell = &c->ring[pos & (CM_RING_CAP - 1)];
    uint32_t seq = atomic_load_explicit(&cell->seq, memory_order_acquire);
    if ((int32_t)(seq - (pos + 1)) < 0) return 0;
    *out = cell->msg;
    atomic_store_explicit(&cell->seq, pos + CM_RING_CAP, memory_order_release);
    atomic_store_explicit(&c->ring_deq, pos + 1, memory_order_relaxed);
    return 1;
}

int cm_post(struct cm_core *c, const struct cm_completion *m)
{
    uint32_t pos = atomic_load_explicit(&c->ring_enq, memory_order_relaxed);
    struct cm_ring_cell *cell;
    for (;;) {
        cell = &c->ring[pos & (CM_RING_CAP - 1)];
        uint32_t seq = atomic_load_explicit(&cell->seq, memory_order_acquire);
        int32_t dif = (int32_t)(seq - pos);
        if (dif == 0) {
            if (atomic_compare_exchange_weak_explicit(&c->ring_enq, &pos, pos + 1, memory_order_relaxed,
                                                      memory_order_relaxed))
                break;
        } else if (dif < 0) {
            atomic_fetch_add_explicit(&c->ring_overflow, 1u, memory_order_relaxed);
            return -1;
        } else {
            pos = atomic_load_explicit(&c->ring_enq, memory_order_relaxed);
        }
    }
    cell->msg = *m;
    atomic_store_explicit(&cell->seq, pos + 1, memory_order_release);
    return 0;
}

/* ---- step: completions, expiry, timeouts, one dispatch -------------------- */

static void check_timeouts(struct cm_core *c, uint64_t now)
{
    if (c->phase == CM_PH_START_DISPATCHED && now >= c->start_deadline) {
        /* No completion: the native effect is unknown. Require cleanup; keep the
         * start token so a late completion is still recognised. */
        c->c.start_timeouts++;
        if (!(c->sflags & CM_SF_STOP_REQUESTED)) c->stop_reason = CM_R_START_TIMEOUT;
        c->sflags |= CM_SF_START_UNRESOLVED | CM_SF_STOP_REQUESTED;
        c->phase = CM_PH_STOP_QUEUED;
        c->retry_at = now;
    }
    if (c->phase == CM_PH_CLEANUP_DISPATCHED && now >= c->cleanup_deadline) {
        c->c.cleanup_timeouts++;
        c->sflags |= CM_SF_CLEANUP_UNRESOLVED | CM_SF_CLEANUP_OVERDUE;
        c->phase = CM_PH_CLEANUP_UNCERTAIN;  /* no second cleanup while one is unresolved */
    }
}

static void dispatch_one(struct cm_core *c, uint64_t now)
{
    const struct cm_item *it = q_peek(c);
    if (c->phase == CM_PH_QUEUED && it) {
        struct cm_item item = *it;
        q_pop(c);
        /* Revalidate immediately before dispatch. */
        int ok = item.session_id == c->session_id && c->owner_state == CM_OWN_HELD &&
                 item.gen == c->owner_gen && now < c->lease_expiry && now < item.deadline_ms &&
                 !(c->sflags & CM_SF_STOP_REQUESTED);
        if (!ok) {
            c->c.stale_at_dispatch++;
            c->c.cancelled_before_dispatch++;
            session_end(c, now >= item.deadline_ms ? CM_R_EXPIRED_BEFORE_DISPATCH : CM_R_STALE_AT_DISPATCH, 1);
            return;
        }
        struct cm_start_params p = {c->profile, 0, 0, c->session_id};
        c->start_token = ++c->op_seq;
        c->start_deadline = now + c->cfg.start_timeout_ms;
        c->phase = CM_PH_START_DISPATCHED;
        if (c->be->submit_start(c->be->ctx, c->start_token, &p) < 0) {
            session_end(c, CM_R_SUBMIT_REFUSED, 1);   /* refused synchronously, no effect */
            return;
        }
        c->c.dispatched_starts++;
        return;
    }
    if (c->phase == CM_PH_STOP_QUEUED && now >= c->retry_at) {
        c->cleanup_token = ++c->op_seq;
        c->cleanup_attempts++;
        c->cleanup_deadline = now + c->cfg.cleanup_timeout_ms;
        c->phase = CM_PH_CLEANUP_DISPATCHED;
        c->c.cleanups_dispatched++;
        if (c->be->submit_cleanup(c->be->ctx, c->cleanup_token, c->start_token) < 0) {
            c->cleanup_token = 0;
            cleanup_failed(c);
        }
    }
}

void cm_step(struct cm_core *c)
{
    uint64_t now = tick(c);
    if (!c->be) return;
    struct cm_completion m;
    for (unsigned i = 0; i < CM_RING_CAP && ring_pop(c, &m); i++) {
        if (m.type == CM_COMP_START) apply_start(c, &m);
        else if (m.type == CM_COMP_CLEANUP) apply_cleanup(c, &m);
        else if (m.type == CM_COMP_EVENT) apply_event(c, &m);
        else c->c.stale_completions++;
    }
    check_lease(c, now);
    check_timeouts(c, now);
    dispatch_one(c, now);
}

/* ---- admission ------------------------------------------------------------- */

static struct cm_ctl_slot *ctl_slot(struct cm_core *c, uint64_t id)
{
    struct cm_ctl_slot *victim = NULL;
    for (unsigned i = 0; i < CM_CTL_SLOTS; i++)
        if (c->ctl[i].controller_id == id) return &c->ctl[i];
    /* New controller: take an empty slot, else evict the least recently used
     * non-owner slot. Eviction forgets that controller's high-water mark; the
     * deadline horizon and per-connection controller IDs bound the exposure. */
    for (unsigned i = 0; i < CM_CTL_SLOTS; i++) {
        struct cm_ctl_slot *s = &c->ctl[i];
        if (s->controller_id && s->controller_id == c->owner_ctl && c->owner_state != CM_OWN_FREE) continue;
        if (!s->controller_id) { victim = s; break; }
        if (!victim || s->touched_ms < victim->touched_ms) victim = s;
    }
    if (victim) {
        victim->controller_id = id;
        victim->last_request_id = 0;
    }
    return victim;
}

static void fill(struct cm_core *c, const struct cm_request *rq, struct cm_reply *rp, uint16_t st, uint16_t field)
{
    rp->status = st;
    rp->field = field;
    rp->phase = c->phase;
    rp->owner_state = c->owner_state;
    if (c->owner_ctl && rq->controller_id == c->owner_ctl) {   /* generation only to its holder */
        rp->owner_generation = c->owner_gen;
        rp->session_id = c->session_id;
        rp->lease_expiry_ms = c->owner_state == CM_OWN_HELD ? c->lease_expiry : 0;
    }
    if (st >= CM_ST_UNKNOWN_KIND || st == CM_ST_ALREADY_OWNER) c->c.rejected++;
}

void cm_submit(struct cm_core *c, const struct cm_request *rq, struct cm_reply *rp)
{
    memset(rp, 0, sizeof *rp);
    uint64_t now = tick(c);
    if (c->be) check_lease(c, now);
    int mutating = rq->kind != CM_REQ_STATUS;

    if (rq->kind < 1 || rq->kind > CM_REQ_MAX) { fill(c, rq, rp, CM_ST_UNKNOWN_KIND, CM_F_KIND); return; }
    if (rq->reserved) { fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_RESERVED); return; }
    if (mutating && !rq->controller_id) { fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_CONTROLLER); return; }
    if (rq->profile && rq->kind != CM_REQ_START) { fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_PROFILE); return; }
    if (rq->lease_ms && rq->kind != CM_REQ_ACQUIRE && rq->kind != CM_REQ_RENEW) {
        fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_LEASE);
        return;
    }
    if ((mutating || rq->expect_instance) && rq->expect_instance != c->cfg.instance) {
        fill(c, rq, rp, CM_ST_STALE_INSTANCE, CM_F_INSTANCE);
        return;
    }
    if ((mutating || !zero16(rq->boot_id)) &&
        (zero16(rq->boot_id) || memcmp(rq->boot_id, c->cfg.boot_id, 16) != 0)) {
        fill(c, rq, rp, CM_ST_BOOT_MISMATCH, CM_F_BOOT);
        return;
    }
    if (mutating && !rq->deadline_mono_ms) { fill(c, rq, rp, CM_ST_BAD_DEADLINE, CM_F_DEADLINE); return; }
    if (rq->deadline_mono_ms) {
        if (zero16(rq->boot_id)) { fill(c, rq, rp, CM_ST_BAD_DEADLINE, CM_F_DEADLINE); return; }
        if (now >= rq->deadline_mono_ms) { fill(c, rq, rp, CM_ST_EXPIRED, CM_F_DEADLINE); return; }
        if (rq->deadline_mono_ms - now > CM_MAX_DEADLINE_HORIZON_MS) {
            fill(c, rq, rp, CM_ST_BAD_DEADLINE, CM_F_DEADLINE);
            return;
        }
    }
    if (!rq->request_id) { fill(c, rq, rp, CM_ST_BAD_REQUEST_ID, CM_F_REQUEST_ID); return; }
    if (rq->controller_id) {
        struct cm_ctl_slot *s = ctl_slot(c, rq->controller_id);
        if (!s) { fill(c, rq, rp, CM_ST_QUEUE_FULL, CM_F_CONTROLLER); return; }
        if (rq->request_id <= s->last_request_id) {
            c->c.replays++;
            fill(c, rq, rp, CM_ST_REPLAYED, CM_F_REQUEST_ID);
            return;
        }
        s->last_request_id = rq->request_id;   /* consumed even if a later check fails */
        s->touched_ms = now;
    }
    if (rq->kind == CM_REQ_STATUS) { fill(c, rq, rp, CM_ST_OK, CM_F_NONE); return; }
    if (!c->be) { fill(c, rq, rp, CM_ST_BACKEND_UNAVAILABLE, CM_F_NONE); return; }
    if (c->owner_state == CM_OWN_FAULTED) { fill(c, rq, rp, CM_ST_FAULTED, CM_F_NONE); return; }

    if (rq->kind == CM_REQ_ACQUIRE) {
        if (rq->owner_generation) { fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_GENERATION); return; }
        if (rq->lease_ms < CM_MIN_LEASE_MS || rq->lease_ms > CM_MAX_LEASE_MS) {
            fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_LEASE);
            return;
        }
        if (c->owner_state == CM_OWN_HELD) {
            fill(c, rq, rp, rq->controller_id == c->owner_ctl ? CM_ST_ALREADY_OWNER : CM_ST_OWNED_BY_OTHER,
                 CM_F_CONTROLLER);
            return;
        }
        if (c->owner_state == CM_OWN_DRAINING || c->phase != CM_PH_NONE) {
            fill(c, rq, rp, CM_ST_CLEANUP_PENDING, CM_F_NONE);
            return;
        }
        c->owner_state = CM_OWN_HELD;
        c->owner_reason = CM_R_NONE;
        c->owner_gen = ++c->gen_seq;
        c->owner_ctl = rq->controller_id;
        c->lease_expiry = now + rq->lease_ms;
        c->c.owners_granted++;
        fill(c, rq, rp, CM_ST_OK, CM_F_NONE);
        return;
    }

    if (c->owner_state == CM_OWN_FREE || rq->owner_generation != c->owner_gen) {
        c->c.stale_generation++;
        fill(c, rq, rp, CM_ST_STALE_GENERATION, CM_F_GENERATION);
        return;
    }
    if (rq->controller_id != c->owner_ctl) { fill(c, rq, rp, CM_ST_NOT_OWNER, CM_F_CONTROLLER); return; }
    if (c->owner_state != CM_OWN_HELD) { fill(c, rq, rp, CM_ST_LEASE_EXPIRED, CM_F_GENERATION); return; }

    switch (rq->kind) {
    case CM_REQ_RENEW:
        if (rq->lease_ms < CM_MIN_LEASE_MS || rq->lease_ms > CM_MAX_LEASE_MS) {
            fill(c, rq, rp, CM_ST_BAD_FIELD, CM_F_LEASE);
            return;
        }
        c->lease_expiry = now + rq->lease_ms;
        fill(c, rq, rp, CM_ST_OK, CM_F_NONE);
        return;
    case CM_REQ_RELEASE: {
        uint16_t phase_before = c->phase;
        owner_lost(c, CM_R_RELEASED);
        fill(c, rq, rp, phase_before == CM_PH_NONE ? CM_ST_OK
                        : (c->phase == CM_PH_NONE ? CM_ST_STOP_NOT_STARTED : CM_ST_STOP_QUEUED), CM_F_NONE);
        if (c->owner_state != CM_OWN_FREE) {      /* holder still sees its draining record */
            rp->owner_generation = c->owner_gen;
            rp->session_id = c->session_id;
        }
        return;
    }
    case CM_REQ_START:
        if (rq->profile != CM_PROFILE_PREVIEW_MAIN) { fill(c, rq, rp, CM_ST_BAD_PROFILE, CM_F_PROFILE); return; }
        if (c->phase != CM_PH_NONE) {
            int stopping = (c->sflags & CM_SF_STOP_REQUESTED) || c->phase >= CM_PH_STOP_QUEUED;
            fill(c, rq, rp, stopping ? CM_ST_CLEANUP_PENDING : CM_ST_ALREADY_STARTED, CM_F_NONE);
            return;
        }
        {
            struct cm_item it = {1, 0, 0, c->session_seq + 1, c->owner_gen, rq->deadline_mono_ms};
            if (q_push(c, &it) < 0) { fill(c, rq, rp, CM_ST_QUEUE_FULL, CM_F_NONE); return; }
        }
        c->session_id = ++c->session_seq;
        c->phase = CM_PH_QUEUED;
        c->profile = rq->profile;
        c->sflags &= ~(uint32_t)(SESSION_FLAGS | CM_SF_LAST_CLEANUP_DONE);
        c->c.admitted++;
        fill(c, rq, rp, CM_ST_ACCEPTED, CM_F_NONE);
        return;
    case CM_REQ_STOP:
        fill(c, rq, rp, stop_session(c, CM_R_STOPPED), CM_F_NONE);
        return;
    default:
        fill(c, rq, rp, CM_ST_UNKNOWN_KIND, CM_F_KIND);
        return;
    }
}

void cm_controller_lost(struct cm_core *c, uint64_t controller_id)
{
    (void)tick(c);
    if (controller_id && c->owner_state == CM_OWN_HELD && c->owner_ctl == controller_id)
        owner_lost(c, CM_R_CONTROLLER_LOST);
}

/* ---- status ----------------------------------------------------------------- */

void cm_get_status(struct cm_core *c, struct cm_status *st)
{
    memset(st, 0, sizeof *st);
    uint64_t now = tick(c);
    st->backend_kind = (uint16_t)(c->be ? c->be->kind : c->cfg.requested_kind);
    st->backend_state = c->bstate;
    st->backend_reason = c->breason;
    /* Core v1: no physical capability is ever available, whatever a backend reports. */
    st->video_state = CM_BS_UNAVAILABLE;
    st->video_reason = c->be ? (c->be->kind == CM_BK_FAKE ? CM_BR_FAKE_BACKEND : CM_BR_SERIALIZATION_UNPROVEN)
                             : c->breason;
    st->owner_state = c->owner_state;
    st->owner_reason = c->owner_reason;
    st->phase = c->phase;
    st->last_end_reason = c->last_end_reason;
    uint32_t f = c->sflags & (CM_SF_STOP_REQUESTED | CM_SF_START_UNRESOLVED | CM_SF_CLEANUP_UNRESOLVED |
                              CM_SF_FRAMES_SYNTHETIC | CM_SF_LAST_CLEANUP_DONE | CM_SF_CLEANUP_OVERDUE);
    int live = c->phase == CM_PH_ACTIVE && !(c->sflags & CM_SF_STOP_REQUESTED);
    if (live) {
        f |= c->sflags & (CM_SF_LISTENER_READY | CM_SF_SDP_READY);
        if (c->last_frame_ms && now - c->last_frame_ms <= c->cfg.frame_fresh_ms) f |= CM_SF_FRAME_FRESH;
    }
    st->flags = (uint16_t)f;
    st->cleanup_attempts = c->cleanup_attempts;
    st->instance = c->cfg.instance;
    st->owner_generation = c->owner_gen;
    st->owner_controller = c->owner_ctl;
    st->lease_expiry_ms = c->owner_state == CM_OWN_HELD ? c->lease_expiry : 0;
    st->session_id = c->session_id;
    st->last_session_id = c->last_session_id;
    st->last_frame_ms = c->last_frame_ms;
    st->now_ms = now;
    st->counters = c->c;
    st->counters.ring_overflows = atomic_load_explicit(&c->ring_overflow, memory_order_relaxed);
}

/* ---- init / fini --------------------------------------------------------------- */

int cm_core_init(struct cm_core *c, const struct cm_config *cfg, const struct cm_backend *be, cm_clock_fn clock,
                 void *clock_ctx)
{
    memset(c, 0, sizeof *c);
    if (!cfg || !clock || !cfg->instance || zero16(cfg->boot_id) || cfg->queue_cap < 1 ||
        cfg->queue_cap > CM_MAX_QUEUE || !cfg->start_timeout_ms || !cfg->cleanup_timeout_ms ||
        !cfg->cleanup_max_attempts || !cfg->frame_fresh_ms || cfg->reserved ||
        (cfg->requested_kind != CM_BK_FAKE && cfg->requested_kind != CM_BK_NATIVE))
        return -1;
    c->cfg = *cfg;
    c->clock = clock;
    c->clock_ctx = clock_ctx;
    for (uint32_t i = 0; i < CM_RING_CAP; i++) atomic_init(&c->ring[i].seq, i);
    atomic_init(&c->ring_enq, 0u);
    atomic_init(&c->ring_deq, 0u);
    atomic_init(&c->ring_overflow, 0u);
    (void)tick(c);
    c->bstate = CM_BS_UNAVAILABLE;
    if (!be) {
        c->breason = cfg->requested_kind == CM_BK_NATIVE ? CM_BR_NATIVE_BACKEND_ABSENT : CM_BR_TABLE_REJECTED;
        return 0;
    }
    if (be->abi_version != CM_BACKEND_ABI || be->struct_size != sizeof *be || be->kind != cfg->requested_kind ||
        be->reserved || !be->name || !be->init || !be->submit_start || !be->submit_cleanup || !be->fini) {
        c->breason = CM_BR_TABLE_REJECTED;
        return 0;
    }
    if (be->kind == CM_BK_NATIVE) {
        /* WP3: no proven native serialization owner. Refuse any native table
         * until a reviewed core version removes this gate explicitly. */
        c->breason = CM_BR_SERIALIZATION_UNPROVEN;
        return 0;
    }
    if (be->init(be->ctx, c) != 0) {
        c->breason = CM_BR_INIT_FAILED;
        return 0;
    }
    c->be = be;
    c->bstate = CM_BS_READY;
    c->breason = CM_BR_FAKE_BACKEND;
    return 0;
}

void cm_core_fini(struct cm_core *c)
{
    if (c->be) c->be->fini(c->be->ctx);
    c->be = NULL;
    c->bstate = CM_BS_UNAVAILABLE;
}

/* ---- names (fixtures and diagnostics) --------------------------------------------- */

const char *cm_status_name(unsigned v)
{
    static const char *const lo[] = {"ok", "accepted", "stop_queued", "stop_not_started", "already_started",
                                     "already_stopping", "no_session"};
    static const char *const hi[] = {"unknown_kind", "bad_field", "stale_instance", "boot_mismatch",
                                     "bad_deadline", "expired", "bad_request_id", "replayed",
                                     "backend_unavailable", "owned_by_other", "stale_generation", "not_owner",
                                     "lease_expired", "cleanup_pending", "faulted", "queue_full", "bad_profile",
                                     "already_owner"};
    if (v < sizeof lo / sizeof lo[0]) return lo[v];
    if (v >= 16 && v - 16 < sizeof hi / sizeof hi[0]) return hi[v - 16];
    return "unknown";
}

const char *cm_phase_name(unsigned v)
{
    static const char *const n[] = {"none", "queued", "start_dispatched", "active", "stop_queued",
                                    "cleanup_dispatched", "cleanup_uncertain", "faulted"};
    return v < sizeof n / sizeof n[0] ? n[v] : "unknown";
}

const char *cm_reason_name(unsigned v)
{
    static const char *const n[] = {"none", "stopped", "stopped_before_dispatch", "expired_before_dispatch",
                                    "lease_expired", "controller_lost", "released", "start_failed",
                                    "start_no_effect", "start_timeout", "submit_refused", "stale_at_dispatch",
                                    "cleanup_failed"};
    return v < sizeof n / sizeof n[0] ? n[v] : "unknown";
}

const char *cm_owner_name(unsigned v)
{
    static const char *const n[] = {"free", "held", "draining", "faulted"};
    return v < sizeof n / sizeof n[0] ? n[v] : "unknown";
}
