/* SPDX-License-Identifier: MIT. cammy-ui's loading gate: what the boot
 * Loading screen shows, decided only from this boot's canonical records.
 *
 * Picking CammyOS on the chooser starts the face service's gate (face_service.h): an opaque
 * Loading screen instead of the face or the live view. cammy-ui starts right after the
 * supervisor verifies its payload, reads the records below about once a second while the
 * gate holds, draws what is still waiting, and reports CUI_BOOT_READY through its page once
 * they show this boot ready. Health's GUI sample is the face service's own tick, which
 * runs while the gate holds.
 *
 * Records (all must name this boot; anything from another boot is stale and never counts):
 *   /etc/cammy/startup.pending          phase=<supervisor phase>, boot_id=<boot>
 *   /etc/cammy/runtime-health-fault     a health fault latched for boot_id=<boot>
 *   /tmp/cammy-runtime/state            HOME_STATION, AP_CammyOS, DRIVER_UNAVAILABLE, RADIO_FAULT, RADIO_HUNG, ...
 *                                       (AP_CammyOS counts only with hostapd and udhcpd
 *                                       running, as release health checks it)
 *   /tmp/cammy-runtime/portal.pid       the portal process (comm cammy-portal)
 *   /tmp/cammy-runtime/release-health/display-ready   release, boot and network
 *   /tmp/cammy-runtime/media-prepare/state      optional media preparation
 *
 * Ready requires the short display health check, a live portal, and the checked network.
 * A media job already started by Control Center must settle. The startup guard can
 * remain idle while the longer release acceptance window runs in the background.
 *
 * Failed = a fault for this boot (the guard's runtime_health_fault phase or the fault
 * record), the Wi-Fi driver missing, or the supervisor gone before ready. The screen says so
 * and offers the stock camera for this boot; the kept guard makes the next start stock. */
#ifndef CAMMY_UI_BOOT_H
#define CAMMY_UI_BOOT_H
#include <stdio.h>
#include <string.h>
#define UB_BOOT_ID 40
enum ub_stage { UB_STOCK, UB_RADIO, UB_WIFI, UB_HOTSPOT, UB_SERVICES, UB_READY, UB_FAILED };
enum ub_guard { UBG_ABSENT, UBG_CURRENT, UBG_STALE }; /* STALE: another boot's or malformed */
struct ub_input {
    int guard;               /* enum ub_guard */
    char phase[24];          /* guard phase when UBG_CURRENT */
    char net[24];            /* runtime state word */
    int ap;                  /* AP_CammyOS only: hostapd and udhcpd both run (health's check) */
    int portal;              /* 1: the portal process runs */
    int supervisor;          /* 1 runs, 0 gone, -1 unknown (not started by the supervisor) */
    int services_ready;      /* display-ready names this boot, release and network */
    int fault;               /* runtime-health-fault names this boot */
    char fault_reason[40];   /* its reason= */
    char media[24];          /* media-prepare/state, empty when none */
};
struct ub_view {
    int stage;               /* enum ub_stage */
    int step;                /* screen row reached: 0 camera startup, 1 Wi-Fi, 2 services */
    int progress;            /* progress bar fill, based on completed startup milestones */
    char wait[48];           /* what is waiting now */
    char reason[48];         /* failure code, or empty */
    char media[48];          /* optional media note, or empty */
};
/* Value of "key=" at the start of a line of text, or -1. Values are [A-Za-z0-9_.-] only. */
static inline int ub_field(const char *text, const char *key, char *out, size_t n) {
    size_t k = strlen(key);
    for (const char *p = text; p; ) {
        if (!strncmp(p, key, k) && p[k] == '=') {
            const char *v = p + k + 1;
            size_t m = 0;
            for (; v[m] && v[m] != '\n'; m++) {
                char c = v[m];
                if (m + 1 >= n || !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                                    c == '_' || c == '-' || c == '.')) return -1;
                out[m] = c;
            }
            out[m] = 0;
            return m ? 0 : -1;
        }
        p = strchr(p, '\n');
        if (p) p++;
    }
    return -1;
}
/* Tail of the release-bound latch the RC06 startup writes at idle_ready (since 56d9ae6):
 * "\nrelease_id=<24 hex>\ncandidate_md5=<32 hex>" plus an optional newline. */
static inline int ub_hex_line(const char **t, const char *key, size_t len) {
    size_t k = strlen(key);
    if (strncmp(*t, key, k)) return 0;
    const char *v = *t + k;
    if (strspn(v, "0123456789abcdef") != len) return 0;
    *t = v + len;
    return 1;
}
static inline int ub_release_tail(const char *t) {
    return ub_hex_line(&t, "\nrelease_id=", 24) && ub_hex_line(&t, "\ncandidate_md5=", 32) && (!*t || !strcmp(t, "\n"));
}
/* The guard: exactly "phase=<p>\nboot_id=<b>" plus an optional newline, as the supervisor's
 * phase() and health write it, or plus the release-bound tail above, naming this boot.
 * Rejecting the tail held the loading screen until health cleared the guard 120 s later
 * (2026-11-24: services ready at 90 s, menu at 204 s). */
