# Supervised metadata probes — v0.3

Date: 2026-11-14. **TF-deployed and boot/probe verified on the actual camera.**
The camera now runs0.3.0, with all four probes checked through the restarted gateway. This includes the first probe increment and the subsequent preview/desktop work in the
[v0.3 plan](archive/PLAN-0.3.0.md), not acceptance of the entire release or media milestone.
A [v0.3 TF candidate](archive/RELEASE-0.3.0.md) is now built and staged locally; boot/probe/cleanup acceptance has passed; working media remains open.

## What can be asked

| Catalogue ID | Fixed sources | Limits |
|---|---|---|
| `runtime` | `/tmp/cammy-runtime/RELEASE`, `state`, `/proc/uptime` |256bytes per source; no process-liveness/UI inference |
| `network-bindings` | `/proc/net/tcp` |8192bytes,16matching IPv4 listeners; ports8082/8554/8787/9999 only |
| `display-layout` | fb0 sysfs `virtual_size`, `bits_per_pixel`, `stride` |256bytes per source; no pixels/device opening |
| `audio-state` | ALSA cards, card0 playback/capture `hw_params`, capture `status` |256bytes per source; no mixer/PCM opening |

Every result also records monotonic observation time and the boot-ID source with
its availability. Individual file samples report source, available, errno,
truncated and value. Missing/error values are null. Source bytes are JSON escaped;
non-ASCII bytes map to Unicode code points0–255, not a UTF-8 interpretation.
A job completing means collection finished, even when sources were absent. It
never means a physical adapter or stream worked. Results are snapshots, not an
atomic kernel state or a continuing readiness guarantee.

A listener inventory includes each selected IPv4 address and port. A wildcard,
loopback, AP or home binding can be distinguished if it occurs within the bounded
source window. Absent rows in a truncated result are inconclusive. IPv6 and other
network namespaces are outside this collector. An RTSP listener is not proof of
valid SDP or a decoded/fresh frame. `audio-state` is narrower than the planned
`audio-routing` collector; it does not infer microphone routing from PCM state.

## API and SDK

All routes require the existing bearer credential. Start and cancel also require
an `X-Agent-ID` label. Labels coordinate trusted clients; they are not separately
authenticated identities. Metadata collection does not acquire a control lease.

| Operation | Response |
|---|---|
| `GET /v1/probes` |200catalogue; supervisor ready/busy/degraded; no job starts |
| `POST /v1/probes` with `{"probe":"runtime"}` |202with generated `job_id`, or409if worker busy/awaiting reap |
| `GET /v1/probes/job-N` |200status/result;404if unknown or evicted |
| `POST /v1/probes/job-N/cancel` with `{}` |200terminal status for the matching job label;409for another label |
| `POST /v1/stop` |Existing authenticated stop, now also cancels the active metadata job |

Job IDs and catalogue IDs are distinct. Unknown fields, duplicate fields, supplied
paths/commands and unknown collectors are rejected. A completed/failed job can be
cancelled idempotently by its label without changing its terminal status. Any
trusted authenticated client can use Stop; the shared credential remains the trust
boundary. Lease expiry alone does not cancel read-only jobs.

The Python standard-library SDK and gateway proxy support these routes. An installed
v0.2 body returns404; do not silently substitute local PC observations. The simulator
now implements labelled synthetic jobs, deadlines and preview faults. The desktop
has explicit catalogue/run/cancel controls and polls the selected job. The running
gateway service has now been restarted and the new routes checked against Cammy.

```python
from cammy import Cammy

# Supply your existing token through private configuration; never put it in git.
body = Cammy("http://127.0.0.1:8790", token, "diagnostics-agent")
print(body.probes())
job = body.probe_start("network-bindings")
# Poll body.probe_status(job["job_id"]) until status is terminal.
# body.probe_cancel(job["job_id"]) cancels an owned running job.
```

Statuses are running, completed, failed, timed_out or cancelled. Only completed
jobs contain results. Started/finished times use monotonic seconds; a running job
has finished time0. Two jobs remain in RAM, including the current job. New jobs
can evict older terminal results; there is no automatic disk logging or retention.

## Scheduling and containment

One forked collector runs at a time with a one-second deadline, enforced both by
the parent loop and a child alarm. Its child closes
inherited portal listener/client/preview sockets. A parent-death SIGKILL and
parent-PID race check prevent a collector surviving a portal crash. The parent drains a nonblocking
pipe and rejects results of16000bytes or more. Each source and row count is bounded
before serialization. Two public result buffers plus one compatibility buffer retain less than48KiB of payload;
there is no unbounded job queue. A timeout/cancel sends SIGKILL, closes the pipe,
and uses nonblocking waitpid. A child still awaiting reap makes the supervisor
**degraded** and refuses replacements, including if a kernel operation cannot yet
be killed. The normal timeout/reaping/replacement path is covered by fixtures;
an actual uninterruptible kernel task has not been reproduced.

HTTP input now has eight nonblocking client slots, each limited to8192request bytes,
1024body bytes and a three-second absolute deadline. Partial input no longer
serializes every other client behind a socket read. Exhausted slots reject new
connections; this is not denial-of-service protection or a reserved Stop channel.
Responses are bounded and nonblocking; a slow reader can receive a disconnected
response rather than holding the event loop indefinitely.

The compatibility `/v1/diagnostics` collector now uses the same supervised worker.
Its HTTP request waits asynchronously for the usual v0.2-shaped200 response; the
worker is serialized with named jobs, so a busy worker returns409, and failed,
cancelled or timed-out collection returns503. It does not consume public job IDs
or emit public job events. Half-closed HTTP request writers can still receive the
reply. Compatibility results use a separate fixed buffer, without public history.
Stop cancels this read as well. Native event connection, keepalive and RTSP checking
are now nonblocking too; see [preview readiness](PREVIEW-READINESS.md). Local tests
cover these paths during slow reads and partial HTTP input. Real kernel failure,
CPU starvation and saturated client capacity are not a hardware latency guarantee.
No collection runs at boot and no shell/path/URL/command argument is accepted.

## Validation and remaining work

`make test`: **65native/host tests pass**. `make arm` builds the static ARMv7 binary;
`make test-arm-local`: **15Cortex-A7 protocol tests plus9preview fault tests pass**.
These are local tests, not camera deployment or full-board emulation.

New real-C fixtures exercise a blocked FIFO collector, one-second timeout and
reaping, abnormal worker exit, replacement after cleanup, busy rejection, owner cancellation, parent
crash cleanup with adopted-child reaping, Stop with
both blocked collection and a partial HTTP client (under500ms assertion), encoding,
missing/truncated files, bounded history and selected listener/address filtering.
Gateway tests exercise SDK job operations through a real local C portal. Fixtures
set an internal source prefix in a compiled harness; the shipped CLI and HTTP API
have no source-prefix option.

Preview status/keepalive, simulator/desktop views, static app follow-up and local
TF packaging are now implemented. The boot/probe checks now pass on actual hardware. Remaining: the unresolved
native media-start bridge, richer process/mixer/IPC mapping and actual frame evidence. Do not describe this candidate as a finished media fix.
