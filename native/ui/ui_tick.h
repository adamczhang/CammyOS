/* SPDX-License-Identifier: MIT
 * cammy-ui loop pacing (0.9). The menu, the loading screen, a held or injected key, a
 * record check, a frame or volume save still pending, and the first seconds after any
 * input tick at 10 Hz as before. Otherwise (menu closed, nothing loading) the loop
 * ticks once a second: buttons and the touchscreen still wake it at once, and the
 * heartbeat stays well inside the face service's CUI_STALE_MS. Without a touchscreen
 * wake in CammyOS mode, a tap reaches the face page only, so the idle tick stays at
 * 250 ms (touch_wake is also set in stock mode, where taps are not cammy-ui's).
 */
#ifndef CAMMY_UI_TICK_H
#define CAMMY_UI_TICK_H
#include <stdint.h>
#include "cammy_ui_page.h"

#define UI_TICK_FAST_MS 100u
#define UI_TICK_IDLE_MS 1000u
#define UI_TICK_IDLE_NO_TOUCH_MS 250u
#define UI_INPUT_HOLD_MS 2000u
_Static_assert(UI_TICK_IDLE_MS * 2u < CUI_STALE_MS, "an idle tick must leave the heartbeat fresh");

struct ui_tick_in {
    int menu_open, loading, keys_busy, record_busy, publish_pending, volume_pending, touch_wake;
    uint64_t now, fast_until; /* fast_until: input seen, keep the fast tick until then */
};
static inline unsigned ui_tick_ms(const struct ui_tick_in *t) {
    if (t->menu_open || t->loading || t->keys_busy || t->record_busy || t->publish_pending || t->volume_pending ||
        t->now < t->fast_until)
        return UI_TICK_FAST_MS;
    return t->touch_wake ? UI_TICK_IDLE_MS : UI_TICK_IDLE_NO_TOUCH_MS;
}
#endif
