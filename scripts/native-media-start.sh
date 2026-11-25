#!/bin/sh
# SPDX-License-Identifier: MIT
# Opt-in, one attempt per boot. Runs before the station supervisor loop.
# Native firmware/application/init are never replaced, restarted or signalled.
set -u
umask 077
R=/tmp/cammy-runtime
D=$R/native-video
C=$R/native-service-client
B=$R/busybox
note(){ [ -n "${CAMMY_DIAG_LOG:-}" ] || return 0; printf '%s nv %s\n' "$(cut -d' ' -f1 /proc/uptime)" "$1" >>"$CAMMY_DIAG_LOG" 2>/dev/null; sync; }
status(){ printf '%s\n' "$1" >"$R/native-video.state"; note "state=$1"; }
starttime(){ awk '{print $22}' "/proc/$1/stat" 2>/dev/null; }
owned(){ [ "$(starttime "$1")" = "$2" ] && [ "$(readlink /proc/$1/exe)" = "$3" ]; }
# One end-to-end bound for every shell phase: the request's monotonic deadline
# (uptime ms, exported by the media worker) and its cancel record. Every phase
# runs to a limit (LIMIT) no later than that deadline; mutating client calls get
# the same limit and refuse past it, and gate() bounds the sleeps and retries.
# The lease is acquired for the full deadline. The mode reload must end
# RESERVE before it (WORK_END), so restoring the mode always has time left
# inside that lease; restoration stops 2 s before it (RESTORE_END), within the
# whole-second lease TTL, and is never retried under a new lease or owner.
# An unreadable clock counts as expired.
END=${CAMMY_PREPARE_DEADLINE_MS:-}
WORK_END=;RESTORE_END=
if [ -n "$END" ];then
 WORK_END=$(awk -v e="$END" 'BEGIN{printf "%.0f",e-25000}')
 RESTORE_END=$(awk -v e="$END" 'BEGIN{printf "%.0f",e-2000}')
