/* SPDX-License-Identifier: MIT. Who owns each camera button in CammyOS mode (0.7.4).
 *
 * Adam (2026-11-16): presses under Cammy's face must not put the stock app into modes
 * nobody can see. In CammyOS mode cammy-ui grabs event0 and event1, so presses (physical
 * and portal-injected alike) reach only cammy-ui, and it decides per key:
 *   M beside Record   toggles CCC capture mute (MENU, one action on press)
 *   OK (center)        Cammy's menu (opens on release)
 *   up (zoom in)       dropped while the face shows; passed to stock in the live view
 *   Side circle/play toggles the local live preview (stock PLAYBACK key)
 *   MODE (code 140)   dropped while CammyOS owns the buttons
 *   left, right        passed to stock (pan; holds keep working)
 *   down (zoom out)    passed to stock: a short press toggles face follow, and cammy-ui
 *                      shows the live view while it is on
 *   turn_180           passed to stock
 * The red record button (event3) is never grabbed.
 *
 * Routing goes by key code on either device, as sdvcam's own reader does (RE 0xbb208): the
 * physical DOWN was never mapped to a device, and on 0.7.4 (2026-11-16) a physical DOWN did
 * nothing while the portal's event0 DOWN worked, which fits DOWN arriving on event1. Codes
 * from /etc/sunxi-keyboard.kl: 1 ZOOM_IN, 2 ZOOM_OUT, 3 DISP, 4 MENU, 5 LED (up), 6 DEL (down),
 * 7 ENTER (OK), 102/115 pan, 139 PLAYBACK (side circle/play), 140 MODE.
 *
 * Passing a key on: ungrab, then write the press as value 2. The kernel drops an injected
 * value 1 while the key is physically down, but sdvcam reads levels (value != 0), so 2 is
 * a press to it (RE 2026-11-16, 0xbb364). The release then reaches stock by itself. A press
 * and release that both arrived while grabbed are replayed as press, a pause of at least
 * UIB_TAP_MS (stock debounces 15 ms), then release.
 */
#ifndef UI_BUTTONS_H
#define UI_BUTTONS_H
#include <stdint.h>

#define UIB_KEY_LEFT 115
#define UIB_KEY_RIGHT 102
#define UIB_KEY_UP 5
#define UIB_KEY_DOWN 6
#define UIB_KEY_OK 7
#define UIB_KEY_MUTE 4
#define UIB_KEY_TURN 3
#define UIB_KEY_PLAYBACK 139
#define UIB_KEY_MODE 140
#define UIB_KEY_ZOOM_IN 1
#define UIB_KEY_ZOOM_OUT 2
#define UIB_TAP_MS 40u
#define UIB_SHORT_MS 450u /* stock DOWN toggles follow on a release before ~500 ms */

enum uib_route {
    UIB_IGNORE,   /* nothing for cammy-ui to do (stock saw it, or it is dropped) */
    UIB_MENU,     /* a local Settings or preview action */
    UIB_OPEN,     /* OK released with the menu closed: open it */
    UIB_PRESS,    /* pass to stock: ungrab, write value 2 */
    UIB_RELEASE,  /* pass to stock: a release stock has not seen */
    UIB_MUTE
};

static inline int uib_pass(unsigned dev, int code, int live_view) {
    (void)dev;
    if (code == UIB_KEY_LEFT || code == UIB_KEY_RIGHT || code == UIB_KEY_DOWN || code == UIB_KEY_TURN) return 1;
    return live_view && (code == UIB_KEY_UP || code == UIB_KEY_ZOOM_IN || code == UIB_KEY_ZOOM_OUT);
}

/* exclusive: the event reached cammy-ui only (it was grabbed when the event happened).
 * injected: cammy-ui already passed this key's press on. */
static inline enum uib_route uib_route(unsigned dev, int code, uint32_t value, int exclusive, int cammy,
                                       int menu_open, int live_view, int injected) {
    if (!cammy || !exclusive) return UIB_IGNORE;
    (void)dev;
    if (code == UIB_KEY_MUTE) return value == 1 ? UIB_MUTE : UIB_IGNORE;
    if (code == UIB_KEY_PLAYBACK) return value == 1 ? UIB_MENU : UIB_IGNORE;
    if (menu_open) return value == 1 && (code == UIB_KEY_UP || code == UIB_KEY_DOWN || code == UIB_KEY_OK)
        ? UIB_MENU : UIB_IGNORE;
    if (code == UIB_KEY_OK) return value == 0 ? UIB_OPEN : UIB_IGNORE;
    if (!uib_pass(dev, code, live_view) && !injected) return UIB_IGNORE;
    if (value == 1) return UIB_PRESS;
    if (value == 0) return UIB_RELEASE;
    return UIB_IGNORE; /* autorepeat: stock times long presses itself */
}
#endif
