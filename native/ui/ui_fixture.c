/* SPDX-License-Identifier: MIT. Host fixture for cammy-ui: drives the menu with a fake
 * backend, checks transitions and writes one PPM snapshot per screen to argv[1]. */
#include "ui_app.h"
#include "ui_buttons.h"
#include "ui_face_boot.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned checks, failed;
#define CHECK(x) do { checks++; if (!(x)) { failed++; fprintf(stderr, "check %u failed line %d: %s\n", checks, __LINE__, #x); } } while (0)
static struct {
    int scans, joins, commits, aborts, reconnects, camera, volume, volume_writes, pack, connect_on_join, hotspot;
    char joined_ssid[UI_SSID_MAX], joined_psk[UI_PSK_MAX];
} fake;
static int f_status(void *c, struct wifi_status *w) {
    (void)c;
    memset(w, 0, sizeof *w);
    if (fake.hotspot) { w->mode = WIFI_HOTSPOT; w->signal = -1; return 0; }
    w->mode = WIFI_HOME; w->connected = 1; w->signal = 3;
    snprintf(w->ssid, sizeof w->ssid, "%s", fake.joins && fake.connect_on_join ? fake.joined_ssid : "ZhangHome");
    snprintf(w->ip, sizeof w->ip, "192.168.1.50");
    snprintf(w->state, sizeof w->state, "COMPLETED");
    return 0;
}
static int f_scan(void *c) { (void)c; fake.scans++; return fake.hotspot ? -1 : 0; }
static int f_results(void *c, struct wifi_net *n, int max) {
    (void)c;
    static const struct wifi_net all[] = {{"ZhangHome", 1, 3}, {"Lab 5G", 1, 2}, {"Coffee Guest", 0, 1},
                                          {"Neighbour Extremely Long Name 5G", 1, 2}};
    int k = (int)(sizeof all / sizeof all[0]);
    if (k > max) k = max;
    memcpy(n, all, (size_t)k * sizeof all[0]);
    return k;
}
static int f_join(void *c, const char *ssid, const char *psk) {
    (void)c; fake.joins++;
    snprintf(fake.joined_ssid, sizeof fake.joined_ssid, "%s", ssid);
    snprintf(fake.joined_psk, sizeof fake.joined_psk, "%s", psk ? psk : "");
    return 0;
}
static int f_reconnect(void *c) { (void)c; fake.reconnects++; return 0; }
static int f_commit(void *c) { (void)c; fake.commits++; return 0; }
static int f_abort(void *c) { (void)c; fake.aborts++; return 0; }
static int f_agent(void *c, struct agent_info *g) {
    (void)c;
    memset(g, 0, sizeof *g);
    g->reachable = 1;
    snprintf(g->owner, sizeof g->owner, "c3");
    snprintf(g->label, sizeof g->label, "Cammy Control Center");
    snprintf(g->address, sizeof g->address, "192.168.1.20");
    snprintf(g->cammy_ip, sizeof g->cammy_ip, "192.168.1.50");
    g->since_s = 754;
    return 0;
}
static int f_volume(void *c, int v) { (void)c; fake.volume = v; fake.volume_writes++; return 0; }
static int f_pack(void *c, int i) { (void)c; fake.pack = i; return 0; }
static void f_camera(void *c) { (void)c; fake.camera++; }
static unsigned char px[CUI_FRAME_BYTES];
static const char *dir;
static void snap(const struct ui_app *a, const char *name) {
    struct ui_canvas c = {px};
    ui_app_render(a, &c);
    char path[512];
    snprintf(path, sizeof path, "%s/%s.ppm", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { failed++; return; }
    fprintf(f, "P6\n%u %u\n255\n", CUI_W, CUI_H);
    for (unsigned i = 0; i < CUI_W * CUI_H; i++) { fputc(px[i * 4 + 2], f); fputc(px[i * 4 + 1], f); fputc(px[i * 4], f); }
    fclose(f);
}
/* Centre of the key labelled ch on the current keyboard layer (found by scanning taps). */
static int type_char(struct ui_app *a, char ch, uint64_t now) {
    for (int y = 130; y < 350; y += 8)
        for (int x = 4; x < 352; x += 6) {
            size_t len = strlen(a->psk);
            struct ui_app probe = *a;
            (void)ui_app_tap(&probe, x, y, now);
            if (strlen(probe.psk) == len + 1 && probe.psk[len] == ch) { *a = probe; return 1; }
        }
    return 0;
}
/* Boot ID reader stand-in: counts reads, fails while boot_fail is set. */
static unsigned boot_reads; static int boot_fail;
static int fake_boot(uint32_t b[4]) { boot_reads++; if (boot_fail) return -1; for (unsigned i = 0; i < 4; i++) b[i] = 10u + i; return 0; }
/* A boot gate input for this boot at phase p (NULL: no guard). */
static struct ub_input boot_at(const char *p, const char *net, int portal) {
    struct ub_input in;
    memset(&in, 0, sizeof in);
    in.supervisor = 1; in.portal = portal;
    if (p) { in.guard = UBG_CURRENT; snprintf(in.phase, sizeof in.phase, "%s", p); }
    snprintf(in.net, sizeof in.net, "%s", net);
    in.ap = 1;
    return in;
}
static int stage_of(struct ub_input in) { struct ub_view v; ub_eval(&in, &v); return v.stage; }
static void loading_checks(const struct ui_backend *be, uint64_t t) {
    /* Records: only an exact guard naming this boot counts. */
    struct ub_input in;
    memset(&in, 0, sizeof in);
    ub_guard("phase=dhcp\nboot_id=b-1\n", "b-1", &in); CHECK(in.guard == UBG_CURRENT && !strcmp(in.phase, "dhcp"));
    ub_guard("phase=idle_ready\nboot_id=b-1", "b-1", &in); CHECK(in.guard == UBG_CURRENT && !strcmp(in.phase, "idle_ready"));
    ub_guard("phase=idle_ready\nboot_id=b-0\n", "b-1", &in); CHECK(in.guard == UBG_STALE && !in.phase[0]); /* other boot */
    ub_guard("phase=idle_ready\nboot_id=b-1\nx=1\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    /* the release-bound idle latch RC06 startup writes (boot c3f3e01d: menu held 2 min) */
    ub_guard("phase=idle_ready\nboot_id=b-1\nrelease_id=2ba6ccbe0fecbab6daabccc7\ncandidate_md5=e506fce2d6edbc06c2ee08ab48a428c7\n", "b-1", &in);
    CHECK(in.guard == UBG_CURRENT && !strcmp(in.phase, "idle_ready"));
    in = boot_at("idle_ready", "HOME_STATION", 1); in.services_ready = 1; CHECK(stage_of(in) == UB_READY);
    ub_guard("phase=idle_ready\nboot_id=b-1\nrelease_id=2ba6ccbe0fecbab6daabccc7\ncandidate_md5=e506fce2d6edbc06c2ee08ab48a428c7", "b-1", &in);
    CHECK(in.guard == UBG_CURRENT);
    ub_guard("phase=idle_ready\nboot_id=b-1\nrelease_id=2ba6ccbe0fecbab6daabccc7\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    ub_guard("phase=idle_ready\nboot_id=b-1\nrelease_id=2ba6ccbe0fecbab6daabccc\ncandidate_md5=e506fce2d6edbc06c2ee08ab48a428c7\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    ub_guard("phase=idle_ready\nboot_id=b-1\nrelease_id=2ba6ccbe0fecbab6daabccc7\ncandidate_md5=e506fce2d6edbc06c2ee08ab48a428c7\nx=1\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    ub_guard("phase=idle_ready\nboot_id=b-0\nrelease_id=2ba6ccbe0fecbab6daabccc7\ncandidate_md5=e506fce2d6edbc06c2ee08ab48a428c7\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    ub_guard("boot_id=b-1\nphase=idle_ready\n", "b-1", &in); CHECK(in.guard == UBG_STALE);  /* order is part of it */
    ub_guard("phase=idle ready\nboot_id=b-1\n", "b-1", &in); CHECK(in.guard == UBG_STALE);
    ub_guard("phase=idle_ready\nboot_id=b-1\n", "", &in); CHECK(in.guard == UBG_STALE);      /* boot unknown */
    char v[16]; CHECK(!ub_field("a=1\nreason=media-failed\n", "reason", v, sizeof v) && !strcmp(v, "media-failed"));
    CHECK(ub_field("reason=\n", "reason", v, sizeof v) && ub_field("reason=a/b\n", "reason", v, sizeof v));
    CHECK(ub_field("reason=0123456789abcdefg\n", "reason", v, sizeof v)); /* too long for out */
    /* The idle supervisor still waits for this boot's display health check. */
    static const struct { const char *phase; int stage; } prog[] = {
        {"launch", UB_STOCK}, {"payload", UB_STOCK}, {"stock_settle", UB_STOCK}, {"radio_load", UB_RADIO},
        {"station_setup", UB_WIFI}, {"dhcp", UB_WIFI}, {"ap_fallback", UB_HOTSPOT}, {"portal", UB_SERVICES},
        {"menu", UB_SERVICES}, {"idle_ready", UB_SERVICES}, {"media_prepare", UB_SERVICES}};
    for (unsigned i = 0; i < sizeof prog / sizeof *prog; i++) CHECK(stage_of(boot_at(prog[i].phase, "HOME_STATION", 1)) == prog[i].stage);
    CHECK(stage_of(boot_at("idle_ready", "AP_CammyOS", 1)) == UB_SERVICES);
    in = boot_at("idle_ready", "AP_CammyOS", 1); in.ap = 0; CHECK(stage_of(in) == UB_WIFI); /* AP servers not running */
    CHECK(stage_of(boot_at("idle_ready", "HOME_STATION", 0)) == UB_SERVICES);     /* portal not running */
    CHECK(stage_of(boot_at("idle_ready", "", 1)) == UB_WIFI);                     /* no network path */
    CHECK(stage_of(boot_at("idle_ready", "AP_STARTING", 1)) == UB_WIFI);
    CHECK(stage_of(boot_at(NULL, "HOME_STATION", 1)) == UB_STOCK);                /* no record yet */
    in = boot_at(NULL, "HOME_STATION", 1); in.guard = UBG_STALE; CHECK(stage_of(in) == UB_STOCK); /* stale never readies */
    in = boot_at(NULL, "", 0); in.services_ready = 1; CHECK(stage_of(in) == UB_WIFI);
    in = boot_at(NULL, "AP_CammyOS", 1); in.services_ready = 1; CHECK(stage_of(in) == UB_READY);
    in.ap = 0; CHECK(stage_of(in) == UB_WIFI);
    /* An optional job only holds the gate while it is pending. */
    struct ub_view view;
    in = boot_at("idle_ready", "HOME_STATION", 1); in.services_ready = 1;
    CHECK(stage_of(in) == UB_READY);
    snprintf(in.media, sizeof in.media, "initializing");
    CHECK(stage_of(in) == UB_SERVICES);
    snprintf(in.media, sizeof in.media, "refused");
    CHECK(stage_of(in) == UB_READY);
    snprintf(in.phase, sizeof in.phase, "media_prepare"); CHECK(stage_of(in) == UB_SERVICES);
    in.guard = UBG_STALE; CHECK(stage_of(in) == UB_SERVICES);
    in = boot_at("portal", "HOME_STATION", 0); snprintf(in.media, sizeof in.media, "queued");
    ub_eval(&in, &view); CHECK(view.stage == UB_SERVICES && !strcmp(view.media, "Camera media is preparing."));
    in = boot_at("portal", "HOME_STATION", 0); ub_eval(&in, &view); CHECK(!view.media[0]);
    /* Faults: this boot's latch or record fails the gate, ahead of anything ready. */
    in = boot_at("runtime_health_fault", "HOME_STATION", 1); ub_eval(&in, &view);
    CHECK(view.stage == UB_FAILED && !strcmp(view.reason, "runtime-health-fault") && view.step == 2);
    in = boot_at("idle_ready", "HOME_STATION", 1); in.services_ready = 1; in.fault = 1; snprintf(in.fault_reason, sizeof in.fault_reason, "media-failed");
    ub_eval(&in, &view); CHECK(view.stage == UB_FAILED && !strcmp(view.reason, "media-failed"));
    in = boot_at("radio_load", "DRIVER_UNAVAILABLE", 0); ub_eval(&in, &view); CHECK(view.stage == UB_FAILED && view.step == 1);
    in = boot_at("station_setup", "RADIO_FAULT", 0); ub_eval(&in, &view); CHECK(view.stage == UB_FAILED && !strcmp(view.reason, "wifi-driver-fault"));
    in = boot_at("ap_fallback", "RADIO_HUNG", 0); ub_eval(&in, &view); CHECK(view.stage == UB_FAILED && view.step == 1);
    in = boot_at("dhcp", "", 0); in.supervisor = 0; ub_eval(&in, &view);
    CHECK(view.stage == UB_FAILED && !strcmp(view.reason, "startup-stopped-dhcp") && view.step == 1);
    in = boot_at(NULL, "HOME_STATION", 1); in.services_ready = 1; in.supervisor = 0; CHECK(stage_of(in) == UB_FAILED);
    in = boot_at("dhcp", "", 0); in.supervisor = -1; CHECK(stage_of(in) == UB_WIFI); /* unknown parent: no claim */
    /* The screen: Loading replaces everything, never times out, ignores Settings and the
     * side play button, and opens only on a ready view, for good. */
    struct ui_app g;
    ui_app_init(&g, be, 70);
    int cams = fake.camera;
    in = boot_at("stock_settle", "", 0); ub_eval(&in, &view);
    CHECK(ui_app_loading(&g, &view, t) && g.screen == S_LOADING && g.boot.step == 0);
    snap(&g, "loading-stock");
    CHECK(!ui_app_loading(&g, &view, t + 1000)); /* unchanged: no new frame */
    CHECK(!ui_app_tick(&g, t + 600000) && g.screen == S_LOADING);
    CHECK(!ui_app_key(&g, UIB_KEY_MUTE, t) && !ui_app_key(&g, UIB_KEY_PLAYBACK, t) && g.screen == S_LOADING && !g.local_preview);
    CHECK(!ui_app_key(&g, UI_KEY_OK, t) && !ui_app_key(&g, UI_KEY_DOWN, t) && !ui_app_tap(&g, 178, 360, t) && g.screen == S_LOADING);
    CHECK(uib_route(0, UIB_KEY_LEFT, 1, 1, 1, g.screen != S_CLOSED, 0, 0) == UIB_IGNORE); /* routed as an open menu */
    in = boot_at("dhcp", "", 0); ub_eval(&in, &view); CHECK(ui_app_loading(&g, &view, t) && g.boot.step == 1);
    snap(&g, "loading-wifi");
    in = boot_at("portal", "HOME_STATION", 0); snprintf(in.media, sizeof in.media, "queued"); ub_eval(&in, &view);
    CHECK(ui_app_loading(&g, &view, t)); snap(&g, "loading-services");
    in = boot_at("idle_ready", "HOME_STATION", 1); in.services_ready = 1; ub_eval(&in, &view);
    CHECK(view.progress == 100);
    CHECK(ui_app_loading(&g, &view, t) && g.screen == S_CLOSED && g.boot_done && fake.camera == cams);
    in = boot_at("runtime_health_fault", "HOME_STATION", 1); ub_eval(&in, &view);
    CHECK(!ui_app_loading(&g, &view, t) && g.screen == S_CLOSED); /* the gate never returns */
    /* After the gate, buttons are the menu's again. */
    CHECK(ui_app_key(&g, UI_KEY_OK, t) && g.screen == S_MENU);
    CHECK(uib_route(0, UIB_KEY_LEFT, 1, 1, 1, 0, 0, 0) == UIB_PRESS);
    /* Failure: said plainly, with the stock camera for this boot by tap or OK. */
    ui_app_init(&g, be, 70);
    in = boot_at("idle_ready", "HOME_STATION", 1); in.fault = 1; snprintf(in.fault_reason, sizeof in.fault_reason, "gui-owner");
    ub_eval(&in, &view); CHECK(ui_app_loading(&g, &view, t) && g.screen == S_LOADING);
    snap(&g, "loading-failed");
    CHECK(!ui_app_tap(&g, 178, 120, t) && fake.camera == cams);
    CHECK(ui_app_key(&g, UI_KEY_OK, t) && fake.camera == cams + 1 && g.screen == S_CLOSED && g.boot_done);
    ui_app_init(&g, be, 70);
    in = boot_at("dhcp", "", 0); in.supervisor = 0; ub_eval(&in, &view); CHECK(ui_app_loading(&g, &view, t));
    snap(&g, "loading-stopped");
    CHECK(ui_app_tap(&g, 178, 350, t) && fake.camera == cams + 2 && g.boot_done);
}
int main(int argc, char **argv) {
    dir = argc > 1 ? argv[1] : ".";
    { /* cammy-ui reads the boot ID once per process; every loop still checks the page's boot. */
        _Atomic uint32_t words[4]; struct cui_boot cache = {{0}, 0};
        for (unsigned i = 0; i < 4; i++) atomic_init(&words[i], 10u + i);
        boot_fail = 1; CHECK(!cui_boot_matches(&cache, words, fake_boot) && boot_reads == 1 && !cache.known);
        boot_fail = 0; CHECK(cui_boot_matches(&cache, words, fake_boot) && boot_reads == 2);
        for (unsigned n = 0; n < 1000; n++) CHECK(cui_boot_matches(&cache, words, fake_boot));
        CHECK(boot_reads == 2);
        atomic_store(&words[3], 99u); CHECK(!cui_boot_matches(&cache, words, fake_boot) && boot_reads == 2); /* other boot's page */
        atomic_store(&words[3], 13u); CHECK(cui_boot_matches(&cache, words, fake_boot));
    }
    struct ui_backend be = {NULL, f_status, f_scan, f_results, f_join, f_reconnect, f_commit, f_abort, f_agent, f_volume, f_camera, f_pack};
    struct ui_app a;
    ui_app_init(&a, &be, 70);
    uint64_t t = 1000;
    CHECK(a.screen == S_CLOSED);
    /* 0.7.4 key ownership in CammyOS mode (dev, code, value, exclusive, cammy, menu, live, injected). */
    CHECK(uib_route(0, UIB_KEY_OK, 0, 1, 1, 0, 0, 0) == UIB_OPEN);
    CHECK(uib_route(0, UIB_KEY_OK, 1, 1, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_MUTE, 1, 1, 1, 0, 0, 0) == UIB_MUTE);
    CHECK(uib_route(1, UIB_KEY_MUTE, 1, 1, 1, 1, 1, 0) == UIB_MUTE);
    CHECK(uib_route(0, UIB_KEY_MUTE, 2, 1, 1, 1, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_MUTE, 0, 1, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_MUTE, 1, 0, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_MUTE, 1, 1, 0, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 1, 1, 1, 0, 0, 0) == UIB_MENU);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 1, 1, 1, 1, 1, 0) == UIB_MENU);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 2, 1, 1, 1, 1, 0) == UIB_IGNORE);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 0, 1, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 1, 0, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(1, UIB_KEY_PLAYBACK, 1, 1, 0, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_UP, 1, 1, 1, 0, 0, 0) == UIB_IGNORE);         /* no hidden zoom */
    CHECK(uib_route(0, UIB_KEY_UP, 1, 1, 1, 0, 1, 0) == UIB_PRESS);          /* zoom in the live view */
    CHECK(uib_route(0, UIB_KEY_DOWN, 1, 1, 1, 0, 0, 0) == UIB_PRESS);        /* face follow toggle */
    CHECK(uib_route(0, UIB_KEY_LEFT, 1, 1, 1, 0, 0, 0) == UIB_PRESS);
    CHECK(uib_route(0, UIB_KEY_LEFT, 2, 1, 1, 0, 0, 1) == UIB_IGNORE);       /* repeats: stock times holds */
    CHECK(uib_route(0, UIB_KEY_LEFT, 0, 1, 1, 0, 0, 1) == UIB_RELEASE);
    CHECK(uib_route(0, UIB_KEY_LEFT, 0, 0, 1, 0, 0, 1) == UIB_IGNORE);       /* stock saw the release */
    CHECK(uib_route(1, UIB_KEY_TURN, 1, 1, 1, 0, 0, 0) == UIB_PRESS);
    CHECK(uib_route(0, UIB_KEY_DOWN, 1, 1, 1, 1, 0, 0) == UIB_MENU);         /* the open menu takes all */
    CHECK(uib_route(1, UIB_KEY_TURN, 1, 1, 1, 1, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(1, UIB_KEY_DOWN, 1, 1, 1, 0, 0, 0) == UIB_PRESS);        /* by code, either device */
    CHECK(uib_route(1, UIB_KEY_OK, 0, 1, 1, 0, 0, 0) == UIB_OPEN);
    CHECK(uib_route(0, UIB_KEY_MODE, 1, 1, 1, 0, 1, 0) == UIB_IGNORE);          /* M dropped even in live view */
    CHECK(uib_route(1, UIB_KEY_MODE, 1, 1, 1, 0, 0, 0) == UIB_IGNORE);                /* MODE dropped */
    CHECK(uib_route(1, UIB_KEY_ZOOM_IN, 1, 1, 1, 0, 1, 0) == UIB_PRESS);     /* zoom only in the live view */
    CHECK(uib_route(1, UIB_KEY_ZOOM_IN, 1, 1, 1, 0, 0, 0) == UIB_IGNORE);
    CHECK(uib_route(0, UIB_KEY_DOWN, 1, 1, 0, 0, 0, 0) == UIB_IGNORE);       /* not CammyOS mode */
    CHECK(ui_app_tap(&a, 178, 200, t) && a.screen == S_MENU);
    CHECK(!strcmp(a.wifi.ssid, "ZhangHome") && !strcmp(a.agent.owner, "c3"));
    snap(&a, "menu");
    /* Auto-close after 30 s without input. */
    CHECK(!ui_app_tick(&a, t + 29000) && a.screen == S_MENU);
    CHECK(ui_app_tick(&a, t + 30001) && a.screen == S_CLOSED);
    t += 40000;
    (void)ui_app_tap(&a, 178, 200, t);
    CHECK(ui_app_tap(&a, 320, 20, t) && a.screen == S_CLOSED); /* close button */
    (void)ui_app_tap(&a, 178, 200, t);
    /* Wi-Fi tile, scan, results, pick a secured network, type, join, commit. */
    CHECK(ui_app_tap(&a, 90, 140, t) && a.screen == S_WIFI);
    snap(&a, "wifi");
    CHECK(ui_app_tap(&a, 178, 230, t) && a.screen == S_NETWORKS && fake.scans == 1 && a.scan_wait);
    snap(&a, "networks-searching");
    t += 2000;
    CHECK(ui_app_tick(&a, t) && a.nets_n == 4 && !a.scan_wait);
    snap(&a, "networks");
    CHECK(ui_app_tap(&a, 178, 60 + 48 + 20, t) && a.screen == S_PASSWORD && a.net_sel == 1);
    snap(&a, "password-empty");
    CHECK(!ui_app_tap(&a, 300, 400 - 60 + 20, t) || a.screen == S_PASSWORD); /* Join disabled < 8 chars */
    const char *pw = "Pika2026";
    for (const char *p = pw; *p; p++) {
        if (*p >= 'A' && *p <= 'Z') { CHECK(ui_app_tap(&a, 28, 128 + 2 * 56 + 25, t)); } /* shift on */
        if (*p >= '0' && *p <= '9' && !a.symbols) { CHECK(ui_app_tap(&a, 40, 128 + 3 * 56 + 25, t)); }
        if (*p >= 'a' && *p <= 'z' && a.symbols) { CHECK(ui_app_tap(&a, 40, 128 + 3 * 56 + 25, t)); }
        CHECK(type_char(&a, *p, t));
        if (*p >= 'A' && *p <= 'Z') { CHECK(ui_app_tap(&a, 28, 128 + 2 * 56 + 25, t)); } /* shift off */
    }
    CHECK(!strcmp(a.psk, pw));
    snap(&a, "password");
    fake.connect_on_join = 1;
    CHECK(ui_app_tap(&a, 300, 128 + 3 * 56 + 25, t) && a.screen == S_JOINING && fake.joins == 1);
    CHECK(!strcmp(fake.joined_ssid, "Lab 5G") && !strcmp(fake.joined_psk, pw));
    t += 2500;
    CHECK(ui_app_tick(&a, t) && a.join_result == 1 && fake.commits == 1);
    snap(&a, "joined");
    CHECK(ui_app_tap(&a, 178, 340, t) && a.screen == S_WIFI);
    /* A join that never completes: times out and aborts. */
    fake.connect_on_join = 0;
    (void)ui_app_tap(&a, 178, 230, t); t += 2000; (void)ui_app_tick(&a, t);
    CHECK(ui_app_tap(&a, 178, 60 + 2 * 48 + 20, t) && a.screen == S_JOINING); /* open network: no password */
    CHECK(!strcmp(fake.joined_psk, ""));
    t += 31000;
    CHECK(ui_app_tick(&a, t) && a.join_result == -1 && fake.aborts == 1);
    snap(&a, "join-failed");
    /* Back to the menu, then the other tiles. */
    CHECK(ui_app_tap(&a, 178, 340, t) && a.screen == S_PASSWORD);
    (void)ui_app_tap(&a, 50, 20, t); (void)ui_app_tap(&a, 50, 20, t);
    CHECK(a.screen == S_WIFI);
    CHECK(ui_app_tap(&a, 178, 300, t) && fake.reconnects == 1);
    CHECK(ui_app_tap(&a, 50, 20, t) && a.screen == S_MENU);
    CHECK(ui_app_tap(&a, 260, 140, t) && a.screen == S_AGENT);
    snap(&a, "agent");
    (void)ui_app_tap(&a, 50, 20, t);
    CHECK(ui_app_tap(&a, 90, 310, t) && a.screen == S_CONTROLS);
    a.cammy_available = 1;
    CHECK(ui_app_tap(&a, 300, 140, t) && a.volume == 80 && fake.volume_writes == 0); /* saved later */
    for (int i = 0; i < 5; i++) (void)ui_app_tap(&a, 300, 140, t);
    CHECK(a.volume == 100);
    for (int i = 0; i < 9; i++) (void)ui_app_tap(&a, 40, 140, t);
    CHECK(a.volume == 10);
    (void)ui_app_tick(&a, t + 1000); CHECK(fake.volume_writes == 0);
    (void)ui_app_tick(&a, t + 1500); CHECK(fake.volume == 10 && fake.volume_writes == 1); /* 15 taps, one NOR write */
    (void)ui_app_tick(&a, t + 3000); CHECK(fake.volume_writes == 1);
    CHECK(ui_app_tap(&a, 178, 313, t) && a.pack == 1 && fake.pack == 1); /* Fin and Fur */
    snap(&a, "controls");
    CHECK(ui_app_tap(&a, 178, 367, t) && a.pack == 2 && fake.pack == 2);
    a.shown_pack = 2;
    snap(&a, "controls-cammy");
    CHECK(ui_app_tap(&a, 178, 313, t) && a.pack == 1 && fake.pack == 1);
    a.shown_pack = 1; fake.pack = -1;
    (void)ui_app_tap(&a, 178, 313, t); CHECK(fake.pack == -1); /* saved pack, no grant: no write */
    a.cammy_available = 0;
    CHECK(!ui_app_tap(&a, 178, 367, t) && a.pack == 1 && fake.pack == -1);
    (void)ui_app_tap(&a, 50, 20, t);
    CHECK(ui_app_tap(&a, 260, 310, t) && a.screen == S_CAMERA);
    snap(&a, "camera");
    CHECK(ui_app_tap(&a, 178, 205, t) && a.screen == S_CLOSED && a.local_preview && !fake.camera);
    CHECK(ui_app_key(&a, UI_KEY_OK, t) && a.screen == S_MENU && a.local_preview);
    CHECK(ui_app_tap(&a, 260, 310, t) && a.screen == S_CAMERA);
    snap(&a, "camera-preview-active");
    CHECK(ui_app_tap(&a, 178, 205, t) && a.screen == S_CLOSED && !a.local_preview && !fake.camera);
    CHECK(ui_app_key(&a, UI_KEY_OK, t) && a.screen == S_MENU);
    CHECK(ui_app_tap(&a, 260, 310, t) && a.screen == S_CAMERA);
    CHECK(ui_app_tap(&a, 178, 300, t) && a.screen == S_CAMERA_CONFIRM);
    snap(&a, "camera-stock-confirm");
    CHECK(ui_app_tap(&a, 90, 340, t) && a.screen == S_CAMERA && !fake.camera);
    CHECK(ui_app_tap(&a, 178, 300, t) && a.screen == S_CAMERA_CONFIRM);
    CHECK(ui_app_tap(&a, 260, 340, t) && a.screen == S_CLOSED && fake.camera == 1);
    /* AP fallback shows its status and offers no unavailable scanning or reconnect action. */
    fake.hotspot = 1;
    (void)ui_app_tap(&a, 178, 200, t); (void)ui_app_tap(&a, 90, 140, t);
    snap(&a, "wifi-hotspot");
    int scans = fake.scans, reconnects = fake.reconnects;
    CHECK(!ui_app_tap(&a, 178, 230, t) && a.screen == S_WIFI && fake.scans == scans);
    CHECK(!ui_app_tap(&a, 178, 300, t) && fake.reconnects == reconnects);
    CHECK(ui_app_tap(&a, 50, 20, t) && ui_app_tap(&a, 320, 20, t) && a.screen == S_CLOSED);
    /* Buttons: OK opens the menu, down/up move the focus (wrapping), OK presses the
     * focused control, the camera screen defaults to Cancel, a tap hides the ring. */
    fake.hotspot = 0;
    struct ui_app k;
    ui_app_init(&k, &be, 50);
    t += 100000;
    CHECK(!ui_app_key(&k, UI_KEY_DOWN, t) && k.screen == S_CLOSED);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_MENU && k.keys_used);
    snap(&k, "keys-menu");
    CHECK(ui_app_key(&k, UI_KEY_UP, t) && k.focus == 4); /* wraps to Close */
    CHECK(ui_app_key(&k, UI_KEY_DOWN, t) && k.focus == 0);
    (void)ui_app_key(&k, UI_KEY_DOWN, t); (void)ui_app_key(&k, UI_KEY_DOWN, t);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_CONTROLS);
    (void)ui_app_key(&k, UI_KEY_DOWN, t); /* default Minus -> Plus */
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.volume == 60);
    (void)ui_app_key(&k, UI_KEY_DOWN, t); /* Plus -> Pikachu */
    CHECK(ui_app_key(&k, UI_KEY_DOWN, t) && ui_app_key(&k, UI_KEY_OK, t) && k.pack == 1);
    k.cammy_available = 1;
    CHECK(ui_app_key(&k, UI_KEY_DOWN, t) && ui_app_key(&k, UI_KEY_OK, t) && k.pack == 2);
    CHECK(ui_app_key(&k, UI_KEY_UP, t) && ui_app_key(&k, UI_KEY_OK, t) && k.pack == 1);
    CHECK(ui_app_key(&k, UI_KEY_UP, t) && ui_app_key(&k, UI_KEY_OK, t) && k.pack == 0);
    (void)ui_app_key(&k, UI_KEY_UP, t); /* back to Plus */
    snap(&k, "keys-controls");
    (void)ui_app_key(&k, UI_KEY_UP, t); (void)ui_app_key(&k, UI_KEY_UP, t); /* Back */
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_MENU);
    for (int i = 0; i < 3; i++) (void)ui_app_key(&k, UI_KEY_DOWN, t);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_CAMERA);
    snap(&k, "keys-camera");
    int cams = fake.camera;
    (void)ui_app_key(&k, UI_KEY_DOWN, t); /* Preview -> Stock camera */
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_CAMERA_CONFIRM);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_CAMERA && fake.camera == cams); /* Cancel */
    CHECK(ui_app_tap(&k, 50, 20, t) && ui_app_tap(&k, 320, 20, t) && k.screen == S_CLOSED);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_MENU);
    /* Typing a password with buttons reaches every key; OK on Join joins. */
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_WIFI);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_NETWORKS); t += 2000; (void)ui_app_tick(&k, t);
    (void)ui_app_key(&k, UI_KEY_DOWN, t);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_PASSWORD && k.net_sel == 1);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && !strcmp(k.psk, "q")); /* default focus: first key */
    snap(&k, "keys-password");
    CHECK(ui_app_tap(&k, 5, 5, t) && !k.keys_used && k.screen == S_NETWORKS && !k.psk[0]);
    CHECK(ui_app_tap(&k, 178, 60 + 48 + 20, t) && k.screen == S_PASSWORD);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.psk[0]);
    CHECK(ui_app_tap(&k, 50, 20, t) && k.screen == S_NETWORKS && !k.psk[0]);
    t += 2000; (void)ui_app_tick(&k, t);
    CHECK(ui_app_tap(&k, 178, 60 + 2 * 48 + 20, t) && k.screen == S_JOINING);
    int aborts = fake.aborts, commits = fake.commits;
    CHECK(ui_app_tap(&k, 178, 342, t) && k.screen == S_CLOSED && fake.aborts == aborts + 1);
    CHECK(!ui_app_tick(&k, t + 30001) && fake.commits == commits && fake.aborts == aborts + 1);
    t += 30002;
    CHECK(ui_app_key(&k, UIB_KEY_PLAYBACK, t) && k.local_preview && k.screen == S_CLOSED);
    CHECK(ui_app_key(&k, UIB_KEY_PLAYBACK, t) && !k.local_preview && k.screen == S_CLOSED);
    CHECK(ui_app_key(&k, UI_KEY_OK, t) && k.screen == S_MENU);
    CHECK(ui_app_key(&k, UIB_KEY_PLAYBACK, t) && k.local_preview && k.screen == S_CLOSED);
    CHECK(fake.camera == cams);
    loading_checks(&be, t);
    printf("{\"checks\":%u,\"failed\":%u}\n", checks, failed);
    return failed != 0;
}
