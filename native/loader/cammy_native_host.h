/* SPDX-License-Identifier: MIT
 * Loader-stub <-> status-library interface v1 (WP1 NATIVE-ABI section 4).
 *
 * The stub in the offline status candidate builds one struct cammy_native_host_v1
 * on the main thread's stack at the 0x31DB0 bracket and calls attach once. The
 * library copies what it needs and never keeps the pointer or main_module.
 * Exactly two symbols are exported by the library; the stub resolves no others.
 */
#ifndef CAMMY_NATIVE_HOST_H
#define CAMMY_NATIVE_HOST_H

#include <stdint.h>

#define CAMMY_NATIVE_HOST_ABI 1u
#define CAMMY_NATIVE_EXIT_QUEUE_CLOSED 1u

enum cammy_native_profile { CAMMY_PROFILE_STATUS_ONLY = 1 };

struct cammy_native_host_v1 {      /* built by the stub; read-only for the library */
    uint32_t abi_version;          /* CAMMY_NATIVE_HOST_ABI */
    uint32_t struct_size;          /* sizeof(struct cammy_native_host_v1): 48 on ARM32 */
    uint32_t profile;              /* CAMMY_PROFILE_STATUS_ONLY; anything else rejected */
    uint8_t source_sha256[32];     /* exact sdvcam hash baked into the stub */
    void *main_module;             /* opaque; never dereferenced or stored */
};

/* 0, or a negative errno. -EPROTO: record rejected. -EALREADY: not the first call. */
int cammy_native_attach_v1(const struct cammy_native_host_v1 *host);
/* Idempotent; a no-op before a successful attach. Bounded; never unloads anything. */
void cammy_native_detach_v1(uint32_t reason);

#endif
