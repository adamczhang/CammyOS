#!/usr/bin/env bash
set -euo pipefail
findmnt -n "${CAMMY_WORK_MOUNT:-/}" >/dev/null
cd -- "$(dirname -- "$0")/.."
archive=downloads/armv7-eabihf--musl--stable-2025.08-1.tar.xz
expected=2f3a34458c3a8b961bd09f89669130fcdc4c1dbc6e31ada720527e4ad3741c11
mkdir -p downloads build/toolchain build/arm
if [[ ! -f "$archive" ]]; then
 curl -fL "https://toolchains.bootlin.com/downloads/releases/toolchains/armv7-eabihf/tarballs/$(basename "$archive")" -o "$archive"
fi
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
if [[ ! -x build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc ]]; then
 tar -xf "$archive" --strip-components=1 -C build/toolchain
fi
build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc -Os -Wall -Wextra -c src/compat/arm_legacy_stat.c -o build/arm/arm_legacy_stat.o
build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc build/arm/arm_legacy_stat.o -Os -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion src/cammy_portal.c -o build/arm/cammy-portal
build/toolchain/bin/arm-buildroot-linux-musleabihf-strip build/arm/cammy-portal
file build/arm/cammy-portal

build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc build/arm/arm_legacy_stat.o -Os -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion src/boot/native_service_client.c -o build/arm/native-service-client
build/toolchain/bin/arm-buildroot-linux-musleabihf-strip build/arm/native-service-client