static inline void ub_guard(const char *text, const char *boot, struct ub_input *in) {
    char b[UB_BOOT_ID], expect[80];
    in->guard = UBG_STALE;
    if (ub_field(text, "phase", in->phase, sizeof in->phase) || ub_field(text, "boot_id", b, sizeof b)) { in->phase[0] = 0; return; }
    int e = snprintf(expect, sizeof expect, "phase=%s\nboot_id=%s", in->phase, b);
    if (e < 0 || (size_t)e >= sizeof expect || strncmp(text, expect, (size_t)e) || (text[e] && strcmp(text + e, "\n") && !ub_release_tail(text + e)) ||
        !boot[0] || strcmp(b, boot)) { in->phase[0] = 0; return; }
    in->guard = UBG_CURRENT;
}
static inline int ub_is(const char *a, const char *b) { return !strcmp(a, b); }
static inline int ub_display_ready(const char *text, const char *boot, const char *release, const char *network) {
    char expect[128];
    if (!boot[0] || strlen(release) != 24 || strspn(release, "0123456789abcdef") != 24 ||
        (!ub_is(network, "HOME_STATION") && !ub_is(network, "AP_CammyOS"))) return 0;
    int n = snprintf(expect, sizeof expect, "release_id=%s\nboot=%s\nnetwork=%s\n", release, boot, network);
    return n > 0 && (size_t)n < sizeof expect && ub_is(text, expect);
}
static inline void ub_eval(const struct ub_input *in, struct ub_view *v) {
    memset(v, 0, sizeof *v);
    const char *m = in->media;
    if (ub_is(m, "queued") || ub_is(m, "initializing")) snprintf(v->media, sizeof v->media, "Camera media is preparing.");
    else if (ub_is(m, "ready")) snprintf(v->media, sizeof v->media, "Camera media is ready.");
    else if (ub_is(m, "refused")) snprintf(v->media, sizeof v->media, "Camera media was refused (optional).");
    int cur = in->guard == UBG_CURRENT;
    const char *p = cur ? in->phase : "";
    if (in->fault || (cur && ub_is(p, "runtime_health_fault"))) {
        v->stage = UB_FAILED; v->step = 2;
        snprintf(v->reason, sizeof v->reason, "%s", in->fault && in->fault_reason[0] ? in->fault_reason : "runtime-health-fault");
        snprintf(v->wait, sizeof v->wait, "A startup check failed.");
        return;
    }
    if (ub_is(in->net, "DRIVER_UNAVAILABLE")) {
        v->stage = UB_FAILED; v->step = 1;
        snprintf(v->reason, sizeof v->reason, "wifi-driver-unavailable");
        snprintf(v->wait, sizeof v->wait, "The Wi-Fi driver did not load.");
        return;
    }
    /* Startup saw a kernel oops or a hung network command (boot fbaa812c). */
    if (ub_is(in->net, "RADIO_FAULT") || ub_is(in->net, "RADIO_HUNG")) {
        v->stage = UB_FAILED; v->step = 1;
        snprintf(v->reason, sizeof v->reason, "wifi-driver-fault");
        snprintf(v->wait, sizeof v->wait, "The Wi-Fi driver failed.");
        return;
    }
    int net = ub_is(in->net, "HOME_STATION") || (ub_is(in->net, "AP_CammyOS") && in->ap);
    int media_pending = m[0] && !ub_is(m, "ready") && !ub_is(m, "refused");
    int idle = cur && ub_is(p, "idle_ready");
    if (in->services_ready && (in->guard == UBG_ABSENT || idle) && net && in->portal && in->supervisor && !media_pending) {
        v->stage = UB_READY; v->progress = 100; return;
    }
    if (in->services_ready || idle || (cur && ub_is(p, "media_prepare"))) {
        v->stage = net ? UB_SERVICES : UB_WIFI;
        snprintf(v->wait, sizeof v->wait, !net ? "Waiting for Wi-Fi" :
                 in->portal ? "Checking Cammy's services" : "Waiting for Cammy's services");
    } else if (cur && (ub_is(p, "launch") || ub_is(p, "payload") || ub_is(p, "stock_settle"))) {
        v->stage = UB_STOCK; snprintf(v->wait, sizeof v->wait, "The camera is finishing its own startup");
    } else if (cur && ub_is(p, "radio_load")) {
        v->stage = UB_RADIO; snprintf(v->wait, sizeof v->wait, "Loading Wi-Fi");
    } else if (cur && ub_is(p, "station_setup")) {
        v->stage = UB_WIFI; snprintf(v->wait, sizeof v->wait, "Joining home Wi-Fi");
    } else if (cur && ub_is(p, "dhcp")) {
        v->stage = UB_WIFI; snprintf(v->wait, sizeof v->wait, "Getting an address on home Wi-Fi");
    } else if (cur && ub_is(p, "ap_fallback")) {
        v->stage = UB_HOTSPOT; snprintf(v->wait, sizeof v->wait, "Home Wi-Fi not found: starting Cammy's Wi-Fi");
    } else if (cur && (ub_is(p, "portal") || ub_is(p, "menu"))) {
        v->stage = UB_SERVICES; snprintf(v->wait, sizeof v->wait, "Starting Cammy's services");
    } else {
        /* No record for this boot yet (or only a stale one). */
        v->stage = UB_STOCK; snprintf(v->wait, sizeof v->wait, "Waiting for CammyOS startup");
    }
    v->step = v->stage <= UB_STOCK ? 0 : v->stage <= UB_HOTSPOT ? 1 : 2;
    static const int milestone[] = {8, 26, 42, 56, 72};
    v->progress = milestone[v->stage];
    if (ub_is(p, "dhcp")) v->progress = 54;
    if (v->stage == UB_SERVICES && in->portal) v->progress = media_pending ? 82 : 93;
    if (!in->supervisor) {
        v->stage = UB_FAILED;
        snprintf(v->reason, sizeof v->reason, "startup-stopped%s%s", p[0] ? "-" : "", p);
        snprintf(v->wait, sizeof v->wait, "CammyOS startup stopped.");
    }
}
#endif
