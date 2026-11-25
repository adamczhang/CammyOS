#!/bin/sh
# Local health acknowledgement; no native restart/reboot or automatic sensing.
set -u
umask 077
R=/tmp/cammy-runtime
D=$R/release-health
CFG=/etc/cammy
GUARD=$CFG/startup.pending
MEDIA=$R/media-prepare
SPENT=$CFG/native-select.crash-used
RADIO_SPENT=$CFG/native-select.radio-used
STAMP=/run/cammy-select/launch.stamp

nor_sync() {
 "$R/fault-evidence" sync
}

crash_identity() {
 [ -f "$STAMP" ] && [ ! -L "$STAMP" ] || return 1
 [ "$(sed -n 's/^target=//p' "$STAMP")" = candidate ] &&
 [ "$(sed -n 's/^boot=//p' "$STAMP")" = "$BOOT" ] || return 1
 CRASH_MD5=$(sed -n 's/^md5=//p' "$STAMP")
 case "$CRASH_MD5" in *[!0-9a-f]*) return 1;; esac
 [ "${#CRASH_MD5}" -eq 32 ] || return 1
 [ -f "$CFG/native-select.resident" ] && [ ! -L "$CFG/native-select.resident" ] &&
 [ "$(cat "$CFG/native-select.resident")" = "$(printf 'format=cammy-resident/1\nrelease_id=%s' "$ID")" ]
}

mark_eligible_loss() {
 [ "$GUARD_STATE" = absent ] && [ -z "$WORKER" ] && [ -f "$D/committed" ] || return 1
 for path in "$SPENT" "$CFG/native-select.retry" "$CFG/native-select.retry-used" "$CFG/native-select.advance" "$MEDIA/work.lock" "$MEDIA/cancel"; do
  [ ! -e "$path" ] && [ ! -L "$path" ] || return 1
 done
 case "$(cat "$MEDIA/state" 2>/dev/null)" in ''|refused|ready) ;; *) return 1;; esac
 # The original verified process must actually be gone. An ambiguous pidof
 # result or live replacement does not authorize recovery.
 read -r OLD_PID OLD_TICK <"$D/owner" || return 1
 case "$OLD_PID:$OLD_TICK" in *[!0-9:]*|:*|*:) return 1;; esac
 [ ! -e "/proc/$OLD_PID" ] && [ -z "$(pidof sdvcam)" ] || return 1
 crash_identity && printf '%s\n' "$CRASH_MD5" >"$D/eligible-loss"
}
CARD=@CARD@

fail() {
 [ ! -f "$D/display-ready" ] || rm -f "$D/display-ready"
 printf 'health_reason=%s\n' "$1"
 exit 1
}

write_record() {
 target=$1
 shift
 temp=$CFG/.health-write.$$
 (set -C; printf '%s\n' "$@" >"$temp") || return 1
 # Persist the complete record before the rename, so a power cut leaves the
 # old record or the new one. Stock BusyBox mv has no -T: refuse directories.
 if ! nor_sync || [ -d "$target" ] || ! mv -f "$temp" "$target"; then
  rm -f "$temp"
  return 1
 fi
}

