/* SPDX-License-Identifier: MIT. cammy-ui menu state machine and screens (0.7, PLAN.md).
 * Pure logic + drawing: every effect goes through struct ui_backend, so the host fixture
 * drives the same code with fakes. Times are monotonic milliseconds. */
#ifndef CAMMY_UI_APP_H
#define CAMMY_UI_APP_H
#include <stdint.h>
#include "ui_draw.h"
#include "ui_boot.h"
#define UI_SSID_MAX 33
#define UI_PSK_MAX 64
#define UI_NETS 8
#define UI_IDLE_MS 30000u
#define UI_TYPING_IDLE_MS 120000u
/* /etc/cammy is internal NOR flash (JFFS2): a run of volume taps is saved once, this
 * long after the last tap or when Controls closes (memory audit 2026-11-16). */
#define UI_VOLUME_SAVE_MS 1500u
enum ui_screen { S_CLOSED, S_MENU, S_WIFI, S_NETWORKS, S_PASSWORD, S_JOINING, S_AGENT, S_CONTROLS, S_CAMERA, S_CAMERA_CONFIRM, S_LOADING };
enum wifi_mode { WIFI_UNKNOWN, WIFI_HOME, WIFI_HOTSPOT };
struct wifi_status {
    int mode;                 /* enum wifi_mode */
    int connected;            /* wpa_state COMPLETED */
    int signal;               /* 0..3 bars, -1 unknown */
    char ssid[UI_SSID_MAX], ip[16], state[24];
};
struct wifi_net { char ssid[UI_SSID_MAX]; int secured, bars; };
struct agent_info {
    int reachable;            /* portal answered */
    char owner[49], label[81], address[16], cammy_ip[16];
    unsigned since_s;         /* seconds the current owner has held the lease */
};
struct ui_backend {
    void *ctx;
    int (*wifi_status)(void *, struct wifi_status *);
    int (*wifi_scan)(void *);                                   /* start a scan */
    int (*wifi_results)(void *, struct wifi_net *, int max);     /* <0 not ready */
    int (*wifi_join)(void *, const char *ssid, const char *psk); /* psk NULL: open */
    int (*wifi_reconnect)(void *);
    int (*wifi_commit)(void *);  /* joined: keep the new network (persist it) */
    int (*wifi_abort)(void *);   /* join failed: drop it, re-enable the old ones */
    int (*agent)(void *, struct agent_info *);
    int (*set_volume)(void *, int percent);
    void (*camera_mode)(void *);
    int (*set_pack)(void *, int index); /* face pack (cammy_ui_page.h order) */
};
struct ui_app {
    struct ui_backend be;
    enum ui_screen screen;
    int dirty, volume, shift, symbols, scan_wait, nets_n, net_sel, join_result;
    int pack;             /* chosen face pack index */
    int shown_pack;       /* active pack from the face service, or -1 until reported */
    int cammy_available;  /* the face service loaded the optional Cammy assets */
    int capture_muted;    /* physical M switch, from the boot-bound face page */
    int local_preview;    /* display the viewfinder until the user leaves it */
    int volume_saved;     /* last volume written to the volume file */
    int focus, keys_used; /* button focus (-1: the screen's default); ring shown after a key */
    uint64_t last_input, refreshed, join_started, scan_started, volume_at;
    struct wifi_status wifi;
    struct wifi_net nets[UI_NETS];
    struct agent_info agent;
    char psk[UI_PSK_MAX];
    char note[64];
    struct ub_view boot;  /* S_LOADING: the boot gate's view (ui_boot.h) */
    int boot_done;        /* the gate opened (ready) or gave way to the stock camera */
};
void ui_app_init(struct ui_app *, const struct ui_backend *, int volume);
/* A tap on the panel (face mode is CammyOS). Returns 1 if the screen changed. */
int ui_app_tap(struct ui_app *, int x, int y, uint64_t now);
/* Timers, refreshes and auto-close. Returns 1 if the screen changed. */
int ui_app_tick(struct ui_app *, uint64_t now);
void ui_app_render(const struct ui_app *, struct ui_canvas *);
/* Boot loading gate (ui_boot.h): shows or updates S_LOADING. A ready view closes it for
 * good (later calls do nothing). While it shows, Settings and the side play button do
 * nothing, it never times out, and only a failed view has a control: the stock camera for
 * this boot. Returns 1 if the screen changed. */
int ui_app_loading(struct ui_app *, const struct ub_view *, uint64_t now);
/* M105 buttons on event0: zoom in = up, zoom out = down, centre = OK. Up/down move the
 * focus through the screen's controls, OK presses the focused one (closed: opens). */
#define UI_KEY_UP 5
#define UI_KEY_DOWN 6
#define UI_KEY_OK 7
int ui_app_key(struct ui_app *, int code, uint64_t now);
#endif
