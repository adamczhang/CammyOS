/* SPDX-License-Identifier: MIT. Cammy-owned IPC, never process/VRAM access. */
#ifndef CAMMY_FACE_SERVICE_H
#define CAMMY_FACE_SERVICE_H
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#define CFS_MAGIC 0x31534643u
#define CFS_ABI 1u
#define CFS_PAGE_BYTES 256u
#define CFS_MOODS 6u
#define CFS_SLEEPY 5u /* asleep = sleepy mood, eyes closed */
#define CFS_FRAMES 12u
#define CFS_PIXELS_BYTES (356u*400u*4u)
#define CFS_MAX_LEASE_MS 5000u
/* All shared words are lock-free 32bit atomics. No 64bit shared atomic ABI. */
struct cfs_page { _Atomic uint32_t header[16], command[24], status[24]; };
_Static_assert(sizeof(struct cfs_page)==CFS_PAGE_BYTES,"IPC layout");
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"lock-free32bit IPC required");
enum cfs_op { CFS_SHOW=1, CFS_HIDE, CFS_IDLE, CFS_STOP, CFS_RENEW, CFS_SPEAK };
enum cfs_phase { CFS_WAITING, CFS_VISIBLE, CFS_HIDDEN, CFS_SUSPENDED, CFS_FAILED, CFS_DETACHED };
enum cfs_error { CFS_OK, CFS_BAD_BINDING, CFS_STALE, CFS_EXPIRED, CFS_INVALID, CFS_BAD_OWNER, CFS_NOT_CAMMY };
/* Boot chooser (Adam 2026-11-16): Normal leaves the stock camera alone for this boot;
 * CammyOS keeps the face on top. Legacy = no chooser compiled in (fixtures). */
enum cfs_mode { CFS_MODE_LEGACY, CFS_MODE_CHOOSING, CFS_MODE_NORMAL, CFS_MODE_CAMMY };
#define CFS_CHOOSER_SPLIT_Y 200 /* tap y below this = Normal (top half) */
#define CFS_CHOOSER_FRAME CFS_FRAMES
/* Chooser bank: 3 frames (no focus, Normal focused, CammyOS focused). M105 keys on
 * event0 (docs/STOCK-CONTROL-BASELINE-2026-11-14.md): zoom in = up, zoom out = down. */
#define CFS_CHOOSER_ID(focus) (0x20000000u|(focus))
#define CFS_KEY_UP 5
#define CFS_KEY_DOWN 6
#define CFS_KEY_OK 7
/* Recoveries from unexpected display failures (stock LVGL deleting the face object, or a
 * failed create) per engine. The face's own removals (hide, live view, menu, camera mode)
 * are not failures and never count. The next failure after this many stays suspended. */
#define CFS_MAX_RECREATE 32u
#define CFS_SLEEP_FRAMES 8u
#define CFS_SLEEP_PERIOD_MS 3500u
#define CFS_SLEEP_ID(i) (0x10000000u|(i))
/* Two-character packs (Fin and Fur): idle, the one in front changes this often. */
#define CFS_TURN_MS 8000u
#define CFS_TURN_ID CFS_FRAMES /* frame ids >= this: the second (turned) bank */
/* Awake eyes (2026-11-17): idle holds neutral (no mood carousel) and every awake pose
 * blinks at varied, seeded intervals, now and then twice. Waking from sleep starts
 * with the eyes still closed for CFS_WAKE_MS. */
#define CFS_BLINK_GAP_MS 2400u   /* plus 0..4095 ms of jitter */
#define CFS_BLINK_MS 130u        /* plus 0..63 ms */
#define CFS_BLINK_AGAIN_MS 160u  /* open gap inside a double blink, plus 0..63 ms */
#define CFS_WAKE_MS 260u
/* CCC pack override, command word 17. GRANT selects Cammy, CHILD selects a child
 * pack, and HOLD keeps the current override. NONE, lease loss and a physical
 * pick restore the saved pack. A pack command only changes the displayed face. */
enum { CFS_PACK_NONE=0, CFS_PACK_HOLD=1, CFS_PACK_GRANT=2, CFS_PACK_CHILD=3 };
/* Why the last grant ended (status readback). */
enum { CFS_END_NONE=0, CFS_END_CCC, CFS_END_LEASE, CFS_END_PHYSICAL, CFS_END_CAMERA, CFS_END_UNAVAILABLE };
/* header[12] (face library -> portal): pack word. Bits 0-7: 1 + the pack on screen
 * (cammy_ui_page.h order, 0 unknown); bit 8: grant held; bits 16-23: CFS_END_*.
 * header[13]: bit i set when pack i is built in and valid. */