record_fault() {
 REASON=$(sed -n 's/^health_reason=//p' "$R/health.log" 2>/dev/null)
 case "$REASON" in ''|*[!a-z0-9-]*) REASON=health-script-failed;; esac
 # Keep an earlier media or unexpected boot latch intact. Write the guard
 # first so an interruption cannot create a retryable half-fault.
 if [ "$GUARD_STATE" = absent ] && [ "$REASON" = native-pid ] &&
    [ -f "$D/eligible-loss" ] && crash_identity &&
    [ "$(cat "$D/eligible-loss")" = "$CRASH_MD5" ]; then
  write_record "$GUARD" 'phase=post_healthy_fault' "boot_id=$BOOT" "release_id=$ID" "candidate_md5=$CRASH_MD5" || return 1
 elif [ "$GUARD_STATE" = absent ] || [ "$GUARD_STATE" = idle ] ||
      { [ "$GUARD_STATE" = media ] && [ "$RETRYABLE_GUARD" = 1 ]; }; then
  write_record "$GUARD" 'phase=runtime_health_fault' "boot_id=$BOOT" || return 1
 fi
 nor_sync || return 1
 DETAIL=none
 case "$REASON" in
  media-*)
   DETAIL=$(cat "$MEDIA/reason" 2>/dev/null)
   case "$DETAIL" in ''|*[!a-z0-9-]*) DETAIL=none;; esac
   ;;
 esac
 [ "${#REASON}" -le 64 ] || REASON=health-script-failed
 [ "${#DETAIL}" -le 64 ] || DETAIL=none
 case "$ID" in ''|*[!a-z0-9-]*) ID=unknown;; esac
 [ "${#ID}" -le 64 ] || ID=unknown
 REARMED=no
 [ ! -f "$D/committed" ] || REARMED=yes
 write_record "$CFG/runtime-health-fault" 'format=cammy-runtime-health-fault/1' \
  "boot_id=$BOOT" "release_id=$ID" "uptime_s=${NOW:-unknown}" \
  "reason=$REASON" "media_reason=$DETAIL" "guard_before=${GUARD_STATE%-before-crash}" "rearmed=$REARMED" || return 1
 nor_sync
}

# The supervisor names the media worker it is running as PID:start-tick (empty
# when none runs). While one runs, its records (state, request and the boot
# latch) change between any two reads here, so none of them is compared; the
# check below reads only a terminal failure, which a worker never leaves. A
# watchdog timed per worker identity bounds the deferral: 150 s from the first
# sight of a worker, more than its 90 s request deadline plus the 45 s after
# which the worker refuses every latch write. Past it the latch is settled.
WORKER=${CAMMY_MEDIA_WORKER:-}
case "$WORKER" in
 '') ;;
 *[!0-9:]*|:*|*:|*:*:*) [ "${1:-}" = latch ] && WORKER= || fail media-worker-identity;;
 *:*) ;;
 *) [ "${1:-}" = latch ] && WORKER= || fail media-worker-identity;;
esac
# 0: this worker is inside its bound; 1: past it; 2: the timer is unusable.
worker_watch() {
 mkdir -p "$D" || return 2
 if [ "$(cat "$D/media-worker" 2>/dev/null)" != "$WORKER" ]; then
  rm -f "$D/media-worker-since" || return 2
  printf '%s\n' "$WORKER" >"$D/media-worker" || return 2
 fi
 if [ ! -e "$D/media-worker-since" ]; then printf '%s\n' "$NOW" >"$D/media-worker-since" || return 2; fi
 START=$(cat "$D/media-worker-since")
 case "$START" in ''|*[!0-9]*) return 2;; esac
 [ "$NOW" -ge "$START" ] || return 2
 [ "$NOW" -lt "$((START + 150))" ] || return 1
}

BOOT=$(cat /proc/sys/kernel/random/boot_id)
case "$BOOT" in
 ''|*[!0-9a-f-]*) [ "${1:-}" = latch ] || fail boot-id; BOOT=unknown;;
esac
ID=$(sed -n 's/^release_id=//p' "$R/native-release.conf")
NOW=$(awk '{print int($1)}' /proc/uptime)
case "$NOW" in ''|*[!0-9]*) NOW=;; esac
GUARD_STATE=absent
RETRYABLE_GUARD=0
if [ -L "$GUARD" ]; then
 GUARD_STATE=unexpected
