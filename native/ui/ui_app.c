/* SPDX-License-Identifier: MIT. See ui_app.h. Layout is 356x400 portrait; every screen's
 * hit boxes come from the same rect helpers its drawing uses. */
#include "ui_app.h"
#include "ui_buttons.h"
#include <stdio.h>
#include <string.h>
struct rect { int x, y, w, h; };
static int in(struct rect r, int x, int y) { return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h; }
#define HEADER ((struct rect){0, 0, 190, 54})
#define CLOSE ((struct rect){286, 0, 70, 54})
static struct rect tile(int i) { return (struct rect){10 + (i & 1) * 173, 60 + (i >> 1) * 156, 163, 144}; }
#define WIFI_FIND ((struct rect){10, 202, 336, 58})
#define WIFI_RECONNECT ((struct rect){10, 270, 336, 58})
#define NET_ROW0 60
#define NET_ROW_H 48
#define NET_ROWS 6
#define NET_RESCAN ((struct rect){10, 350, 336, 44})
#define PSK_FIELD ((struct rect){10, 60, 336, 52})
#define KEY_Y0 128
#define KEY_H 50
#define KEY_GAP 6
#define VOL_MINUS ((struct rect){20, 112, 56, 56})
#define VOL_PLUS ((struct rect){280, 112, 56, 56})
#define PACK_ROW(i) ((struct rect){22, 230 + 58 * (i), 312, (i) == (int)CUI_PACK_CAMMY ? 40 : 50})
#define CAM_PREVIEW ((struct rect){10, 174, 336, 60})
#define CAM_STOCK ((struct rect){10, 270, 336, 60})
#define CAM_CANCEL ((struct rect){10, 316, 163, 62})
#define CAM_SWITCH ((struct rect){183, 316, 163, 62})
#define JOIN_BACK ((struct rect){10, 316, 336, 62})
#define LOAD_STOCK ((struct rect){10, 326, 336, 62})
static const char *rows_alpha[3] = {"qwertyuiop", "asdfghjkl", "zxcvbnm"};
static const char *rows_sym[3] = {"1234567890", "-/:;()&@\"", ".,?!'_#"};
enum { K_NONE, K_CHAR, K_SHIFT, K_BACK, K_SYM, K_SPACE, K_JOIN };
struct key { struct rect r; int kind; char ch; };
/* Keyboard: rows of 10/9/7(+shift,backspace)/[123 space join]. Returns the key count. */
static int keys(const struct ui_app *a, struct key *k) {
    const char **rows = a->symbols ? rows_sym : rows_alpha;
    int n = 0, kw = 31, gap = 4;
    for (int r = 0; r < 3; r++) {
        int len = (int)strlen(rows[r]), y = KEY_Y0 + r * (KEY_H + KEY_GAP);
        int x = r == 2 ? 5 + 46 + gap : (356 - (len * kw + (len - 1) * gap)) / 2;
        for (int i = 0; i < len; i++, n++) {
            char c = rows[r][i];
            if (!a->symbols && a->shift && c >= 'a' && c <= 'z') c = (char)(c - 32);
            k[n] = (struct key){{x + i * (kw + gap), y, kw, KEY_H}, K_CHAR, c};
        }
        if (r == 2) {
            k[n++] = (struct key){{5, y, 46, KEY_H}, a->symbols ? K_NONE : K_SHIFT, 0};
            k[n++] = (struct key){{356 - 5 - 46, y, 46, KEY_H}, K_BACK, 0};
        }
    }
    int y = KEY_Y0 + 3 * (KEY_H + KEY_GAP);
    k[n++] = (struct key){{5, y, 70, KEY_H}, K_SYM, 0};
    k[n++] = (struct key){{79, y, 172, KEY_H}, K_SPACE, ' '};
    k[n++] = (struct key){{255, y, 96, KEY_H}, K_JOIN, 0};
    return n;
}
static void go(struct ui_app *a, enum ui_screen s) {
    if (a->screen == S_PASSWORD && s != S_PASSWORD) memset(a->psk, 0, sizeof a->psk);
    a->screen = s;
    a->dirty = 1;
    a->note[0] = 0;
    a->focus = -1;
}
static void close_menu(struct ui_app *a) {
    if (a->screen == S_JOINING && !a->join_result) (void)a->be.wifi_abort(a->be.ctx);
    a->keys_used = 0;
    go(a, S_CLOSED);
}
static void toggle_preview(struct ui_app *a) {
    a->local_preview = !a->local_preview;
    close_menu(a);
}
static void refresh(struct ui_app *a, uint64_t now, int force) {
    if (!force && now - a->refreshed < 2000u) return;
    a->refreshed = now;
    struct wifi_status w; struct agent_info g;
    memset(&w, 0, sizeof w); memset(&g, 0, sizeof g);
    if (a->screen == S_MENU || a->screen == S_WIFI || a->screen == S_JOINING) {
        if (a->be.wifi_status(a->be.ctx, &w)) { memset(&w, 0, sizeof w); w.signal = -1; }
        if (memcmp(&w, &a->wifi, sizeof w)) { a->wifi = w; a->dirty = 1; }
    }
    if (a->screen == S_MENU || a->screen == S_AGENT) {
        if (a->be.agent(a->be.ctx, &g)) memset(&g, 0, sizeof g);
        if (memcmp(&g, &a->agent, sizeof g)) { a->agent = g; a->dirty = 1; }
    }
}
static void open_menu(struct ui_app *a, uint64_t now) {
    a->last_input = now;
    go(a, S_MENU);
    refresh(a, now, 1);
}
void ui_app_init(struct ui_app *a, const struct ui_backend *be, int volume) {
    memset(a, 0, sizeof *a);
    a->be = *be; a->volume = volume < 0 ? 0 : volume > 100 ? 100 : volume;
    a->volume_saved = a->volume;
    a->shown_pack = -1;
    a->wifi.signal = -1;
}
static void start_scan(struct ui_app *a, uint64_t now) {
    a->nets_n = 0; a->scan_started = now; a->dirty = 1;
    a->scan_wait = a->be.wifi_scan(a->be.ctx) == 0;
    if (!a->scan_wait) snprintf(a->note, sizeof a->note, "%s",
        a->wifi.mode == WIFI_HOTSPOT ? "Scanning needs home Wi-Fi mode." : "Wi-Fi scan unavailable.");
}
static void start_join(struct ui_app *a, uint64_t now) {
    const struct wifi_net *n = &a->nets[a->net_sel];
    int result = a->be.wifi_join(a->be.ctx, n->ssid, n->secured ? a->psk : NULL);
    go(a, S_JOINING);
    a->join_started = now;
    a->join_result = result ? -1 : 0;
    refresh(a, now, 1);
}
static int key_tap(struct ui_app *a, int x, int y, uint64_t now) {
    struct key k[40];
    int n = keys(a, k);
    size_t len = strlen(a->psk);
    for (int i = 0; i < n; i++) {
        if (!in(k[i].r, x, y)) continue;
        switch (k[i].kind) {
        case K_CHAR: case K_SPACE:
            if (len + 1 < sizeof a->psk) { a->psk[len] = k[i].ch; a->psk[len + 1] = 0; }
            break;
        case K_SHIFT: a->shift = !a->shift; break;
        case K_BACK: if (len) a->psk[len - 1] = 0; break;
        case K_SYM: a->symbols = !a->symbols; break;
        case K_JOIN: if (len >= 8) start_join(a, now); break;
        default: return 0;
        }
        a->dirty = 1;
        return 1;
    }
    return 0;
}
static int tap(struct ui_app *a, int x, int y, uint64_t now) {
    a->last_input = now;
    enum ui_screen before = a->screen;
    a->dirty = 0;
    if (a->screen == S_LOADING) {
        if (a->boot.stage == UB_FAILED && in(LOAD_STOCK, x, y)) {
            a->be.camera_mode(a->be.ctx);
            a->boot_done = 1;
            close_menu(a);
            return 1;
        }
        return 0;
    }
    if (a->screen == S_CLOSED) { open_menu(a, now); return 1; }
    if (a->screen == S_MENU) {
        if (in(CLOSE, x, y)) { close_menu(a); return 1; }
        static const enum ui_screen to[4] = {S_WIFI, S_AGENT, S_CONTROLS, S_CAMERA};
        for (int i = 0; i < 4; i++) if (in(tile(i), x, y)) { go(a, to[i]); refresh(a, now, 1); }
        return a->dirty;
    }
    if (a->screen != S_JOINING && in(HEADER, x, y)) {
        go(a, a->screen == S_NETWORKS ? S_WIFI : a->screen == S_PASSWORD ? S_NETWORKS :
              a->screen == S_CAMERA_CONFIRM ? S_CAMERA : S_MENU);
        refresh(a, now, 1);
        return 1;
    }
    switch (a->screen) {
    case S_WIFI:
        if (a->wifi.mode == WIFI_HOTSPOT) break;
        if (in(WIFI_FIND, x, y)) { go(a, S_NETWORKS); start_scan(a, now); }
        else if (in(WIFI_RECONNECT, x, y)) {
            snprintf(a->note, sizeof a->note, "%s", a->be.wifi_reconnect(a->be.ctx) ? "Reconnect failed." : "Reconnecting...");
            a->dirty = 1;
        }
        break;
    case S_NETWORKS:
        if (in(NET_RESCAN, x, y)) { start_scan(a, now); break; }
        if (y >= NET_ROW0 && y < NET_ROW0 + NET_ROWS * NET_ROW_H) {
            int row = (y - NET_ROW0) / NET_ROW_H;
            if (row < a->nets_n) {
                a->net_sel = row; a->psk[0] = 0; a->shift = a->symbols = 0;
                if (a->nets[row].secured) go(a, S_PASSWORD); else start_join(a, now);
            }
        }
        break;
    case S_PASSWORD: (void)key_tap(a, x, y, now); break;
    case S_JOINING:
        if (in(JOIN_BACK, x, y)) {
            if (!a->join_result) close_menu(a);
            else { go(a, a->join_result > 0 ? S_WIFI : S_PASSWORD); refresh(a, now, 1); }
        }
        break;
    case S_CONTROLS: {
        int v = a->volume;
        if (in(VOL_MINUS, x, y)) v -= 10;
        if (in(VOL_PLUS, x, y)) v += 10;
        v = v < 0 ? 0 : v > 100 ? 100 : v;
        if (v != a->volume) { a->volume = v; a->volume_at = now; a->dirty = 1; } /* saved by ui_app_tick */
        for (int i = 0; i < (int)CUI_PACK_COUNT; i++) {
            if (i == (int)CUI_PACK_CAMMY && !a->cammy_available) continue;
            if (in(PACK_ROW(i), x, y) && (i != a->pack || (a->shown_pack >= 0 && a->shown_pack != i)) &&
                !a->be.set_pack(a->be.ctx, i)) { a->pack = i; a->dirty = 1; }
        }
        break;
    }
    case S_CAMERA:
        if (in(CAM_PREVIEW, x, y)) toggle_preview(a);
        else if (in(CAM_STOCK, x, y)) go(a, S_CAMERA_CONFIRM);
        break;
    case S_CAMERA_CONFIRM:
        if (in(CAM_CANCEL, x, y)) go(a, S_CAMERA);
        else if (in(CAM_SWITCH, x, y)) {
            a->be.camera_mode(a->be.ctx);
            close_menu(a);
        }
        break;
    default: break;
    }
    return a->dirty || a->screen != before;
}
int ui_app_tap(struct ui_app *a, int x, int y, uint64_t now) {
    int was = a->keys_used;
    a->keys_used = 0; /* touching hides the button focus ring */
    return tap(a, x, y, now) || was;
}
/* The controls a button user can reach on each screen, top to bottom. Index 0 is the
 * back/close control where the screen has one. */
