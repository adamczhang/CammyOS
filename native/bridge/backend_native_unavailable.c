/* SPDX-License-Identifier: MIT
 * Native hardware table placeholder (WP2). Deliberately supplies no table: the
 * bridge reports backend_kind=NATIVE, state UNAVAILABLE, reason
 * NATIVE_TABLE_ABSENT, and every physical capability unavailable.
 *
 * Replacing this file is a separate, reviewed change. It requires the WP1 native
 * ABI/thread/lifetime evidence (docs/native-bridge/NATIVE-ABI.md) for each
 * callback; protocol 1.0 still reports no physical capability as available.
 */
#include <stddef.h>
#include "cammy_bridge_backend.h"

const struct cammy_bridge_backend *cammy_bridge_native_backend(void) { return NULL; }
