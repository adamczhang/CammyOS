# Asynchronous audio integration — 2026-11-14

The worker, callback mailbox and camera-libc-compatible shared component are
implemented and locally validated. **This native PCM binding remains unavailable.**
A subsequent
[stock-input route](STOCK-CONTROL-BASELINE-2026-11-14.md) does remotely play an
existing camera video audibly; that route does not require this native binding.
No camera access, upload, firmware change, mixer operation or native app restart
occurred. The native resolver remains NULL: the exact binary/source comparison
found two concrete integration mismatches that must be resolved before a trial.

## Implemented lifecycle

[`cammy_audio_async`](../native/audio/cammy_audio_async.h) connects the finite
speaker core to a driver table with open/send/close calls. These calls run on
one private worker with signals masked, outside adapter locks. Actor submission
uses an atomic slot; it cannot spuriously lose a fresh job because the worker
temporarily owns its condition-variable mutex. Actor cancel sets a stop request;
it does not run SDK stop inline or let cleanup overtake a blocked send.

Callback threads copy only job/event/frame identifiers into a16-slot sequence
mailbox. Publication has at most eight CAS attempts and uses no allocation,
mutex or core access. Stale job tokens and duplicate frame returns are tracked.
Unknown event/frame combinations are rejected. Overflow invalidates ownership
and permanently blocks reclamation for that adapter instance; it never becomes
an invented completion. One logical PCM job is submitted, not a native SDK frame
layout; future native fragmentation stays within the driver binding.

Frame return, EOF, successful channel shutdown and callback-producer quiescence
are separate. Reuse requires successful close and return of any accepted PCM
reference. A no-effect send refusal needs close but no frame-return callback.
Partial open and uncertain send results still require cleanup. Failed cleanup,
missing return, or a blocked worker retain PCM and object storage. Core deadlines
remain responsive while the SDK worker is stuck. The adapter makes no promise
of physical cutoff when a driver hangs; it reports uncertainty. There are no
automatic retries or replacement workers.

Destroy is allowed only after confirmed core quiescence; it has a200ms join
budget. Failed destroy retains the entire object, driver context and loaded
library until process exit. Actual callback-producer quiescence is part of the
driver contract and still needs native proof. No library constructor is used.
The status-only bridge protocol, portal endpoints and resident desktop service
are unchanged. `/v1/speak` and `/v1/audio/capture` remain501.

## SDK and exact-binary findings

