# Injected by tools/rc06_build.py into the reviewed boot helper ahead of
# cammy_prepare_one_shot. Definitions only; it runs inside S01app before any
# TF probe, mount or read, and only from cammy_prepare_one_shot.
#
# Reserve up to two slots before a history write. Only this directory's
# recognized evidence files can be removed. The live claims, guards, fault
# and retry ledgers in CFG/STATE are the selector's authority.
cammy_history_record() {
 case "$1" in
  claimed-*.healthy-*|claimed-*.retry-*|claimed-*.crash-*|claimed-*.radio-*)
   hr_claim=${1#claimed-}
   cammy_is_hex32 "${hr_claim%%.*}" || return 1
   hr_suffix=${hr_claim#*.} ;;
  startup.pending.retry-*|startup.pending.crash-*|startup.pending.radio-*) hr_suffix=${1#startup.pending.} ;;
  runtime-health-fault.crash-*) hr_suffix=${1#runtime-health-fault.} ;;
  native-select.retry-used.healthy-*) hr_suffix=${1#native-select.retry-used.} ;;
  *) return 1 ;;
 esac
 case "$hr_suffix" in
  healthy-*|retry-*|crash-*|radio-*) cammy_is_uuid "${hr_suffix#*-}" ;;
  *) return 1 ;;
 esac
}

cammy_history_reserve() {
 hr_dir=$CFG/native-release-history
 [ -d "$hr_dir" ] && [ ! -L "$hr_dir" ] || return 1
 hr_limit=$((4 - $1))
 hr_sync=$2
 while :; do
  hr_count=0
  hr_oldest=
  for hr_file in "$hr_dir"/* "$hr_dir"/.[!.]* "$hr_dir"/..?*; do
   [ -e "$hr_file" ] || [ -L "$hr_file" ] || continue
   hr_count=$((hr_count + 1))
   [ -f "$hr_file" ] && [ ! -L "$hr_file" ] &&
    cammy_history_record "${hr_file##*/}" || continue
   if [ -z "$hr_oldest" ] || [ "$hr_file" -ot "$hr_oldest" ]; then
    hr_oldest=$hr_file
   fi
  done
  [ "$hr_count" -gt "$hr_limit" ] || return 0
  [ -n "$hr_oldest" ] && rm -f "$hr_oldest" && "$hr_sync" || return 1
 done
}

