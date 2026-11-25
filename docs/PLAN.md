# Cammy project plan

This is the forward plan. [STATUS.md](STATUS.md) records what is running and
the evidence behind it. [Architecture](../architecture.md) describes the
system, and [C3.md](C3.md) describes the Control Center.

## Goal

Use the M105 as a Wi-Fi desk companion with its own display, camera, microphone,
speaker and gimbal. Cammy should be easy to talk to, clear about what it can
see or do, and useful in a household.

## Now

### 0.9 resilience acceptance

Adam approves every hardware step. Keep the stock boot files and the
previous verified TF release. Stop after any spontaneous restart, lost
native PID, uncertain mode restore, or failed NOR check. Preserve the latch,
known-good rollback and TF evidence before any rearm.

1. **Read-only baseline.** Match the installed receipt, stock SHA and boot ID.
   Record overlay and TF free bytes separately, current boot targets, stock
   mode, available RAM, shared RAM, native RSS, GUI tick and CPU timing. Verify
   the 0.9 candidate digest on LAB and inspect the reports directory on the
   card. This establishes the rollback, capacity and resource baseline.
2. **One-shot trial.** Stage the approved inert slot, confirm the installer
   archives obsolete NOR files and measures headroom before it changes selection, and
   install with the existing guarded process. Cold boot and choose CammyOS.
   Confirm the Loading progress bar, Ready, 120 s of health, normal buttons,
   settings and all three packs. Repeat with an early CCC media request.
   Record the same RAM measurements and frame-timing samples.
   Then verify one-shot stock fallback and reboot once with the card removed.
   This checks that fallback runs without creating a new CammyOS latch.
3. **GUI and capture trial.** With separately approved CCC code active,
   exercise screen taps, Settings open/close, physical photo/video mode
   switches and four successful CCC photos separated by at least 30 s.
   Each capture must confirm the face hide before switching, restore the
   original mode before letting the face return, and produce a distinct PC
   file even when the TF basename is reused. Make a second request during
   capture and another during cooldown. Observe no canvas in stock photo
   mode, prompt redraw on return to video, and one continuously owned native
   PID. Inspect the per-phase timings and confirm the menu still receives taps.
4. **Soak and audio.** Complete three approved one-shot cold boots and at least
   90 minutes of mixed preview, microphone rollover, speech and capture.
   Sample RAM, GUI
   latency and speaker status at startup and around the 70-minute mark.
   Through one Wi-Fi outage verify reconnect and the fallback behavior. Use a
   canned utterance to verify received Gemini PCM, terminal speaker status and
   audible playback separately. Keep the tested half-duplex policy.
   After the approved CCC restart, verify that the silent check runs in the
   adult deployment on the new lease, that the speaker stays quiet during it,
   and that the desktop shows the voice health verdict. Send a typed turn and
   a spoken turn. Check that model PCM and a finished speaker job accompany
   audible replies. During the planned Wi-Fi outage, queue a typed turn and
   verify it plays after recovery. Check the journal for paired down and up
   transitions and record any guardian refusal reason alongside speaker state.
5. **Resident evidence and one retry.** Approve resident mode separately.
   After the release commits with no media job, induce one controlled exit of
   the original native process. Observe the snapshot and NOR fault before
   manual power-on where possible. Verify the report names the right boot,
   release and phase and contains no credentials. Power on once and verify
   the spent record names that boot and selection succeeds. Induce a second
   failure before 900 s of continuous health and verify stock on the next two
   boots. Compare the two reports and the selector decisions.
6. **Replenishment and fault cases.** On an expendable bench TF card, repeat
   the resident fault, let the retry boot commit and run passing probes for
   900 s, then confirm the budget is available again. Break health during the
   window and confirm the timer restarts. Repeat with a read-only, full and
   absent TF card, and with an interrupted evidence write. Exercise cuts
   before spend, after spend and after archive on expendable media. Verify
   each cut leaves stock or one designated retry, and a missing or slow TF
   report cannot prevent NOR quarantine. Run a portal startup failure and an
   uncertain media cleanup case to verify neither qualifies for crash retry.

