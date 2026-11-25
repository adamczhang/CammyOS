#!/bin/sh
# SPDX-License-Identifier: MIT
# Invoke manually through root ADB after normal stock boot. This is not BROM boot.
set -eu
CARD=/mnt/extsd/Cammy
RAM=/tmp/cammyos-tf
TOOLS=/tmp/cammyos-tf-tools
TOKEN=${1:?Usage: sh /mnt/extsd/Cammy/start.sh /absolute/path/to/token [bind-address] [port]}
BIND=${2:-127.0.0.1}
PORT=${3:-8789}
[ "$(id -u)" = 0 ] || { echo 'Root required'; exit 1; }
case "$TOKEN" in /*) ;; *) echo 'Token path must be absolute'; exit 1 ;; esac
[ -r "$TOKEN" ] || { echo 'Token file is not readable'; exit 1; }
grep '^tmpfs /tmp tmpfs ' /proc/mounts >/dev/null || { echo '/tmp is not verified tmpfs'; exit 1; }
grep ' /mnt/extsd vfat ' /proc/mounts >/dev/null || { echo 'TF is not mounted'; exit 1; }
[ ! -e "$RAM" ] || { echo 'Prior TF RAM deployment exists; inspect it first'; exit 1; }
mkdir "$TOOLS"
# Copy bootstrap utility into RAM; no dependency on FAT executable permission bits.
cp "$CARD/busybox" "$TOOLS/busybox"
chmod 700 "$TOOLS/busybox"
(cd "$CARD" && "$TOOLS/busybox" sha256sum -c SHA256SUMS)
mkdir "$RAM"
"$TOOLS/busybox" tar -xzf "$CARD/rootfs.tar.gz" -C "$RAM"
"$RAM/usr/bin/cammy-portal" --root "$RAM" --token-file "$TOKEN" \
  --bind "$BIND" --port "$PORT" --stock-preview --daemonize \
  --pid-file /tmp/cammyos-tf.pid
echo "CammyOS userspace launched from TF into RAM on $BIND:$PORT; stock kernel unchanged"