fi
LIMIT=$END
past(){
 [ -n "$1" ] || return 1
 awk -v end="$1" '{exit ($1*1000<end)}' /proc/uptime 2>/dev/null
 [ "$?" != 1 ]
}
stop_reason(){
 if [ -n "${CAMMY_PREPARE_CANCEL:-}" ] && { [ -e "$CAMMY_PREPARE_CANCEL" ] || [ -L "$CAMMY_PREPARE_CANCEL" ]; };then echo cancelled;return 0;fi
 past "$LIMIT" || return 1
 echo deadline-exceeded
}
# The client under the current phase limit.
cl(){ if [ -n "$LIMIT" ];then CAMMY_PREPARE_DEADLINE_MS=$LIMIT "$C" "$@";else "$C" "$@";fi; }
# The camera has neither rmdir nor rm -d. Remove only a real, empty directory.
remove_empty_dir(){
 [ -d "$1" ] && [ ! -L "$1" ] || return 1
 for entry in "$1"/* "$1"/.[!.]* "$1"/..?*;do
  if [ -e "$entry" ] || [ -L "$entry" ];then return 1;fi
 done
 rm -r "$1"
}
# The runtime's fault-evidence makes one bounded control-socket request;
# wpa_cli and grep remain only for a payload without it.
wpa(){
 if [ -x "$R/fault-evidence" ]; then "$R/fault-evidence" wpa "$R/ctrl" wlan0 "$1" >/dev/null 2>&1
 elif [ "$1" = STATUS ]; then "$R/wpa_cli" -p "$R/ctrl" -i wlan0 status 2>/dev/null | grep '^wpa_state=COMPLETED$' >/dev/null
 else "$R/wpa_cli" -p "$R/ctrl" -i wlan0 reassociate >/dev/null 2>&1; fi
}
station(){ wpa STATUS; }
# Only stock udhcpd with the exact known argv may be stopped. Any other AP
# helper blocks the attempt. TERM is insufficient on this firmware (live proof).
ap_helpers(){
 found=0
 for proc in /proc/[0-9]*;do
  name=$(cat "$proc/comm" 2>/dev/null) || continue
  case "$name" in hostapd|udhcpd)
   # Ignore exited zombies, but never count a live DHCP server as recovered.
   [ "$(awk '{print $3}' "$proc/stat" 2>/dev/null)" = Z ] && continue
   found=1
   if [ "$1" = stop ] && [ "$name" = udhcpd ];then
    pid=${proc##*/};tick=$(starttime "$pid")
    args=$(tr '\000' ' ' <"$proc/cmdline" 2>/dev/null)
    if [ "$args" = '/usr/sbin/udhcpd /etc/udhcpd.conf ' ] &&
       [ -n "$tick" ] && [ "$tick" -ge "$MIN_START" ] && owned "$pid" "$tick" /bin/busybox;then
     kill -KILL "$pid" 2>/dev/null || true
     echo 'stock-ap-dhcp-cleanup'
    fi
   fi
  esac
 done
 [ "$found" = 0 ]
}
recover(){
 read -r BOOT NPID NTICK WPID WTICK HPID HTICK CAMMY_HOME_IP MIN_START <"$D/identity" || exit 1
 case "$NPID:$NTICK:$WPID:$WTICK:$HPID:$HTICK:$MIN_START" in *[!0-9:]*) exit 1;; esac
 echo ready >"$D/ready"
 i=0;stable=0
 while [ "$i" -lt 28 ];do
  [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] && owned "$NPID" "$NTICK" /usr/bin/sdvcam || break
  owned "$WPID" "$WTICK" "$R/wpa_supplicant" && owned "$HPID" "$HTICK" "$R/busybox" || break
  if [ -f "$D/go" ];then
   ap_helpers stop >/dev/null || true
   if [ -f "$D/command.done" ] || [ "$i" -ge 10 ];then
    if station;then
     current=$("$C" ip 2>/dev/null || true)
     if [ "$current" = "$CAMMY_HOME_IP" ] && ap_helpers check;then
      stable=$((stable+1))
      if [ "$stable" -ge 2 ];then echo restored >"$D/recovery";exit 0;fi
     else
      stable=0
      [ "$(cat "$R/dhcp.pid" 2>/dev/null)" = "$HPID" ] || break
      kill -USR1 "$HPID" 2>/dev/null || break
     fi
    else
     stable=0
     # Owned station process only; no radio driver reload or app restart.
     [ "$i" != 10 ] || wpa REASSOCIATE
    fi
   fi
  fi
  sleep 1;i=$((i+1))
 done
 echo recovery-required >"$D/recovery"
 exit 1
}
if [ "${1:-}" = recover ];then recover;exit $?;fi
[ "${1:-}" = start ] || exit 2
[ "$(cat "$R/state" 2>/dev/null)" = HOME_STATION ] || { status skipped-network;exit 0; }
# Exact namespace, never reclaim an earlier attempt.
mkdir "$D" 2>/dev/null || exit 0
chmod 700 "$D" || exit 1
HELD=0
MODE_DIRTY=0
# Until the recovery guard is given an identity, nothing outside this empty
# namespace exists and nothing on the camera has changed (CLEAN=1). A clean
# stop removes the namespace so a later request can make a fresh attempt.
CLEAN=1
# Restore normal video mode exactly once, under the lease still held and never
# past RESTORE_END. Cancellation does not stop it (this is the cleanup), and no
# lease is reacquired: the client itself refuses unless cammy-boot still owns
# the portal. Without proof the outcome is a terminal state of its own.
restore_mode(){
 MODE_DIRTY=0
 LIMIT=$RESTORE_END
 if past "$LIMIT";then status mode-restore-expired;return 1;fi
 cl video >/dev/null 2>&1 || { status mode-restore-failed;return 1; }
 i=0
 while ! "$C" mode-video >/dev/null 2>&1;do
  if [ "$i" -ge 4 ] || past "$LIMIT";then status mode-restore-unconfirmed;return 1;fi
  sleep 1;i=$((i+1))
 done
 note mode-video
}
finish(){
 trap '' TERM INT HUP
 if [ "$CLEAN" = 1 ];then
  if [ "$HELD" = 1 ];then "$C" release >/dev/null 2>&1 || true;fi
  remove_empty_dir "$D" || status namespace-cleanup-failed
  return
 fi
 : >"$D/command.done"
 if [ "$HELD" = 1 ] && [ "$MODE_DIRTY" = 1 ];then note "restore-after=$(cat "$R/native-video.state" 2>/dev/null)";restore_mode || true;fi
 if [ "$HELD" = 1 ];then "$C" release >/dev/null 2>&1 || true;fi
}
# Stop at the phase limit or on cancellation. Before the recovery guard exists
# an expired deadline is a clean refusal; afterwards it fails closed, and the
# EXIT trap restores the mode inside the reserve and releases.
gate(){
 why=$(stop_reason) || return 0
 if [ "$why" = deadline-exceeded ] && [ "$CLEAN" = 1 ];then status refused-deadline;else status "$why";fi
 exit 1
}
trap finish EXIT
trap 'status interrupted;exit 1' TERM INT HUP
status preflight
gate
# One exact stock app; no late native selection or replacement route. The
# 11 MB read of internal flash is done once per boot; the marker lives in the
# private RAM run directory, which a reboot clears.
STOCK_OK=$R/stock-sdvcam.verified
BOOT_NOW=$(cat /proc/sys/kernel/random/boot_id)
if [ "$(cat "$STOCK_OK" 2>/dev/null)" != "$BOOT_NOW" ]; then
 [ "$("$B" sha256sum /usr/bin/sdvcam | awk '{print $1}')" = 8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec ] || { status refused-stock;exit 1; }
 printf '%s\n' "$BOOT_NOW" >"$STOCK_OK"