elif [ -e "$GUARD" ]; then
 GUARD_STATE=unexpected
 if [ -f "$GUARD" ]; then
  case "$(cat "$GUARD")" in
   "$(printf 'phase=idle_ready\nboot_id=%s' "$BOOT")") GUARD_STATE=idle;;
   "$(printf 'phase=media_prepare\nboot_id=%s' "$BOOT")") GUARD_STATE=media;;
   "$(printf 'phase=idle_ready\nboot_id=%s\nrelease_id=%s\ncandidate_md5=' "$BOOT" "$ID")"*|\
   "$(printf 'phase=media_prepare\nboot_id=%s\nrelease_id=%s\ncandidate_md5=' "$BOOT" "$ID")"*)
    MD5=$(sed -n 's/^md5=//p' "$STAMP")
    for phase in idle_ready media_prepare; do
     [ "$(cat "$GUARD")" = "$(printf 'phase=%s\nboot_id=%s\nrelease_id=%s\ncandidate_md5=%s' "$phase" "$BOOT" "$ID" "$MD5")" ] || continue
     case "$MD5" in *[!0-9a-f]*) break;; esac
     [ "${#MD5}" -eq 32 ] || break
     RETRYABLE_GUARD=1
     case "$phase" in idle_ready) GUARD_STATE=idle;; *) GUARD_STATE=media;; esac
    done;;
   "$(printf 'phase=post_healthy_fault\nboot_id=%s\nrelease_id=%s\ncandidate_md5=' "$BOOT" "$ID")"*) GUARD_STATE=absent-before-crash;;
   "$(printf 'phase=runtime_health_fault\nboot_id=%s' "$BOOT")") GUARD_STATE=fault;;
   # Startup recorded a radio fault for this boot; the next boot owns it.
   "$(printf 'phase=radio_fault\nboot_id=%s\nrelease_id=%s\ncandidate_md5=' "$BOOT" "$ID")"*) GUARD_STATE=radio;;
  esac
 fi
fi
snapshot() {
 # Deduplicate on RAM. A blocked or missing card must never delay the
 # authoritative latch. The child has its own deadline and flushes only TF.
 mkdir -p "$D" || return
 (set -C; : >"$D/evidence-attempt") 2>/dev/null || return
 PHASE=$(sed -n 's/^phase=//p' "$GUARD" 2>/dev/null)
 if [ "$PHASE" = launch ] && [ -r "$R/startup.phase" ]; then
  read -r PHASE <"$R/startup.phase" || PHASE=launch
 fi
 case "$PHASE" in
  launch|payload|stock_settle|radio_load|station_setup|dhcp|ap_fallback|portal|menu|idle_ready|media_prepare|runtime_health_fault|post_healthy_fault|radio_fault) ;;
  '')
   if [ -f "$D/committed" ]; then PHASE=resident; else PHASE=$GUARD_STATE; fi
   ;;
  *) PHASE=unknown;;
 esac
 "$R/fault-evidence" snapshot "$BOOT" "$ID" "$1" "$PHASE" "${NOW:-0}" >/dev/null 2>&1 &
}

if [ "${1:-}" = snapshot ]; then
 snapshot "${2:-startup-failed}"
 exit 0
fi
if [ "${1:-}" = latch ]; then
 REASON=$(sed -n 's/^health_reason=//p' "$R/health.log" 2>/dev/null)
 snapshot "${REASON:-health-script-failed}"
 # A running worker may still restore or replace the boot latch, so the
 # snapshot above could already be stale. Wait for it (the supervisor retries
 # every pass) until its watchdog expires; an unusable timer does not wait.
 if [ -n "$WORKER" ] && [ -n "$NOW" ] && worker_watch; then
  echo latch-deferred-media-worker
  exit 3
 fi
 # A radio-fault latch already decides the next boot; never add a health
 # fault record over it (that would also forfeit the one radio retry).
 if [ "$GUARD_STATE" = radio ]; then
  echo latch-kept-radio-fault
  exit 0
 fi
 record_fault
 exit $?
fi