static int focusables(const struct ui_app *a, struct rect *r) {
    int n = 0;
    switch (a->screen) {
    case S_MENU: for (int i = 0; i < 4; i++) r[n++] = tile(i); r[n++] = CLOSE; break;
    case S_WIFI:
        r[n++] = HEADER;
        if (a->wifi.mode != WIFI_HOTSPOT) { r[n++] = WIFI_FIND; r[n++] = WIFI_RECONNECT; }
        break;
    case S_NETWORKS:
        r[n++] = HEADER;
        for (int i = 0; i < a->nets_n && i < NET_ROWS; i++) r[n++] = (struct rect){10, NET_ROW0 + i * NET_ROW_H + 2, 336, NET_ROW_H - 4};
        if (!a->scan_wait) r[n++] = NET_RESCAN;
        break;
    case S_PASSWORD: {
        struct key k[40];
        int m = keys(a, k);
        r[n++] = HEADER;
        for (int i = 0; i < m; i++) if (k[i].kind != K_NONE) r[n++] = k[i].r;
        break;
    }
    case S_JOINING: r[n++] = JOIN_BACK; break;
    case S_AGENT: r[n++] = HEADER; break;
    case S_CONTROLS:
        r[n++] = HEADER; r[n++] = VOL_MINUS; r[n++] = VOL_PLUS; r[n++] = PACK_ROW(0); r[n++] = PACK_ROW(1);
        if (a->cammy_available) r[n++] = PACK_ROW((int)CUI_PACK_CAMMY);
        break;
    case S_CAMERA: r[n++] = HEADER; r[n++] = CAM_PREVIEW; r[n++] = CAM_STOCK; break;
    case S_CAMERA_CONFIRM: r[n++] = HEADER; r[n++] = CAM_CANCEL; r[n++] = CAM_SWITCH; break;
    case S_LOADING: if (a->boot.stage == UB_FAILED) r[n++] = LOAD_STOCK; break;
    default: break;
    }
    return n;
}
static int focused(const struct ui_app *a, int n) {
    if (n <= 0) return -1;
    if (a->focus >= 0 && a->focus < n) return a->focus;
    if (a->screen == S_MENU || a->screen == S_JOINING || a->screen == S_LOADING) return 0;
    return n > 1 ? 1 : 0;
}
int ui_app_key(struct ui_app *a, int code, uint64_t now) {
    struct rect r[48];
    a->dirty = 0;
    if (a->screen == S_LOADING && code != UI_KEY_OK) return 0; /* nothing else while loading */
    if (code == UIB_KEY_PLAYBACK) {
        toggle_preview(a);
        return 1;
    }
    if (code != UI_KEY_UP && code != UI_KEY_DOWN && code != UI_KEY_OK) return 0;
    if (a->screen == S_CLOSED) {
        if (code != UI_KEY_OK) return 0;
        open_menu(a, now);
        a->keys_used = 1;
        return 1;
    }
    a->last_input = now;
    int n = focusables(a, r), f = focused(a, n);
    if (f < 0) return 0;
    if (code == UI_KEY_OK) {
        (void)tap(a, r[f].x + r[f].w / 2, r[f].y + r[f].h / 2, now);
        a->keys_used = a->screen != S_CLOSED;
        return 1;
    }
    a->focus = (f + (code == UI_KEY_DOWN ? 1 : n - 1)) % n;
    a->keys_used = 1;
    return 1;
}
int ui_app_tick(struct ui_app *a, uint64_t now) {
    a->dirty = 0;
    if (a->volume != a->volume_saved && (a->screen != S_CONTROLS || now - a->volume_at >= UI_VOLUME_SAVE_MS) &&
        !a->be.set_volume(a->be.ctx, a->volume))
        a->volume_saved = a->volume;
    if (a->screen == S_CLOSED || a->screen == S_LOADING) return 0; /* loading never times out */
    uint64_t idle = a->screen == S_PASSWORD ? UI_TYPING_IDLE_MS : UI_IDLE_MS;
    if ((a->screen != S_JOINING || a->join_result) && now - a->last_input > idle) {
        close_menu(a);
        return 1;
    }
    if (a->screen == S_NETWORKS && a->scan_wait && now - a->scan_started >= 1500u) {
        int n = a->be.wifi_results(a->be.ctx, a->nets, UI_NETS);
        if (n >= 0) { a->nets_n = n; a->scan_wait = 0; a->dirty = 1; }
        else if (now - a->scan_started > 10000u) { a->scan_wait = 0; snprintf(a->note, sizeof a->note, "No scan results."); a->dirty = 1; }
    }
    refresh(a, now, 0);
    if (a->screen == S_JOINING && !a->join_result) {
        if (a->wifi.connected && !strcmp(a->wifi.ssid, a->nets[a->net_sel].ssid) && a->wifi.ip[0]) {
            a->join_result = 1; a->dirty = 1;
            if (a->be.wifi_commit(a->be.ctx)) snprintf(a->note, sizeof a->note, "Joined, but could not save it.");
        } else if (now - a->join_started > 20000u) { /* runtime falls back to hotspot after ~40 s */
            a->join_result = -1; a->dirty = 1; (void)a->be.wifi_abort(a->be.ctx);
        }
        a->last_input = now;
    }
    return a->dirty;
}

