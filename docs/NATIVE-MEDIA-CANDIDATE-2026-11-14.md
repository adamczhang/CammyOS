# Native media-start route and offline candidate — 2026-11-14

**A concrete native integration candidate is built and locally tested. It is not
installed, activated or a demonstrated camera-video fix.** The camera still runs
its original stock `sdvcam` with CammyOS 0.3. No camera code/settings/radio/driver
change, app restart or process-memory injection occurred in this investigation.

## Traced boundary

The exact preserved/live executable SHA256 is
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
Fresh read-only USB hashing confirmed that the live executable is unchanged.
ARM disassembly, exception-table function boundaries, C++ vtables and direct
branch references were used together; misleading nearest-symbol labels were not
used as function identities. Private raw analysis is in `private/native-start/`.

Selected findings:

- `httpServer::handSocketmassge` at 0x850EC explicitly compares command 4016. Its
  matched path updates the client bookkeeping tree/liveness count. It has no call
  into RTSP creation. Unsupported command IDs use a disconnection path; scanning
  event IDs is not an appropriate startup strategy.
- Accepting a native 9999 client emits APP_CONNECTED (0x9B8); disconnect emits
  APP_DISCONNECTED (0x9B9). The presenter handles these at 0xAC528 and 0xAC5D4.
  Their existing callbacks update native app/UI state without creating RTSP.
- HTTP command 3033 logs ENTERPREVIEWSCREEN and immediately returns to the common
  exit. It does not invoke a preview/start routine in this exact firmware, despite
  the Android client's corresponding request. No live 3033 experiment was needed.
- Presenter SOFTAP_ENABLE (0x8F8) enters media initialization at 0xAC44C. After its
  AP-specific log, 0xAC46C checks current preview/work mode, fetches recorder 1,
  checks the already-started flag, then creates the RTSP server 0xADB90, starts it
  through 0xAD90C and creates its stream sender 0xAE210. The server wrapper supplies
  native port 8554. This block itself does not call the radio controller.
- The recreate routine 0xAE3C8 has direct callers in recording/capture window-loaded
  methods, but they require NetManager mode 3. The NetManager getter at 0x90D30
  reads offset 0x64; its AP-enabled notification (0x8F9) stores 3 there, while native
  station initialization stores 2. Therefore simply re-entering a native window
  while using managed Wi-Fi is not an established bypass.
- The create/sender functions' direct branch references were enumerated, including
  tail branches. This narrows the known call sites; it is not proof against every
  possible computed indirect call.
- The external Unix event receiver maps one-byte hotplug events. Its decoded
  cases do not expose the media's 0x8F8 notification. Live metadata showed that
  socket, udev control, JDWP and wpa control; no System V message queues, and TCP
  listeners 5037/9999/8082/8787. No eligible existing public start API was found in
  the examined HTTP, client-event or Unix-hotplug paths.

**Inference:** the missing integration is between app-client connection and
AP-specific media initialization. Existing drivers are not the immediate gap.
A small native behavior change can reuse the stock camera/ISP/encoder/RTSP code
without making a broad AP event or switching the radio. That behavior remains a
candidate until its real native lifecycle is tested.

## Offline candidate

[`tools/build_native_media_candidate.py`](../tools/build_native_media_candidate.py)
accepts only that exact source hash and builds a new private file. It never edits
the source, accesses a device/process, launches the vendor executable or deploys.
Output is restricted to the repository's ignored `private/` tree and mode 0600.
The vendor executable itself is not committed or added to the TF portal package.

The candidate changes four byte ranges:

1. APP_CONNECTED's final branch at 0xAC5D0 goes to 0xAC46C, after preserving its
   original callbacks. This deliberately bypasses the AP log at 0xAC44C: the normal
   connection callbacks overwrite registers the logging prefix expects. The
   chosen media body initializes its own needed arguments from `r4=this`.
