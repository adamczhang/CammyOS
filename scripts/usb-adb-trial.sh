#!/bin/sh
# SPDX-License-Identifier: MIT
# Run detached from the existing Wi-Fi root shell after setusbconfig adb.
# Revert to the original TCP/debug choice after60s unless USB is acknowledged.
set -u
OLD=${1:?Original adbd PID required}
case "$OLD" in ''|*[!0-9]*) exit 2 ;; esac
[ "$(cat /proc/$OLD/comm)" = adbd ] || exit 3
sleep 2
kill "$OLD"
sleep 1
/bin/adbd >/tmp/cammy-adbd-usb.log 2>&1 &
NEW=$!
T=0
while [ "$T" -lt 60 ]; do
 if [ -f /tmp/cammy-usb-confirmed ]; then
  echo 'USB shell acknowledged; leaving USB ADB enabled'
  exit 0
 fi
 sleep 1
 T=$((T + 1))
done
echo 'USB not acknowledged; restoring mass-storage configuration and TCP adbd'
kill "$NEW" 2>/dev/null || true
umount /dev/usb-ffs/adb 2>/dev/null || true
rm -rf /dev/usb-ffs/adb
/usr/bin/setusbconfig init >/tmp/cammy-usb-revert.log 2>&1
ADB_TRANSPORT_PORT=5555 /bin/adbd >/tmp/cammy-adbd-tcp.log 2>&1 &