Primary reference is Lindenis' [V853 SDK audio implementation](https://github.com/lindenis-org/lindenis-v853-external/blob/bf42476c330aa41aada55e7c030e4be22e46107d/eyesee-mpp/middleware/sun8iw21/media/mpi_ao.c),
pinned to commit `bf42476c330aa41aada55e7c030e4be22e46107d`.
Six private source copies match the repository's immutable Git blob hashes.
The [frame declaration](https://github.com/lindenis-org/lindenis-v853-external/blob/bf42476c330aa41aada55e7c030e4be22e46107d/eyesee-mpp/middleware/sun8iw21/include/media/mm_comm_aio.h)
and [AO component](https://github.com/lindenis-org/lindenis-v853-external/blob/bf42476c330aa41aada55e7c030e4be22e46107d/eyesee-mpp/middleware/sun8iw21/media/component/AOChannel_Component.c)
provide candidate semantics, not an exact M105 source match. No SDK source is
committed or compiled into the new component.

| Evidence | Consequence |
|---|---|
| SDK SendFrame is asynchronous and returns caller PCM through RELEASE_AUDIO_BUFFER; its `s32MilliSec` is not used in that function | A timeout argument is not a bounded playback/cleanup guarantee. |
| SDK StopChn sends Idle and waits on a component semaphore; DestroyChn sends Loaded, waits and frees the component | Driver calls belong off the actor; stop acceptance does not prove buffer or callback release. |
| Exact M105 names resolve through pinned PC/literal/add instructions: StartChn=`0x24D710`, StopChn=`0x24D934`, SendFrame=`0x24DBAC` | Names are derived from their own log references, not nearby exported symbols. |
| M105 StopChn calls `0x130440`; that helper loops through `pthread_cond_wait` at `0x13047C` without a timeout | Native stop can itself block, independently of the stock player's EOF loop. |
| Stock post-EOF calls StopChn then StartChn (`0x6572C`, `0x65734`) | The stock player reuses its channel. An adapter must not destroy it as disposable state. |
| SDK ARM32 frame begins with sample rate, PCM pointer at+12; M105 preparation stores1 at+0, PCM pointer at+8 and44,100 at+36 | The public header is incompatible. Candidate field meanings beyond these stores remain inferred; blindly relinking the SDK is invalid. |
| M105 amplifier timer reads `pa_flag` and `g_play_state`; the speaker wrapper uses `g_audio_mutex` | Holding the speaker mutex alone does not establish amplifier ownership. No bridge changes these globals. |

[`audit_native_profile.py`](../native/audio/audit_native_profile.py) rechecks six
groups of exact-stock assertions, including24 pinned words, and emits
`activation_eligible:false`, `native_driver_available:false`. Exit4 is the
expected LOCAL_ONLY verdict. It exposes no override to enable a native backend.
The stock identity is `8771961f…62b0ec`, as recorded in the preceding
[speaker report](OWNED-AUDIO-2026-11-14.md). Old draft adapter claims remain drafts.

## Build and verification

- 16 asynchronous scenarios cover blocked open/send/close, late cleanup, failed
  close, missing frame return, stale/duplicate/invalid callbacks, saturation,
  partial initialization, uncertain send and queued cancellation. They pass
  host, ASan/UBSan, TSan and static ARM Cortex-A7 qemu.
- The concurrent-producer scenario posts1,600 stale events from four threads
  without losing accounting or applying an event to the active job. TSan keeps
  race detection on; only cases4/5/7 suppress expected process-lifetime thread
  retention diagnostics.
- The same16 cases pass through `libcammy_audio_async.so` linked to the **saved
  camera libc**, using musl1.1.24 headers under qemu. ABI audit passes:19 exact
  exports,22 imports resolved by saved libc, no time64, TLS, constructors or
  unexpected library dependency. Only `libc.so` is needed.
- Shared library SHA256:
  `63dafa91e5496db4e2cd2603f3ccde369b205e631e4b5d7fe37470ffcf56a912`.
  This is a synthetic component ABI proof, not loading inside sdvcam or a camera
  measurement. No native addresses are called or bound by the component.
- Four native-profile regression tests reject changed instructions, function
  references, PA globals and the incompatible public header layout.
- Full repository suite:497 tests passed in123.061s. The finite core's18 cases
  pass ASan/UBSan and ARM after adding failure/quiescence events. GCC static
  analysis reports no findings for the asynchronous implementation; new
  documentation links and `git diff --check` pass.

Commands and supported targets are in [`native/audio/README.md`](../native/audio/README.md).
Exact artifacts/logs are under ignored `build/native-audio/device/` and
`private/audio-adapter/`. The fixture is a standalone synthetic host; it never
executes the vendor application or opens audio devices.

## Next hardware boundary

This is not an installable speaker trial. Finish the exact M105 frame adapter
and a cooperative channel/amplifier ownership hook, without overwriting stock
callback/state or destroying its reused channel. Connect those native operations
to this worker, and prove buffer return plus channel/callback shutdown. Then
build a reviewable candidate with the existing device-compatible loader and
recovery workflow. Current library has no native resolver or startup hook.

Acceptance remains a fresh owned lease, one≤250ms cue, measured speaker output
and stop, and normal live-view/idle restoration. A native microphone recording
can then establish the simultaneous near-end cue. Speaker phrase, duplex/AEC
and physical stop remain incomplete; no hardware acceptance gate is closed.
