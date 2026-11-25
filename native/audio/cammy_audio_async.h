/* SPDX-License-Identifier: MIT */
#ifndef CAMMY_AUDIO_ASYNC_H
#define CAMMY_AUDIO_ASYNC_H
#include "cammy_audio.h"

#define CA_DRIVER_ABI 1u
enum ca_driver_event { CA_FRAME_RETURNED = 1, CA_STREAM_EOF = 2 };
typedef int (*ca_driver_callback)(void *, uint64_t job, unsigned event, uint32_t frame);
struct ca_driver {
    uint32_t abi, struct_size;
    int synthetic; /* native resolver deliberately absent until ABI/owner proof */
    void *ctx;
    /* All three run on the SAME private worker, outside adapter locks.
     * open may partially initialize even when it fails: close must handle it.
     * send receives frame id 1; return -1 means no effect/reference. Other
     * nonzero values mean uncertain effects and require cleanup + frame return.
     * An accepted frame must be returned once through callback. No callback is
     * allowed after close returns 0, including one already in progress. */
    int (*open)(void *, uint64_t job, ca_driver_callback, void *cookie);
    int (*send)(void *, uint64_t job, uint32_t frame, const uint8_t *, uint32_t);
    /* 0 means channel effects/PCM references AND callback producers quiescent.
     * Stop acceptance or merely a frame-return callback is insufficient.
     * May block: actor timeout never frees borrowed objects underneath it. */
    int (*close)(void *, uint64_t job);
};
struct ca_async;
struct ca_async_status {
    uint32_t stale, duplicates, rejected, overflow;
    int frame_returned, eof, closed, failed;
};
/* Actor-only except callback. Returned object owns core + worker + mailbox;
 * driver table/ctx must outlive successful destroy. No camera backend exists. */
struct ca_async *ca_async_create(const struct ca_binding *, const struct ca_driver *);
struct ca_core *ca_async_core(struct ca_async *);
void ca_async_poll(struct ca_async *, uint64_t now);
void ca_async_status(struct ca_async *, struct ca_async_status *);
/* Any callback thread. Bounded mailbox/CAS attempts, no allocation, locks or
 * core access. -1 means not delivered; overflow fails closed on the actor. */
int ca_async_callback(void *, uint64_t job, unsigned event, uint32_t frame);
/* Actor-only. 1 frees a quiescent object after join; 0 retains EVERYTHING.
 * First cancel/owner_lost + poll. No dlclose/free on failure. Join budget 200ms.
 * A blocked driver/failed close requires keeping object/library until exit. */
int ca_async_destroy(struct ca_async *);
const struct ca_driver *ca_native_audio_driver(void); /* NULL */
#endif
