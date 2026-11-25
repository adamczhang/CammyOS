/* SPDX-License-Identifier: MIT. Face-trial profile; separate from status v1. */
#ifndef CAMMY_FACE_HOST_H
#define CAMMY_FACE_HOST_H
#include <stdint.h>
#define CMF_HOST_ABI 1u
#define CMF_HOST_PROFILE 2u
struct cmf_host {
    uint32_t abi_version, struct_size, profile;
    uint8_t source_sha256[32];
    void *main_module; /* opaque, never dereferenced/stored */
};
int cmf_attach_v1(const struct cmf_host *host);
void cmf_detach_v1(uint32_t reason);
void cmf_gui_v1(void *window_manager, uint32_t phase);
#endif
