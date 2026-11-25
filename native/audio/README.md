# Finite owned speaker component

Local C component, 2026-11-14. Synthetic sinks only; not installed on Cammy.
This is the payload/lifetime part of a future audio bridge. It is not a running
bridge, USB audio router, mixer controller, microphone source or native adapter.
Existing status protocol 1.0 and camera `/v1/speak` remain unchanged.

The future integration layer must validate the existing portal lease and mint
a boot/instance-bound, strictly increasing local generation. That integration
does not exist yet; current portal leases do not supply this generation.
This component checks the supplied binding again at admission. It copies one
finite, even-sized 16 kHz mono S16LE PCM job,
up to 64,000 bytes (two seconds). It does not accept paths, pointers over a
wire, file formats, arbitrary sample rates or a growing speech queue.

Core APIs run non-reentrantly on one actor thread. Backends may not call
a core API inline from `play`/`stop`. The optional `cammy_audio_async` layer now
provides one serialized driver worker and a bounded callback-to-actor mailbox.
Its native binding is **not implemented**; all driver fixtures are synthetic.
The component makes no latency guarantee for a backend that violates its
nonblocking submission contract. No native table is accepted in this version.

`ca_step` rechecks the lease/deadline immediately before dispatch. Mutators
advance expiry accounting but never dispatch queued work: cancel/disconnect
before dispatch means zero playback calls. Playback has its PCM duration plus
250 ms, capped by the request deadline; cleanup has 200 ms. The caller must
step frequently (planned ≤20 ms); there is no internal timer or watchdog.

The sink receives a pointer to the component's private immutable copy. Stop
acceptance, refusal, timeout and owner loss all retain that buffer. A new job
or owner is refused, and `ca_can_destroy()` remains false while cleanup is
unresolved. Only a
matching `FINISHED` or serialized `QUIESCENT` event proves the sink no longer
references the buffer or produces effects. `QUIESCENT` must cover an earlier
play, not merely receipt of the stop. Old tokens cannot complete a newer job;
tokens never wrap. A complete no-effect play refusal is the only synchronous
reclamation path. Other submission results trigger cleanup and remain pinned.
There are no cleanup retries or automatic playback retries.

Use `ca_can_destroy()` before releasing component/backend storage, and quiesce
all callback producers first. If cleanup never arrives, storage must live until
process exit. Core buffers are wiped after confirmed reclamation. Caller-owned
PCM and backend-owned copies remain their owners' responsibility. Simulated
completion proves this bookkeeping, never physical silence. Physical capability
is always false, even if a fake sink claims otherwise.

```sh
python3 native/audio/run_fixtures.py --target host
python3 native/audio/run_fixtures.py --target host-asan
python3 native/audio/run_fixtures.py --target arm
python3 -m unittest tests.test_speaker_static_evidence
python3 native/audio/verify_speaker_path.py
```

The ARM fixture is a static, synthetic process under qemu, not an in-process
camera ABI proof. The core object imports only `memcmp`, `memmove`, `memset`.
The asynchronous shared library separately builds against saved musl1.1.24 and
passes ABI auditing plus dynamic fixture tests under that libc in qemu.
It has no native resolver, loader stub, camera transport or deployment tool.

```sh
python3 native/audio/run_async_fixtures.py --target host
python3 native/audio/run_async_fixtures.py --target host-asan
python3 native/audio/run_async_fixtures.py --target host-tsan
python3 native/audio/run_async_fixtures.py --target arm
python3 native/audio/run_async_fixtures.py --target device
python3 native/audio/audit_native_profile.py  # exit4: LOCAL_ONLY, not eligible
```

The asynchronous driver contract treats one logical PCM buffer as frame1; a
future native binding must split it into appropriate SDK frames and report its
return only after all native references end. EOF, frame return and successful
channel/callback shutdown are separate evidence. Missing events or mailbox
overflow never authorize reuse. Open/send/close all run on the same worker;
cancel is an atomic request and cannot overtake a blocked driver call. A stuck
driver retains object, worker, PCM and loaded library until process exit.
There is no automatic retry. Destroy joins only when the core is quiescent.
TSan thread-leak reporting is disabled only for the three deliberately retained
worker scenarios; race detection remains active throughout.

See [speaker audit and next integration boundary](../../docs/OWNED-AUDIO-2026-11-14.md).
See [asynchronous integration evidence](../../docs/ASYNC-AUDIO-2026-11-14.md).
