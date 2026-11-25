#!/bin/sh
# SPDX-License-Identifier: MIT
# Boot-session runtime: payload on TF, credentials in private internal config.
set -u
umask 077
CARD=/mnt/extsd/CammyOS
R=/tmp/cammy-runtime
GUARD=/etc/cammy/startup.pending
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id)
DIAG_LOG=
# Opt-in diagnostics: append-and-sync a per-boot log on the TF card so a reset
# leaves evidence. Nothing here changes startup behaviour.
diag() {
 [ -n "$DIAG_LOG" ] || return 0
 printf '%s %s\n' "$(cut -d' ' -f1 /proc/uptime)" "$*" >>"$DIAG_LOG" 2>/dev/null
 sync
}
diag_heartbeat() {
 klast=0
 rlast=0
 i=0
 # The camera has one CPU and an idle load near 4; keep each beat cheap and
 # read the kernel log only every fifth beat (about 10 s).
 while :; do
  i=$((i + 1))
  up=$(cut -d. -f1 /proc/uptime)
  [ "$up" -lt 420 ] || break
  sp=$(pidof sdvcam)
  st=-
  dt=0
  if [ -n "$sp" ]; then
   # Process states come from the stat lines with shell builtins: no
   # per-thread fork on a one-CPU camera.
   { read -r line <"/proc/$sp/stat"; } 2>/dev/null && { line=${line##*) }; st=${line%% *}; }
   for t in /proc/$sp/task/*/stat; do
    { read -r line <"$t"; } 2>/dev/null || continue
    line=${line##*) }
    [ "${line%% *}" != D ] || dt=$((dt + 1))
   done
  fi
  wl=0
  [ -d /sys/class/net/wlan0 ] && wl=1
  diag "hb load=$(cut -d' ' -f1-3 /proc/loadavg) sdvcam=${sp:-none}:$st D=$dt mem=$(awk '/MemAvailable/{print $2}' /proc/meminfo) wlan=$wl nv=$(cat "$R/native-video.state" 2>/dev/null)"
  if [ $((i % 5)) = 1 ]; then
   n=$(dmesg | wc -l)
   [ "$n" -ge "$klast" ] || klast=0
   if [ "$n" -gt "$klast" ]; then
    # The Wi-Fi driver hex-dumps link keys ("key: 00000000: ..."); never
    # copy key material to the card.
    dmesg | sed -n "$((klast + 1)),${n}p" | sed -e '/key: [0-9a-f]\{8\}:/d' -e 's/^/k /' >>"$DIAG_LOG" 2>/dev/null
    klast=$n
   fi
  fi
  RL=$R/native-video/recovery.log
  if [ -r "$RL" ]; then
   n=$(wc -l <"$RL")
   if [ "$n" -gt "$rlast" ]; then
    sed -n "$((rlast + 1)),${n}p" "$RL" | sed 's/^/r /' >>"$DIAG_LOG" 2>/dev/null
    rlast=$n
   fi
  fi
  sync
  sleep 2
 done
 diag 'hb end'
}
# Radio watch (boot fbaa812c): the vendor Wi-Fi driver can oops inside a
# network call and leave a task holding the kernel's network lock, so the
# next ifconfig sleeps forever in D state. Kernel taint bit 7 (128, TAINT_DIE)
# is set by any oops; the out-of-tree module bit (4096) is already set, so
# only a change of bit 7 from this baseline counts. The shell's `read` gets
# only the first byte of a sysctl file (EOF at any offset > 0), so a direct
# read is one cat; the resident trail (fault-evidence) watches the taint
# instead and leaves $R/kernel-oops, which a builtin test sees with no fork.
read_taint() {
 TAINT=$(cat /proc/sys/kernel/tainted 2>/dev/null)
 case "$TAINT" in ''|*[!0-9]*) TAINT=0 ;; esac
}
read_taint
TAINT_BASE=$TAINT
RADIO_DOWN=0
WPA_PID=0
proc_running() {
 { read -r PROC_LINE <"/proc/$1/stat"; } 2>/dev/null || return 1
 case "${PROC_LINE##*) }" in Z*|X*) return 1 ;; esac
}
radio_check() {
 [ "$RADIO_DOWN" = 0 ] || return 1
 if [ "${1:-}" != direct ] && [ "${TRAIL_PID:-0}" -gt 1 ] &&
  { read -r TRAIL_COMM <"/proc/$TRAIL_PID/comm"; } 2>/dev/null && [ "$TRAIL_COMM" = fault-evidence ]; then
  [ -e "$R/kernel-oops" ] || return 0
 else
  read_taint
  [ $((TAINT & 128)) -ne 0 ] && [ $((TAINT_BASE & 128)) -eq 0 ] || return 0
 fi
 radio_fault RADIO_FAULT kernel-oops
 return 1
}
# The release-bound boot latch earns the one automatic radio retry. Only a
# latch this startup still owns is replaced; anything else is kept.
radio_latch() {
 [ -n "${RELEASE_ID:-}" ] && [ -n "${CANDIDATE_MD5:-}" ] && [ -f "$GUARD" ] && [ ! -L "$GUARD" ] || return 0
 case "$(cat "$GUARD" 2>/dev/null)" in
  "phase=launch
boot_id=$BOOT_ID"|"phase=idle_ready
boot_id=$BOOT_ID
release_id=$RELEASE_ID
candidate_md5=$CANDIDATE_MD5") ;;
  *) return 0 ;;
 esac
 RADIO_TMP=/etc/cammy/.startup-radio.$$
 rm -f "$RADIO_TMP"
 if (set -C; printf 'phase=radio_fault\nboot_id=%s\nrelease_id=%s\ncandidate_md5=%s\n' "$BOOT_ID" "$RELEASE_ID" "$CANDIDATE_MD5" >"$RADIO_TMP") &&
  "$R/fault-evidence" sync && mv -f "$RADIO_TMP" "$GUARD" && "$R/fault-evidence" sync; then
  return 0
 fi
 rm -f "$RADIO_TMP"
}
# After a radio fault no further network command runs in this boot: the
# state names the fault (cammy-ui treats it like an unavailable driver), the
# evidence snapshot goes to TF in the background, and the latch is recorded.
radio_fault() {
 [ "$RADIO_DOWN" = 0 ] || return 0
 RADIO_DOWN=1
 echo "$1" >"$R/state"
 printf 'state=%s\nreason=%s\ncommand=%s\n' "$1" "$2" "${3:-none}" >"$R/radio-fault"
 echo "radio fault: $1 $2 ${3:-}"
 diag "radio $1 $2 ${3:-}"
 [ ! -f "$R/rc-health.sh" ] || /bin/sh "$R/rc-health.sh" snapshot "radio-$2" >/dev/null 2>&1
 radio_latch
}
# Run one network command with a bound. It runs in the background and its
# /proc entry is polled; a task stuck in D state cannot be killed, so on the
# bound the radio is marked hung and the task is abandoned. Callers check
# RADIO_DOWN afterwards. Stock /bin/sleep takes fractions; the payload's does not.
net_run() {
 NET_LIMIT=$(($1 * 5))
 shift
 [ "$RADIO_DOWN" = 0 ] || return 1
 "$@" &
 NET_PID=$!
 NET_T=0
 while proc_running "$NET_PID"; do
  if [ "$NET_T" -ge "$NET_LIMIT" ]; then
   radio_fault RADIO_HUNG command-hung "${1##*/}"
   return 1
  fi
  /bin/sleep 0.2
  NET_T=$((NET_T + 1))
 done
 wait "$NET_PID"
}
radio_ok() {
 [ "$RADIO_DOWN" = 0 ]
}
# Before the supervisor loop a radio fault ends startup, like a missing driver.
radio_stop() {
 [ "$RADIO_DOWN" = 0 ] || exit 1
}
wpa_completed() {
 if [ -x "$R/fault-evidence" ]; then
  "$R/fault-evidence" wpa "$R/ctrl" wlan0 STATUS >/dev/null 2>&1
 else
  "$R/wpa_cli" -p "$R/ctrl" -i wlan0 status 2>/dev/null | grep '^wpa_state=COMPLETED' >/dev/null
 fi
}
phase() {
 diag "phase $1"
 if [ -e "$GUARD" ]; then
  printf 'phase=%s\nboot_id=%s\n' "$1" "$BOOT_ID" >"$GUARD"
  sync
 fi
 echo "phase=$1"
}
export CAMMY_RUN_DIR=$R
export PATH=$R:/sbin:/usr/sbin:/bin:/usr/bin
mkdir -p "$R"
echo "$$" >/tmp/cammy-supervisor.pid
phase payload
for program in busybox wpa_supplicant wpa_cli cammy-portal daemon-exec native-service-client; do
 cp "$CARD/$program" "$R/$program" || exit 1
 chmod 700 "$R/$program"
done
B=$R/busybox
(cd "$CARD" && "$B" sha256sum -c SHA256SUMS) >"$R/payload-check.log" 2>&1 || exit 1
for program in ifconfig route hostname sleep kill sha256sum; do
 ln -s "$R/busybox" "$R/$program" 2>/dev/null || true
done
cp "$CARD/dhcp.sh" "$R/dhcp.sh"
chmod 700 "$R/dhcp.sh"
for data in shared-capture.sh shared-capture.fragment native-media-start.sh; do
 cp "$CARD/$data" "$R/$data" || exit 1
 chmod 600 "$R/$data" || exit 1
done
NATIVE_VIDEO=0
if [ -f "$CARD/enable-native-video" ] && [ ! -L "$CARD/enable-native-video" ]; then
 [ "$(cat "$CARD/enable-native-video")" = experimental-native-video-v1 ] || exit 1
 NATIVE_VIDEO=1
fi
SHARED_AUDIO=0
# Local test configuration, deliberately absent from the generic release.
if [ -f "$CARD/enable-shared-capture" ] && [ ! -L "$CARD/enable-shared-capture" ]; then
 [ "$(cat "$CARD/enable-shared-capture")" = experimental-shared-capture-v1 ] || exit 1
 SHARED_AUDIO=1
fi
if [ -f "$CARD/enable-diag" ] && [ ! -L "$CARD/enable-diag" ] &&
   [ "$(cat "$CARD/enable-diag")" = cammy-diag-v1 ]; then
 DD=/mnt/extsd/CammyOS-diag
 mkdir -p "$DD" 2>/dev/null
 # Bounded: stop logging rather than fill the card.
 if [ -d "$DD" ] && [ "$(ls "$DD" | wc -l)" -lt 200 ]; then
  DIAG_LOG=$DD/boot-$(cut -c1-8 /proc/sys/kernel/random/boot_id).log
  export CAMMY_DIAG_LOG=$DIAG_LOG
  diag "start boot=$BOOT_ID release=$(cat "$CARD/RELEASE" 2>/dev/null) video=$NATIVE_VIDEO audio=$SHARED_AUDIO s01=$(md5sum /etc/init.d/S01app | cut -c1-8)"
  diag_heartbeat &
 fi
fi
# Optional release marker is verified with the payload and copied only to RAM.
[ ! -r "$CARD/RELEASE" ] || cp "$CARD/RELEASE" "$R/RELEASE"
sed "s#^ctrl_interface=.*#ctrl_interface=$R/ctrl#" /etc/cammy/home.conf >"$R/home.conf"
hostname cammyos
phase stock_settle
# Warm startup succeeded; immediate boot startup reset the unit. Keep startup
# away from the vendor application's initial hardware/USB/media initialization.
# This is an instrumented hypothesis, not a verified fix for the reset.
sleep 30
phase radio_load
WLAN_LOADED=0
if [ ! -d /sys/class/net/wlan0 ]; then
 WLAN_LOADED=1
 net_run 30 /etc/firmware/ap_ctrl.sh 6600s load >"$R/driver.log" 2>&1
fi
T=0
while [ ! -d /sys/class/net/wlan0 ] && [ "$T" -lt 20 ] && radio_check; do sleep 1; T=$((T + 1)); done
radio_stop
[ -d /sys/class/net/wlan0 ] || { echo DRIVER_UNAVAILABLE >"$R/state"; exit 1; }

stop_clients() {
 for file in "$R/wpa.pid" "$R/dhcp.pid"; do
  [ -r "$file" ] || continue
  pid=$(cat "$file")
  case "$pid" in ''|*[!0-9]*) continue ;; esac
  case "$(cat /proc/$pid/comm 2>/dev/null)" in
   wpa_supplicant|busybox) kill "$pid" 2>/dev/null || true ;;
  esac
 done
}