#define CFS_HEADER_PACK 12u
#define CFS_HEADER_PACKS 13u
/* Boot loading gate (0.7.12 candidate). Picking CammyOS on the chooser shows an opaque
 * Loading screen, never the face or the live view, until cammy-ui reports this boot's
 * services ready (cammy_ui_page.h boot word). cammy-ui draws the detailed status through
 * its menu buffer; the built-in loading frame covers any time without a fresh cammy-ui
 * page. header[14] (face library -> cammy-ui/portal) is the gate's state. Commands are
 * still applied while it holds (CCC is never refused or waited on); only the picture waits.
 * With no fresh cammy-ui page for CFS_LOADING_ORPHAN_MS (it never started, or its restarts
 * ran out), the gate gives the screen to the stock camera for this boot. The gate is a
 * one-way latch per boot: once ready it never returns. */
#define CFS_HEADER_BOOT 14u
/* cammy-ui's physical capture switch. The low bit is mute; each press advances the
 * sequence so consumers can detect a mute/unmute between observations. Zero is legacy. */
#define CFS_HEADER_CAPTURE 15u
enum { CFS_BOOT_NONE=0, CFS_BOOT_LOADING=1, CFS_BOOT_READY=2, CFS_BOOT_STOCK=3 };
#define CFS_LOADING_FRAME 0x30000000u
#define CFS_LOADING_ORPHAN_MS 180000u
struct cfs_command {
    uint64_t request, owner, generation, deadline, instance;
    uint32_t boot[4], op, expression, pack;
};
struct cfs_status {
    uint64_t request, owner, generation, deadline, gui_ms, applied_request;
    uint32_t phase, expression, idle, suspended, draws, created, removed, error;
    uint32_t mode, taps, last_tap; /* last_tap = (x&0xffff)<<16 | (y&0xffff) */
    uint32_t tracking; /* stock face follow: CFS_TRACK_UNKNOWN/OFF/ON (high half of the mode word) */
};
struct cfs_engine;
struct cfs_api {
    void *context;
    void *(*screen)(void *);
    void *(*top)(void *);
    void *(*create)(void *, void *);
    void (*pixels)(void *,void *,const void *);
    void (*remove)(void *,void *);
    void (*clickable)(void *,void *); /* optional: make obj take taps */
    /* optional: cammy-ui menu. Returns 1 with the published buffer and its sequence when
     * the menu should show; *camera=1 hands the screen to the stock camera. */
    int (*ui)(void *,uint64_t,const unsigned char **,uint32_t *,uint32_t *);
    /* optional: stock face follow, 0 off, 1 on, -1 unknown (0.7.4) */
    int (*tracking)(void *);
    /* optional: 1 while cammy-ui asks for the stock live view in CammyOS mode (0.7.4) */
    int (*live_view)(void *,uint64_t);
    /* optional: the loading gate's cammy-ui readback. -1: no fresh cammy-ui page;
     * 0: cammy-ui still loading; 1: cammy-ui reports this boot ready. */
    int (*boot)(void *,uint64_t);
};
enum { CFS_TRACK_UNKNOWN=0, CFS_TRACK_OFF=1, CFS_TRACK_ON=2 };
#define CFS_TRANSITION_MS 350u
/* Boot chooser timing (0.9.6). Stock wm+0x78 is the current window, +0x7c the previous
 * one. On the 0.9.5 USB boot fbaa812c, USBModeWindow (6) loaded 0.42 s after the chooser
 * could first draw, and the UDC still read "not attached" at that moment, so it is
 * no gate. The first draw waits CFS_FIRST_HOLD_MS, or until the prompt is up and the
 * 350 ms guard has passed. The chooser then sits over the prompt, so one OK answers
 * both, as in 0.9.4. The prompt waits for a key and does not close on its own. */
#define CFS_STOCK_USB_WINDOW 6u
#define CFS_FIRST_HOLD_MS 2000u
/* cfs_observe busy bits (0.9.9). WM: ChangeWindow is in flight (wm+0xe1). MODE: the stock
 * presenter's work mode is not 0, or could not be read. On the 0.9.8 USB boot 65164e08 the
 * USB prompt came up while MODE was still set (13-116 s), the chooser stayed hidden, the
 * prompt was answered as charging at 28 s and the stock restarted its media only at 116 s:
 * CammyOS took two minutes. While choosing, MODE no longer hides the chooser over the
 * prompt window, which is stable; WM and the 350 ms guard still do. */
