/* SPDX-License-Identifier: MIT. cammy-ui's check that the face page belongs to this boot.
 * The kernel boot ID cannot change while a process runs, so cammy-ui reads it once (and
 * again only until a read succeeds) instead of opening /proc/sys/kernel/random/boot_id
 * on every 100 ms menu loop. The page's recorded boot is still compared on every loop. */
#ifndef CAMMY_UI_FACE_BOOT_H
#define CAMMY_UI_FACE_BOOT_H
#include <stdint.h>
#include <stdatomic.h>
struct cui_boot { uint32_t id[4]; int known; };
/* 1 when the page's four boot words match this boot; 0 if they differ or the boot ID
 * has not been read yet (read_boot failed). */
static inline int cui_boot_matches(struct cui_boot *b, const _Atomic uint32_t page_boot[4],
                                   int (*read_boot)(uint32_t[4])) {
    if (!b->known) {
        if (read_boot(b->id)) return 0;
        b->known = 1;
    }
    for (unsigned i = 0; i < 4; i++)
        if (atomic_load_explicit(page_boot + i, memory_order_acquire) != b->id[i]) return 0;
    return 1;
}
#endif