cleanup() {
 trap '' TERM INT HUP
 stop_clients
 if [ -r "$R/portal.pid" ]; then
  pid=$(cat "$R/portal.pid")
  case "$pid" in ''|*[!0-9]*) pid=0 ;; esac
  if [ "$pid" -gt 1 ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = cammy-portal ]; then
   kill "$pid" 2>/dev/null || true
  fi
 fi
 if [ "$SHARED_AUDIO" = 1 ]; then
  sleep 1
  /bin/sh "$R/shared-capture.sh" stop >"$R/shared-capture-stop.log" 2>&1 || true
 fi
 rm -f /tmp/cammy-supervisor.pid "$R/wpa.pid" "$R/dhcp.pid" "$R/portal.pid"
 exit 0
}
trap cleanup TERM INT

stop_ap() {
 for proc in /proc/[0-9]*; do
  [ -r "$proc/comm" ] || continue
  name=$(cat "$proc/comm" 2>/dev/null)
  case "$name" in hostapd|udhcpd) kill -9 "${proc##*/}" 2>/dev/null || true ;; esac
 done
}

ap_fallback() {
 radio_check || return 1
 stop_clients
 stop_ap
 sleep 1
 net_run 5 ifconfig wlan0 down; radio_ok || return 1
 net_run 5 iw dev wlan0 set type __ap; radio_ok || return 1
 net_run 5 ifconfig wlan0 192.168.10.1 netmask 255.255.255.0 up; radio_ok || return 1
 net_run 10 hostapd -B /etc/cammy/ap.conf >"$R/ap.log" 2>&1; radio_ok || return 1
 net_run 5 udhcpd /etc/udhcpd.conf >"$R/ap-dhcp.log" 2>&1; radio_ok || return 1
 echo AP_CammyOS >"$R/state"
}

