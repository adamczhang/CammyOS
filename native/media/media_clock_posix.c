/* SPDX-License-Identifier: MIT
 * Host clock for the owned-media core, kept out of the core object so the core
 * imports no time function (the camera libc is musl 1.1.24 time32; this
 * toolchain's clock_gettime resolves to __clock_gettime64).
 */
#define _POSIX_C_SOURCE 200809L
#include "cammy_media.h"

#include <time.h>

uint64_t cm_monotonic_ms(void *unused)
{
    (void)unused;
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)((uint32_t)ts.tv_nsec / 1000000u);
}
