/* SPDX-License-Identifier: MIT
 * Synthetic sink only; never opens any sound device or file. */
#include "cammy_audio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "%d: %s\n", __LINE__, #x); exit(1); } } while (0)
struct sink { unsigned plays, stops; int play_result, stop_result; const uint8_t *held; uint32_t bytes; };
static int play(void *ctx, uint64_t job, const uint8_t *pcm, uint32_t bytes)
{
    struct sink *s = ctx;
    CHECK(job != 0); CHECK(bytes > 0 && bytes <= CA_MAX_BYTES);
    ++s->plays;
    if (s->play_result != -1) { s->held = pcm; s->bytes = bytes; }
    return s->play_result;
}
static int stop(void *ctx, uint64_t job)
{
    struct sink *s = ctx;
    CHECK(job != 0); ++s->stops; return s->stop_result;
}
static struct ca_core c;
static struct sink s;
static struct ca_binding id = { .instance = 7, .boot = {1} };
static struct ca_binding owner = { .instance = 7, .controller = 9, .generation = 1, .boot = {1} };
static uint8_t samples[CA_MAX_BYTES + 2u];
static struct ca_backend backend = { CA_BACKEND_ABI, sizeof(struct ca_backend), 1, &s, play, stop };
static void setup(void)
{
    memset(&s, 0, sizeof s); memset(samples, 0x5a, sizeof samples);
    CHECK(ca_init(&c, &id, &backend) == CA_OK);
    CHECK(ca_bind(&c, &owner, 100, 10000) == CA_OK);
    CHECK(!ca_physical_available(&c));
}
static void queued(void) { CHECK(ca_enqueue(&c, &owner, 1, 100, 9000, samples, 640) == CA_OK); }
static void playing(void) { queued(); ca_step(&c, 100); CHECK(c.phase == CA_PLAYING); CHECK(s.plays == 1); }
static void clean(void)
{
    /* Backend is quiescent BEFORE the actor delivers its completion. */
    s.held = NULL; ca_event(&c, c.job, c.phase == CA_PLAYING ? CA_FINISHED : CA_QUIESCENT);
    CHECK(ca_can_destroy(&c)); CHECK(c.bytes == 0); CHECK(c.pcm[0] == 0);
}
int main(int argc, char **argv)
{
    unsigned which; struct ca_binding other = owner; uint64_t old;
    if (argc != 2) return 2;
    which = (unsigned)strtoul(argv[1], NULL, 10); setup();
    switch (which) {
    case 0: /* normal completion, immutable copy, no repeat dispatch */
        playing(); samples[0] = 0; CHECK(s.held[0] == 0x5a);
        ca_step(&c, 101); CHECK(s.plays == 1); clean(); CHECK(c.reason == CA_COMPLETE); break;
    case 1: /* cancel BEFORE dispatch must never call play */
        queued(); CHECK(ca_cancel(&c, &owner, 101) == CA_OK);
        ca_step(&c, 101); CHECK(s.plays == 0 && s.stops == 0); CHECK(ca_can_destroy(&c)); break;
    case 2: /* lease/deadline revalidation before dispatch */
        queued(); ca_step(&c, 10000); CHECK(!c.bound && s.plays == 0); CHECK(ca_can_destroy(&c)); break;
    case 3: /* request deadline expires first */
        queued(); ca_step(&c, 9000); CHECK(s.plays == 0 && c.reason == CA_DEADLINE); break;
    case 4: /* accepted stop is NOT cleanup; lease takeover blocked */
        playing(); CHECK(ca_cancel(&c, &owner, 101) == CA_OK);
        CHECK(c.phase == CA_STOPPING && !ca_can_destroy(&c)); CHECK(s.held[0] == 0x5a);
        ca_owner_lost(&c, 102); other.generation = 2;
        CHECK(ca_bind(&c, &other, 102, 9000) == CA_BUSY);
        ca_step(&c, 301); CHECK(c.phase == CA_UNCERTAIN); CHECK(s.stops == 1);
        clean(); CHECK(ca_bind(&c, &other, 302, 9000) == CA_OK); break;
    case 5: /* cleanup refusal still pins PCM until late completion */
        playing(); s.stop_result = -1; ca_owner_lost(&c, 101);
        CHECK(c.phase == CA_UNCERTAIN && !ca_can_destroy(&c)); CHECK(s.held[639] == 0x5a);
        ca_step(&c, 90000); CHECK(s.stops == 1 && !ca_can_destroy(&c)); clean(); break;
    case 6: /* exact no-effect refusal */
        s.play_result = -1; queued(); ca_step(&c, 101);
        CHECK(ca_can_destroy(&c) && c.reason == CA_SUBMIT_REFUSED && s.held == NULL); break;
    case 7: /* undocumented return must not free potentially referenced bytes */
        s.play_result = 1; queued(); ca_step(&c, 100);
        CHECK(s.plays == 1 && s.stops == 1 && c.phase == CA_STOPPING);
        CHECK(!ca_can_destroy(&c) && s.held[0] == 0x5a); clean(); break;
    case 8: /* wrong owner, boot, instance and stale replay */
        other.controller++; CHECK(ca_enqueue(&c, &other, 1, 100, 1000, samples, 640) == CA_STALE);
        other = owner; other.boot[0]++; CHECK(ca_cancel(&c, &other, 100) == CA_STALE);
        other = owner; other.instance++; CHECK(ca_cancel(&c, &other, 100) == CA_STALE);
        queued(); CHECK(ca_enqueue(&c, &owner, 1, 100, 1000, samples, 640) == CA_STALE);
        CHECK(ca_enqueue(&c, &owner, 2, 100, 1000, samples, 640) == CA_BUSY);
        CHECK(ca_cancel(&c, &owner, 100) == CA_OK);
        CHECK(ca_enqueue(&c, &owner, 2, 100, 1000, samples, 640) == CA_STALE); break;
    case 9: /* PCM bounds; request ids consumed on invalid jobs */
        CHECK(ca_enqueue(&c, &owner, 1, 100, 1000, samples, CA_MAX_BYTES+2) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 2, 100, 1000, samples, 3) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 3, 100, 1000, samples, 0) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 4, 100, 1000, NULL, 640) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 5, 100, 100, samples, 640) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 6, 100, 60101, samples, 640) == CA_BAD_REQUEST);
        CHECK(ca_enqueue(&c, &owner, 7, 100, 9000, samples, CA_MAX_BYTES) == CA_OK);
        ca_step(&c, 100); CHECK(c.job_deadline == 2350); clean(); break;
    case 10: /* stale completion cannot finish next job; invalid queued event */
        queued(); old = c.job; ca_event(&c, old, CA_FINISHED); CHECK(c.phase == CA_QUEUED);
        ca_step(&c, 100); clean();
        CHECK(ca_enqueue(&c, &owner, 2, 100, 9000, samples, 640) == CA_OK); ca_step(&c, 100);
        ca_event(&c, old, CA_FINISHED); CHECK(c.phase == CA_PLAYING);
        ca_event(&c, c.job, CA_QUIESCENT); CHECK(c.phase == CA_PLAYING);
        CHECK(c.stale_events == 3); clean(); break;
    case 11: /* automatic finite end deadline; missing finish -> cleanup, no retry */
        playing(); ca_step(&c, 370); CHECK(c.phase == CA_STOPPING && s.stops == 1);
        ca_step(&c, 570); CHECK(c.phase == CA_UNCERTAIN); CHECK(!ca_can_destroy(&c));
        s.held = NULL; ca_event(&c, c.job, CA_FINISHED); CHECK(ca_can_destroy(&c)); break;
    case 12: /* owner expiry while playing; cannot renew expired generation */
        playing(); CHECK(ca_renew(&c, &owner, 101, 110) == CA_OK); ca_step(&c, 110);
        CHECK(!c.bound && s.stops == 1); CHECK(ca_renew(&c, &owner, 110, 200) == CA_STALE);
        clean(); CHECK(ca_bind(&c, &owner, 111, 1000) == CA_STALE);
        other.generation++; CHECK(ca_bind(&c, &other, 111, 1000) == CA_OK); break;
    case 13: /* no native or fake-labelled-as-native dispatch */
        ca_owner_lost(&c, 100); backend.synthetic = 0;
        CHECK(ca_native_backend() == NULL); CHECK(ca_init(&c, &id, &backend) == CA_UNAVAILABLE);
        CHECK(ca_bind(&c, &owner, 100, 1000) == CA_UNAVAILABLE); CHECK(!ca_physical_available(&c)); break;
    case 14: /* overflow and backwards clocks fail closed */
        CHECK(ca_enqueue(&c, &owner, 1, 99, 1000, samples, 640) == CA_BAD_REQUEST);
        c.sequence = UINT64_MAX;
        CHECK(ca_enqueue(&c, &owner, 2, 100, 1000, samples, 640) == CA_EXHAUSTED);
        CHECK(c.exhausted && ca_can_destroy(&c)); break;
    case 15: /* disconnect prevents queued start; no body I/O */
        queued(); ca_owner_lost(&c, 100); ca_step(&c, 101);
        CHECK(!c.bound && ca_can_destroy(&c) && !s.plays && !s.stops); break;
    case 16: /* adapter error requests cleanup without claiming it */
        playing();ca_backend_fault(&c,101);
        CHECK(c.phase==CA_STOPPING && !ca_can_destroy(&c) && s.stops==1);
        s.held=NULL;ca_event(&c,c.job,CA_FAILED_QUIESCENT);
        CHECK(ca_can_destroy(&c) && c.reason==CA_BACKEND_ERROR);break;
    case 17: /* failure event cannot cancel work that never reached backend */
        queued();ca_event(&c,c.job,CA_FAILED_QUIESCENT);CHECK(c.phase==CA_QUEUED);
        CHECK(ca_cancel(&c,&owner,100)==CA_OK);CHECK(s.plays==0);break;
    default: return 2;
    }
    CHECK(!ca_physical_available(&c));
    printf("{\"scenario\":%u,\"checks\":%u,\"synthetic\":true,\"physical_available\":false}\n", which, checks);
    return 0;
}