enum { CFS_BUSY_WM=1, CFS_BUSY_MODE=2 };
enum { CFS_USB_UNKNOWN=0, CFS_USB_DETACHED=1, CFS_USB_ATTACHED=2, CFS_USB_CONFIGURED=3 };
/* Chooser timeline marks, kept while choosing so a boot trial can qualify the gates. */
enum { CFS_MARK_STOCK=1, CFS_MARK_ELIGIBLE, CFS_MARK_HOLD, CFS_MARK_PROMPT, CFS_MARK_DRAW, CFS_MARK_USB, CFS_MARK_PICK };
#define CFS_MARKS 24u
struct cfs_mark { uint64_t ms; uint32_t what, window, overlay, value; };
#define CFS_MENU_FRAME 0x40000000u
struct cfs_engine {
    struct cfs_page *page;
    struct cfs_api api;
    const unsigned char *frames[CFS_FRAMES];
    struct cfs_status state;
    uint64_t instance, start_at, idle_at, last_now, seen, pending_request, visible_since;
    uint32_t boot[4], current_frame, want_hidden, suspended, detached;
    const unsigned char *talk; /* two mouth frames per front character */
    uint32_t speaking;
    uint32_t asleep; /* no session owner: hold the eyes-closed sleepy frame */
    const unsigned char *chooser; uint32_t tap_pending, chooser_focus; int32_t tap_x, tap_y;
    const unsigned char *sleep; /* CFS_SLEEP_FRAMES contiguous frames, or NULL */
    const unsigned char *turned[CFS_FRAMES]; /* the same moods, other one in front */
    uint32_t turns, front; /* turns: pack has a turned bank; front: it is showing */
    void *object,*screen,*parent;
    uint64_t resume_at;
    uint32_t window, overlay, observed, transition, stock_busy, busy_bits;
    uint32_t first_window, first_overlay, usb, usb_hold, prompt_seen, marks;
    uint64_t eligible_at, hold_until;
    struct cfs_mark mark[CFS_MARKS];
    /* An owner's explicit sleepy show: the sleep loop, lease and owner kept. */
    uint32_t dozing, front_base, blink_seed, blink_double;
    uint64_t sleep_at, blink_at, blink_end; /* sleep loop phase; current/next blink */
    uint32_t faults; /* unexpected display failures, saturating at CFS_MAX_RECREATE+1 */
    /* 0.7.11: CCC pack grant, and a packed bank drawn through decode (frame id -> pixels
     * that stay valid until the next decode call, or NULL to keep the current picture). */
    uint32_t adult, adult_end, child_override, packed;
    const unsigned char *(*decode)(void *,uint32_t);
    void *decode_ctx;
    /* Loading gate: the built-in frame (made on first use; NULL disables the gate), its
     * state (CFS_BOOT_*) and the last time a fresh cammy-ui page was seen. */
    const unsigned char *(*loading_px)(void *);
    void *loading_ctx;
    uint32_t loading;
    uint64_t loading_seen;
};
void cfs_page_init(struct cfs_page *,uint64_t,const uint32_t[4],uint32_t);
int cfs_publish(struct cfs_page *,const struct cfs_command *);
int cfs_read_command(const struct cfs_page *,struct cfs_command *);
int cfs_read_status(const struct cfs_page *,struct cfs_status *);
int cfs_init(struct cfs_engine *,struct cfs_page *,const struct cfs_api *,
             const unsigned char *const[CFS_FRAMES],uint64_t,uint64_t);
void cfs_observe(struct cfs_engine *,uint64_t,uint32_t,uint32_t,int);
void cfs_tick(struct cfs_engine *,uint64_t);
/* The native side's sampled USB device state (CFS_USB_*), recorded while choosing. */
void cfs_usb(struct cfs_engine *,uint64_t,uint32_t);
void cfs_deleted(struct cfs_engine *,void *);
void cfs_detach(struct cfs_engine *); /* GUI thread only */
void cfs_enable_chooser(struct cfs_engine *,const unsigned char *); /* after cfs_init */
void cfs_tap(struct cfs_engine *,int32_t,int32_t); /* GUI thread: record only */
void cfs_key(struct cfs_engine *,uint32_t code); /* GUI thread: a key press (chooser) */
void cfs_enable_sleep(struct cfs_engine *,const unsigned char *); /* after cfs_init */
/* After cfs_init and cfs_enable_chooser: picking CammyOS starts the loading gate. */
void cfs_enable_loading(struct cfs_engine *,const unsigned char *(*)(void *),void *);
/* GUI thread: switch face pack. turned may be NULL (one look); sleep may be NULL;
 * chooser NULL keeps the current one. Returns -1 (nothing changed) on a missing frame. */
int cfs_set_pack(struct cfs_engine *,const unsigned char *const frames[CFS_FRAMES],
                 const unsigned char *const turned[CFS_FRAMES],const unsigned char *sleep,
                 const unsigned char *chooser);
/* GUI thread: draw a packed one-look pack through decode, with the same frame ids (moods,
 * talk 0x08000000+n, sleep CFS_SLEEP_ID). The chooser stays the previous pack's. */
int cfs_set_packed(struct cfs_engine *,const unsigned char *(*decode)(void *,uint32_t),void *);
/* GUI thread: end the CCC pack grant (physical pick, missing pack). */
void cfs_adult_end(struct cfs_engine *,uint32_t reason);
#endif
