/* SPDX-License-Identifier: MIT
 * Status-only fake backend (WP2). It answers status queries from process-local
 * state, touches no device, file or native symbol, and never claims a physical
 * capability. Every response carrying its data is labeled backend_kind=FAKE.
 */
#include "cammy_bridge_backend.h"
#include "cammy_bridge_proto.h"

struct fake_ctx {
    unsigned initialised;
};

static struct fake_ctx fake_state;

static int fake_init(void *ctx) {
    ((struct fake_ctx *)ctx)->initialised = 1;
    return 0;
}

static int fake_query_status(void *ctx, struct cammy_bridge_backend_status *out) {
    if (!((struct fake_ctx *)ctx)->initialised) return -1;
    out->state = CB_BSTATE_READY;
    out->reason = CB_BREASON_FAKE_BACKEND;
    return 0;
}

static int fake_query_capabilities(void *ctx, struct cammy_bridge_cap *caps, unsigned count) {
    (void)ctx;
    for (unsigned i = 0; i < count; i++) {
        caps[i].state = CB_CAP_UNAVAILABLE;
        caps[i].reason = CB_CREASON_FAKE_BACKEND;
    }
    return 0;
}

static void fake_fini(void *ctx) { ((struct fake_ctx *)ctx)->initialised = 0; }

static const struct cammy_bridge_backend fake_table = {
    .abi_version = CAMMY_BRIDGE_BACKEND_ABI,
    .struct_size = sizeof(struct cammy_bridge_backend),
    .kind = CB_BACKEND_FAKE,
    .reserved = 0,
    .name = "fake-status-only",
    .ctx = &fake_state,
    .init = fake_init,
    .query_status = fake_query_status,
    .query_capabilities = fake_query_capabilities,
    .fini = fake_fini,
};

const struct cammy_bridge_backend *cammy_bridge_fake_backend(void) { return &fake_table; }