fi
set -- $(pidof sdvcam)
[ "$#" = 1 ] || { status refused-app;exit 1; };NPID=$1;NTICK=$(starttime "$NPID")
WPID=$(cat "$R/wpa.pid");HPID=$(cat "$R/dhcp.pid")
case "$WPID:$HPID" in ''|*[!0-9:]*) status refused-identity;exit 1;; esac
WTICK=$(starttime "$WPID");HTICK=$(starttime "$HPID")
owned "$NPID" "$NTICK" /usr/bin/sdvcam && owned "$WPID" "$WTICK" "$R/wpa_supplicant" && owned "$HPID" "$HTICK" "$R/busybox" && station && ap_helpers check || { status refused-identity;exit 1; }
CAMMY_HOME_IP=$("$C" ip) || { status refused-address;exit 1; }
MIN_START=$(awk '{print int($1*100)}' /proc/uptime)
BOOT=$(cat /proc/sys/kernel/random/boot_id)
gate
"$C" acquire >/dev/null || { status skipped-busy;exit 0; };HELD=1
note acquired
"$C" preflight >/dev/null || { status refused-native-mode;exit 1; }
gate
CLEAN=0
printf '%s %s %s %s %s %s %s %s %s\n' "$BOOT" "$NPID" "$NTICK" "$WPID" "$WTICK" "$HPID" "$HTICK" "$CAMMY_HOME_IP" "$MIN_START" >"$D/identity"
"$R/daemon-exec" "$D/recovery.log" /bin/sh "$R/native-media-start.sh" recover >"$D/guard-launch.log" 2>&1 || { status guard-unavailable;exit 1; }
i=0;while [ ! -f "$D/ready" ] && [ "$i" -lt 3 ];do gate;sleep 1;i=$((i+1));done
[ -f "$D/ready" ] || { status guard-unavailable;exit 1; }
note guard-ready
gate
status initializing
: >"$D/go"
# Exactly once. An uncertain acknowledgement is never retried.
note init-send
ACK=0;"$C" init >/dev/null && ACK=1
note "init-ack=$ACK"
: >"$D/command.done"
i=0;while [ ! -f "$D/recovery" ] && [ "$i" -lt 30 ];do gate;sleep 1;i=$((i+1));done
[ "$(cat "$D/recovery" 2>/dev/null)" = restored ] || { status recovery-required;exit 1; }
[ "$ACK" = 1 ] || { status native-ack-uncertain;exit 1; }
note restored
# Normal mode reload makes RTSP bind the recovered station address. No shutter.
# Entered only with the restoration reserve still ahead, and bounded by it.
LIMIT=$WORK_END
gate
MODE_DIRTY=1
cl photo >/dev/null || { status mode-reload-failed;exit 1; }
i=0;while ! "$C" mode-photo >/dev/null 2>&1 && [ "$i" -lt 4 ];do gate;sleep 1;i=$((i+1));done
"$C" mode-photo >/dev/null || { status mode-reload-failed;exit 1; }
note mode-photo
gate
restore_mode || exit 1
LIMIT=$END
# The mode reload restarts the stock event server (:9999); on rc3 boot ff533369 the
# first start came before it listened. 8 s was not enough on 0.7.4 boot e3fdb04a
# (2026-11-16: event-unavailable, and it listened later), so wait up to 30 s.
i=0;STARTED=0
while [ "$i" -lt 30 ];do gate;if "$C" start >/dev/null 2>&1;then STARTED=1;break;fi;note start-retry;sleep 1;i=$((i+1));done
[ "$STARTED" = 1 ] || { status event-unavailable;exit 1; }
note started
i=0;SDP=0
while [ "$i" -lt 8 ];do gate;if "$C" sdp >/dev/null 2>&1;then SDP=1;break;fi;sleep 1;i=$((i+1));done
note "sdp=$SDP"
"$C" release >/dev/null || { status release-uncertain;exit 1; };HELD=0
note released
# Native release is asynchronous. Do not claim cleanup from HTTP ack alone.
i=0
while [ "$i" -lt 5 ];do
 gate
 if "$C" idle >/dev/null 2>&1 && [ "$(cat /proc/asound/card0/pcm0c/sub0/status 2>/dev/null)" = closed ];then
  [ "$SDP" = 1 ] && status sdp-ready-idle || status sdp-unavailable-idle
  exit 0
 fi
 sleep 1;i=$((i+1))
done
status cleanup-unconfirmed
exit 1
