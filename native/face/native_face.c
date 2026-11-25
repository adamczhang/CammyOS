/* SPDX-License-Identifier: MIT. OFFLINE TRIAL BUILD; not deployed/approved.
 * Exact stock GUI entrypoints; NO LVGL structure definitions or framebuffer I/O.
 * Static pixel storage + never dlclose means abrupt GUI exit cannot dangle it.
 */
#define _POSIX_C_SOURCE 200809L
#include "cammy_face_surface.h"
#include "cammy_face_host.h"
#include <time.h>
#include <string.h>
#include <errno.h>
#include "trial_pixels.h"
#ifndef CMF_TRIAL_DELAY_MS
#define CMF_TRIAL_DELAY_MS 3000u
#endif

#define PUBLIC __attribute__((visibility("default")))
static struct cmf_surface surface;
static atomic_uint published, attached, detached;
static const unsigned char source_hash[32] = {
  0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e,
  0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec
};
/* Separate profile: cannot be loaded by the status-only stub/activation route. */
PUBLIC int cmf_attach_v1(const struct cmf_host *host);
PUBLIC void cmf_detach_v1(uint32_t reason);
PUBLIC void cmf_gui_v1(void *window_manager, uint32_t phase);

static int milliseconds(uint64_t *out) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) || ts.tv_sec < 0 || ts.tv_nsec < 0 || ts.tv_nsec >= 1000000000L) return -1;
    *out = (uint64_t)ts.tv_sec * 1000 + (uint32_t)ts.tv_nsec / 1000000u;
    return 0;
}
static void *screen(void *unused) {
    (void)unused; return ((void *(*)(void *))(uintptr_t)0x48aad0)(NULL);
}
static void *top(void *unused) {
    (void)unused; return ((void *(*)(void *))(uintptr_t)0x48ab5c)(NULL);
}
static void deleted(void *event) {
    void *target = ((void *(*)(void *))(uintptr_t)0x4969bc)(event);
    cmf_surface_deleted(&surface, target);
}
static void remove_object(void *unused, void *object) {
    (void)unused; ((void (*)(void *))(uintptr_t)0x496294)(object);
}
static void *create(void *unused, void *parent, struct cmf_surface *s) {
    (void)unused; (void)s;
    void *obj = ((void *(*)(void *))(uintptr_t)0x4aaa3c)(parent);
    if (!obj) return NULL;
    /* Exact delete event is 33, verified from obj_del_core (0x496100). */
    void *dsc = ((void *(*)(void *, void (*)(void *), unsigned, void *))(uintptr_t)0x496a6c)(obj, deleted, 33, NULL);
    if (!dsc) { remove_object(NULL, obj); return NULL; }
    return obj;
}
static void pixels(void *unused, void *obj, const void *p, unsigned w, unsigned h) {
    (void)unused;
    ((void (*)(void *, int))(uintptr_t)0x490838)(obj, 0);
    if (surface.object != obj) return;
    ((void (*)(void *, int))(uintptr_t)0x49088c)(obj, 0);
    if (surface.object != obj) return;
    ((void (*)(void *, const void *, unsigned, unsigned, unsigned))(uintptr_t)0x4aaa7c)(obj, p, w, h, 4);
}
PUBLIC int cmf_attach_v1(const struct cmf_host *host) {
    if (!host || host->abi_version != CMF_HOST_ABI || host->struct_size != sizeof(*host) ||
        host->profile != CMF_HOST_PROFILE || memcmp(host->source_sha256, source_hash, 32)) return -EPROTO;
    if (atomic_exchange_explicit(&attached, 1, memory_order_acq_rel)) return -EALREADY;
    uint64_t now;
    if (milliseconds(&now)) return -EIO;
    const struct cmf_gui_api api = {NULL, screen, top, create, pixels, remove_object};
    if (cmf_surface_init(&surface, &api, trial_pixels, sizeof(trial_pixels), 356, 400, now, CMF_TRIAL_DELAY_MS, 10000)) return -EINVAL;
    atomic_store_explicit(&published, 1, memory_order_release);
    return 0;
}
PUBLIC void cmf_detach_v1(uint32_t reason) {
    (void)reason;
    atomic_store_explicit(&detached, 1, memory_order_release);
    /* Main thread only requests; it never deletes a GUI object. No dlclose. */
}
PUBLIC void cmf_gui_v1(void *window_manager, uint32_t phase) {
    (void)window_manager;
    if (phase || !atomic_load_explicit(&published, memory_order_acquire)) return;
    uint64_t now;
    if (atomic_load_explicit(&detached, memory_order_acquire) || milliseconds(&now)) {
        cmf_surface_request_stop(&surface); now = surface.last_now;
    }
    cmf_surface_tick(&surface, now);
}
