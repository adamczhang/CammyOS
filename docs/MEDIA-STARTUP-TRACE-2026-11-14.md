# Saved-app media startup trace — 2026-11-14

This is **static analysis of saved artifacts**, not an Android packet trace or a
new camera experiment. It narrows the missing native initialization question; it
does not establish a safe bootstrap command. No camera requests/settings were
changed in this analysis. Earlier [native findings](TARGETED-PROBES-2026-11-14.md)
and [PCCAM/app findings](PCCAM-AND-APP-2026-11-14.md) still apply.

## Evidence trail

Saved sources are under the sibling investigation workspace:
`Cammy Bot/M105 investigation/work/hdvcam-vendor-decompiled/sources/`.
Method names are obfuscated; identify the backend and constructor variant before
interpreting a similarly named helper. Several coroutine methods contain failed
JADX reconstruction plus an instruction dump. The dump supports individual calls
and delays, but does not establish a complete runtime ordering of parallel tasks.

| Saved location | Static observation | Implication |
|---|---|---|
| `p069n3/I.java:58` | QZ backend constructs `p063m3.d0(1)` | Use variant1 of the shared helper, not its case0 implementation |
| `p063m3/d0.java:73` | Variant1 sets host192.168.10.1, HTTP port8082 and `/api/` | The original client assumes the camera AP, not managed home Wi-Fi |
| `p127z3/U.java:633` | Attaching an event listener launches `C0643u` | Event connection/keepalive is a separate coroutine |
| `p127z3/C0643u.java:1103` | Dump logs initListener, waits300ms, calls backend `y()`, then repeatedly `C()`; QZ/non-G delay30000ms | Retain one event connection and repeat4016; this is not a proven native RTSP-init operation |
| `p069n3/I.java:2705` | `y()` creates/launches `C0486f` | QZ event transport is the traced Java socket path |
| `p069n3/I.java:155` | `C()` constructs the known4016 payload | Keepalive has no new guessed event ID |
| `p127z3/U.java:610` | `k()` launches `C0641s` and `C0642t` | Initial data and status polling are parallel work, not proof of a single required sequence |
| `p127z3/C0641s.java:114` | Initial data calls backend `j()` | Follow QZ's `I.j`, not another backend implementation |
| `p063m3/d0.java:1232` | Variant1 `m0()` queries2008 and reads `curworkmodename` | Work-mode metadata query; not evidence of a stream-start request |
| `p063m3/d0.java:1345` | Variant1 `o0()` queries2005 and reads `RecodStatus` | Record-status metadata query; not a record toggle |
| `p063m3/d0.java:921` / `:467` | `g0(id)` calls the getdeviceinfo wrapper; URL suffix `/?custom=1&cmd=id` | Recovered read-query structure, not authorization to expose arbitrary command IDs |
| `p127z3/C0642t.java:1091` | Dump polls `U.u()` about every5s; that calls backend `k()` | Status polling is separate from the30s event heartbeat |
| `p069n3/I.java:1360` | Media URL is8554 `/ch00` | Readiness check should target this fixed path |

The body of `I.j()` also assembles resolution/mode/settings metadata. The entries
above are selected confirmed calls, **not an exhaustive initial request list**.
Time/date setters, reset-like commands, recording toggles and mode changes must
not be replayed as a generic initialization sequence.

## Avoid mixing backend protocols

`p063m3/d0.n0()` contains a `StreamStatus` → `ON` operation. Its recovered caller is
`p063m3/G.C()` (line134); G constructs `d0(0)` (line43). QZ uses `I`, `d0(1)` and
4016. Thus this tempting stream-enable string is evidence for the other backend,
not a validated M105/QZ bootstrap. The shared class also has case0 strings such as
`Camera.Menu.UIMode`; QZ's corresponding variant uses2008 instead.

The APK's bundled `libaw_net_client` and other native libraries likewise do not
prove that QZ preview uses their initialization interface. The concrete QZ path
traced here uses the Java event client and its own HTTP/RTSP endpoints. Additional
library or caller tracing must retain that backend distinction.

## What remains unresolved

The native presenter creates RTSP/stream senders in a soft-AP-related branch at
the previously documented addresses. The app's known heartbeat and read queries
do not prove entry into that native lifecycle. Managed Wi-Fi can therefore have
an event connection without a listening media service. No new verified narrow
native media-start bridge was found in this pass.

The locally implemented v0.3 readiness check can now classify event failure,
unreachable RTSP transport, reachable-but-slow RTSP, malformed/error response, and
received video SDP. Its result does not identify every cause of unreachable
transport; the metadata listener inventory can provide separate binding evidence.
SDP still does not prove a decoded frame.

Next hardware acceptance should collect these results through a TF-installed
CammyOS hook. A future bridge requires its native receiver, preconditions, effects
and cleanup to be understood and locally tested. Do not guess udev/presenter
messages, switch the radio, restart the stock app or use memory injection to make
this unresolved dependency appear solved.
