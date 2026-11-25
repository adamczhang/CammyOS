#!/bin/sh
# Opt-in RAM configuration only. No internal file bytes, app or kernel replaced.
set -u
umask 077
R=/tmp/cammy-runtime
D=$R/shared-capture
B=$R/busybox
ASOUND=/etc/asound.conf
GLOBAL=/usr/share/alsa/alsa.conf
EXPECTED_ASOUND=1e4399abc3506fcc71de5fcdc96eb5ae24dc825a1b605a2f2a4f01550190edf9
EXPECTED_GLOBAL=b2776093cbe6c4560de86f08166ed97761e5324861394df72c2b0c4fde732d31
digest() { result=$("$B" sha256sum "$1") || return 1; set -- $result; [ "$#" = 2 ] || return 1; printf '%s' "$1"; }
mounted_at() { grep " $1 " /proc/mounts >/dev/null; }
same_inode() {
 set -- $(ls -i "$1"); first=$1
 set -- $(ls -i "$2"); [ "$first" = "$1" ]
}
starttime() {
 read -r st_line <"/proc/$1/stat" 2>/dev/null || return 1
 case "$st_line" in *') '*) ;; *) return 1;; esac
 set -- ${st_line##*) }
 [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
 shift 19; printf '%s' "$1"
}
same_epoch() {
 read -r ep_line <"$D/epoch" 2>/dev/null && [ "$ep_line" = "$1" ]
}
restore() {
 # Only one of supervisor, guardian or explicit stop owns restoration.
 mkdir -m 700 "$R/shared-capture.restore.lock" 2>/dev/null || return 1
 rm -f "$R/shared-capture.active"
 failed=0
 if mounted_at "$GLOBAL"; then
  if [ -f "$D/global.conf" ] && same_inode "$GLOBAL" "$D/global.conf"; then
   umount "$GLOBAL" || failed=1
  else failed=1; fi
 fi
 if mounted_at "$ASOUND"; then
  if [ -f "$D/asound.conf" ] && same_inode "$ASOUND" "$D/asound.conf"; then
   umount "$ASOUND" || failed=1
  else failed=1; fi
 fi
 [ "$(digest "$ASOUND")" = "$EXPECTED_ASOUND" ] || failed=1
 [ "$(digest "$GLOBAL")" = "$EXPECTED_GLOBAL" ] || failed=1
 if [ "$failed" = 0 ]; then
  echo restored >"$R/shared-capture.state"
  rm -rf "$D"
 else echo recovery-required >"$R/shared-capture.state"; fi
 rm -rf "$R/shared-capture.restore.lock" || failed=1
 return "$failed"
}
case "${1:-}" in
 start)
  grep '^Uid:[[:space:]]*0[[:space:]]*0[[:space:]]*0[[:space:]]*0[[:space:]]*$' /proc/self/status >/dev/null || exit 2
  [ -d "$R" ] && [ ! -L "$R" ] && [ ! -e "$D" ] && [ ! -e "$R/shared-capture.restore.lock" ] || exit 3
  set -- $(ls -ldn "$R")
  [ "$1" = drwx------ ] && [ "$3" = 0 ] && [ "$4" = 0 ] || exit 3
  if [ -r "$R/portal.pid" ]; then
   read -r prior <"$R/portal.pid"
   case "$prior" in ''|*[!0-9]*) prior=0;; esac
   [ "$prior" -lt 2 ] || [ "$(cat /proc/$prior/comm 2>/dev/null)" != cammy-portal ] || exit 13
  fi
  mounted_at "$ASOUND" && exit 4
  mounted_at "$GLOBAL" && exit 4
  [ "$(cat /proc/asound/card0/pcm0c/sub0/status)" = closed ] || exit 5
  [ "$(digest "$ASOUND")" = "$EXPECTED_ASOUND" ] || exit 6
  [ "$(digest "$GLOBAL")" = "$EXPECTED_GLOBAL" ] || exit 6
  # Never attach to an unrelated pre-existing direct-PCM namespace.
  grep -E '^[[:space:]]*1129142530[[:space:]]' /proc/sysvipc/shm >/dev/null && exit 7
  [ -x "$R/daemon-exec" ] || exit 14
  mkdir -m 700 "$D" || exit 8
  cp "$ASOUND" "$D/asound.conf" && cp "$GLOBAL" "$D/global.conf" || exit 9
  chmod 600 "$D/asound.conf" "$D/global.conf"
  cat "$R/shared-capture.fragment" >>"$D/asound.conf" || exit 9
  epoch=$$-$(starttime $$)
  printf '%s\n' "$epoch" >"$D/epoch"
  # Guard is armed before the first bind; portal launch follows immediately.
  detached=$("$R/daemon-exec" "$R/shared-capture-guard.log" /bin/sh "$R/shared-capture.sh" watch "$epoch") || { restore; exit 15; }
  set -- $detached
  [ "$#" = 3 ] && [ "$1" = Detached ] && [ "$2" = PID ] || { restore; exit 15; }
  case "$3" in ''|*[!0-9]*) restore; exit 15;; esac
  echo "$3" >"$D/guard.pid"
  if ! mount -o bind "$D/asound.conf" "$ASOUND"; then restore; exit 10; fi
  # Identical global bytes, new timestamp: refresh cached hook-loaded config.
  if ! mount -o bind "$D/global.conf" "$GLOBAL"; then restore; exit 11; fi
  same_inode "$ASOUND" "$D/asound.conf" && same_inode "$GLOBAL" "$D/global.conf" || { restore; exit 12; }
  printf 'm105-shared-v1\n' >"$R/shared-capture.active"
  echo active >"$R/shared-capture.state"
  ;;
 stop) restore ;;
 watch)
  pid=0; attempt=0
  while [ "$attempt" -lt 10 ]; do
   same_epoch "${2:-}" || exit 0
   if [ -r "$R/portal.pid" ]; then
    read -r pid <"$R/portal.pid"
    case "$pid" in ''|*[!0-9]*) pid=0;; esac
    if [ "$pid" -gt 1 ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = cammy-portal ]; then break; fi
    pid=0
   fi
   sleep 1; attempt=$((attempt+1))
  done
  if [ "$pid" -gt 1 ]; then
   reference=$(starttime "$pid")
   printf '%s %s\n' "$pid" "$reference" >"$D/guard.ready"
   # The native watcher polls the portal once a second with no fork. A
   # pipe-EOF notice would need the daemonizing portal to hold a pipe end.
   # Without the watcher the shared mode is not kept: restore at once.
   if [ -n "$reference" ] && [ -x "$R/fault-evidence" ]; then
    "$R/fault-evidence" watch-shared "$pid" "$reference" "${2:-}" || true
   fi
  fi
  # Native preview closes asynchronously after its portal event socket closes.
  attempt=0
  while [ "$(cat /proc/asound/card0/pcm0c/sub0/status)" != closed ] && [ "$attempt" -lt 3 ]; do sleep 1; attempt=$((attempt+1)); done
  same_epoch "${2:-}" || exit 0
  restore
  ;;
 *) exit 2;;
esac
