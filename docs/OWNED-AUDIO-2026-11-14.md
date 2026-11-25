# Finite speaker jobs and native speaker audit — 2026-11-14

The local finite-speaker component is implemented and tested on host and ARM.
It does not yet make Cammy speak. Exact static analysis identifies why calling
the stock player from the existing bridge would violate bounded stop semantics.
No camera connection, command, code upload, firmware edit or playback occurred
during this work. The previous verified camera state remains the last live
observation; it was not rechecked for this report.

## Exact stock evidence

Source is the saved `sdvcam`, SHA256
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
The [read-only verifier](../native/audio/verify_speaker_path.py) checks 11
instruction assertions against that exact identity. It deliberately does not
repair or promote the older draft adapter verifier's other claims.

| Static evidence | Meaning and limit |
|---|---|
| `0x80E80` resolves `g_audio_mutex`; `0x80E8C` calls `pthread_mutex_trylock` | Reconfirms AO2 at this speaker entry, not universal audio serialization. |
| `0x81050` calls `0x655EC` | The speaker wrapper reaches this player. Narrow ARM BL scan finds this as its sole direct caller; indirect/tail paths are not ruled out. |
| Full loop `0x656F8`–`0x6570C` reads object+`0xD8`, compares zero, sleeps 5,000 µs and jumps back | There is no timeout or cancellation test in this six-instruction loop. Missing completion can block indefinitely. |
| Callback `0x64DAC` compares event `0x100`; `0x64DB4` conditionally stores 1 at +`0xD8` | This event can release the wait. Callback delivery, shutdown ordering and synchronization remain unproved. |
| `0x656C8` writes constant 44,100 to the prepared frame at `0x656CC` | Appears to be sample-rate metadata; matching type/layout and resampling must be proved before using our 16 kHz PCM. No direct format compatibility is claimed. |
| Calls at `0x656D8`, `0x656F4`, `0x65724`, `0x6572C`, `0x65734` | Frame/EOF/post-wait helper boundaries are pinned. Their exact API types and cleanup guarantees are not proved by nearby objdump names. |
| `0x81154` calls `pthread_mutex_unlock` after the player call returns | The normal path retains the outer mutex over the blocking player. Full exceptional/unlock coverage is not established. |

There are nine direct BL sites to the speaker wrapper: `0x3AACC`, `0x3BAA4`,
`0x3BADC`, `0x45F38`, `0x4ADF0`, `0x4F320`, `0xA4948`, `0xA51D8`, `0xBF05C`.
This is a static caller list, not a runtime thread-ownership map. The timer's
speaker-amplifier behavior and all output paths still need serialization review.
Instruction slices and JSON results are private in `private/audio-bridge/`.

An external timeout around `PlaySound` cannot safely free its input or unlock
its native mutex. Moving that call to a thread would leave an unbounded thread
and borrowed storage behind. Therefore no native speaker backend or trial
payload is generated from this call alone. A kernel driver is not the identified
blocker: the missing piece is an eligible asynchronous native audio adapter.

## Implemented component

[`native/audio`](../native/audio/README.md) supplies one finite copied PCM job,
boot/instance/controller/generation binding, monotonic request IDs, lease and
dispatch-time deadline checks, cancel-before-dispatch, finite playback/cleanup
budgets and buffer retention during uncertain cleanup. Only a matching genuine
completion allows reuse. Stop acknowledgments are distinct from cleanup.
No-effect refusal is distinguished from an unexpected/uncertain submission.
Delayed tokens cannot affect the next job. Native backend is absent and physical
availability is always false. The status-only bridge protocol is unchanged.

The component accepts an existing authenticated portal lease; it does not
implement another authentication or lease authority. All calls are serialized
on one actor. The [subsequent asynchronous layer](ASYNC-AUDIO-2026-11-14.md) now
provides a worker and callback mailbox locally. Native dispatch, transport,
gadget routing and continuous microphone adapter remain unimplemented.
The C header documents the exact backend and lifetime contracts.

## Verification

- 16 named synthetic lifecycle scenarios pass on host, ASan/UBSan and ARM
  Cortex-A7 qemu, with compiler warnings treated as errors.
- Four static-verifier unit tests pass, including one subcase for every
  instruction mutation; no vendor ELF fixture is committed.
- 11/11 exact-stock instruction assertions pass. This is static evidence.
- ARM core object imports only `memcmp`, `memmove`, `memset`. No time64, atomic
  helper or syscall imports. Static qemu success is not a combined in-process
  audio-library proof or a hardware acceptance result.
- Full repository `make test`: 475 tests passed in 119.291 seconds. GCC
  `-fanalyzer` reported no findings for the new core. New documentation links
  resolve and `git diff --check` passes.

## Next concrete integration boundary

Resolve the native output helper types, serialized owner and stop/flush/drain
semantics, including timer/amplifier interaction. Either expose an asynchronous
job on that proven owner or make a reviewed cooperative player change with a
bounded wait, cancellation and correct buffer cleanup. The existing synchronous
stock call is not eligible as-is. The worker/mailbox and saved-libc library are
now built and fixture-tested, but their native binding remains unavailable.

The first hardware trial should be a single ≤250 ms cue with a fresh lease,
followed by an explicit stop and independently recorded acoustic evidence.
Require normal live view/recording idle before and after, exact identity checks,
and verified stock restore for any candidate launch. Only after audible speaker
and stop evidence should simultaneous native mic/speaker and AEC testing start.
This is a test specification, not an executable or approved camera deployment.
The existing `/v1/speak` and `/v1/audio/capture` still return 501; no native
launch, driver, mixer or hardware ownership gate is closed by this work.