# One bounded, opt-in retry after a power cut during late startup.
# A previous boot left /etc/cammy/startup.pending. The retry is taken only
# when every internal record names that same interrupted boot and release:
#   - selector mode is select (the boot approval is unchanged);
#   - startup.pending is a regular file, exactly phase=idle_ready and the
#     boot_id of an earlier boot (never media_prepare, runtime_health_fault,
#     an early phase, or anything malformed);
#   - no runtime-health-fault record and no retry ledger exist;
#   - the state directory holds exactly one valid claimed-<attempt> for that
#     same boot (no observed, inflight or other entry);
#   - the resident policy and the retry opt-in are valid, name one release,
#     and the opt-in candidate MD5 equals both the claim and the allowlist.
# Durable steps, each synced before the next:
#   1. exclusively create the ledger native-select.retry-used;
#   2. rename the claim into native-release-history;
#   3. rename the startup flag into native-release-history.
# A crash after step 1 or 2 leaves the flag and the ledger, so later boots
# select stock. A crash after step 3 leaves only the ledger, so the next boot
# is the one retry. Health retires the ledger only after a healthy commit.
# Both live records are archived into the bounded evidence history.
cammy_power_retry() {
	CAMMY_RETRY_REASON=previous-runtime-fault
	pr_guard=$CFG/startup.pending
	pr_ledger=$CFG/native-select.retry-used
	pr_hist=$CFG/native-release-history
	[ "$LAUNCH_MODE" = select ] || return 1
	cammy_is_uuid "$LAUNCH_BOOT" || return 1
	if [ -e "$pr_ledger" ] || [ -L "$pr_ledger" ]; then
		CAMMY_RETRY_REASON=retry-consumed
		return 1
	fi
	if [ -e "$CFG/runtime-health-fault" ] || [ -L "$CFG/runtime-health-fault" ]; then
		return 1
	fi
	cammy_record_ok "$pr_guard" phase boot_id || return 1
	[ "$(cammy_get phase "$pr_guard")" = idle_ready ] || return 1
	pr_boot=$(cammy_get boot_id "$pr_guard")
	cammy_is_uuid "$pr_boot" && [ "$pr_boot" != "$LAUNCH_BOOT" ] || return 1
	# The same cheap gates the selector applies next; never spend the retry
	# when the selector would refuse for an unrelated reason.
	[ ! -e "$OVERLAY/.firstboot" ] && [ ! -L "$OVERLAY/.firstboot" ] || return 1
	! cammy_private_staging_residue || return 1
	! cammy_mounted_at "$APP" || return 1
	[ -d "$STATE" ] && [ ! -L "$STATE" ] && cammy_dir_private_ok "$STATE" || return 1
	pr_count=0
	pr_claim=
	for pr_entry in "$STATE"/* "$STATE"/.[!.]* "$STATE"/..?*; do
		[ -e "$pr_entry" ] || [ -L "$pr_entry" ] || continue
		pr_count=$((pr_count + 1))
		pr_claim=$pr_entry
	done
	[ "$pr_count" -eq 1 ] || return 1
	pr_att=${pr_claim##*/claimed-}
	[ "$pr_claim" = "$STATE/claimed-$pr_att" ] && cammy_is_hex32 "$pr_att" || return 1
	cammy_record_ok "$pr_claim" format attempt boot md5 || return 1
	[ "$(cammy_get format "$pr_claim")" = cammy-native-claim/1 ] || return 1
	[ "$(cammy_get attempt "$pr_claim")" = "$pr_att" ] || return 1
	[ "$(cammy_get boot "$pr_claim")" = "$pr_boot" ] || return 1
	pr_md5=$(cammy_get md5 "$pr_claim")
	cammy_is_hex32 "$pr_md5" || return 1
	pr_policy=$CFG/native-select.resident
	pr_optin=$CFG/native-select.retry
	cammy_record_ok "$pr_policy" format release_id || return 1
	[ "$(cammy_get format "$pr_policy")" = cammy-resident/1 ] || return 1
	pr_release=$(cammy_get release_id "$pr_policy")
	case "$pr_release" in ''|*[!a-z0-9-]*) return 1 ;; esac
	[ "${#pr_release}" -le 64 ] || return 1
	cammy_record_ok "$pr_optin" format release_id candidate_md5 || return 1
	[ "$(cammy_get format "$pr_optin")" = cammy-power-retry/1 ] || return 1
	[ "$(cammy_get release_id "$pr_optin")" = "$pr_release" ] || return 1
	[ "$(cammy_get candidate_md5 "$pr_optin")" = "$pr_md5" ] || return 1
	pr_allow=$CFG/native-select.allow
	cammy_record_ok "$pr_allow" format s01app_md5 source_md5 source_size candidate library || return 1
	pr_allowed=$(cammy_get candidate "$pr_allow")
	[ "${pr_allowed%%:*}" = "$pr_md5" ] || return 1
	if [ ! -e "$pr_hist" ] && [ ! -L "$pr_hist" ]; then
		mkdir "$pr_hist" 2>/dev/null || return 1
	fi
	[ -d "$pr_hist" ] && [ ! -L "$pr_hist" ] || return 1
	pr_old_claim=$pr_hist/claimed-$pr_att.retry-$pr_boot
	pr_old_guard=$pr_hist/startup.pending.retry-$pr_boot
	for pr_entry in "$pr_old_claim" "$pr_old_guard"; do
		[ ! -e "$pr_entry" ] && [ ! -L "$pr_entry" ] || return 1
	done
	cammy_history_reserve 2 sync || return 1
	# Step 1: consume the retry before changing any live record.
	CAMMY_RETRY_REASON=retry-ledger-failed
	cammy_write_record "$pr_ledger" "format=cammy-power-retry-used/1
attempt=$pr_att
failed_boot=$pr_boot
retry_boot=$LAUNCH_BOOT" || return 1
	# Steps 2 and 3: archive the claim, then the flag (atomic renames, synced).
	CAMMY_RETRY_REASON=retry-archive-failed
	mv "$pr_claim" "$pr_old_claim" 2>/dev/null || return 1
	sync || return 1
	[ ! -e "$pr_claim" ] && [ -f "$pr_old_claim" ] || return 1
	mv "$pr_guard" "$pr_old_guard" 2>/dev/null || return 1
	sync || return 1
	[ ! -e "$pr_guard" ] && [ ! -L "$pr_guard" ] && [ -f "$pr_old_guard" ] || return 1
	CAMMY_RETRY_REASON=power-retry
	return 0
}

