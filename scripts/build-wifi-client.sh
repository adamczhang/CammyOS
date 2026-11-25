#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
findmnt -n "${CAMMY_WORK_MOUNT:-/}" >/dev/null
cd -- "$(dirname -- "$0")/.."
root="$PWD"
archive=downloads/wpa_supplicant-2.12.tar.gz
if [[ ! -f "$archive" ]]; then
 curl -fL https://w1.fi/releases/wpa_supplicant-2.12.tar.gz -o "$archive"
fi
printf '%s  %s\n' 08e23937e16d0155e55cab2b51f51fbe10d80a1aa91c4e15442645059b737ef6 "$archive" | sha256sum -c -
if [[ ! -d downloads/libnl-tiny/.git ]]; then
 git clone https://github.com/openwrt/libnl-tiny.git downloads/libnl-tiny
 git -C downloads/libnl-tiny checkout 40493a655d8caa2ccf5206dde1e733abe2920432
fi
[[ $(git -C downloads/libnl-tiny rev-parse HEAD) == 40493a655d8caa2ccf5206dde1e733abe2920432 ]]
compiler="$root/build/toolchain/bin/arm-buildroot-linux-musleabihf-"
cmake -S downloads/libnl-tiny -B build/libnl-tiny -DCMAKE_SYSTEM_NAME=Linux \
 -DCMAKE_SYSTEM_PROCESSOR=arm -DCMAKE_C_COMPILER="${compiler}gcc" -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/libnl-tiny --target nl-tiny-static -j8
if [[ ! -d build/wpa_supplicant-2.12 ]]; then tar -xf "$archive" -C build; fi
python3 - <<'PY'
from pathlib import Path
root=Path.cwd()
(root/'build/wpa_supplicant-2.12/wpa_supplicant/.config').write_text((root/'configs/wpa-client.config').read_text().replace('@ROOT@',str(root)))
PY
make -C build/wpa_supplicant-2.12/wpa_supplicant CC="${compiler}gcc" -j8 wpa_supplicant wpa_cli
for program in wpa_supplicant wpa_cli; do
 cp "build/wpa_supplicant-2.12/wpa_supplicant/$program" "build/arm/$program"
 "${compiler}strip" "build/arm/$program"
 file "build/arm/$program"
done
