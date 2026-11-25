/* SPDX-License-Identifier: MIT. Host driver for ui_backend.c against mock wpa_supplicant
 * and portal servers (tests/test_cammy_ui.py). Prints one JSON object. */
#include "ui_backend.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static const char *js(const char *s) { /* JSON-escape quotes and backslashes */
    static char b[8][160]; static int k;
    char *o = b[k++ & 7]; size_t n = 0;
    for (; *s && n + 3 < sizeof b[0]; s++) { if (*s == '"' || *s == '\\') o[n++] = '\\'; o[n++] = *s; }
    o[n] = 0; return o;
}
/* Loading gate records: argv[1] is a run dir with cfg/ beside its records; this boot is
 * "aaaa-boot". Prints the records as read and the gate's view. */
static int boot_records(const char *run) {
    char cfg[256];
    snprintf(cfg, sizeof cfg, "%s/cfg", run);
    struct ui_real r;
    memset(&r, 0, sizeof r);
    r.run_dir = run; r.cfg_dir = cfg; r.wpa_fd = -1;
    snprintf(r.boot_id, sizeof r.boot_id, "aaaa-boot");
    struct ub_input in;
    struct ub_view v;
    ui_real_boot(&r, &in);
    in.supervisor = 1;
    ub_eval(&in, &v);
    printf("{\"guard\":%d,\"phase\":\"%s\",\"net\":\"%s\",\"portal\":%d,\"services_ready\":%d,\"fault\":%d,\"reason\":\"%s\",\"media\":\"%s\","
           "\"stage\":%d,\"step\":%d,\"progress\":%d,\"view_reason\":\"%s\",\"wait\":\"%s\",\"note\":\"%s\"}\n",
           in.guard, in.phase, in.net, in.portal, in.services_ready, in.fault, in.fault_reason, in.media,
           v.stage, v.step, v.progress, v.reason, js(v.wait), js(v.media));
    return 0;
}
int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[2], "boot")) return boot_records(argv[1]);
    if (argc != 4) return 2;
    char home[256], token[256], volume[256], pack[256];
    snprintf(home, sizeof home, "%s/home.conf", argv[1]);
    snprintf(token, sizeof token, "%s/portal.token", argv[1]);
    snprintf(volume, sizeof volume, "%s/volume", argv[1]);
    snprintf(pack, sizeof pack, "%s/face-pack", argv[1]);
    struct cui_header page;
    memset(&page, 0, sizeof page);
    struct ui_real r = {argv[1], home, token, volume, &page, -1, -1, 0, atoi(argv[2]), 0, 0, {0}, {0}, pack, NULL, {0}};
    struct ui_backend be;
    ui_real_backend(&r, &be);
    struct wifi_status w;
    struct wifi_net nets[UI_NETS];
    struct agent_info g;
    int st = be.wifi_status(be.ctx, &w);
    printf("{\"status\":%d,\"mode\":%d,\"connected\":%d,\"ssid\":\"%s\",\"ip\":\"%s\",\"signal\":%d,", st, w.mode, w.connected, js(w.ssid), w.ip, w.signal);
    printf("\"scan\":%d,", be.wifi_scan(be.ctx));
    printf("\"early\":%d,", be.wifi_results(be.ctx, nets, UI_NETS));
    r.scan_at -= 4000;
    int n = be.wifi_results(be.ctx, nets, UI_NETS);
    printf("\"nets\":[");
    for (int i = 0; i < n; i++) printf("%s[\"%s\",%d,%d]", i ? "," : "", js(nets[i].ssid), nets[i].secured, nets[i].bars);
    printf("],");
    printf("\"join\":%d,", be.wifi_join(be.ctx, !strcmp(argv[3], "open") ? "Coffee Guest" : "Lab \"5G\"", !strcmp(argv[3], "open") ? NULL : "Pika\"2026"));
    printf("\"commit\":%d,", be.wifi_commit(be.ctx));
    int vol = be.set_volume(be.ctx, 40);
    printf("\"vol\":%d,\"vol_read\":%d,", vol, ui_real_volume(&r));
    int pack0 = ui_real_pack(&r), packs = be.set_pack(be.ctx, (int)CUI_PACK_CAMMY), bad = be.set_pack(be.ctx, 7);
    printf("\"pack\":[%d,%d,%d,%d,%u,%u],", pack0, packs, bad, ui_real_pack(&r), atomic_load(&page.face_pack), atomic_load(&page.pack_seq));
    be.agent(be.ctx, &g);
    printf("\"agent\":[%d,\"%s\",\"%s\",\"%s\",%u],", g.reachable, js(g.owner), js(g.label), g.address, g.since_s);
    be.camera_mode(be.ctx);
    printf("\"camera\":[%u,%u]}\n", atomic_load(&page.camera_mode), atomic_load(&page.show));
    ui_real_close(&r);
    return 0;
}