mkdir -p "$D" || fail health-directory
case "$ID" in ''|*[!a-z0-9-]*) fail release-id;; esac
[ -n "$NOW" ] || fail uptime
[ "$GUARD_STATE" != unexpected ] || fail startup-guard
case "$GUARD_STATE" in fault|absent-before-crash) fail startup-fault-latched;; esac
# After a radio fault startup runs no network command; nothing here to prove.
[ "$GUARD_STATE" != radio ] || exit 0
# Always validate the native and portal identity, including after rearm.
PID=$(pidof sdvcam)
case "$PID" in '') mark_eligible_loss || true; fail native-pid;; *[!0-9]*) fail native-pid;; esac
[ "$(readlink /proc/$PID/exe)" = /run/cammy-select/sdvcam ] || fail native-exe
TICK=$(awk '{print $22}' /proc/$PID/stat)
case "$TICK" in ''|*[!0-9]*) fail native-start-tick;; esac
# S01 checked the RAM candidate's MD5 immediately before exec and stamped it
# for this boot; bind that stamp to this release instead of hashing the
# 11 MB executable again. The process identity check above names the file.
HASH=$(sed -n 's/^candidate_md5=//p' "$R/native-release.conf")
case "$HASH" in *[!0-9a-f]*|'') fail release-hash;; esac
[ "${#HASH}" -eq 32 ] || fail release-hash
if [ ! -f "$D/owner" ]; then
 [ -f "$STAMP" ] && [ ! -L "$STAMP" ] && [ "$(sed -n 's/^target=//p' "$STAMP")" = candidate ] &&
  [ "$(sed -n 's/^boot=//p' "$STAMP")" = "$BOOT" ] && [ "$(sed -n 's/^md5=//p' "$STAMP")" = "$HASH" ] || fail native-hash
 printf '%s %s\n' "$PID" "$TICK" >"$D/owner" || fail owner-write
fi
[ "$(cat "$D/owner")" = "$PID $TICK" ] || fail native-owner-changed
P=$(cat "$R/portal.pid")
case "$P" in ''|*[!0-9]*) fail portal-pid;; esac
[ "$(cat /proc/$P/comm)" = cammy-portal ] || fail portal-owner

reset_window() {
 [ ! -e "$D/display-ready" ] || rm -f "$D/display-ready" || fail display-ready-clear
 [ ! -e "$D/since" ] || rm -f "$D/since" || fail health-timer-clear
 [ ! -e "$D/retry-since" ] || rm -f "$D/retry-since" || fail health-timer-clear
 [ ! -e "$D/radio-since" ] || rm -f "$D/radio-since" || fail health-timer-clear
}

# Defer acknowledgement during bounded gaps, restarting the full healthy window.
wait_for() {
 reset_window
 TIMER=$D/$1-since
 if [ ! -e "$TIMER" ]; then printf '%s\n' "$NOW" >"$TIMER" || fail health-timer-write; fi
 START=$(cat "$TIMER")
 case "$START" in ''|*[!0-9]*) fail health-timer;; esac
 [ "$NOW" -ge "$START" ] || fail uptime-regressed
 [ "$NOW" -lt "$((START + $2))" ] || fail "$1-timeout"
}

# A cancel record (portal or shutdown) and a terminal failure are final, with
# or without a running worker.
[ ! -e "$MEDIA/cancel" ] && [ ! -L "$MEDIA/cancel" ] || fail media-cancelled
MEDIA_STATE=$(cat "$MEDIA/state" 2>/dev/null)
case "$MEDIA_STATE" in
 requires-recovery|failed|cancelled) fail "media-$MEDIA_STATE";;
 ''|refused|queued|initializing|ready) ;;
 *) fail media-invalid-state;;
esac
# A running worker: defer every other media comparison, the GUI sample and
# acknowledgement (as for a queued job) until it has exited, within the bound.
if [ -n "$WORKER" ]; then
 reset_window
 worker_watch
 case "$?" in
  0) exit 0;;
  1) fail media-worker-timeout;;
  *) fail health-timer;;
 esac
