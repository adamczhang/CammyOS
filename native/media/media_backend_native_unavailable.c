/* SPDX-License-Identifier: MIT
 * Native media backend placeholder: ABSENT.
 * WP3 static analysis (docs/work-packages/WP3-OWNED-VIDEO.md) found no existing
 * sdvcam lock or thread that owns every RTSP/media entrypoint, so no native
 * media table may exist yet. The core additionally refuses any native-kind
 * table with reason serialization_unproven.
 */
#include "cammy_media.h"

#include <stddef.h>

const struct cm_backend *cm_native_media_backend(void)
{
    return NULL;
}
