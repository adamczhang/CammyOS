/* SPDX-License-Identifier: MIT */
#define _GNU_SOURCE
#include "cammy_audio_async.h"
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CAP 16u
#define MSG_SENT 3u
#define MSG_CLOSED 4u
struct message { uint64_t job; unsigned kind, value; int failed; };
struct cell { _Atomic uint32_t seq; struct message msg; };
struct ca_async {
    struct ca_core core;
    struct ca_backend backend;
    const struct ca_driver *driver;
    pthread_t worker;
    pthread_mutex_t lock;
    pthread_cond_t wake;
    _Atomic unsigned stop, quit, overflow, rejected, busy, pending;
    _Atomic uint32_t enq, deq;
    struct cell ring[CAP];
    uint64_t worker_job, actor_job;
    const uint8_t *worker_pcm;
    uint32_t worker_bytes;
    int sent, needs_return;
    struct ca_async_status status;
};

/* Sequence-cell mailbox follows native/media's MPSC ownership rule, with a
 * bounded eight-attempt reservation budget for callbacks. Saturation or
 * contention is observable loss, never synthetic completion. */
static int post(struct ca_async *a, const struct message *m)
{
    uint32_t pos = atomic_load_explicit(&a->enq, memory_order_relaxed);
    struct cell *cell; unsigned attempt;
    for (attempt = 0; attempt < 8; ++attempt) {
        cell = &a->ring[pos & (CAP - 1u)];
        uint32_t seq = atomic_load_explicit(&cell->seq, memory_order_acquire);
        if (seq == pos && atomic_compare_exchange_weak_explicit(&a->enq, &pos, pos + 1u,
                    memory_order_relaxed, memory_order_relaxed)) {
            cell->msg = *m;
            atomic_store_explicit(&cell->seq, pos + 1u, memory_order_release);
            return 0;
        }
        pos = atomic_load_explicit(&a->enq, memory_order_relaxed);
    }
    atomic_store_explicit(&a->overflow, 1u, memory_order_release);
    return -1;
}
static int pop(struct ca_async *a, struct message *m)
{
    uint32_t pos = atomic_load_explicit(&a->deq, memory_order_relaxed);
    struct cell *cell = &a->ring[pos & (CAP - 1u)];
    if (atomic_load_explicit(&cell->seq, memory_order_acquire) != pos + 1u) return 0;
    *m = cell->msg;
    atomic_store_explicit(&cell->seq, pos + CAP, memory_order_release);
    atomic_store_explicit(&a->deq, pos + 1u, memory_order_relaxed);
    return 1;
}
int ca_async_callback(void *cookie, uint64_t job, unsigned event, uint32_t frame)
{
    struct ca_async *a = cookie;
    struct message m = {job, event, frame, 0};
    if (!job || (event != CA_FRAME_RETURNED && event != CA_STREAM_EOF) ||
        (event == CA_FRAME_RETURNED && frame != 1u) ||
        (event == CA_STREAM_EOF && frame != 0u)) {
        atomic_store_explicit(&a->rejected, 1u, memory_order_relaxed);
        return -1;
    }
    return post(a, &m);
}
static void request_stop(struct ca_async *a)
{
    atomic_store_explicit(&a->stop, 1u, memory_order_release);
    /* Signal is a hint. A 20ms predicate recheck also covers lost wakeups. */
    (void)pthread_cond_signal(&a->wake);
}
static int submit(void *ctx, uint64_t job, const uint8_t *pcm, uint32_t bytes)
{
    struct ca_async *a = ctx; unsigned free_slot = 0;
    if (atomic_load_explicit(&a->overflow, memory_order_acquire) ||
        atomic_load_explicit(&a->quit, memory_order_acquire) ||
        !atomic_compare_exchange_strong_explicit(&a->busy, &free_slot, 1u,
                    memory_order_acq_rel, memory_order_relaxed)) return -1;
    a->worker_job = job; a->worker_pcm = pcm; a->worker_bytes = bytes;
    a->actor_job = job; a->sent = a->needs_return = 0;
    memset(&a->status, 0, sizeof a->status);
    atomic_store_explicit(&a->stop, 0u, memory_order_release);
    atomic_store_explicit(&a->pending, 1u, memory_order_release);
    (void)pthread_cond_signal(&a->wake);
    return 0;
}
static int stop(void *ctx, uint64_t job)
{
    struct ca_async *a = ctx;
    if (job != a->actor_job) return -1;
    request_stop(a);
    return 0;
}
static void wait_short(struct ca_async *a)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) {
        atomic_store_explicit(&a->stop, 1u, memory_order_release);
        return;
    }
    t.tv_nsec += 20000000L;
    if (t.tv_nsec >= 1000000000L) { ++t.tv_sec; t.tv_nsec -= 1000000000L; }
    (void)pthread_cond_timedwait(&a->wake, &a->lock, &t);
}
static void *worker(void *ctx)
{
    struct ca_async *a = ctx;
    for (;;) {
        uint64_t job; const uint8_t *pcm; uint32_t bytes;
        int opened = 0, failed = 0, needs_return = 0, closed = 1, result;
        struct message m;
        (void)pthread_mutex_lock(&a->lock);
        while (!atomic_load_explicit(&a->pending, memory_order_acquire) &&
               !atomic_load_explicit(&a->quit, memory_order_acquire)) wait_short(a);
        if (atomic_load_explicit(&a->quit, memory_order_acquire)) {
            (void)pthread_mutex_unlock(&a->lock); return NULL;
        }
        job = a->worker_job; pcm = a->worker_pcm; bytes = a->worker_bytes;
        atomic_store_explicit(&a->pending, 0u, memory_order_relaxed);
        (void)pthread_mutex_unlock(&a->lock);
        if (!atomic_load_explicit(&a->stop, memory_order_acquire)) {
            opened = 1; /* close even a partially failed open */
            result = a->driver->open(a->driver->ctx, job, ca_async_callback, a);
            failed = result != 0;
            if (!failed && !atomic_load_explicit(&a->stop, memory_order_acquire)) {
                result = a->driver->send(a->driver->ctx, job, 1u, pcm, bytes);
                needs_return = result != -1; failed = result != 0;
            }
        }
        m = (struct message){job, MSG_SENT, (unsigned)needs_return, failed};
        (void)post(a, &m);
        if (opened && !failed) {
            (void)pthread_mutex_lock(&a->lock);
            while (!atomic_load_explicit(&a->stop, memory_order_acquire)) wait_short(a);
            (void)pthread_mutex_unlock(&a->lock);
        }
        if (opened) closed = a->driver->close(a->driver->ctx, job) == 0;
        atomic_store_explicit(&a->busy, 0u, memory_order_release);
        m = (struct message){job, MSG_CLOSED, (unsigned)closed, failed};
        (void)post(a, &m);
    }
}
struct ca_async *ca_async_create(const struct ca_binding *id, const struct ca_driver *driver)
{
    struct ca_async *a; pthread_condattr_t attr; sigset_t mask, previous; unsigned i;
    if (!driver || driver->abi != CA_DRIVER_ABI || driver->struct_size != sizeof *driver ||
        driver->synthetic != 1 || !driver->open || !driver->send || !driver->close) return NULL;
    a = calloc(1, sizeof *a);
    if (!a) return NULL;
    a->driver = driver;
    a->backend = (struct ca_backend){CA_BACKEND_ABI, sizeof(struct ca_backend), 1, a, submit, stop};
    if (ca_init(&a->core, id, &a->backend) != CA_OK) goto bad;
    for (i = 0; i < CAP; ++i) atomic_init(&a->ring[i].seq, i);
    atomic_init(&a->enq, 0); atomic_init(&a->deq, 0);
    atomic_init(&a->stop, 0); atomic_init(&a->quit, 0);
    atomic_init(&a->overflow, 0); atomic_init(&a->rejected, 0);
    atomic_init(&a->busy, 0); atomic_init(&a->pending, 0);
    if (pthread_mutex_init(&a->lock, NULL)) goto bad;
    if (pthread_condattr_init(&attr)) goto bad_lock;
    if (pthread_condattr_setclock(&attr, CLOCK_MONOTONIC)) {
        (void)pthread_condattr_destroy(&attr); goto bad_lock;
    }
    i = (unsigned)pthread_cond_init(&a->wake, &attr);
    (void)pthread_condattr_destroy(&attr);
    if (i) goto bad_lock;
    (void)sigfillset(&mask);
    if (pthread_sigmask(SIG_SETMASK, &mask, &previous)) goto bad_cond;
    i = (unsigned)pthread_create(&a->worker, NULL, worker, a);
    (void)pthread_sigmask(SIG_SETMASK, &previous, NULL);
    if (i) goto bad_cond;
    return a;
bad_cond: (void)pthread_cond_destroy(&a->wake);
bad_lock: (void)pthread_mutex_destroy(&a->lock);
bad: free(a); return NULL;
}
struct ca_core *ca_async_core(struct ca_async *a) { return &a->core; }
void ca_async_poll(struct ca_async *a, uint64_t now)
{
    struct message m; unsigned count;
    ca_step(&a->core, now);
    if (atomic_load_explicit(&a->overflow, memory_order_acquire)) ca_owner_lost(&a->core, now);
    for (count = 0; count < CAP && pop(a, &m); ++count) {
        if (m.job != a->actor_job || a->core.phase == CA_IDLE) { ++a->status.stale; continue; }
        if (m.kind == CA_FRAME_RETURNED) {
            if (a->status.frame_returned) ++a->status.duplicates;
            a->status.frame_returned = 1;
        } else if (m.kind == CA_STREAM_EOF) {
            a->status.eof = 1; request_stop(a);
        } else if (m.kind == MSG_SENT) {
            a->sent = 1; a->needs_return = (int)m.value; a->status.failed |= m.failed;
        } else if (m.kind == MSG_CLOSED) {
            a->status.closed = (int)m.value; a->status.failed |= m.failed || !m.value;
            if (!m.value) ca_backend_fault(&a->core, now);
        }
    }
    /* Even a successful close cannot reclaim an accepted frame whose return
     * was lost. Overflow is terminal for this adapter instance, and never a
     * cleanup proof. Keep all storage pinned until process exit in that case. */
    if (!atomic_load_explicit(&a->overflow, memory_order_acquire) && a->sent &&
        a->status.closed && (!a->needs_return || a->status.frame_returned) &&
        a->core.phase != CA_IDLE) {
        ca_event(&a->core, a->actor_job, a->status.failed ? CA_FAILED_QUIESCENT :
                 (a->core.phase == CA_PLAYING ? CA_FINISHED : CA_QUIESCENT));
    }
}
void ca_async_status(struct ca_async *a, struct ca_async_status *s)
{
    *s = a->status;
    s->overflow = atomic_load_explicit(&a->overflow, memory_order_acquire);
    s->rejected = atomic_load_explicit(&a->rejected, memory_order_relaxed);
}
int ca_async_destroy(struct ca_async *a)
{
    struct timespec start, now, pause = {0, 1000000}; int rc;
    if (!ca_can_destroy(&a->core) || atomic_load_explicit(&a->overflow, memory_order_acquire)) return 0;
    if (clock_gettime(CLOCK_MONOTONIC, &start)) return 0;
    atomic_store_explicit(&a->quit, 1u, memory_order_release); (void)pthread_cond_signal(&a->wake);
    for (;;) {
        rc = pthread_tryjoin_np(a->worker, NULL);
        if (!rc) break;
        if (rc != EBUSY || clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
        if (now.tv_sec - start.tv_sec > 1 ||
            (now.tv_sec - start.tv_sec)*1000000000L + now.tv_nsec - start.tv_nsec >= 200000000L) return 0;
        (void)nanosleep(&pause, NULL);
    }
    (void)pthread_cond_destroy(&a->wake); (void)pthread_mutex_destroy(&a->lock);
    free(a); return 1;
}
const struct ca_driver *ca_native_audio_driver(void) { return NULL; }