# The crash budget is shared with the selector, health and maintenance. A spent
# record admits exactly its designated boot, even if power is cut after all
# fault records were archived but before the ordinary claim could be written.
cammy_crash_ledger() {
 pr_spent=$CFG/native-select.crash-used
 cammy_record_ok "$pr_spent" format release_id candidate_md5 failed_boot retry_boot || return 1
 [ "$(cammy_get format "$pr_spent")" = cammy-crash-retry-used/1 ] || return 1
 pr_release=$(cammy_get release_id "$pr_spent")
 pr_md5=$(cammy_get candidate_md5 "$pr_spent")
 pr_failed=$(cammy_get failed_boot "$pr_spent")
 pr_retry=$(cammy_get retry_boot "$pr_spent")
 case "$pr_release" in *[!0-9a-f]*) return 1;; esac
 [ "${#pr_release}" -eq 24 ] && cammy_is_hex32 "$pr_md5" &&
 cammy_is_uuid "$pr_failed" && cammy_is_uuid "$pr_retry" && [ "$pr_failed" != "$pr_retry" ]
}

cammy_crash_authority() {
 cammy_record_ok "$CFG/native-select.resident" format release_id &&
 [ "$(cat "$CFG/native-select.resident")" = "$(printf 'format=cammy-resident/1\nrelease_id=%s' "$pr_release")" ] &&
 cammy_record_ok "$CFG/native-select.allow" format s01app_md5 source_md5 source_size candidate library &&
 pr_allowed=$(cammy_get candidate "$CFG/native-select.allow") &&
 [ "${pr_allowed%%:*}" = "$pr_md5" ]
}