### 0.9.3 silent camera acceptance

Use the checked install and preserve its previous release receipt. Disable
CCC sensing, preview and speech for this check. Keep camera stock startup
volume at its provisioned quiet setting. Capture read-only evidence over the
approved USB maintenance connection.

1. Boot on USB and choose CammyOS. Verify the LCD advances through startup,
   Wi-Fi and service progress and reaches Ready. Open and close Settings and
   select each face pack. Switch between stock photo and video modes without
   pressing the shutter. The face should disappear in stock photo mode and
   return in video after the transition settles.
2. On a separately approved resident boot, wait for the 120-second health
   window to complete. Compare `/proc/sys/kernel/random/boot_id`,
   `/run/cammy-select/launch.stamp` and
   `/tmp/cammy-runtime/release-health/committed`. Verify
   `/etc/cammy/startup.pending` is absent, run
   `/tmp/cammy-runtime/face-status` and match its `native_pid` to the
   committed supervisor's `release-health/owner` record. Confirm
   `/tmp/cammy-runtime/state` reports the intended network. Check
   `wc -c </mnt/extsd/CammyOS-trail` returns 32768. Copy the binary ring
   before any further power test.
3. Soak 65 minutes on USB, then unplug USB while the camera is idle and
   observe for at least 15 minutes on battery. Through CCC read-only status,
   record battery level and boot ID. Compare `MemAvailable`, `Cached`
   and `Slab` in `/proc/meminfo` and `VmRSS` and `Threads` in the
   `sdvcam` proc status before and after. If it resets, preserve the NOR
   latch, selector status, TF ring and any `previous-boot.txt` before
   rearming. Compare the report's boot ID and phase with the last sample.
4. Only with approval on expendable bench media, interrupt a previously
   committed resident release during a later boot's `idle_ready`, before
   claim retirement. Verify the first retry's boot ID matches
   `retry_boot` in `/etc/cammy/native-select.crash-used`, and the old boot
   matches `failed_boot`. Repeat the interruption before replenishment.
   Check `/etc/cammy/native-select.status` for `crash-retry-spent` on two
   successive stock boots. In a separate run let the retry settle, wait
   for 900 seconds of continuous passing health and verify the ledger
   disappears. Record the previous-boot report if a CammyOS retry ran.
5. Keep the `media_prepare` cut matrix offline during the silent check.
   A live preparation starts the stock preview transport as part of its
   SDP check. Check saved field evidence for spontaneous media interruptions,
   and keep any live test of that path separately approved.

### Face library upgrade