fi
# No worker runs, so the records below are settled apart from the portal
# queueing a new request. A queued, initializing or still-refusing job is not a
# failed media transaction. The wait covers the request's 90 s deadline plus
# bounded cleanup (each client call is capped at 8 s).
[ ! -L "$MEDIA/request" ] || fail media-request
if [ -f "$MEDIA/request" ]; then
 case "$MEDIA_STATE" in
  ''|queued|initializing|refused)
   # Time each request on its own: a refused job's successor (the portal
   # allows five per boot) gets a fresh, still bounded wait.
   JOB=$(sed -n 's/^deadline_ms=//p' "$MEDIA/request")
   if [ "$(cat "$D/media-job" 2>/dev/null)" != "$JOB" ]; then
    rm -f "$D/media-since" || fail health-timer-clear
    printf '%s\n' "$JOB" >"$D/media-job" || fail health-timer-write
   fi
   wait_for media 120
   exit 0
   ;;
  ready)
   [ "$GUARD_STATE" != media ] || fail media-guard-not-restored
   if [ ! -f "$D/media-ready" ]; then
    reset_window
    : >"$D/media-ready" || fail media-ready-write
   fi
   ;;
  *) fail media-invalid-state;;
 esac
else
 [ ! -e "$MEDIA/request" ] || fail media-request
 case "$MEDIA_STATE" in queued|initializing|ready) fail media-request-missing;; esac
 [ "$GUARD_STATE" != media ] || fail media-guard-not-restored
fi
[ ! -e "$D/media-since" ] || rm -f "$D/media-since" || fail health-timer-clear

# The helper fails when its GUI sample is stale. Bound a run of misses while
# ensuring a poll with a mismatched owner can never contribute to health.
# PCM open/close is expected during a live mic session and its rollover.
"$R/face-status" >"$D/current"
case "$?" in
 0) ;;
 1) wait_for gui 10; exit 0;;
 *) fail gui-probe;;
esac
[ "$(sed -n 's/^native_pid=//p' "$D/current")" = "$PID" ] || fail gui-owner
[ ! -e "$D/gui-since" ] || rm -f "$D/gui-since" || fail health-timer-clear
# A working network path is the home station or, after a router outage, the
# camera's own access point with its servers running. A healthy fallback boot
# is not an interrupted startup, so it clears the startup flag like a station
# boot. Any other state defers; a change of path restarts the window.
NET=$(cat "$R/state" 2>/dev/null)
case "$NET" in
 HOME_STATION) ;;
 AP_CammyOS) [ -n "$(pidof hostapd)" ] && [ -n "$(pidof udhcpd)" ] || NET=;;
 *) NET=;;
esac
if [ -z "$NET" ]; then
 reset_window
 exit 0
fi
if [ "$(cat "$D/network" 2>/dev/null)" != "$NET" ]; then
 reset_window
 printf '%s\n' "$NET" >"$D/network" || fail health-network-write
fi
if [ ! -e "$D/since" ]; then printf '%s\n' "$NOW" >"$D/since" || fail health-timer-write; fi
SINCE=$(cat "$D/since")
case "$SINCE" in ''|*[!0-9]*) fail health-timer;; esac
[ "$NOW" -ge "$SINCE" ] || fail uptime-regressed
# Open the screen after a short run of passing probes. The longer window
# still controls guard clearance and persistent boot authority below.
if [ "$NOW" -ge "$((SINCE+6))" ] && [ ! -f "$D/display-ready" ]; then
 (set -C; printf 'release_id=%s\nboot=%s\nnetwork=%s\n' "$ID" "$BOOT" "$NET" >"$D/display-ready.$$") &&
  [ ! -d "$D/display-ready" ] && mv -f "$D/display-ready.$$" "$D/display-ready" || fail display-ready-write
fi
[ "$NOW" -ge "$((SINCE+120))" ] || exit 0
if [ ! -f "$D/healthy" ]; then
 if [ "$GUARD_STATE" = idle ] && [ "$RETRYABLE_GUARD" = 0 ]; then
  rm -f "$GUARD" || fail startup-guard-clear
  nor_sync || fail startup-guard-sync
 fi
 printf 'release_id=%s\nboot=%s\nnetwork=%s\n' "$ID" "$BOOT" "$NET" >"$D/healthy" || fail healthy-write
