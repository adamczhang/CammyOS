#!/bin/sh
# SPDX-License-Identifier: MIT
# Prepare the WP2A device-ABI sysroot: musl 1.1.24 ARM headers and crt objects,
# matching the camera's saved /lib/libc.so (musl 1.1.24, 32-bit time_t).
# Downloads ONE pinned official tarball (about 1 MB) only if it is not already in
# build/native-loader/third_party/, verifies its SHA-256 and the musl release
# signature in a throwaway keyring, and installs nothing outside build/native-loader.
# The camera's libc itself is never rebuilt or replaced: we link against the saved one.
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT="$ROOT/build/native-loader"
TP="$OUT/third_party"
DL="$TP/musl-1.1.24-dl"
SRC="$TP/musl-1.1.24-src"
SYSROOT="$OUT/sysroot-1.1.24"
TC="$ROOT/build/toolchain/bin/arm-buildroot-linux-musleabihf-"
TARBALL=musl-1.1.24.tar.gz
URL=https://musl.libc.org/releases
SHA256=1370c9a812b2cf2a7d92802510cca0058cc37e66a7bedd70051f0a34015022a3
FPR=836489290BB6B70F99FFDA0556BCDB593020450F

findmnt -n "${CAMMY_WORK_MOUNT:-/}" >/dev/null || { echo 'LAB not mounted; refusing' >&2; exit 1; }
test -x "${TC}gcc" || { echo 'ARM toolchain missing (top-level make arm); no download here' >&2; exit 1; }
mkdir -p "$DL"
cd "$DL"
for f in "$TARBALL" "$TARBALL.asc" musl.pub; do
    if [ ! -f "$f" ]; then
        case $f in musl.pub) u=https://musl.libc.org/musl.pub ;; *) u="$URL/$f" ;; esac
        curl -fsSL --max-time 120 -o "$f.part" "$u" && mv "$f.part" "$f"
    fi
done
echo "$SHA256  $TARBALL" | sha256sum -c -
GNUPGHOME="$TP/gnupg-verify"; export GNUPGHOME
mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --quiet --import musl.pub
gpg --batch --with-colons --fingerprint | grep -q "^fpr:::::::::$FPR:" || { echo 'unexpected musl key' >&2; exit 1; }
gpg --batch --verify "$TARBALL.asc" "$TARBALL"

rm -rf "$SRC" "$SYSROOT"
mkdir -p "$SRC"
tar -xzf "$TARBALL" -C "$SRC" --strip-components=1 --no-same-owner
cd "$SRC"
./configure --target=arm-linux-musleabihf --prefix=/ --disable-shared CROSS_COMPILE="$TC" CC="${TC}gcc" \
    CFLAGS='-march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard' >"$TP/configure.log" 2>&1
make -s install-headers DESTDIR="$SYSROOT" >"$TP/headers.log" 2>&1
make -s lib/crt1.o lib/crti.o lib/crtn.o >"$TP/crt.log" 2>&1
mkdir -p "$SYSROOT/lib"
cp lib/crt1.o lib/crti.o lib/crtn.o "$SYSROOT/lib/"
grep -q 'typedef long time_t;' "$SYSROOT/include/bits/alltypes.h" || { echo 'not a time32 header set' >&2; exit 1; }
{
    echo "musl 1.1.24 sysroot for WP2A"
    echo "source $URL/$TARBALL sha256 $SHA256 signature-fingerprint $FPR"
    echo "toolchain $("${TC}gcc" --version | head -1)"
} >"$SYSROOT/PROVENANCE"
echo "sysroot ready: $SYSROOT"