# Abrupt resets need both a same-release late latch and a prior healthy
# commit for this exact attempt. A committed boot has its claim in history;
# a boot still proving health must have the live claim for the failed boot.
cammy_abrupt_records() {
 [ ! -e "$pr_fault" ] && [ ! -L "$pr_fault" ] || return 1
 [ -d "$pr_hist" ] && [ ! -L "$pr_hist" ] || return 1
 pr_claim=
 for pr_path in "$STATE"/* "$STATE"/.[!.]* "$STATE"/..?*; do
  [ -e "$pr_path" ] || [ -L "$pr_path" ] || continue
  [ -z "$pr_claim" ] || return 1
  pr_att=${pr_path##*/claimed-}
  [ "$pr_path" = "$STATE/claimed-$pr_att" ] && cammy_is_hex32 "$pr_att" &&
   cammy_recovery_claim "$pr_path" "$pr_att" "$pr_failed" || return 1
  pr_claim=$pr_path
  [ ! -e "$pr_hist/claimed-$pr_att.healthy-$pr_failed" ] &&
   [ ! -L "$pr_hist/claimed-$pr_att.healthy-$pr_failed" ] || return 1
 done
 for pr_entry in "$pr_hist"/claimed-*.healthy-*; do
  pr_name=${pr_entry##*/claimed-}
  pr_old_attempt=${pr_name%%.*}
  pr_old_boot=${pr_name#*.healthy-}
  cammy_is_hex32 "$pr_old_attempt" && cammy_is_uuid "$pr_old_boot" &&
   [ "$pr_old_boot" != "$LAUNCH_BOOT" ] || continue
  if [ -n "$pr_claim" ]; then
   [ "$pr_old_attempt" = "$pr_att" ] && [ "$pr_old_boot" != "$pr_failed" ] || continue
  else
   [ "$pr_old_boot" = "$pr_failed" ] || continue
  fi
  cammy_recovery_claim "$pr_entry" "$pr_old_attempt" "$pr_old_boot" && return 0
 done
 return 1
}

cammy_recovery_claim() {
 cammy_record_ok "$1" format attempt boot md5 &&
 [ "$(cammy_get format "$1")" = cammy-native-claim/1 ] &&
 [ "$(cammy_get attempt "$1")" = "$2" ] &&
 [ "$(cammy_get boot "$1")" = "$3" ] &&
 [ "$(cammy_get md5 "$1")" = "$pr_md5" ]
}

cammy_crash_retry() {
 CAMMY_RETRY_REASON=previous-runtime-fault
 pr_guard=$CFG/startup.pending
 pr_fault=$CFG/runtime-health-fault
 pr_spent=$CFG/native-select.crash-used
 pr_hist=$CFG/native-release-history
 [ "$LAUNCH_MODE" = select ] && cammy_is_uuid "$LAUNCH_BOOT" || return 1
 for pr_path in "$pr_spent" "$CFG/native-select.retry" "$CFG/native-select.retry-used" "$CFG/native-select.advance" "$OVERLAY/.firstboot"; do
  [ ! -e "$pr_path" ] && [ ! -L "$pr_path" ] || return 1
 done
 ! cammy_private_staging_residue && ! cammy_mounted_at "$APP" || return 1
 [ -d "$STATE" ] && [ ! -L "$STATE" ] && cammy_dir_private_ok "$STATE" || return 1
 cammy_record_ok "$pr_guard" phase boot_id release_id candidate_md5 || return 1
 pr_phase=$(cammy_get phase "$pr_guard")
 pr_failed=$(cammy_get boot_id "$pr_guard")
 pr_release=$(cammy_get release_id "$pr_guard")
 pr_md5=$(cammy_get candidate_md5 "$pr_guard")
 case "$pr_release" in *[!0-9a-f]*) return 1;; esac
 [ "${#pr_release}" -eq 24 ] && cammy_is_hex32 "$pr_md5" &&
 cammy_is_uuid "$pr_failed" && [ "$pr_failed" != "$LAUNCH_BOOT" ] || return 1
 cammy_crash_authority || return 1
 case "$pr_phase" in
  idle_ready|media_prepare)
   cammy_abrupt_records || return 1
   pr_archive=$pr_claim
   ;;
  post_healthy_fault)
   for pr_path in "$STATE"/* "$STATE"/.[!.]* "$STATE"/..?*; do
    [ ! -e "$pr_path" ] && [ ! -L "$pr_path" ] || return 1
   done
   cammy_record_ok "$pr_fault" format boot_id release_id uptime_s reason media_reason guard_before rearmed || return 1
   [ "$(cammy_get format "$pr_fault")" = cammy-runtime-health-fault/1 ] &&
   [ "$(cammy_get boot_id "$pr_fault")" = "$pr_failed" ] &&
   [ "$(cammy_get release_id "$pr_fault")" = "$pr_release" ] &&
   [ "$(cammy_get reason "$pr_fault")" = native-pid ] &&
   [ "$(cammy_get media_reason "$pr_fault")" = none ] &&
   [ "$(cammy_get guard_before "$pr_fault")" = absent ] &&
   [ "$(cammy_get rearmed "$pr_fault")" = yes ] || return 1
   pr_archive=$pr_fault
   ;;
  *) return 1;;
 esac
 if [ ! -e "$pr_hist" ] && [ ! -L "$pr_hist" ]; then mkdir "$pr_hist" || return 1; fi
 [ -d "$pr_hist" ] && [ ! -L "$pr_hist" ] || return 1
 for pr_path in "$pr_archive" "$pr_guard"; do
  [ -n "$pr_path" ] || continue
  pr_archived=$pr_hist/${pr_path##*/}.crash-$pr_failed
  [ ! -e "$pr_archived" ] && [ ! -L "$pr_archived" ] || return 1
 done
 pr_slots=1
 [ -z "$pr_archive" ] || pr_slots=2
 cammy_history_reserve "$pr_slots" sync || return 1
 CAMMY_RETRY_REASON=crash-retry-spend-failed
 cammy_write_record "$pr_spent" "format=cammy-crash-retry-used/1
release_id=$pr_release
candidate_md5=$pr_md5
failed_boot=$pr_failed
retry_boot=$LAUNCH_BOOT" || return 1
 CAMMY_RETRY_REASON=crash-retry-archive-failed
 for pr_path in "$pr_archive" "$pr_guard"; do
  [ -n "$pr_path" ] || continue
  mv "$pr_path" "$pr_hist/${pr_path##*/}.crash-$pr_failed" && sync || return 1
 done
 CAMMY_RETRY_REASON=post-healthy-recovery
}

