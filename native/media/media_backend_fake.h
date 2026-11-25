/* SPDX-License-Identifier: MIT
 * Explicit fake media backend: TESTS ONLY. It simulates an asynchronous owner
 * context whose completions are scripted by the fixture. Its "running" flag
 * models a side effect that a dropped reply cannot undo. Frames it reports
 * are synthetic, and the core never turns them into a physical capability.
 */
#ifndef CAMMY_MEDIA_FAKE_H
#define CAMMY_MEDIA_FAKE_H
#include "cammy_media.h"

struct cm_fake {
    struct cm_backend table;
    struct cm_core *core;
    int fail_init, refuse_start, refuse_cleanup;
    uint64_t last_start_token, prev_start_token, last_cleanup_token, last_cleanup_session;
    uint32_t starts_submitted, cleanups_submitted;
    int running, partial;            /* simulated native side effects */
    uint32_t effects_started, effects_cleaned;
};

const struct cm_backend *cm_fake_backend(struct cm_fake *f);
int cm_fake_complete_start(struct cm_fake *f, uint64_t token, uint16_t result);
int cm_fake_complete_cleanup(struct cm_fake *f, uint64_t token, uint16_t result);
int cm_fake_event(struct cm_fake *f, uint64_t token, uint32_t events, uint64_t mono_ms, uint32_t flags);
#endif