start_portal() {
 set --
 if [ "$SHARED_AUDIO" = 1 ]; then
  if ! /bin/sh "$R/shared-capture.sh" start >"$R/shared-capture-start.log" 2>&1; then
   /bin/sh "$R/shared-capture.sh" stop >>"$R/shared-capture-start.log" 2>&1 || true
   [ "$(cat "$R/shared-capture.state" 2>/dev/null)" = recovery-required ] || echo unavailable >"$R/shared-capture.state"
   SHARED_AUDIO=0
  else
   set -- --shared-audio
  fi
 fi
 "$R/cammy-portal" --token-file /etc/cammy/portal.token --bind 0.0.0.0 \
  --port 8787 --stock-preview "$@" --daemonize --pid-file "$R/portal.pid"
}

stop_ap
rm -f "$R/lease.txt"
{ read -r UP _ </proc/uptime; } 2>/dev/null || UP=0
WLAN_AT=${UP%%.*}
# The settle applies only to an interface this startup's driver load created.
[ "${WLAN_LOADED:-1}" = 1 ] || WLAN_AT=0
phase station_setup
# The driver creates wlan0 as a station (type=2 in its log). Retype only an
# interface that is not one: the down/retype/up right after creation is a
# needless stress on a driver that oopsed in the first scan after it.
net_run 5 iw dev wlan0 info >"$R/iw-info.log" 2>&1
STATION_TYPE=0
while read -r IW_LINE; do
 [ "$IW_LINE" != "type managed" ] || STATION_TYPE=1
