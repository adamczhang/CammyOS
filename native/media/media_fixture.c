/* SPDX-License-Identifier: MIT
 * Scenario fixture for the WP3 owned-media core (tests only).
 * Reads one command per stdin line and prints one result line per command.
 * The clock is injected (`now`/`adv`); the fake backend's owner context is
 * played by `complete`/`event` commands, and `stress` posts from real threads.
 * No socket, device, network or vendor code is involved.
 */
#define _POSIX_C_SOURCE 200809L
#include "cammy_media.h"
#include "media_backend_fake.h"

#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t g_now = 1000;
static struct cm_core g_core;
static struct cm_fake g_fake;
static int g_inited;
static const uint8_t BOOT_CUR[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
static const uint8_t BOOT_OTHER[16] = {0xff, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};

static uint64_t fix_clock(void *ctx)
{
    (void)ctx;
    return g_now;
}

/* key=value lookup among the remaining tokens. */
static const char *arg(int n, char **v, const char *key)
{
    size_t k = strlen(key);
    for (int i = 0; i < n; i++)
        if (!strncmp(v[i], key, k) && v[i][k] == '=') return v[i] + k + 1;
    return NULL;
}

static uint64_t num(const char *s, uint64_t dflt)
{
    return s ? strtoull(s, NULL, 0) : dflt;
}

static uint64_t rel(const char *s, uint64_t dflt)
{
    if (!s) return dflt;
    if (s[0] == '+') return g_now + strtoull(s + 1, NULL, 0);
    if (s[0] == '-') return g_now - strtoull(s + 1, NULL, 0);
    return strtoull(s, NULL, 0);
}

static void print_flags(unsigned f)
{
    static const char *const n[] = {"stop_requested", "start_unresolved", "cleanup_unresolved", "listener",
                                    "sdp", "frame_fresh", "synthetic", "last_cleanup_done", "cleanup_overdue",
                                    "video_physical"};
    int first = 1;
    printf(" flags=");
    for (unsigned i = 0; i < sizeof n / sizeof n[0]; i++)
        if (f & (1u << i)) { printf("%s%s", first ? "" : ",", n[i]); first = 0; }
    if (first) printf("-");
}

static const char *bk_name(unsigned v) { return v == 1 ? "fake" : v == 2 ? "native" : "none"; }
static const char *bs_name(unsigned v) { return v == 1 ? "ready" : v == 2 ? "error" : "unavailable"; }
static const char *br_name(unsigned v)
{
    static const char *const n[] = {"none", "fake_backend", "native_backend_absent", "table_rejected",
                                    "init_failed", "serialization_unproven"};
    return v < 6 ? n[v] : "unknown";
}

static void cmd_status(void)
{
    struct cm_status s;
    cm_get_status(&g_core, &s);
    const struct cm_counters *k = &s.counters;
    printf("status bk=%s bs=%s br=%s video=%s vr=%s owner=%s oreason=%s gen=%llu ctl=%llu phase=%s end=%s "
           "sess=%llu last=%llu attempts=%u now=%llu",
           bk_name(s.backend_kind), bs_name(s.backend_state), br_name(s.backend_reason), bs_name(s.video_state),
           br_name(s.video_reason), cm_owner_name(s.owner_state), cm_reason_name(s.owner_reason),
           (unsigned long long)s.owner_generation, (unsigned long long)s.owner_controller, cm_phase_name(s.phase),
           cm_reason_name(s.last_end_reason), (unsigned long long)s.session_id,
           (unsigned long long)s.last_session_id, s.cleanup_attempts, (unsigned long long)s.now_ms);
    print_flags(s.flags);
    printf(" admitted=%u rejected=%u replays=%u stale_gen=%u dispatched=%u cancelled=%u stale_dispatch=%u "
           "start_ok=%u start_failed=%u start_timeouts=%u late=%u stale_comp=%u cleanups=%u cleanup_ok=%u "
           "cleanup_fail=%u cleanup_timeouts=%u ev_applied=%u ev_ignored=%u overflow=%u granted=%u lost=%u\n",
           k->admitted, k->rejected, k->replays, k->stale_generation, k->dispatched_starts,
           k->cancelled_before_dispatch, k->stale_at_dispatch, k->start_ok, k->start_failed, k->start_timeouts,
           k->late_completions, k->stale_completions, k->cleanups_dispatched, k->cleanups_ok, k->cleanup_failures,
           k->cleanup_timeouts, k->events_applied, k->events_ignored, k->ring_overflows, k->owners_granted,
           k->owners_lost);
}

static int cmd_init(int n, char **v)
{
    struct cm_config cfg;
    memset(&cfg, 0, sizeof cfg);
    const char *kind = arg(n, v, "kind");
    cfg.instance = num(arg(n, v, "inst"), 0x1111);
    memcpy(cfg.boot_id, BOOT_CUR, 16);
    cfg.queue_cap = (uint32_t)num(arg(n, v, "queue"), 4);
    cfg.start_timeout_ms = (uint32_t)num(arg(n, v, "start_to"), 3000);
    cfg.cleanup_timeout_ms = (uint32_t)num(arg(n, v, "cleanup_to"), 3000);
    cfg.cleanup_retry_ms = (uint32_t)num(arg(n, v, "retry"), 500);
    cfg.cleanup_max_attempts = (uint32_t)num(arg(n, v, "attempts"), 3);
    cfg.frame_fresh_ms = (uint32_t)num(arg(n, v, "fresh"), 2000);
    if (g_inited) cm_core_fini(&g_core);
    memset(&g_fake, 0, sizeof g_fake);
    g_fake.fail_init = arg(n, v, "fake_init") != NULL;
    const struct cm_backend *be = NULL;
    struct cm_backend bad;
    if (!kind || !strcmp(kind, "fake")) {
        cfg.requested_kind = CM_BK_FAKE;
        be = cm_fake_backend(&g_fake);
    } else if (!strcmp(kind, "native")) {
        cfg.requested_kind = CM_BK_NATIVE;
        be = cm_native_media_backend();
    } else if (!strcmp(kind, "native_table")) {
        /* A well-formed table labelled native: the core must still refuse it. */
        cfg.requested_kind = CM_BK_NATIVE;
        bad = *cm_fake_backend(&g_fake);
        bad.kind = CM_BK_NATIVE;
        be = &bad;
    } else if (!strcmp(kind, "mislabel")) {
        cfg.requested_kind = CM_BK_NATIVE;   /* fake table offered for a native request */
        be = cm_fake_backend(&g_fake);
    } else if (!strcmp(kind, "badabi")) {
        cfg.requested_kind = CM_BK_FAKE;
        bad = *cm_fake_backend(&g_fake);
        bad.abi_version = 99;
        be = &bad;
    } else {
        printf("error bad-kind\n");
        return 2;
    }
    int rc = cm_core_init(&g_core, &cfg, be, fix_clock, NULL);
    g_inited = rc == 0;
    printf("init rc=%d\n", rc);
    return 0;
}

static int cmd_req(const char *kname, int n, char **v)
{
    static const char *const kinds[] = {"", "acquire", "renew", "release", "start", "stop", "status"};
    struct cm_request rq;
    memset(&rq, 0, sizeof rq);
    for (unsigned k = 1; k < 7; k++)
        if (!strcmp(kname, kinds[k])) rq.kind = (uint16_t)k;
    if (!rq.kind) rq.kind = (uint16_t)num(kname, 0);
    rq.controller_id = num(arg(n, v, "ctl"), 0);
    rq.request_id = (uint32_t)num(arg(n, v, "rid"), 0);
    const char *inst = arg(n, v, "inst");
    rq.expect_instance = (!inst || !strcmp(inst, "cur")) ? g_core.cfg.instance : num(inst, 0);
    const char *boot = arg(n, v, "boot");
    if (!boot || !strcmp(boot, "cur")) memcpy(rq.boot_id, BOOT_CUR, 16);
    else if (!strcmp(boot, "other")) memcpy(rq.boot_id, BOOT_OTHER, 16);
    const char *gen = arg(n, v, "gen");
    rq.owner_generation = (gen && !strcmp(gen, "cur")) ? g_core.owner_gen : num(gen, 0);
    rq.deadline_mono_ms = rel(arg(n, v, "dl"), g_now + 1000);
    rq.lease_ms = (uint32_t)num(arg(n, v, "lease"), rq.kind == CM_REQ_ACQUIRE || rq.kind == CM_REQ_RENEW ? 5000 : 0);
    rq.profile = (uint16_t)num(arg(n, v, "profile"), rq.kind == CM_REQ_START ? CM_PROFILE_PREVIEW_MAIN : 0);
    rq.reserved = (uint32_t)num(arg(n, v, "res"), 0);
    struct cm_reply rp;
    cm_submit(&g_core, &rq, &rp);
    static const char *const fields[] = {"none", "kind", "profile", "request_id", "controller", "instance",
                                         "boot", "generation", "deadline", "lease", "reserved"};
    printf("reply kind=%s st=%s field=%s phase=%s owner=%s gen=%llu sess=%llu lease_exp=%llu\n", kname,
           cm_status_name(rp.status), rp.field < 11 ? fields[rp.field] : "?", cm_phase_name(rp.phase),
           cm_owner_name(rp.owner_state), (unsigned long long)rp.owner_generation,
           (unsigned long long)rp.session_id, (unsigned long long)rp.lease_expiry_ms);
    return 0;
}

static uint16_t result_of(const char *s)
{
    if (!s || !strcmp(s, "ok")) return CM_RES_OK;
    if (!strcmp(s, "fail")) return CM_RES_FAILED;
    if (!strcmp(s, "noeffect")) return CM_RES_NO_EFFECT;
    return 77;   /* bogus value: the core must treat it as failure */
}

static uint64_t tok_of(const char *s, uint64_t last, uint64_t prev)
{
    if (!s || !strcmp(s, "last")) return last;
    if (!strcmp(s, "prev")) return prev;
    return strtoull(s, NULL, 0);
}

struct stress_arg { unsigned per; unsigned ok, fail, refused; uint64_t token; };
static _Atomic unsigned g_done;

static void *stress_thread(void *p)
{
    struct stress_arg *a = p;
    for (unsigned i = 0; i < a->per; i++) {
        unsigned tries = 0;
        while (cm_fake_event(&g_fake, a->token, CM_EV_FRAME, g_now, 0) != 0) {   /* bounded producer retry */
            a->refused++;
            if (++tries == 200) break;
            sched_yield();
        }
        if (tries == 200) a->fail++;
        else a->ok++;
    }
    atomic_fetch_add(&g_done, 1u);
    return NULL;
}

static void cmd_stress(int n, char **v)
{
    unsigned threads = (unsigned)num(arg(n, v, "threads"), 4), per = (unsigned)num(arg(n, v, "per"), 2000);
    if (threads > 16) threads = 16;
    pthread_t t[16];
    struct stress_arg a[16];
    struct cm_status before, after;
    cm_get_status(&g_core, &before);
    atomic_store(&g_done, 0u);
    for (unsigned i = 0; i < threads; i++) {
        a[i] = (struct stress_arg){per, 0, 0, 0, i % 2 ? g_fake.last_start_token : 0xdead0000u + i};
        if (pthread_create(&t[i], NULL, stress_thread, &a[i]) != 0) { printf("error pthread\n"); exit(2); }
    }
    while (atomic_load(&g_done) < threads) cm_step(&g_core);   /* consume concurrently */
    for (unsigned i = 0; i < threads; i++) pthread_join(t[i], NULL);
    for (unsigned i = 0; i < 64; i++) cm_step(&g_core);
    cm_get_status(&g_core, &after);
    unsigned ok = 0, fail = 0, refused = 0;
    for (unsigned i = 0; i < threads; i++) { ok += a[i].ok; fail += a[i].fail; refused += a[i].refused; }
    unsigned consumed = (after.counters.events_applied - before.counters.events_applied) +
                        (after.counters.events_ignored - before.counters.events_ignored);
    unsigned overflow = after.counters.ring_overflows - before.counters.ring_overflows;
    printf("stress posted=%u gave_up=%u refused=%u overflow=%u consumed=%u total=%u balanced=%d\n", ok, fail, refused,
           overflow, consumed, threads * per, ok == consumed && refused == overflow && ok + fail == threads * per);
}

int main(void)
{
    char line[512];
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(line, sizeof line, stdin)) {
        char *v[24];
        int n = 0;
        for (char *t = strtok(line, " \t\r\n"); t && n < 24; t = strtok(NULL, " \t\r\n")) v[n++] = t;
        if (!n || v[0][0] == '#') continue;
        if (!strcmp(v[0], "init")) { if (cmd_init(n, v)) return 2; continue; }
        if (!g_inited) { printf("error not-initialised\n"); return 2; }
        if (!strcmp(v[0], "now")) { g_now = num(n > 1 ? v[1] : NULL, g_now); printf("now %llu\n", (unsigned long long)g_now); }
        else if (!strcmp(v[0], "adv")) { g_now += num(n > 1 ? v[1] : NULL, 0); printf("now %llu\n", (unsigned long long)g_now); }
        else if (!strcmp(v[0], "step")) {
            uint64_t k = num(n > 1 ? v[1] : NULL, 1);
            for (uint64_t i = 0; i < k; i++) cm_step(&g_core);
            printf("step phase=%s starts=%u cleanups=%u start_tok=%llu cleanup_tok=%llu\n", cm_phase_name(g_core.phase),
                   g_fake.starts_submitted, g_fake.cleanups_submitted, (unsigned long long)g_fake.last_start_token,
                   (unsigned long long)g_fake.last_cleanup_token);
        } else if (!strcmp(v[0], "status")) cmd_status();
        else if (!strcmp(v[0], "lost")) { cm_controller_lost(&g_core, num(arg(n, v, "ctl"), 0)); printf("lost ok\n"); }
        else if (!strcmp(v[0], "fake")) {
            const char *s;
            if ((s = arg(n, v, "refuse_start"))) g_fake.refuse_start = atoi(s);
            if ((s = arg(n, v, "refuse_cleanup"))) g_fake.refuse_cleanup = atoi(s);
            printf("fake running=%d partial=%d effects_started=%u effects_cleaned=%u starts=%u cleanups=%u\n",
                   g_fake.running, g_fake.partial, g_fake.effects_started, g_fake.effects_cleaned,
                   g_fake.starts_submitted, g_fake.cleanups_submitted);
        } else if (!strcmp(v[0], "complete") && n > 1) {
            int rc;
            if (!strcmp(v[1], "start"))
                rc = cm_fake_complete_start(&g_fake, tok_of(arg(n, v, "tok"), g_fake.last_start_token, g_fake.prev_start_token),
                                            result_of(n > 2 && !strchr(v[2], '=') ? v[2] : NULL));
            else
                rc = cm_fake_complete_cleanup(&g_fake, tok_of(arg(n, v, "tok"), g_fake.last_cleanup_token, 0),
                                              result_of(n > 2 && !strchr(v[2], '=') ? v[2] : NULL));
            printf("posted rc=%d\n", rc);
        } else if (!strcmp(v[0], "event") && n > 1) {
            uint32_t ev = !strcmp(v[1], "listener") ? CM_EV_LISTENER : !strcmp(v[1], "sdp") ? CM_EV_SDP : CM_EV_FRAME;
            int rc = cm_fake_event(&g_fake, tok_of(arg(n, v, "tok"), g_fake.last_start_token, g_fake.prev_start_token), ev,
                                   rel(arg(n, v, "at"), g_now), (uint32_t)num(arg(n, v, "synthetic"), 1));
            printf("posted rc=%d\n", rc);
        } else if (!strcmp(v[0], "flood")) {
            unsigned k = (unsigned)num(n > 1 ? v[1] : NULL, 32), ok = 0;
            for (unsigned i = 0; i < k; i++) ok += cm_fake_event(&g_fake, 0xbad, CM_EV_FRAME, g_now, 1) == 0;
            printf("flood posted=%u dropped=%u\n", ok, k - ok);
        } else if (!strcmp(v[0], "stress")) cmd_stress(n, v);
        else if (!strcmp(v[0], "acquire") || !strcmp(v[0], "renew") || !strcmp(v[0], "release") ||
                 !strcmp(v[0], "start") || !strcmp(v[0], "stop") || !strcmp(v[0], "req_status") ||
                 !strcmp(v[0], "rawkind")) {
            const char *kname = v[0];
            if (!strcmp(v[0], "req_status")) kname = "status";
            if (!strcmp(v[0], "rawkind")) kname = arg(n, v, "k") ? arg(n, v, "k") : "0";
            cmd_req(kname, n, v);
        } else { printf("error unknown-command %s\n", v[0]); return 2; }
    }
    if (g_inited) cm_core_fini(&g_core);
    return 0;
}