# One automatic retry after a radio fault (approved 2026-11-23, boot fbaa812c).
# The vendor Wi-Fi driver can oops during bring-up and hang the network
# commands after it. Startup then replaces the launch or idle latch it owns
# with a release-bound phase=radio_fault record. The next boot runs CammyOS
# once more; the release-bound ledger native-select.radio-used makes a second
# consecutive radio fault select stock. Health removes the ledger after the
# same 900 s of sampled health that replenishes the crash budget.
# Durable steps, each synced before the next:
#   1. exclusively create the ledger native-select.radio-used;
#   2. rename the failed boot's claim into native-release-history;
#   3. rename the latch into native-release-history.
# A cut after step 1 or 2 leaves the latch and the ledger: later boots select
# stock, as before this policy. A cut after step 3 makes the next boot the retry.
cammy_radio_retry() {
 CAMMY_RETRY_REASON=radio-fault
 pr_guard=$CFG/startup.pending
 pr_spent=$CFG/native-select.radio-used
 pr_hist=$CFG/native-release-history
 [ "$LAUNCH_MODE" = select ] && cammy_is_uuid "$LAUNCH_BOOT" || return 1
 if [ -e "$pr_spent" ] || [ -L "$pr_spent" ]; then
  CAMMY_RETRY_REASON=radio-retry-spent
  return 1
 fi
 for pr_path in "$CFG/runtime-health-fault" "$CFG/native-select.advance" "$OVERLAY/.firstboot"; do
  [ ! -e "$pr_path" ] && [ ! -L "$pr_path" ] || return 1
 done
 ! cammy_private_staging_residue && ! cammy_mounted_at "$APP" || return 1
 [ -d "$STATE" ] && [ ! -L "$STATE" ] && cammy_dir_private_ok "$STATE" || return 1
 cammy_record_ok "$pr_guard" phase boot_id release_id candidate_md5 || return 1
 [ "$(cammy_get phase "$pr_guard")" = radio_fault ] || return 1
 pr_failed=$(cammy_get boot_id "$pr_guard")
 pr_release=$(cammy_get release_id "$pr_guard")
 pr_md5=$(cammy_get candidate_md5 "$pr_guard")
 case "$pr_release" in *[!0-9a-f]*) return 1;; esac
 [ "${#pr_release}" -eq 24 ] && cammy_is_hex32 "$pr_md5" &&
 cammy_is_uuid "$pr_failed" && [ "$pr_failed" != "$LAUNCH_BOOT" ] || return 1
 cammy_crash_authority || return 1
 # Exactly the failed boot's own claim for this candidate, nothing else.
 pr_claim=
 for pr_path in "$STATE"/* "$STATE"/.[!.]* "$STATE"/..?*; do
  [ -e "$pr_path" ] || [ -L "$pr_path" ] || continue
  [ -z "$pr_claim" ] || return 1
  pr_att=${pr_path##*/claimed-}
  [ "$pr_path" = "$STATE/claimed-$pr_att" ] && cammy_is_hex32 "$pr_att" &&
   cammy_recovery_claim "$pr_path" "$pr_att" "$pr_failed" || return 1
  pr_claim=$pr_path
 done
 [ -n "$pr_claim" ] || return 1
 if [ ! -e "$pr_hist" ] && [ ! -L "$pr_hist" ]; then mkdir "$pr_hist" || return 1; fi
 [ -d "$pr_hist" ] && [ ! -L "$pr_hist" ] || return 1
 pr_old_claim=$pr_hist/claimed-$pr_att.radio-$pr_failed
 pr_old_guard=$pr_hist/startup.pending.radio-$pr_failed
 for pr_path in "$pr_old_claim" "$pr_old_guard"; do
  [ ! -e "$pr_path" ] && [ ! -L "$pr_path" ] || return 1
 done
 cammy_history_reserve 2 sync || return 1
 CAMMY_RETRY_REASON=radio-retry-spend-failed
 cammy_write_record "$pr_spent" "format=cammy-radio-retry-used/1
release_id=$pr_release
candidate_md5=$pr_md5
failed_boot=$pr_failed
retry_boot=$LAUNCH_BOOT" || return 1
 CAMMY_RETRY_REASON=radio-retry-archive-failed
 mv "$pr_claim" "$pr_old_claim" && sync || return 1
 mv "$pr_guard" "$pr_old_guard" && sync || return 1
 CAMMY_RETRY_REASON=radio-retry
}