done <"$R/iw-info.log"
if [ "$STATION_TYPE" = 0 ]; then
 net_run 5 ifconfig wlan0 down
 net_run 5 iw dev wlan0 set type managed
fi
net_run 5 ifconfig wlan0 0.0.0.0 up
radio_stop
# Give the driver three seconds after creating wlan0 before the first scan.
while { read -r UP _ </proc/uptime; } 2>/dev/null && [ "${UP%%.*}" -lt $((WLAN_AT + 3)) ] && radio_check; do sleep 1; done
radio_stop
"$R/wpa_supplicant" -i wlan0 -D nl80211 -c "$R/home.conf" >>"$R/wpa.log" 2>&1 &
WPA_PID=$!
echo "$WPA_PID" >"$R/wpa.pid"
STATION_OK=0
T=0
while [ "$T" -lt 35 ] && radio_check; do
 if ! proc_running "$WPA_PID"; then
  # An oops kills the task after setting the taint; read it directly now,
  # ahead of the trail's next poll.
  radio_check direct || break
  echo 'wpa_supplicant exited during station setup'
  diag 'wpa_supplicant exited'
  break
 fi
 if wpa_completed; then STATION_OK=1; break; fi
 sleep 1; T=$((T + 1))
done
radio_stop
if [ "$STATION_OK" = 1 ]; then
 phase dhcp
 "$B" udhcpc -i wlan0 -x hostname:CammyOS -p "$R/dhcp.pid" -s "$R/dhcp.sh" >>"$R/dhcp.log" 2>&1 &
 T=0
 while [ "$T" -lt 25 ] && [ ! -s "$R/lease.txt" ] && radio_check; do sleep 1; T=$((T + 1)); done
 radio_stop
