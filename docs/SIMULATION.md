# Local camera simulation and tuning

The camera simulator runs inside the PC gateway. It uses no USB device, camera,
network camera endpoint, firmware, physical microphone, or physical motor. It is
API/behavior emulation, not V853 CPU, kernel, boot ROM, ISP or firmware emulation.

The simulator profile is reported in identity, capabilities, gateway health and
state. The desktop shows a prominent SIMULATION banner. Success here is evidence
about host software only, not proof of M105 hardware support.

## Start locally

The prepared transient service is `cammy-desktop-simulator.service`, serving
`http://127.0.0.1:8792/`. The original gateway on 8790 and offline panel on 8791 are
separate processes and have not been restarted or reconfigured. Simulator
credentials are generated separately in `private/simulator/portal.token`; open that
local file and paste its token into the simulator panel, then connect. It is not a
camera credential and must not be committed. Stop the simulator with:

```sh
systemctl --user stop cammy-desktop-simulator.service
```

To run it manually after stopping the service:

```sh
python3 host/cammy_host.py --simulate companion --token-file private/simulator/portal.token --port 8792
```

`--simulate` is incompatible with `--config` and `--offline`, so a simulator launch
cannot inherit real camera addresses. Its gateway request path calls the simulator
in-process and bypasses the HTTP transport, even if endpoints are changed in code.
Only the desktop listener is a loopback TCP server. No model server is started.

## Profiles

- `stock`: leases, identity, capabilities, state and event ring; hardware adapters
  return 501, matching the compact portal with stock preview disabled. It is not a
  byte-for-byte emulator of every C parser or vendor API edge case.
- `companion`: proposed synthetic adapters for exercising the host experience.
  Features remain explicitly simulated. These are a development contract, not
  additions to the deployed camera API or measured M105 capabilities.

## Interactive exercise

1. Connect using the simulator token and choose an agent name.
2. Acquire a 30-second lease. Another agent name cannot claim it until released or
   expired. Renew explicitly when needed; browser background throttling is not a
   resident control scheduler.
3. Start preview control. The panel renders timestamped synthetic test-pattern
   frames. This is not RTSP, H.264, sensor imagery or continuous video.
4. Jog virtual gaze left/center/right and select a screen-face state. The displayed
   pose is a commanded estimate with fictional ±45° limits and immediate movement.
   It does not imply those axes/limits or feedback exist on the M105.
5. Simulate a reply: text appears and an event is emitted. No TTS/voice is generated.
   Generate a test tone to get a quiet 0.1s 440Hz WAV sample. Playback is explicit;
   it is not microphone capture, speech recognition or a full-duplex audio test.
6. Freeze frames. Once a frame is over one second old, the simulator returns 503
   and the panel clears the picture. Resume frames to recover fresh data.
7. Inject request latency (0–1500ms), disconnect and reconnect. Fault controls are
   out-of-band and remain authenticated/reachable during the simulated outage.
   Disconnect clears preview/ownership. Reconnection does not reacquire control or
   resume observation. Stop holds the commanded virtual pose, resets expression
   and text, stops preview, and releases ownership.

## Development interfaces

Existing SDK/API routes use their usual `/v1/` paths. Simulator-only controls are
available exclusively when the gateway has a simulator:

| Route | Purpose |
|---|---|
| `GET /v1/simulation` | Profile, virtual limits and active faults |
| `POST /v1/simulation` | Set `disconnected`, `stale_frame` booleans or `latency_ms` integer |
| `GET /v1/simulation/frame` | Synthetic pattern metadata, sequence, capture time and age; reject stale data |

Future-adapter mock request bodies: `/v1/look` takes `pan_degrees`; `/v1/face`
takes `expression`; `/v1/speak` takes `text` (1–160 characters). Audio capture
returns a base64 mono16kHz WAV test tone. No arbitrary command, URL or filename is
accepted. These mock bodies still need review against actual hardware interfaces.

Events use a bounded 32-entry ring. Lease expiry uses monotonic time; unit tests
inject a deterministic clock. All request mutations are serialized by a lock,
while injected delay occurs outside the lock so faults cannot block stop behind a
sleeping request. Stop itself is still subject to configured simulated latency.

## Repeatable local tuning

```sh
make test
python3 tools/tune_simulator.py --samples 50
```

The tuning tool starts its own temporary loopback gateway with an in-process
simulator, exercises gaze/face/frame cycles, rejects frozen frames, reconnects,
checks synthetic capture and stop, then shuts down. It accepts no remote address
and uses an ephemeral credential. Results distinguish simulated acceptance from
hardware timings. A 50-cycle run on this PC measured a three-request cycle median
of 0.786ms, p95 1.156ms and stop round trip 0.284ms; these numbers are not
performance targets or real camera/Wi-Fi latency.

The native C portal remains covered by its existing loopback tests. Simulator tests
cover ownership, expiry, stop, invalid/bounded input, unavailable stock adapters,
synthetic audio, bounded events, stale frames, authentication and fault recovery.
A poisoned external endpoint plus a transport mock verifies that simulation uses
no outbound HTTP. The JavaScript DOM harness checks simulated controls and stale
frame presentation; a complete visual browser test has not been performed.

## Actual ARM userspace emulation

`make test-arm-local` runs the existing portal protocol suite using the real
`build/arm/cammy-portal` binary under local `qemu-arm -cpu cortex-a7`. It uses an
already prepared local QEMU binary (or one on PATH), installs nothing, and starts
only temporary loopback test processes. Use `tools/test_arm_local.py --qemu PATH`
to select an existing emulator explicitly. The ARM binary must already be built.

This tests the actual ARM portal executable, parsing, authentication, ownership,
events, expiry and unsupported-adapter behavior. It does not emulate the full
V853 board or run the camera's Linux4.9 kernel. Kernel-specific behavior, physical
peripherals and real-time scheduling still require hardware verification.

## SDK cleanup correction found locally

The running-simulator check found that calling `stop()` or `release()` inside an
SDK context block was followed by a second release on context exit, raising 409.
The SDK now tracks its successful acquire/release/stop lifecycle and skips
redundant cleanup after explicit stop/release. Failures still propagate normally;
this does not change body ownership rules. The regression is covered using the
native and actual ARM portal binaries locally.

## Next hardware boundary

This environment lets us develop host behaviors, UI, provider adapters and error
handling before hardware integration. It does not validate sensor timing, real
media transport, audio intelligibility/echo cancellation, motor UART commands,
screen ownership, power stability, or replacement OS boot. Do not transfer virtual
limits, timing results, or simulated capabilities to the camera without measuring
and implementing the corresponding body adapter.

## v0.3 probe/readiness fixtures

The simulator now exposes the same four metadata catalogue IDs and job routes,
with two retained jobs, owner cancellation, one active job, synthetic completion
and a one-second simulated deadline. `probe_hang` models timeout without blocking
the process. `preview_mode` accepts `sdp-ready`, `no-listener`, `slow-sdp` or
`protocol-error`; its readiness evidence remains synthetic and separate from
synthetic frame freshness. Direct simulator tests inject a clock; no real device,
file read, worker process, mixer or network request is needed. The native C
preview fixtures separately exercise actual loopback sockets and are also run
under ARM emulation. Existing simulator services were not restarted automatically.