/* ---- drawing ---- */
static void header(struct ui_canvas *c, const char *title, int back) {
    if (back) {
        ui_icon(c, 12, 13, I_BACK, C_YELLOW);
        (void)ui_text(c, 44, 9, UI_TITLE, title, C_TEXT, 300);
    } else {
        (void)ui_text(c, 18, 9, UI_TITLE, title, C_TEXT, 250);
        ui_icon(c, 316, 13, I_CLOSE, C_DIM);
    }
}
static void button(struct ui_canvas *c, struct rect r, const char *label, int primary, int enabled) {
    ui_rrect(c, r.x, r.y, r.w, r.h, 14, primary && enabled ? C_YELLOW : C_CARD);
    uint32_t fg = !enabled ? C_LINE : primary ? C_BG : C_TEXT;
    ui_text_center(c, r.x + r.w / 2, r.y + (r.h - ui_fonts[UI_BODY].height) / 2, UI_BODY, label, fg, r.w - 20);
}
static enum ui_icon bars(int b) { return b >= 3 ? I_BARS3 : b == 2 ? I_BARS2 : I_BARS1; }
static void wifi_line(const struct ui_app *a, char *out, size_t n, uint32_t *color) {
    const struct wifi_status *w = &a->wifi;
    *color = C_DIM;
    if (w->mode == WIFI_HOTSPOT) snprintf(out, n, "Hotspot mode");
    else if (w->connected && w->ip[0]) { snprintf(out, n, "%s", w->ssid); *color = C_GREEN; }
    else if (w->connected) snprintf(out, n, "Getting address...");
    else snprintf(out, n, "Not connected");
}
static void draw_menu(const struct ui_app *a, struct ui_canvas *c) {
    header(c, "Settings", 0);
    static const char *names[4] = {"Wi-Fi", "Connection", "Sound & Face", "Camera"};
    static const enum ui_icon icons[4] = {I_WIFI, I_AGENT, I_SLIDERS, I_CAMERA};
    for (int i = 0; i < 4; i++) {
        struct rect r = tile(i);
        char sub[64]; uint32_t color = C_DIM;
        if (i == 0) wifi_line(a, sub, sizeof sub, &color);
        else if (i == 1) {
            if (a->agent.owner[0]) {
                snprintf(sub, sizeof sub, "%s", !strcmp(a->agent.owner, "c3") ? "Control Center" : "Connected");
                color = C_GREEN;
            } else snprintf(sub, sizeof sub, "%s", a->agent.reachable ? "No controller" : "Service offline");
        } else if (i == 2) snprintf(sub, sizeof sub, "Volume %d%%", a->volume);
        else snprintf(sub, sizeof sub, "%s", a->local_preview ? "Preview on" : "Preview and mode");
        ui_rrect(c, r.x, r.y, r.w, r.h, 18, C_CARD);
        ui_icon(c, r.x + (r.w - 64) / 2, r.y + 13, icons[i], C_YELLOW);
        ui_text_center(c, r.x + r.w / 2, r.y + 85, UI_BODY, names[i], C_TEXT, r.w - 16);
        ui_text_center(c, r.x + r.w / 2, r.y + 113, UI_SMALL, sub, color, r.w - 16);
    }
    ui_text_center(c, 178, 374, UI_SMALL, a->capture_muted ? "M: CCC capture muted" : "M: Pause CCC capture",
                   a->capture_muted ? C_YELLOW : C_DIM, 336);
}
static void draw_wifi(const struct ui_app *a, struct ui_canvas *c) {
    const struct wifi_status *w = &a->wifi;
    header(c, "Wi-Fi", 1);
    ui_rrect(c, 10, 60, 336, 130, 18, C_CARD);
    (void)ui_text(c, 26, 74, UI_SMALL, w->mode == WIFI_HOTSPOT ? "Cammy's own hotspot" : "Network", C_DIM, 0);
    (void)ui_text(c, 26, 98, UI_BODY, w->mode == WIFI_HOTSPOT ? "CammyOS hotspot" : w->ssid[0] ? w->ssid : "None", C_TEXT, 260);
    if (w->connected && w->signal >= 0) ui_icon(c, 306, 98, bars(w->signal), C_YELLOW);
    char line[64]; uint32_t color = C_DIM;
    if (w->mode == WIFI_HOTSPOT) snprintf(line, sizeof line, "No saved network connected");
    else if (w->connected && w->ip[0]) { snprintf(line, sizeof line, "Connected  %s", w->ip); color = C_GREEN; }
    else snprintf(line, sizeof line, "%s", w->state[0] ? w->state : "Not connected");
    (void)ui_text(c, 26, 140, UI_SMALL, line, color, 304);
    if (w->mode == WIFI_HOTSPOT) {
        (void)ui_text(c, 26, 222, UI_SMALL, "Saved networks are tried at startup.", C_DIM, 304);
    } else {
        button(c, WIFI_FIND, "Find networks", 1, 1);
        button(c, WIFI_RECONNECT, "Reconnect", 0, 1);
        if (a->note[0]) ui_text_center(c, 178, 344, UI_SMALL, a->note, C_DIM, 330);
    }
}
static void draw_networks(const struct ui_app *a, struct ui_canvas *c) {
    header(c, "Networks", 1);
    if (a->scan_wait) ui_text_center(c, 178, 170, UI_BODY, "Searching...", C_DIM, 0);
    else if (!a->nets_n) ui_text_center(c, 178, 170, UI_BODY, a->note[0] ? a->note : "No networks found", C_DIM, 330);
    for (int i = 0; i < a->nets_n && i < NET_ROWS; i++) {
        int y = NET_ROW0 + i * NET_ROW_H;
        ui_rrect(c, 10, y + 2, 336, NET_ROW_H - 4, 12, C_CARD);
        (void)ui_text(c, 24, y + 11, UI_BODY, a->nets[i].ssid[0] ? a->nets[i].ssid : "(hidden)", C_TEXT, 236);
        if (a->nets[i].secured) ui_icon(c, 282, y + 15, I_LOCK, C_DIM);
        ui_icon(c, 308, y + 11, bars(a->nets[i].bars), C_YELLOW);
    }
    button(c, NET_RESCAN, "Scan again", 0, !a->scan_wait);
}
static void draw_password(const struct ui_app *a, struct ui_canvas *c) {
    header(c, a->nets[a->net_sel].ssid, 1);
    ui_rrect(c, PSK_FIELD.x, PSK_FIELD.y, PSK_FIELD.w, PSK_FIELD.h, 12, C_CARD_HI);
    int fy = PSK_FIELD.y + (PSK_FIELD.h - ui_fonts[UI_BODY].height) / 2;
    if (a->psk[0]) {
        char masked[UI_PSK_MAX];
        size_t len = strlen(a->psk);
        memset(masked, '*', len);
        masked[len] = 0;
        const char *shown = masked;
        while (ui_text_width(UI_BODY, shown) > 300) shown++;
        int w = ui_text(c, 24, fy, UI_BODY, shown, C_TEXT, 0);
        ui_fill(c, 26 + w, fy + 3, 2, ui_fonts[UI_BODY].height - 6, C_YELLOW);
    } else {
        (void)ui_text(c, 24, fy, UI_BODY, "Password", C_DIM, 0);
        ui_fill(c, 22, fy + 3, 2, ui_fonts[UI_BODY].height - 6, C_YELLOW);
    }
    struct key k[40];
    int n = keys(a, k);
    for (int i = 0; i < n; i++) {
        struct rect r = k[i].r;
        int join = k[i].kind == K_JOIN, ready = strlen(a->psk) >= 8;
        uint32_t bg = join && ready ? C_YELLOW : (k[i].kind == K_SHIFT && a->shift) ? C_YELLOW_DK : C_KEY;
        if (k[i].kind == K_NONE) continue;
        ui_rrect(c, r.x, r.y, r.w, r.h, 8, bg);
        int ty = r.y + (r.h - ui_fonts[UI_KEY].height) / 2;
        char label[8] = {0};
        switch (k[i].kind) {
        case K_CHAR: label[0] = k[i].ch; ui_text_center(c, r.x + r.w / 2, ty, UI_KEY, label, C_TEXT, 0); break;
        case K_SHIFT: ui_icon(c, r.x + (r.w - 26) / 2, r.y + 12, I_SHIFT, a->shift ? C_YELLOW : C_TEXT); break;
        case K_BACK: ui_icon(c, r.x + (r.w - 26) / 2, r.y + 12, I_BACKSPACE, C_TEXT); break;
        case K_SYM: ui_text_center(c, r.x + r.w / 2, ty, UI_KEY, a->symbols ? "abc" : "123", C_TEXT, 0); break;
        case K_SPACE: ui_text_center(c, r.x + r.w / 2, ty, UI_KEY, "space", C_DIM, 0); break;
        case K_JOIN: ui_text_center(c, r.x + r.w / 2, ty, UI_KEY, "Join", ready ? C_BG : C_LINE, 0); break;
        default: break;
        }
    }
    ui_text_center(c, 178, 362, UI_SMALL, strlen(a->psk) < 8 ? "At least 8 characters" : "Tap Join to connect", C_DIM, 0);
}
static void draw_joining(const struct ui_app *a, struct ui_canvas *c) {
    char line[80];
    header(c, "Wi-Fi", 0);
    ui_fill(c, 286, 0, 70, 54, C_BG);
    ui_icon(c, 146, 110, I_WIFI, a->join_result < 0 ? C_RED : a->join_result > 0 ? C_GREEN : C_YELLOW);
    snprintf(line, sizeof line, "%s", a->nets[a->net_sel].ssid);
    ui_text_center(c, 178, 190, UI_BODY, line, C_TEXT, 320);
    const char *msg = a->join_result > 0 ? "Connected" : a->join_result < 0 ? "Could not join. Check the password." : "Connecting...";
    ui_text_center(c, 178, 226, UI_SMALL, msg, a->join_result > 0 ? C_GREEN : a->join_result < 0 ? C_RED : C_DIM, 330);
    if (a->join_result > 0 && a->wifi.ip[0]) ui_text_center(c, 178, 252, UI_SMALL, a->wifi.ip, C_DIM, 0);
    if (a->note[0]) ui_text_center(c, 178, 278, UI_SMALL, a->note, C_DIM, 330);
    button(c, JOIN_BACK, a->join_result > 0 ? "Done" : a->join_result < 0 ? "Try again" : "Cancel", 1, 1);
}
static void fmt_since(unsigned s, char *out, size_t n) {
    if (s < 60u) snprintf(out, n, "just now");
    else if (s < 3600u) snprintf(out, n, "%u min", s / 60u);
    else snprintf(out, n, "%u h %u min", s / 3600u, s / 60u % 60u);
}
static void draw_agent(const struct ui_app *a, struct ui_canvas *c) {
    const struct agent_info *g = &a->agent;
    char line[128], since[32];
    header(c, "Connection", 1);
    ui_rrect(c, 10, 60, 336, 178, 18, C_CARD);
    (void)ui_text(c, 26, 74, UI_SMALL, "Controller", C_DIM, 0);
    if (g->owner[0]) {
        ui_fill(c, 26, 108, 10, 10, C_GREEN);
        int is_c3 = !strcmp(g->owner, "c3");
        (void)ui_text(c, 44, 98, UI_BODY, is_c3 ? "Cammy Control Center" : g->owner, C_TEXT, 286);
        if (g->label[0] && (!is_c3 || strcmp(g->label, "Cammy Control Center")))
            (void)ui_text(c, 26, 134, UI_SMALL, g->label, C_DIM, 304);
        fmt_since(g->since_s, since, sizeof since);
        snprintf(line, sizeof line, "From %s", g->address[0] ? g->address : "unknown");
        (void)ui_text(c, 26, 162, UI_SMALL, line, C_DIM, 304);
        snprintf(line, sizeof line, "Connected %s", since);
        (void)ui_text(c, 26, 188, UI_SMALL, line, C_DIM, 304);
    } else {
        (void)ui_text(c, 26, 98, UI_BODY, g->reachable ? "No controller connected" : "Camera service offline", C_TEXT, 304);
        if (g->reachable)
            (void)ui_text(c, 26, 134, UI_SMALL, "Open Control Center on your PC.", C_DIM, 304);
    }
    ui_rrect(c, 10, 248, 336, 92, 18, C_CARD);
    (void)ui_text(c, 26, 262, UI_SMALL, "This Cammy", C_DIM, 0);
    if (g->cammy_ip[0]) snprintf(line, sizeof line, "%s : 8787", g->cammy_ip);
    else snprintf(line, sizeof line, "No network address");
    (void)ui_text(c, 26, 290, UI_BODY, line, C_TEXT, 304);
    (void)ui_text(c, 26, 353, UI_SMALL, a->capture_muted ? "M: CCC capture muted" : "M: Mute CCC capture",
                  a->capture_muted ? C_YELLOW : C_DIM, 304);
}
static void draw_controls(const struct ui_app *a, struct ui_canvas *c) {
    char pct[16];
    header(c, "Sound & Face", 1);
    ui_rrect(c, 10, 60, 336, 124, 18, C_CARD);
    (void)ui_text(c, 26, 74, UI_SMALL, "Speaker volume", C_DIM, 0);
    struct rect m = VOL_MINUS, p = VOL_PLUS;
    ui_rrect(c, m.x, m.y, m.w, m.h, 28, a->volume > 0 ? C_CARD_HI : C_KEY);
    ui_icon(c, m.x + 13, m.y + 13, I_MINUS, a->volume > 0 ? C_TEXT : C_LINE);
    ui_rrect(c, p.x, p.y, p.w, p.h, 28, a->volume < 100 ? C_CARD_HI : C_KEY);
    ui_icon(c, p.x + 13, p.y + 13, I_PLUS, a->volume < 100 ? C_TEXT : C_LINE);
    ui_rrect(c, 90, 136, 176, 12, 6, C_LINE);
    if (a->volume) ui_rrect(c, 90, 136, 176 * a->volume / 100 < 12 ? 12 : 176 * a->volume / 100, 12, 6, C_YELLOW);
    snprintf(pct, sizeof pct, a->volume ? "%d%%" : "Muted", a->volume);
    ui_text_center(c, 178, 108, UI_BODY, pct, C_TEXT, 0);
    ui_rrect(c, 10, 194, 336, 200, 18, C_CARD);
    int selected = a->shown_pack >= 0 ? a->shown_pack : a->pack;
    (void)ui_text(c, 26, 204, UI_SMALL, "Face pack", C_DIM, 304);
    static const char *names[CUI_CHILD_PACKS] = {"Pikachu", "Fin and Fur"};
    static const enum ui_icon icons[CUI_CHILD_PACKS] = {I_PIKACHU, I_FINFUR};
    static const uint32_t tints[CUI_CHILD_PACKS] = {C_YELLOW, 0x5ab8f2u};
    for (int i = 0; i < (int)CUI_CHILD_PACKS; i++) {
        struct rect r = PACK_ROW(i);
        int on = i == selected;
        ui_rrect(c, r.x, r.y, r.w, r.h, 14, on ? C_YELLOW_DK : C_CARD_HI);
        ui_icon(c, r.x + 10, r.y + 3, icons[i], tints[i]);
        (void)ui_text(c, r.x + 66, r.y + 12, UI_BODY, names[i], C_TEXT, 0);
        if (on) ui_icon(c, r.x + r.w - 38, r.y + 12, I_CHECK, C_YELLOW);
    }
    struct rect cammy = PACK_ROW((int)CUI_PACK_CAMMY);
    int on = selected == (int)CUI_PACK_CAMMY && a->cammy_available;
    ui_rrect(c, cammy.x, cammy.y, cammy.w, cammy.h, 12, on ? C_YELLOW_DK : a->cammy_available ? C_CARD_HI : C_KEY);
    (void)ui_text(c, 36, 354, UI_BODY, "Cammy", a->cammy_available ? C_TEXT : C_DIM, 100);
    if (!a->cammy_available) (void)ui_text(c, 150, 358, UI_SMALL, "Unavailable", C_DIM, 140);
    if (on) ui_icon(c, 296, 354, I_CHECK, C_YELLOW);
}
static void draw_camera(const struct ui_app *a, struct ui_canvas *c) {
    header(c, "Camera", 1);
    ui_rrect(c, 10, 60, 336, 96, 18, C_CARD);
    (void)ui_text(c, 26, 78, UI_BODY, "Live preview", C_TEXT, 304);
    (void)ui_text(c, 26, 112, UI_SMALL, "Use the side play button to toggle.", C_DIM, 304);
    button(c, CAM_PREVIEW, a->local_preview ? "Leave preview" : "Open preview", 1, 1);
    button(c, CAM_STOCK, "Stock camera mode", 0, 1);
    ui_text_center(c, 178, 348, UI_SMALL, "Returns to CammyOS at next startup.", C_DIM, 336);
}
static void draw_camera_confirm(struct ui_canvas *c) {
    header(c, "Stock camera", 1);
    ui_icon(c, 146, 80, I_CAMERA, C_YELLOW);
    ui_text_center(c, 178, 164, UI_BODY, "Switch to the stock camera?", C_TEXT, 330);
    ui_text_center(c, 178, 204, UI_SMALL, "The camera app takes the screen", C_DIM, 330);
    ui_text_center(c, 178, 226, UI_SMALL, "and Cammy's face goes away until", C_DIM, 330);
    ui_text_center(c, 178, 248, UI_SMALL, "Cammy is powered on again.", C_DIM, 330);
    button(c, CAM_CANCEL, "Cancel", 0, 1);
    button(c, CAM_SWITCH, "Switch", 1, 1);
}
int ui_app_loading(struct ui_app *a, const struct ub_view *v, uint64_t now) {
    if (a->boot_done) return 0;
    a->last_input = now;
    if (v->stage == UB_READY) {
        a->boot_done = 1;
        memset(&a->boot, 0, sizeof a->boot);
        a->boot.stage = UB_READY;
        a->keys_used = 0;
        go(a, S_CLOSED);
        return 1;
    }
    int changed = a->screen != S_LOADING || memcmp(v, &a->boot, sizeof *v);
    if (a->screen != S_LOADING) { a->keys_used = 0; go(a, S_LOADING); }
    a->boot = *v;
    return changed;
}
/* One step row: done (check), waiting now (yellow ring), failed here (red cross), later (dim). */
static void step(struct ui_canvas *c, int y, const char *label, int state) {
    uint32_t col = state == 1 ? C_GREEN : state == 2 ? C_YELLOW : state == 3 ? C_RED : C_LINE;
    if (state == 1) ui_icon(c, 32, y + 9, I_CHECK, col);
    else if (state == 3) ui_icon(c, 31, y + 8, I_CLOSE, col);
    else ui_ring(c, 33, y + 10, 24, 24, 12, state == 2 ? 4 : 3, col);
    (void)ui_text(c, 74, y + 8, UI_BODY, label, state ? C_TEXT : C_DIM, 262);
}
static void draw_loading(const struct ui_app *a, struct ui_canvas *c) {
    const struct ub_view *v = &a->boot;
    int failed = v->stage == UB_FAILED;
    ui_text_center(c, 178, 14, UI_TITLE, "CammyOS", C_YELLOW, 336);
    ui_text_center(c, 178, 54, UI_SMALL, failed ? "Could not finish starting" : "Loading", failed ? C_RED : C_DIM, 336);
    ui_rrect(c, 26, 74, 304, 7, 3, C_LINE);
    if (v->progress) ui_rrect(c, 26, 74, 304 * v->progress / 100, 7, 3, failed ? C_RED : C_YELLOW);
    static const char *const label[3] = {"Camera startup", "Wi-Fi", "Cammy's services"};
    ui_rrect(c, 10, 86, 336, 150, 18, C_CARD);
    for (int i = 0; i < 3; i++)
        step(c, 92 + i * 46, label[i], i < v->step ? 1 : i > v->step ? 0 : failed ? 3 : 2);
    ui_text_center(c, 178, 248, UI_SMALL, v->wait, C_TEXT, 336);
    if (failed) {
        char line[64];
        snprintf(line, sizeof line, "Reason: %s", v->reason);
        ui_text_center(c, 178, 272, UI_SMALL, line, C_DIM, 336);
        ui_text_center(c, 178, 296, UI_SMALL, "The next start uses the normal camera.", C_DIM, 336);
        button(c, LOAD_STOCK, "Use the normal camera now", 1, 1);
    } else {
        ui_text_center(c, 178, 284, UI_SMALL, v->media[0] ? v->media : "Camera media is optional:", C_DIM, 336);
        if (!v->media[0]) ui_text_center(c, 178, 306, UI_SMALL, "it starts when Control Center asks.", C_DIM, 336);
        ui_text_center(c, 178, 360, UI_SMALL, "Cammy will appear when ready.", C_DIM, 336);
    }
}
static void draw_focus(const struct ui_app *a, struct ui_canvas *c) {
    struct rect r[48];
    int n = focusables(a, r), f = focused(a, n);
    if (!a->keys_used || f < 0) return;
    struct rect b = r[f];
    if (b.y == 0 && b.h == 54) { b.x += 4; b.y += 6; b.w -= 8; b.h -= 12; } /* header controls */
    ui_ring(c, b.x - 3, b.y - 3, b.w + 6, b.h + 6, 14, 4, C_TEXT);
}
void ui_app_render(const struct ui_app *a, struct ui_canvas *c) {
    ui_fill(c, 0, 0, (int)CUI_W, (int)CUI_H, C_BG);
    switch (a->screen) {
    case S_MENU: draw_menu(a, c); break;
    case S_WIFI: draw_wifi(a, c); break;
    case S_NETWORKS: draw_networks(a, c); break;
    case S_PASSWORD: draw_password(a, c); break;
    case S_JOINING: draw_joining(a, c); break;
    case S_AGENT: draw_agent(a, c); break;
    case S_CONTROLS: draw_controls(a, c); break;
    case S_CAMERA: draw_camera(a, c); break;
    case S_CAMERA_CONFIRM: draw_camera_confirm(c); break;
    case S_LOADING: draw_loading(a, c); break;
    default: break;
    }
    draw_focus(a, c);
}
