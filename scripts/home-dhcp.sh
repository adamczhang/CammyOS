#!/bin/sh
# SPDX-License-Identifier: MIT
# BusyBox udhcpc hook: configure this interface, keep lease/DNS data in RAM.
export PATH=/sbin:/usr/sbin:/bin:/usr/bin
R=${CAMMY_RUN_DIR:-/tmp/cammy-home}
[ -x "$R/busybox" ] && export PATH="$R:$PATH"
case "$1" in
 deconfig)
  ifconfig "$interface" 0.0.0.0
  rm -f "$R/lease.txt"
  ;;
 bound|renew)
  ifconfig "$interface" "$ip" netmask "${subnet:-255.255.255.0}"
  if [ -n "${router:-}" ]; then
   for gateway in $router; do route add default gw "$gateway" dev "$interface" 2>/dev/null || true; break; done
  fi
  umask 077
  printf 'ip=%s\nsubnet=%s\nrouter=%s\n' "$ip" "${subnet:-}" "${router:-}" >"$R/lease.txt"
  : >"$R/resolv.conf"
  for server in ${dns:-}; do echo "nameserver $server" >>"$R/resolv.conf"; done
  ;;
esac
