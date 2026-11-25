#!/bin/sh
# SPDX-License-Identifier: MIT
# Run through daemon-exec after verifying USB ADB. Radio/config changes in RAM.
# Stop the original AP DHCP server before associating with a home LAN.
set -u
export PATH=/sbin:/usr/sbin:/bin:/usr/bin
R=/tmp/cammy-home
AP=${1:?Original hostapd PID required}
shift
[ "$#" -gt 0 ] || exit 2
case "$AP" in ''|*[!0-9]*) exit 2 ;; esac
[ -f /tmp/cammy-usb-confirmed ] || exit 3
[ "$(cat /proc/$AP/comm)" = hostapd ] || exit 4
for DHCP in "$@"; do
 case "$DHCP" in ''|*[!0-9]*) exit 2 ;; esac
 [ "$(cat /proc/$DHCP/comm)" = udhcpd ] || exit 5
done
[ -r "$R/wpa_supplicant.conf" ] || exit 6
umask 077
sed 's/^ssid=.*/ssid=CammyOS/' /var/run/hostapd/hostapd.conf >"$R/fallback-hostapd.conf"
rm -f "$R/lease.txt" "$R/home-confirmed" "$R/outcome" "$R/wpa.pid" "$R/dhcp.pid"

revert() {
 echo 'Returning to the CammyOS fallback AP'
 for file in "$R/wpa.pid" "$R/dhcp.pid"; do
  if [ -f "$file" ]; then
   pid=$(cat "$file")
   case "$pid" in ''|*[!0-9]*) continue ;; esac
   kill "$pid" 2>/dev/null || true
  fi
 done
 sleep 1
 ifconfig wlan0 down
 iw dev wlan0 set type __ap
 ifconfig wlan0 192.168.10.1 netmask 255.255.255.0 up
 hostapd -B "$R/fallback-hostapd.conf"
 udhcpd /etc/udhcpd.conf
 echo AP_RESTORED >"$R/outcome"
}

trap 'revert; exit 1' HUP INT TERM
for DHCP in "$@"; do kill -9 "$DHCP"; done
kill "$AP"
sleep 1
if grep ':0043 ' /proc/net/udp >/dev/null; then
 echo 'A DHCP server still owns UDP67; refusing station connection'
 revert; exit 11
fi
ifconfig wlan0 down
if ! iw dev wlan0 set type managed; then revert; exit 7; fi
ifconfig wlan0 0.0.0.0 up
"$R/wpa_supplicant" -i wlan0 -D nl80211 -c "$R/wpa_supplicant.conf" >"$R/wpa.log" 2>&1 &
echo "$!" >"$R/wpa.pid"
T=0
while [ "$T" -lt 35 ]; do
 if "$R/wpa_cli" -p "$R/ctrl" -i wlan0 status 2>/dev/null | grep '^wpa_state=COMPLETED' >/dev/null; then break; fi
 if ! kill -0 "$(cat "$R/wpa.pid")" 2>/dev/null; then revert; exit 8; fi
 sleep 1
 T=$((T + 1))
done
if [ "$T" -ge 35 ]; then revert; exit 9; fi
"$R/busybox" udhcpc -i wlan0 -p "$R/dhcp.pid" -s "$R/dhcp.sh" -t 5 -T 3 >"$R/dhcp.log" 2>&1 &
T=0
while [ "$T" -lt 25 ]; do
 [ -s "$R/lease.txt" ] && break
 sleep 1
 T=$((T + 1))
done
if [ "$T" -ge 25 ]; then revert; exit 10; fi
echo HOME_LEASE_ACQUIRED >"$R/outcome"
T=0
while [ "$T" -lt 90 ]; do
 if [ -f "$R/home-confirmed" ]; then
  echo 'Home Wi-Fi confirmed by host; leaving station and DHCP renewal running'
  exit 0
 fi
 sleep 1
 T=$((T + 1))
done
revert
