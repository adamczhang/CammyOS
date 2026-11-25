/* SPDX-License-Identifier: MIT
 * CammyOS status bridge core (WP2). Embeddable: installs no signal handlers,
 * changes no process-wide state (umask, signal dispositions), never exits the
 * process and performs no stdio. One allocation at open; none per request.
 */
#ifndef CAMMY_BRIDGE_H
#define CAMMY_BRIDGE_H

#include <stdint.h>
#include <sys/types.h>
#include "cammy_bridge_backend.h"

#define CAMMY_BRIDGE_VERSION "wp2-0.1.0"
#define CB_MAX_CLIENTS_LIMIT 8u
#define CB_DEFAULT_MAX_CLIENTS 4u
#define CB_DEFAULT_IDLE_TIMEOUT_MS 10000u
#define CB_MIN_IDLE_TIMEOUT_MS 100u
#define CB_MAX_IDLE_TIMEOUT_MS 60000u
#define CB_MAX_BURST 4u          /* requests handled per connection per step */
#define CB_ERROR_BUDGET 8u       /* rejected requests per connection before close */

struct cammy_bridge_config {
    const char *socket_path;   /* absolute; parent must be a private directory */
    const char *boot_id_path;  /* NULL: /proc/sys/kernel/random/boot_id */
    uint16_t backend_kind;     /* CB_BACKEND_FAKE or CB_BACKEND_NATIVE, explicit */
    unsigned max_clients;      /* 1..CB_MAX_CLIENTS_LIMIT */
    unsigned idle_timeout_ms;  /* CB_MIN_IDLE_TIMEOUT_MS..CB_MAX_IDLE_TIMEOUT_MS */
    uid_t allowed_uid;         /* only this peer uid may use the socket */
};

enum cammy_bridge_open_error {
    CB_OPEN_OK = 0,
    CB_OPEN_BAD_CONFIG = 1,
    CB_OPEN_UNSAFE_DIR = 2,
    CB_OPEN_ALREADY_RUNNING = 3,
    CB_OPEN_PATH_OCCUPIED = 4,
    CB_OPEN_SYSTEM = 5
};

struct cammy_bridge;

/* backend may be NULL (native table absent). The operator-selected kind in cfg is
 * what the bridge reports; a table whose kind differs is rejected, never relabeled. */
struct cammy_bridge *cammy_bridge_open(const struct cammy_bridge_config *cfg,
                                       const struct cammy_bridge_backend *backend,
                                       enum cammy_bridge_open_error *error);
/* Waits up to max_wait_ms for activity, serves bounded work, expires idle peers.
 * Returns 0, or -1 on a fatal listener error. EINTR and wake return 0. Performs no
 * allocation, so a serving thread abandoned mid-step holds no allocator lock. */
int cammy_bridge_step(struct cammy_bridge *b, int max_wait_ms);
/* Makes a concurrent or the next cammy_bridge_step() return promptly. Thread-safe
 * and async-signal-safe (one write to a private non-blocking pipe). Must not be
 * called after cammy_bridge_close(). Returns 0, or -1 on error. */
int cammy_bridge_wake(struct cammy_bridge *b);
/* Closes peers and listener, unlinks the socket only if it is still ours,
 * releases the instance lock, calls backend fini. Safe with NULL. Call only after
 * the thread running cammy_bridge_step() has stopped; an abandoned serving
 * thread means the handle must be leaked, never closed. */
void cammy_bridge_close(struct cammy_bridge *b);

uint64_t cammy_bridge_instance(const struct cammy_bridge *b);
const char *cammy_bridge_open_error_name(enum cammy_bridge_open_error e);

#endif