Use the 0.9.3 CPK1 pack as the camera format for the first candidate.
It uses a bounded LZ decoder with one cached key and decodes into the
existing canvas buffers. Keep the approved art pixel-exact at 356 × 400.
Compare candidates against the same 0.9.3 build and physical camera before
authorizing a resident upgrade.
[Frame audit](../architecture.md#conversation-face-cues) ·
[Decoder](../native/face_service/face_pack_codec.h)

1. **Reduce the pack and simplify frame selection.** Remove the seven
   unreachable sleepy entries and the two unfocused chooser entries.
   Re-encode the focused chooser images and alias Pikachu's identical happy
   frames. Generate the native frame index tables and build manifest from the
   same asset definitions. Track all art and generator inputs, including Fin
   and Fur, so incremental builds regenerate the pack when needed. Keep
   the three packs, all public pose commands, eight-step sleep, wake through
   the existing blink, and the two-mouth speech cadence. The estimated
   first-candidate target is a complete `face.so` at or below 3.0 MiB, down
   from 3.30 MiB, including the chooser and Loading screen.
2. **Keep GUI work bounded.** Preserve the two BGRA canvas slots and one
   BGR key buffer, about 1.49 MiB of explicit pixel buffers. Decode only on a
   cache miss, leave the displayed buffer untouched, and reuse the two
   decoded mouth frames during steady speech. Keep ParamFace and image
   processing on the build PC. Compress the built-in Loading alpha data
   with the existing bounded decoder if it saves space and meets the cold
   start timing gate. Keep file reads, allocations and worker threads out
   of the decode path. Preserve GUI ownership, the stock mode and transition
   checks, Settings priority, capture hide and redraw, and CCC lease and
   pack selection semantics.
3. **Make failures contained and observable.** Check frame indexes, lengths
   and references before use. Publish a new canvas source only after a
   complete decode. On failure, retire the affected pack for that boot and
   report the pack and reason through existing health diagnostics with
   bounded logging. If the selected pack fails, try a verified built-in
   fallback without writing the saved selection or changing CCC family
   policy. If no suitable face can be drawn, yield to stock and report
   degraded health through the existing release guard. Keep selector,
   rollback, reset trail and retry policy intact.
4. **Measure the next size reduction separately.** On the PC, try improving
   the adult key and delta choices with the existing decoder first. If that
   leaves substantial savings available, compare a bounded fixed-dimension
   lossless candidate against CPK1, using PNG and lossless WebP as size
   references. Compare the linked library and staged slot sizes, decoder
   scratch and peak memory, and cold and warm decode time on ARM. Consider a
   new format only if the complete library approaches 2.0 MiB or less and
   the measured GUI and memory costs meet the release gates. Otherwise
   retain the compacted CPK1 candidate.
5. **Verify and release.** Require pixel-exact host and ARM fixture output
   for every reachable frame, tests for truncated and invalid packs,
   pack-switch and cache tests, and the existing loader, lease, UI and
   installer fixtures. On an approved one-shot, compare decode time and
   total GUI callback time at median, p99 and worst case, missed 33 ms
   GUI ticks, startup time, native RSS, available RAM and staged TF bytes
   against 0.9.3. Require no regression in missed ticks, startup readiness
   or peak memory. Exercise both chooser selections, Loading, all three
   packs, the face states, Settings, stock photo/video and capture handoff.
   Run speaking animation with the speaker silent for initial acceptance.
   Keep the approved 0.9.3 rollback available. Require the existing
   120-second health window and USB-to-battery soak before requesting
   resident approval. Stop and preserve evidence on reset, native PID
   loss or uncertain stock restoration.

### Settings assets and boot chooser timing

- 0.9.5 (`ad2071d9…`) did cut 64 KiB from `cammy-ui`: the Settings glyph
  masks are cropped, and all 24 fixture screens are pixel-identical. Its
  chooser rule was wrong.
  - On USB boot fbaa812c, the chooser drew first. Then USBModeWindow
    (window 6) loaded 0.42 s after the first chance to draw. At that moment
    the UDC still read "not attached".
  - wm+0x7c turned out to be the previous window, so the closed prompt still
    looked open, and 0.9.5 hid the chooser for its full 20 s cap.
  - The prompt waits for a key and doesn't close on its own. On 0.9.4 it
    closed because one OK press answered both the prompt and the chooser.
- 0.9.6 (`0fe35a5dd6a66baa78e3c5bf`, commit 1c9f928): the first chooser draw
  waits up to 2 s, or until the prompt is up and the 350 ms guard has passed.
  The chooser then draws over the prompt, and one OK answers both. A hidden
  chooser still takes no keys. The timeline file and
  `private/diag/tools/chooser-timeline.sh` remain for checking PC USB,
  charger-only and battery boots.
- 0.9.6 passed on USB boot 202e28a9.
  - Chooser: ready at 15.12 s, prompt at +0.41 s, drawn over the prompt at
    +0.77 s, CammyOS picked with one OK at +1.60 s.
  - Health cleared with HOME_STATION.
  - Made resident: `private/rc06/resident-20261123-150356`.
  - The TF card now holds only the 0.9.6 slot. The 0.9.2 to 0.9.5 slots were
    removed after matching the PC copies (md5).
  - Still to check: charger-only and battery boots.
- The same boot fbaa812c hit a kernel NULL-pointer oops in `wlan_ecr6600`
  during wpa_supplicant's first scan, at 51 s. `ifconfig wlan0 down` then
  hung in D state, and startup stopped at `ap_fallback`. The loading gate
  returned the screen to stock after 180 s. Evidence is in
  `private/diag/wifi-oops-fbaa812c/`. This is the first recorded case. If
  it happens again, investigate the radio load and scan timing.

### CCC post-restart observation

After the approved CCC restart, passively check that the journal records the
stock battery level, the desktop Connections tab agrees, and the selected
Look Back store has at most 14,400 JPEGs. If a natural camera rebind meets
unprepared media, observe the hold reason and the interval before preparation.
The existing adult silent check should produce a health result without a
speaker job. During ordinary subsequent conversation, watch for ignored speech
to increase separately from unexpected silence. Keep the default half-duplex
policy for the latency acceptance in the planned audio trial.

### NOR overlay verification

On the next approved USB maintenance session, confirm the running boot and
installed receipt. Run `python3 tools/rc_install.py prune --replacing <current installed.json>`
and review the protected paths and proposed deletions. Verify the factory
S01app against the pinned stock MD5 and confirm the receipt's live rollback
and all unfinished transactions are protected. With approval, repeat with
`--apply`. Check the resulting PC receipts and file MD5s, rerun the preview,
and compare `/overlay` free bytes with the verified TF `runtime/fault-evidence
space` probe before and after maintenance.

For the next approved candidate, stage its verified TF slot and run the
guarded one-shot install. Confirm its receipt, the previous rollback
and stock S01app survive. On expendable bench media, interrupt pruning
after a PC receipt and during removal, then rerun and verify evidence.
After resident acceptance, run six healthy boots and the approved retry
scenarios above. Inspect `native-release-history` after each transition
and use the guarded rollback flow to verify stock fallback still works.

After 0.9 acceptance, verify that M pauses and resumes both CCC sources.
Finish the touch and OK navigation checks, Wi-Fi join cancellation and the
confirmation before handing control to stock. Verify the CCC OS action on
an approved maintenance session and first-device setup on a matching spare.

Finish Family and Calendar acceptance with the Pikachu visual and local
games checks, one scoped Executor sign-in, a read-only schedule request by
text and local speech, and profile/Kids privacy checks.

## Parked work

- Phone hotspot and Tailscale. Verify ALPHABET association away from home and
  a private routed path for portal, preview and audio before adding automatic
  endpoint selection. Test recovery to the home network.
- Windows CCC. The platform and interface decision is deferred.
- Wake-word experiments and migration of remaining finite-session MCP clients.
  [Current control surface](C3.md#agent-and-bot-access)
- Playback-synchronized mouth animation. [Face-loop research](FACE-LOOP-RESEARCH-2026-11-17.md)
- Hands-free assembly guidance with visual context and product manuals.
- Reachy host experiments and voice cloning remain parked.

## Release rules

- Use factory stock as the only on-camera recovery target. Keep the verified
  factory boot backup on NOR and store historical CammyOS boot copies on LAB.
  Use the checked installer's `--stock-only` option when migrating an older
  receipt. Subsequent installs inherit the recovery policy from the installed
  receipt. Confirm the factory backup and one-shot selector after installation.
- Keep the stock kernel, drivers and camera app. Use the checked installer and
  its rollback records. ADB reboot powers this camera off in practice.
- Keep the active and rollback releases on the microSD. Before staging an
  update, check space on the internal settings overlay separately. Verify
  older backups on LAB before removing their on-camera copies. Keep the
  factory originals and the rollback for the current transaction.
- Archive startup guard records and get Adam's approval before clearing them.
  Resident authority and power-cut retry are separate from installing a
  one-shot candidate.
- Keep Wi-Fi credentials and tokens outside the portable TF package. Bind
  remote control to the current boot and lease. Keep profile data on the PC.
- The family deployment uses local speech where enabled. Account access is
  an explicit desktop control. [Family deployment](C3.md#family)