2. APP_DISCONNECTED's final branch at 0xAC638 goes to a 24-byte cleanup trampoline.
3. The trampoline clears the native media-started byte, passes the embedded RTSP
   controller (`this+4`) to the existing destroy routine 0xADD60, then resumes the
   native continuation 0xAB9E4. It tail-enters teardown without a new stack frame;
   no broad AP-disabled event, radio control or unrelated UI teardown is added.
   Static inspection also confirms that the native destroy routine checks for an
   absent server; its real cleanup/error behavior has not been exercised.
4. The executable LOAD segment's file/memory sizes extend 28 bytes into verified
   zero padding to include that trampoline. The file length, entry point,
   writable LOAD, section headers, dependencies and original media routines stay
   unchanged. No new segment is introduced. Native exception behavior remains
   unvalidated despite avoiding an extra stack frame.

The private candidate is `private/native-start/sdvcam-media-candidate`, SHA256
`cfee212adf3a78b936062c3cdb8081cfe8b446b5915f61d8b229c556b261ff2f`.
Its adjacent JSON records exact modified ranges and `deployed:false`.

## Local validation

- Four new guard/layout tests pass; the full native/host suite now passes 69 tests.
- The builder rejects a different build, an already-patched input, out-of-range
  or misaligned branches and existing output files. Tests check the allowed
  changed ranges, original preservation, branch targets, cleanup argument setup,
  LOAD bounds, unchanged entry point and writable-segment header.
- [`tools/test_native_media_candidate_arm.py`](../tools/test_native_media_candidate_arm.py)
  runs a synthetic ARM Cortex-A7 fixture containing the candidate's actual branch,
  initialization slice and trampoline, with inert recorder/server/log/destroy
  stubs. Six cases pass: create/start/sender, already-started guard, teardown
  flag/object/continuation, reconnect, missing work mode and missing recorder.
- This fixture replaces the native function's exits with synthetic frame restore
  code. It does not execute the original app, ISP, encoder, radio or hardware,
  and does not validate its preceding observer/UI callbacks or error recovery.
  Successful local flow is not proof of frames, audio, camera stability or a
  recoverable live handoff. The existing portal ARM suite is unchanged.

## Activation remains a separate stage

The follow-on [guarded TF launch plan](archive/GUARDED-TF-LAUNCH-PLAN.md) now defines the
work sequence, one-shot control contract, failure handling and acceptance gates.
It is design only; the candidate's deployment state remains unchanged.

Subsequent [lifecycle mapping](NATIVE-LIFECYCLE-2026-11-14.md) confirms native
watchdog ownership and crash-style SIGTERM handling. The candidate supplies no
cooperative process-only shutdown and is not eligible for a kill/restart trial.

Read-only inspection of actual init scripts confirmed that S01app starts
`/usr/bin/sdvcam` **before** the S99 TF runtime. The `.sdvcam` file mentioned by
S01app is a checked backup; its inspected branches still launch `/usr/bin/sdvcam`.
It is not a demonstrated alternate-app loader. Dropping this candidate beside the
ordinary CammyOS TF files will not activate it.

A hardware trial requires a separately reviewed, reversible TF-native-app launch
or controlled process handoff. That is not implemented by this candidate and
would exceed the current read-only investigation if performed ad hoc. A future
trial build must define, implement and test:

- Exact source/candidate hash checks, an explicit one-shot enable flag, disarm
  before launch, independent portal/network access and bounded supervision.
- A verified original-app shutdown/resource-release path, preserving the normal
  environment without rerunning S01app's unrelated power/MMIO setup. Normal
  vendor startup can reinitialize hardware; local stubs do not bound its effects.
- Restoration of the original app if the candidate fails, plus ordinary power-on
  with the trial disabled/card removed. No internal firmware replacement.
- One controller/event session, no mode changes during the first trial, explicit
  start/stop/expiry/frame tests and native multi-client/disconnect behavior checks.
- The vendor RTSP listener's authentication/exposure behavior and its relationship
  to the CammyOS lease. It is not automatically protected by the portal token.

The camera has been left on its original app. Route 1 now has a concrete offline
implementation candidate, rather than another guessed command. Its guarded
activation and actual media acceptance remain unfinished; no fallback driver or
replacement MPP backend was started.
