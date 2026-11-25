/* SPDX-License-Identifier: MIT
 * Explicit fake media backend (tests only). See media_backend_fake.h.
 */
#include "media_backend_fake.h"

#include <string.h>

static int fake_init(void *ctx, struct cm_core *core)
{
    struct cm_fake *f = ctx;
    if (f->fail_init) return -1;
    f->core = core;
    return 0;
}

/* Nonblocking: records the request; the fixture plays the owner context later. */
static int fake_submit_start(void *ctx, uint64_t token, const struct cm_start_params *p)
{
    struct cm_fake *f = ctx;
    if (f->refuse_start || p->profile != CM_PROFILE_PREVIEW_MAIN) return -1;
    f->prev_start_token = f->last_start_token;
    f->last_start_token = token;
    f->starts_submitted++;
    return 0;
}

static int fake_submit_cleanup(void *ctx, uint64_t token, uint64_t session_token)
{
    struct cm_fake *f = ctx;
    if (f->refuse_cleanup) return -1;
    f->last_cleanup_token = token;
    f->last_cleanup_session = session_token;
    f->cleanups_submitted++;
    return 0;
}

static void fake_fini(void *ctx)
{
    struct cm_fake *f = ctx;
    f->core = NULL;
}

const struct cm_backend *cm_fake_backend(struct cm_fake *f)
{
    struct cm_backend t = {CM_BACKEND_ABI, sizeof(struct cm_backend), CM_BK_FAKE, 0, "fake-media", f,
                           fake_init, fake_submit_start, fake_submit_cleanup, fake_fini};
    f->table = t;
    return &f->table;
}

int cm_fake_complete_start(struct cm_fake *f, uint64_t token, uint16_t result)
{
    if (result == CM_RES_OK) { f->running = 1; f->effects_started++; }
    else if (result == CM_RES_FAILED) f->partial = 1;
    struct cm_completion m;
    memset(&m, 0, sizeof m);
    m.type = CM_COMP_START;
    m.result = result;
    m.token = token;
    return f->core ? cm_post(f->core, &m) : -1;
}

int cm_fake_complete_cleanup(struct cm_fake *f, uint64_t token, uint16_t result)
{
    if (result == CM_RES_OK) {
        if (f->running || f->partial) f->effects_cleaned++;
        f->running = 0;
        f->partial = 0;
    }
    struct cm_completion m;
    memset(&m, 0, sizeof m);
    m.type = CM_COMP_CLEANUP;
    m.result = result;
    m.token = token;
    return f->core ? cm_post(f->core, &m) : -1;
}

int cm_fake_event(struct cm_fake *f, uint64_t token, uint32_t events, uint64_t mono_ms, uint32_t flags)
{
    struct cm_completion m;
    memset(&m, 0, sizeof m);
    m.type = CM_COMP_EVENT;
    m.events = events;
    m.token = token;
    m.mono_ms = mono_ms;
    m.flags = flags;
    return f->core ? cm_post(f->core, &m) : -1;
}
