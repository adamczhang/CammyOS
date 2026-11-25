/* SPDX-License-Identifier: MIT. One immutable, finite GUI-owned face trial. */
#ifndef CAMMY_FACE_SURFACE_H
#define CAMMY_FACE_SURFACE_H
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>

enum cmf_phase { CMF_WAITING, CMF_VISIBLE, CMF_REMOVED, CMF_FAILED };
struct cmf_surface;
struct cmf_gui_api {
    void *context;
    void *(*screen)(void *context);
    void *(*top)(void *context);
    /* Must register the deletion observer BEFORE installing pixels. */
    void *(*create)(void *context, void *parent, struct cmf_surface *surface);
    void (*pixels)(void *context, void *object, const void *bgra, unsigned w, unsigned h);
    void (*remove)(void *context, void *object);
};
struct cmf_surface {
    struct cmf_gui_api api;
    const void *bgra; /* immutable; lifetime extends through process exit */
    unsigned width, height;
    uint64_t show_at, expires_at, last_now;
    enum cmf_phase phase;
    void *object, *screen;
    atomic_uint stop;
    unsigned created, deleted;
};
/* init runs before publication. tick/deleted run ONLY on the one GUI thread.
 * request_stop is the sole cross-thread operation. No waits, I/O or free here.
 * A stopped GUI cannot prove cleanup: callers retain library and pixel storage.
 */
int cmf_surface_init(struct cmf_surface *, const struct cmf_gui_api *, const void *,
                     size_t, unsigned, unsigned, uint64_t, uint64_t, uint64_t);
void cmf_surface_tick(struct cmf_surface *, uint64_t now);
void cmf_surface_deleted(struct cmf_surface *, void *object);
void cmf_surface_request_stop(struct cmf_surface *);
#endif