# Resident releases always start (Adam, 2026-11-24: "I never want to leave it
# normal mode"). Stock rcK and the power key stop sdvcam while health.sh still
# runs, so after the menu a latch or fault record usually means a power-off,
# not a broken release; a crash after the menu is not a reason for stock either.
# Only a boot that never reached the menu (latch phase=launch or radio_fault, or
# a fault recorded from an unexpected/radio guard) counts as failed. Three such
# boots in a row select stock for one boot, so a broken release cannot loop
# forever; the counter is then reset and the next boot tries CammyOS again.
# Startup removes native-select.fails once a boot reaches the menu (idle_ready).
# Everything a failed or interrupted boot left behind (state directory entries,
# latch, fault record, retry ledgers) moves into one bounded archive,
# native-select.archived-recover, replaced at every recovery.
# Returns 0: launch; 1: no valid resident release (older rules apply);
# 2: stock for this boot (failure limit). Sets CAMMY_RETRY_REASON.
CAMMY_FAIL_LIMIT=3
cammy_resident_recover() {
	rr_res=$CFG/native-select.resident
	rr_fails=$CFG/native-select.fails
	rr_guard=$CFG/startup.pending
	rr_fault=$CFG/runtime-health-fault
	rr_keep=$CFG/native-select.archived-recover
	CAMMY_RETRY_REASON=resident-recover-failed
	[ "$LAUNCH_MODE" = select ] && cammy_is_uuid "$LAUNCH_BOOT" || return 1
	cammy_record_ok "$rr_res" format release_id &&
	[ "$(cammy_get format "$rr_res")" = cammy-resident/1 ] || return 1
	rr_release=$(cammy_get release_id "$rr_res")
	case "$rr_release" in *[!0-9a-f]*|'') return 1;; esac
	[ "${#rr_release}" -eq 24 ] || return 1
	cammy_record_ok "$CFG/native-select.allow" format s01app_md5 source_md5 source_size candidate library || return 1
	if [ ! -e "$STATE" ] && [ ! -L "$STATE" ]; then
		(umask 077 && mkdir "$STATE") || return 1
	fi
	[ -d "$STATE" ] && [ ! -L "$STATE" ] || return 1
	rr_left=
	for rr_path in "$STATE"/* "$STATE"/.[!.]* "$STATE"/..?* "$rr_guard" "$rr_fault" \
	    "$CFG/native-select.crash-used" "$CFG/native-select.radio-used" "$CFG/native-select.retry-used"; do
		[ -e "$rr_path" ] || [ -L "$rr_path" ] || continue
		rr_left="$rr_left $rr_path"
	done
	CAMMY_RETRY_REASON=resident
	[ -n "$rr_left" ] || return 0
	# Classify the previous boot before anything moves.
	rr_early=0
	rr_failed=none
	if [ -f "$rr_guard" ] && [ ! -L "$rr_guard" ]; then
		rr_failed=$(cammy_get boot_id "$rr_guard")
		case "$(cammy_get phase "$rr_guard")" in launch|radio_fault) rr_early=1;; esac
	fi
	if [ -f "$rr_fault" ] && [ ! -L "$rr_fault" ]; then
		[ "$rr_failed" != none ] || rr_failed=$(cammy_get boot_id "$rr_fault")
		case "$(cammy_get guard_before "$rr_fault")" in unexpected|radio) rr_early=1;; esac
	fi
	[ "$rr_failed" != "$LAUNCH_BOOT" ] || return 1
	rr_count=0
	if [ "$rr_early" = 1 ]; then
		rr_count=$(cammy_get count "$rr_fails")
		case "$rr_count" in ''|*[!0-9]*) rr_count=0;; esac
		if [ "$(cammy_get last_failed_boot "$rr_fails")" != "$rr_failed" ]; then
			rr_count=$((rr_count + 1))
		fi
	fi
	# Archive (one bounded copy), then clear the live records.
	CAMMY_RETRY_REASON=resident-recover-archive-failed
	# Replace the previous archive: flat files only, never a recursive delete.
	if [ -e "$rr_keep" ] || [ -L "$rr_keep" ]; then
		[ -d "$rr_keep" ] && [ ! -L "$rr_keep" ] || return 1
		for rr_path in "$rr_keep"/* "$rr_keep"/.[!.]* "$rr_keep"/..?*; do
			[ -e "$rr_path" ] || [ -L "$rr_path" ] || continue
			rm -f "$rr_path" || return 1
		done
		rm -r "$rr_keep" || return 1
	fi
	(umask 077 && mkdir "$rr_keep") || return 1
	for rr_path in $rr_left; do
		mv -f "$rr_path" "$rr_keep/" 2>/dev/null || return 1
	done
	rm -f "$rr_fails"
	sync || return 1
	for rr_path in $rr_left; do
		[ ! -e "$rr_path" ] && [ ! -L "$rr_path" ] || return 1
	done
	cammy_dir_private_ok "$STATE" || return 1
	if [ "$rr_count" -ge "$CAMMY_FAIL_LIMIT" ]; then
		CAMMY_RETRY_REASON=resident-fail-limit
		return 2
	fi
	if [ "$rr_count" -gt 0 ]; then
		cammy_write_record "$rr_fails" "format=cammy-boot-fails/1
count=$rr_count
last_failed_boot=$rr_failed" || return 1
	fi
	CAMMY_RETRY_REASON=resident-recovered
	return 0
}