fi
# Count only uninterrupted passing samples on the designated recovery boot.
# The ordinary readiness timer can span a stuck supervisor; this one cannot.
if [ -f "$D/committed" ] && [ -f "$SPENT" ] && [ ! -L "$SPENT" ] && crash_identity; then
 . "$R/boot-helper.sh" || fail helper-unavailable
 if cammy_crash_ledger && [ "$pr_retry" = "$BOOT" ] && [ "$pr_release" = "$ID" ] &&
    [ "$pr_md5" = "$CRASH_MD5" ] && cammy_crash_authority; then
  LAST=$(cat "$D/retry-last" 2>/dev/null)
  case "$LAST" in ''|*[!0-9]*) LAST=0;; esac
  if [ ! -f "$D/retry-since" ] || [ "$NOW" -lt "$LAST" ] || [ "$((NOW-LAST))" -gt 12 ]; then
   printf '%s\n' "$NOW" >"$D/retry-since" || fail recovery-timer
  fi
  printf '%s\n' "$NOW" >"$D/retry-last" || fail recovery-timer
  START=$(cat "$D/retry-since")
  case "$START" in ''|*[!0-9]*) fail recovery-timer;; esac
  if [ "$((NOW-START))" -ge 900 ]; then
   rm -f "$SPENT" && nor_sync || fail recovery-replenish
  fi
 fi
fi
# The radio-retry ledger is replenished by the same 900 s of uninterrupted
# sampled health on any committed boot of its release. A well-formed ledger
# of another release is retired at once: the budget is release-bound.
if [ -f "$D/committed" ] && [ -f "$RADIO_SPENT" ] && [ ! -L "$RADIO_SPENT" ] && crash_identity; then
 . "$R/boot-helper.sh" || fail helper-unavailable
 if cammy_record_ok "$RADIO_SPENT" format release_id candidate_md5 failed_boot retry_boot &&
    [ "$(cammy_get format "$RADIO_SPENT")" = cammy-radio-retry-used/1 ]; then
  if [ "$(cammy_get release_id "$RADIO_SPENT")" != "$ID" ]; then
   rm -f "$RADIO_SPENT" && nor_sync || fail radio-replenish
  elif [ "$(cammy_get candidate_md5 "$RADIO_SPENT")" = "$CRASH_MD5" ]; then
   LAST=$(cat "$D/radio-last" 2>/dev/null)
   case "$LAST" in ''|*[!0-9]*) LAST=0;; esac
   if [ ! -f "$D/radio-since" ] || [ "$NOW" -lt "$LAST" ] || [ "$((NOW-LAST))" -gt 12 ]; then
    printf '%s\n' "$NOW" >"$D/radio-since" || fail radio-timer
   fi
   printf '%s\n' "$NOW" >"$D/radio-last" || fail radio-timer
   START=$(cat "$D/radio-since")
   case "$START" in ''|*[!0-9]*) fail radio-timer;; esac
   if [ "$((NOW-START))" -ge 900 ]; then
    rm -f "$RADIO_SPENT" && nor_sync || fail radio-replenish
   fi
  fi
 fi
fi
[ ! -f "$D/committed" ] || exit 0
# Health alone does not authorize another candidate boot. Resident opt-in or a
# one-boot advance record must match this release; absence preserves one-shot.
POLICY=$CFG/native-select.resident
ONCE=$CFG/native-select.advance
if [ ! -e "$POLICY" ] && [ ! -L "$POLICY" ] && [ ! -e "$ONCE" ] && [ ! -L "$ONCE" ]; then
 if [ "$GUARD_STATE" = idle ]; then rm -f "$GUARD" && nor_sync || fail startup-guard-clear; fi
 exit 0
fi
if [ -e "$POLICY" ] || [ -L "$POLICY" ]; then
 [ -f "$POLICY" ] && [ ! -L "$POLICY" ] && [ "$(cat "$POLICY")" = "$(printf 'format=cammy-resident/1\nrelease_id=%s' "$ID")" ] || fail resident-policy
 AUTHORITY=resident
else
 [ -f "$ONCE" ] && [ ! -L "$ONCE" ] && [ "$(cat "$ONCE")" = "$(printf 'format=cammy-advance/1\nrelease_id=%s\nboot=%s' "$ID" "$BOOT")" ] || fail advance-policy
 AUTHORITY=advance
