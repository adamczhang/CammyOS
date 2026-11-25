/* SPDX-License-Identifier: MIT */
#include "cammy_face_surface.h"
#include <string.h>
#include <limits.h>

int cmf_surface_init(struct cmf_surface *s, const struct cmf_gui_api *a, const void *p,
                     size_t n, unsigned w, unsigned h, uint64_t now,
                     uint64_t delay, uint64_t duration) {
    if (!s || !a || !p || !w || !h || w > 356 || h > 400 ||
        n != (size_t)w * h * 4 || !a->screen || !a->top || !a->create ||
        !a->pixels || !a->remove || delay > 90000 || !duration || duration > 10000 ||
        now > UINT64_MAX - delay - duration) return -1;
    memset(s, 0, sizeof(*s));
    s->api = *a; s->bgra = p; s->width = w; s->height = h;
    s->last_now = now; s->show_at = now + delay; s->expires_at = now + delay + duration;
    s->phase = CMF_WAITING; atomic_init(&s->stop, 0);
    return 0;
}
void cmf_surface_request_stop(struct cmf_surface *s) {
    atomic_store_explicit(&s->stop, 1, memory_order_release);
}
void cmf_surface_deleted(struct cmf_surface *s, void *object) {
    if (object && s->object == object) {
        s->object = NULL; s->deleted++; s->phase = CMF_REMOVED;
    }
}
static void remove_surface(struct cmf_surface *s) {
    void *object = s->object;
    s->object = NULL; s->phase = CMF_REMOVED;
    if (object) { s->api.remove(s->api.context, object); s->deleted++; }
}
void cmf_surface_tick(struct cmf_surface *s, uint64_t now) {
    if (s->phase == CMF_REMOVED || s->phase == CMF_FAILED) return;
    if (now < s->last_now || now >= s->expires_at ||
        atomic_load_explicit(&s->stop, memory_order_acquire)) {
        remove_surface(s); return;
    }
    s->last_now = now;
    if (s->phase == CMF_VISIBLE) {
        if (s->api.screen(s->api.context) != s->screen) remove_surface(s);
        return;
    }
    if (now < s->show_at) return;
    s->screen = s->api.screen(s->api.context);
    void *parent = s->api.top(s->api.context);
    if (!s->screen || !parent) { s->phase = CMF_FAILED; return; }
    s->object = s->api.create(s->api.context, parent, s);
    if (!s->object) { s->phase = CMF_FAILED; return; }
    s->created++; s->phase = CMF_VISIBLE;
    s->api.pixels(s->api.context, s->object, s->bgra, s->width, s->height);
}