fi
if [ ! -s "$R/lease.txt" ]; then
 phase ap_fallback
 ap_fallback
 radio_stop
else
 echo HOME_STATION >"$R/state"
fi
phase portal
start_portal || cleanup
if [ "$NATIVE_VIDEO" = 1 ]; then
 phase native_media
 /bin/sh "$R/native-media-start.sh" start >"$R/native-video.log" 2>&1 || true
 diag "native-media exit state=$(cat "$R/native-video.state" 2>/dev/null)"
fi
phase running
MISSES=0
HEALTHY=0
# The loop waits with the shell's read timeout on a private FIFO instead of
# forking sleep every pass; a writer can wake it early with one line. BusyBox
# here has no mkfifo, so fault-evidence makes it; without one, sleep is kept.
WAKE=$R/wake
if [ -x "$R/fault-evidence" ] && "$R/fault-evidence" mkfifo "$WAKE" 2>/dev/null && [ -p "$WAKE" ] && [ ! -L "$WAKE" ]; then
 exec 3<>"$WAKE"
 supervisor_wait() { read -t 2 WAKE_LINE <&3 || :; }
else
 supervisor_wait() { sleep 2; }
fi
CAP_PASSES=0
while true; do
 supervisor_wait
 # A kernel oops stops every network command for the rest of the boot.
 [ "$RADIO_DOWN" = 1 ] || radio_check || true
 # RAM logs stay at most 64 KiB each; one bounded fork a minute.
 CAP_PASSES=$((CAP_PASSES + 1))
 if [ "$CAP_PASSES" -ge 30 ]; then
  CAP_PASSES=0
  [ ! -x "$R/fault-evidence" ] || "$R/fault-evidence" cap 65536 "$R"/*.log /tmp/cammy-startup.log 2>/dev/null || true
 fi
 if [ -e "$GUARD" ]; then
  P=$(cat "$R/portal.pid" 2>/dev/null)
  case "$P" in ''|*[!0-9]*) P=0 ;; esac
  if [ "$P" -gt 1 ] && [ "$(cat /proc/$P/comm 2>/dev/null)" = cammy-portal ]; then
   HEALTHY=$((HEALTHY + 1))
   if [ "$HEALTHY" -ge 30 ]; then
    rm -f "$GUARD"
    sync
    diag 'latch cleared'
    echo 'Startup latch cleared after 60 s portal survival'
   fi
  else
   HEALTHY=0
  fi
 fi
 [ "$(cat "$R/state")" = HOME_STATION ] || continue
 if wpa_completed; then
  MISSES=0
 else
  MISSES=$((MISSES + 1))
  [ "$MISSES" -lt 20 ] || ap_fallback
 fi
done