fi
# The verified helper contains only definitions. It is never S01app/init code.
. "$CARD/boot-helper.sh" || fail helper-unavailable
COUNT=0
for CLAIM in "$STATE"/claimed-*; do
 [ -e "$CLAIM" ] || continue
 COUNT=$((COUNT+1));CURRENT=$CLAIM
 done
[ "$COUNT" -eq 1 ] || exit 0
cammy_record_ok "$CURRENT" format attempt boot md5 || fail claim-record
[ "$(cammy_get boot "$CURRENT")" = "$BOOT" ] || fail claim-boot
ATT=$(cammy_get attempt "$CURRENT");cammy_is_hex32 "$ATT" || fail claim-attempt
[ "$CURRENT" = "$STATE/claimed-$ATT" ] || fail claim-name
# The TF arm is read, never written: the selector refuses only an attempt that
# still has a claim in internal state, so retiring the claim is the whole rearm.
ARM=$CARD/../native/arm
[ -f "$ARM" ] && [ ! -L "$ARM" ] || fail arm-file
cammy_record_ok "$ARM" format attempt candidate md5 size class lib_sha256 lib_md5 lib_size || fail arm-record
[ "$(cammy_get attempt "$ARM")" = "$ATT" ] || fail arm-attempt
[ "$(cammy_get md5 "$CURRENT")" = "$(cammy_get md5 "$ARM")" ] || fail arm-md5
[ "$(cammy_get class "$ARM")" = gui-face-service-v1 ] || fail arm-class
HISTORY=$CFG/native-release-history
[ ! -L "$HISTORY" ] || fail claim-history-directory
mkdir -p "$HISTORY" || fail claim-history-directory
[ -d "$HISTORY" ] || fail claim-history-directory
OLD=$HISTORY/claimed-$ATT.healthy-$BOOT
[ ! -e "$OLD" ] && [ ! -L "$OLD" ] || fail claim-history-exists
# A power-retry ledger ends with the first healthy station commit after it. A
# fallback commit or a malformed ledger leaves it, keeping the retry disabled.
HISTORY_SLOTS=1
RETRY=$CFG/native-select.retry-used
if [ "$NET" = HOME_STATION ] && [ -f "$RETRY" ] && [ ! -L "$RETRY" ] && cammy_record_ok "$RETRY" format attempt failed_boot retry_boot \
 && [ "$(cammy_get format "$RETRY")" = cammy-power-retry-used/1 ]; then
 RETRY_OLD=$HISTORY/native-select.retry-used.healthy-$BOOT
 [ ! -e "$RETRY_OLD" ] && [ ! -L "$RETRY_OLD" ] || fail retry-history-exists
 HISTORY_SLOTS=2
fi
cammy_history_reserve "$HISTORY_SLOTS" nor_sync || fail claim-history-prune
# A one-boot advance is spent before the claim is retired. A power cut between
# the two leaves the claim, so the next boot is stock rather than unauthorized.
if [ "$AUTHORITY" = advance ]; then
 rm -f "$ONCE" || fail advance-retire
 nor_sync || fail advance-sync
fi
# Commit point: one synced rename on internal flash.
mv "$CURRENT" "$OLD" || fail claim-retire
nor_sync || fail claim-history-sync
# For a release-bound latch, retain the interrupted-boot identity until
# the healthy claim is retired. A cut on either side still has proof.
if [ "$GUARD_STATE" = idle ] && [ "$RETRYABLE_GUARD" = 1 ]; then
 rm -f "$GUARD" && nor_sync || fail startup-guard-clear
fi
[ "$AUTHORITY" = advance ] || [ ! -e "$ONCE" ] || rm -f "$ONCE" || fail advance-retire
if [ "$HISTORY_SLOTS" -eq 2 ]; then
 mv "$RETRY" "$RETRY_OLD" || fail retry-retire
 nor_sync || fail retry-history-sync
fi
printf 'release_id=%s\nboot=%s\nnext_attempt=%s\nnetwork=%s\n' "$ID" "$BOOT" "$ATT" "$NET" >"$D/committed" || fail committed-write
