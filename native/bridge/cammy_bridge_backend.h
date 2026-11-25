/* SPDX-License-Identifier: MIT
 * Backend function table for the CammyOS status bridge (WP2).
 *
 * Contract for every callback (see docs/native-bridge/PROTOCOL.md, "Backend table"):
 *  - called only on the thread that runs cammy_bridge_step();
 *  - must return promptly without blocking, sleeping or waiting on another thread;
 *  - must not perform hardware actions, open device nodes or send native events;
 *  - reports what it observes; the bridge core applies the protocol-1.0 clamp
 *    (no physical capability is ever reported AVAILABLE by protocol 1.0).
 * A native table is not part of WP2. cammy_bridge_native_backend() returns NULL
 * until a separately reviewed table and its ABI evidence are supplied.
 */
#ifndef CAMMY_BRIDGE_BACKEND_H
#define CAMMY_BRIDGE_BACKEND_H

#include <stdint.h>

#define CAMMY_BRIDGE_BACKEND_ABI 1u

struct cammy_bridge_backend_status {
    uint16_t state;   /* enum cb_backend_state */
    uint16_t reason;  /* enum cb_backend_reason */
};

struct cammy_bridge_cap {
    uint16_t id;      /* enum cb_capability_id, filled by the core */
    uint8_t state;    /* enum cb_cap_state, filled by the backend */
    uint8_t reason;   /* enum cb_cap_reason, filled by the backend */
};

struct cammy_bridge_backend {
    uint32_t abi_version;   /* CAMMY_BRIDGE_BACKEND_ABI */
    uint32_t struct_size;   /* sizeof(struct cammy_bridge_backend) */
    uint16_t kind;          /* enum cb_backend_kind; must equal the operator-selected kind */
    uint16_t reserved;      /* must be zero */
    const char *name;
    void *ctx;
    int (*init)(void *ctx);                                                   /* 0 = ok */
    int (*query_status)(void *ctx, struct cammy_bridge_backend_status *out); /* 0 = ok */
    int (*query_capabilities)(void *ctx, struct cammy_bridge_cap *caps, unsigned count); /* 0 = ok */
    void (*fini)(void *ctx);
};

/* Status-only fake backend. Never claims a physical capability. */
const struct cammy_bridge_backend *cammy_bridge_fake_backend(void);

/* Native hardware table. WP2: always NULL (unavailable). */
const struct cammy_bridge_backend *cammy_bridge_native_backend(void);

#endif
