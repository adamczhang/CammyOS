#!/bin/sh
# Explicit one-per-boot preparation. Never called automatically during boot.
set -u
umask 077
R=/tmp/cammy-runtime
D=$R/media-prepare
GUARD=/etc/cammy/startup.pending
BOOT=$(cat /proc/sys/kernel/random/boot_id)
[ -f "$D/request" ] && [ ! -L "$D/request" ] || exit 0
[ ! -e "$D/work.lock" ] || exit 0
mkdir "$D/work.lock" || exit 1
status(){ printf '%s\n' "$1" >"$D/state"; }
# The camera has neither rmdir nor rm -d. Remove only a real, empty directory.
remove_empty_dir(){
 [ -d "$1" ] && [ ! -L "$1" ] || return 1
 for entry in "$1"/* "$1"/.[!.]* "$1"/..?*;do
  if [ -e "$entry" ] || [ -L "$entry" ];then return 1;fi
 done
 rm -r "$1"
}
STAMP=/run/cammy-select/launch.stamp
MEDIA_GUARD=$(printf 'phase=media_prepare\nboot_id=%s' "$BOOT")
# Replace the startup flag atomically from stdin: write and sync a private
# temporary record, then rename it, so a power cut never leaves a partial flag.
# Health's watchdog lets its fault latch proceed 150 s after it first sees this
# worker. No latch write of this worker may land after that, so every write is
# refused from the request deadline plus 45 s (well inside that bound).
late(){
 awk -v end="$END" '{exit ($1*1000<end+45000)}' /proc/uptime 2>/dev/null
 [ "$?" != 1 ]
}
put_guard(){
 GT=/etc/cammy/.media-guard.$$
 rm -f "$GT"
 if (set -C; cat >"$GT") && sync && [ ! -d "$GUARD" ] && ! late && mv -f "$GT" "$GUARD"; then return 0; fi
 rm -f "$GT"
 return 1
}
finish(){ "$R/native-service-client" release >/dev/null 2>&1 || true; }
trap finish EXIT
trap 'fail cancelled' TERM INT HUP
status initializing
fail(){
 if [ -f "$GUARD" ] && [ ! -L "$GUARD" ] &&
    [ "$(cat "$GUARD")" = "$MEDIA_GUARD" ] && [ "${RETRYABLE_GUARD:-0}" = 1 ]; then
  printf 'phase=runtime_health_fault\nboot_id=%s\n' "$BOOT" | put_guard && sync || true
 fi
 status requires-recovery;printf '%s\n' "$1" >"$D/reason";exit 1; }
# Restore the boot latch only while it is still this worker's media latch;
# never overwrite a fault latch written meanwhile.
restore_guard(){
 [ -f "$GUARD" ] && [ ! -L "$GUARD" ] && [ "$(cat "$GUARD")" = "$MEDIA_GUARD" ] || fail guard-changed
 ! late || fail guard-restore-late
 if [ -f "$D/prior-guard" ]; then put_guard <"$D/prior-guard" || fail guard-restore
 else rm -f "$GUARD" || fail guard-clear
 fi
 sync || fail guard-sync
}
# Health defers these records while the supervisor reports this worker running
# and reads them only once it has exited. The lock goes last: until then no new
# attempt can start.
refuse(){
 restore_guard
 printf '%s\n' "$1" >"$D/reason"
 status refused
 rm -f "$D/request" "$D/prior-guard"
 remove_empty_dir "$D/work.lock" || fail lock-clear
 exit 0
}
# Past the request's monotonic deadline (or an unreadable clock).
expired(){
 awk -v end="$END" '{exit ($1*1000<end)}' /proc/uptime 2>/dev/null
 [ "$?" != 1 ]
}
[ "$(wc -l <"$D/request")" -eq 3 ] || fail request-shape
[ "$(sed -n 's/^format=//p' "$D/request")" = cammy-media-prepare/1 ] || fail request-format
[ "$(sed -n 's/^boot=//p' "$D/request")" = "$(printf '%s' "$BOOT" | tr -d '-')" ] || fail request-boot
END=$(sed -n 's/^deadline_ms=//p' "$D/request")
case "$END" in ''|*[!0-9]*) fail request-deadline;; esac
NOW=$(awk '{printf "%.0f",$1*1000}' /proc/uptime)
[ "${#END}" -le 15 ] || fail request-expired
awk -v end="$END" -v now="$NOW" 'BEGIN{exit !(end>now && end-now<=90000)}' || fail request-expired
export CAMMY_PREPARE_DEADLINE_MS="$END"
export CAMMY_PREPARE_CANCEL="$D/cancel"
[ ! -e "$D/cancel" ] || fail cancelled
"$R/face-status" >"$D/gui-before" || fail gui-unavailable
# Bind the exact current native executable; the release profile is hashed as
# part of the verified TF payload. No arbitrary path or sourced shell data.
[ "$(sed -n 's/^format=//p' "$R/native-release.conf")" = cammy-native-release/1 ] || fail release-format
HASH=$(sed -n 's/^candidate_sha256=//p' "$R/native-release.conf")
case "$HASH" in *[!0-9a-f]*|'') fail release-hash;; esac
[ "${#HASH}" -eq 64 ] || fail release-hash
P=$(pidof sdvcam);case "$P" in ''|*[!0-9]*) fail native-owner;; esac
[ "$(readlink /proc/$P/exe)" = /run/cammy-select/sdvcam ] || fail native-exe
# The integrated native-media-start hashes this candidate against the pinned
# template before any native request. This worker only records the intent.
RID=$(sed -n 's/^release_id=//p' "$R/native-release.conf")
MD5=$(sed -n 's/^md5=//p' "$STAMP" 2>/dev/null)
case "$RID:$MD5" in *[!0-9a-f:]*) MD5=;; esac
if [ "${#RID}" -eq 24 ] && [ "${#MD5}" -eq 32 ] &&
 [ "$(sed -n 's/^target=//p' "$STAMP")" = candidate ] &&
 [ "$(sed -n 's/^boot=//p' "$STAMP")" = "$BOOT" ]; then
 RETRYABLE_GUARD=1
 MEDIA_GUARD=$(printf '%s\nrelease_id=%s\ncandidate_md5=%s' "$MEDIA_GUARD" "$RID" "$MD5")
fi
# Keep a failure latch before the first media side effect. Restore the boot
# latch on success; never erase another boot's failure or a new unrelated latch.
if [ -e "$GUARD" ]; then
 [ "$(sed -n 's/^boot_id=//p' "$GUARD")" = "$BOOT" ] || fail prior-startup-fault
 cp "$GUARD" "$D/prior-guard" || fail guard-copy
fi
printf '%s\n' "$MEDIA_GUARD" | put_guard || fail guard-write
sync || fail guard-sync
[ ! -e "$D/cancel" ] || fail cancelled
# Nothing native has happened yet, so running out of time here is clean.
! expired || refuse deadline-before-native
rm -f "$R/native-video.state"
# Step times for diagnosis (RAM only): every failure so far needed them (0.7.5).
export CAMMY_DIAG_LOG="$D/diag.log"
/bin/sh "$R/native-media-start.sh" start >"$D/native.log" 2>&1 || true
# A refusal before the native acquire-and-init step changed nothing on the camera, so it
# is retryable and must not leave a startup latch (0.7.4; 2026-11-16 mode 7 refusal).
NV=$(cat "$R/native-video.state" 2>/dev/null)
# skipped-network: the camera serves its own access point (router outage); the
# native step needs the home station and stopped before touching anything.
case "$NV" in refused-stock|refused-app|refused-identity|refused-address|refused-native-mode|refused-deadline|skipped-busy|skipped-network) refuse "native-$NV";; esac
# Unproved mode restoration outranks why the run stopped.
case "$NV" in mode-restore-expired|mode-restore-failed|mode-restore-unconfirmed) fail "native-$NV";; esac
[ ! -e "$D/cancel" ] || fail cancelled
case "$NV" in deadline-exceeded) fail deadline-exceeded;; cancelled) fail cancelled;; esac
[ "$NV" = sdp-ready-idle ] || fail native-not-ready
# The whole preparation shares one deadline; a late success still fails closed.
! expired || fail deadline-exceeded
"$R/native-service-client" idle >/dev/null || fail ownership-not-idle
[ "$(cat /proc/asound/card0/pcm0c/sub0/status)" = closed ] || fail capture-not-closed
"$R/face-status" >"$D/gui-after" || fail gui-unavailable
restore_guard
finish
"$R/native-service-client" idle >/dev/null || fail ownership-not-idle
trap - EXIT
status ready
