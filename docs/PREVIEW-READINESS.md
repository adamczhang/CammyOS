# Preview readiness contract — v0.3

Implemented, TF-deployed and boot/probe verified on2026-11-14. The camera and
restarted gateway now expose v0.3 hooks. The actual bounded readiness result was
rtsp-unreachable; Stop and short-lease cleanup passed. No new native initialization command was added.

## Explicit operation and ownership

With the stock backend enabled and a valid control lease, `POST /v1/preview/start`
accepts a nonblocking attempt and returns202 with the existing RTSP URL/active
fields. This means work is accepted, not connected or streaming. The request's
accepted local interface address determines the RTSP target. The native event
endpoint stays fixed at loopback9999; the RTSP endpoint is fixed at that local
address, port8554, path `/ch00`. No URL/port/command parameter is accepted.

The event TCP connection has a two-second connect/write deadline. Once connected,
CammyOS sends exactly the known17-byte4016 JSON payload. It retains the connection
and repeats the payload about every30seconds while the body lease is valid. It
does not renew the lease. An idempotent Start leaves the existing event session,
readiness result and lease deadline intact.

After the first payload, one bounded five-second readiness check attempts RTSP
transport and sends a DESCRIBE request. Unreachable TCP connections may retry at
roughly200ms intervals within that overall budget. No SETUP, PLAY, recording,
settings, reset, radio-mode or native presenter/udev command is sent. This checks
metadata; it does not capture camera frames or audio.

`GET /v1/preview/status` is authenticated and reports:

| Field/state | Meaning |
|---|---|
| `idle` | No owned event attempt or readiness check |
| `event-connecting` / `event-connected` | Event transport progressing; media unverified |
| `waiting-for-listener` | Fixed RTSP transport has not yet been established |
| `checking-sdp` | TCP connected; bounded RTSP response pending |
| `sdp-ready` | A matching200 RTSP response with minimally structured video SDP arrived |
| `failed`, `failure_reason` | Bounded transport/protocol check failed; see reason |
| `event_connected`, `commands_sent` | Event transport/command evidence only |
| `listener_reachable` | Null before attempts; otherwise whether this check observed TCP reachability |
| `sdp_ready` | Null before SDP observation; true only on accepted video SDP |
| `checked_monotonic_seconds`, `sample_age_seconds` | Completed check time and age; not continuous readiness |
| `rtsp_status` | Parsed numeric RTSP status,0if none; no native payload logged |
| `frame_fresh`, `frame_evidence` | Always null / `not-collected` in this body implementation |

A false reachability observation means this fixed target was not observed
reachable; it does not establish that no server exists on another interface.
Use the separate selected-listener metadata probe for binding evidence. SDP is
also a snapshot that can become stale. The validator checks RTSP version/status,
matching CSeq1, unique bounded Content-Length, application/sdp, `v=0` and a video
media section with RTP mapping. Headers are bounded to2048bytes and SDP to4096;
the receive buffer is8192bytes. This is not a full SDP interoperability validator
or a decoded-frame test. No names, paths, SDP bodies or arbitrary native event
responses appear in the public result.

## Cancellation and retry

Stop, release, expiry and event failure close the event socket and any owned RTSP
check. No keepalive follows loss of ownership. An RTSP failure alone leaves a
healthy ordinary event session active under its existing lease; it is not silently
reconnected or taken over. `POST /v1/preview/check` with `{}` requires that owner
and a connected event session. It replaces only its own readiness connection,
resets the five-second check and does not renew the lease. Another owner gets409.
There is no separate public probe job ID for this ordinary preview lifecycle.

Socket connects, writes and reads are nonblocking, with bounded per-loop work.
The request parser, legacy diagnostics worker and named metadata worker also avoid
blocking the control loop. Local Stop tests assert a response below500ms during
slow SDP/metadata and partial HTTP input. This is not a guarantee under real
kernel stalls, CPU starvation or exhaustion of all eight client slots.

## Local development and limits

The gateway and Python SDK expose `preview_status()` and `preview_check()`. The
desktop displays readiness separately from its existing preview-control result.
The simulator labels its readiness synthetic and can model no-listener,
slow-SDP and protocol-error outcomes independently of synthetic frame freshness.

Nine native fake-service tests are repeated with the ARM Cortex-A7 userspace
emulator. They cover valid split responses, malformed/oversized/error replies,
unavailable event/listener transport, bounded slow SDP and recheck, keepalive
without renewal, event disconnect, lease expiry and Stop with partial HTTP input.
Shortened timers/ephemeral ports exist only in the compiled test harness, not the
production CLI or API. The production keepalive interval is30seconds.

The [saved-app trace](MEDIA-STARTUP-TRACE-2026-11-14.md) has not established a safe
native media-start bridge. The candidate can diagnose readiness; it does not claim
to fix the missing home-network stream or provide a native display, audio or
motor adapter. Boot/probe/cleanup checks are now verified via the TF update; real media acceptance
remains open. See [release evidence](archive/RELEASE-0.3.0.md).
