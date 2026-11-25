# PC desktop panel and offline development

The PC gateway now serves a small desktop control panel at its loopback HTTP root.
This is a host-only implementation slice of milestones 5 and 9. It does not add
face/gimbal/audio adapters, a model provider or an autonomous companion. It now
also provides an explicitly requested native-photo path verified on the camera.

## Features

- Experimental authenticated photo capture with a matching lease, boot provenance,
  JPEG validation, original-mode restoration and a10second display expiry. The
  resident service enables this explicitly; source defaults are disabled. Photos
  remain on TF and require leaving camera buttons/phone app untouched. Typical
  verified transactions took6.1–8.5seconds. See [snapshot limits](NATIVE-PHOTO-SNAPSHOTS.md).

- Gateway connectivity, configured endpoint, body control owner and remaining lease.
- Authenticated capability, state and recent-event inspection.
- Explicit acquire/renew, release and stop operations through the existing gateway.
- Stock preview start/stop controls when the body advertises the backend and this
  page owns the lease. The preview URL is diagnostic information; browsers do not
  play RTSP and stream availability/freshness is not verified by this panel.
- Disconnected/offline states clear body data and disable controls. Unavailable
  gimbal, face, capture and speech adapters are labelled unavailable.
- Tokens stay in page memory, are cleared from the password field after submission,
  and are not stored in cookies, browser storage or the HTML. Forgetting a token
  does not send a stop command; a lease expires naturally unless renewed.
- Local-only assets, no third-party requests, a restrictive Content Security Policy,
  and text-only rendering of device data.

The portal still uses a shared token and cooperative agent labels. Choose distinct
agent names for independently operated clients. Stop currently closes stock preview
and releases the body lease; it cannot stop unimplemented motor/audio adapters.
A browser tab is not a reliable lease-renewal scheduler: renew explicitly. A future
resident worker must manage lease lifetimes independently of tab throttling.

## Start without contacting the camera

Run these from the Cammy-OS repository. Keep LAB mounted. The token is generated
locally and must remain private:

```sh
python3 - <<'PY'
from pathlib import Path
import os
import secrets
p = Path('private/desktop-offline')
p.mkdir(parents=True, exist_ok=True, mode=0o700)
token = p / 'portal.token'
if not token.exists():
    fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(secrets.token_urlsafe(32) + '\n')
PY
python3 host/cammy_host.py --offline --token-file private/desktop-offline/portal.token --port 8791
```

Open `http://127.0.0.1:8791/`. The panel shows **Offline development — camera
requests disabled**. Since there is no body, actuator and preview buttons stay
disabled. Paste the local token only if inspecting the authenticated gateway API;
there is no simulated body pretending to be connected.

`--offline` disables both monitor discovery and the outbound request method, even
if `--config` includes real camera addresses. It also suppresses configured status
file writes, to avoid overwriting the live gateway's status. It starts no sensing,
opens no USB devices and requires no camera or firmware action. Use a separate port
from the resident gateway at 8790. Interrupt this process with Ctrl+C when done.

For a transient user service, use the same command as `ExecStart` via
`systemd-run --user`; do not restart the existing `cammy-host.service` for offline
work. The prepared offline desktop instance uses unit `cammy-desktop-offline` and
port 8791. Stop it with:

```sh
systemctl --user stop cammy-desktop-offline.service
```

The normal camera-backed service will serve the panel after its next separately
authorized restart. Offline development does not restart or reconfigure it.

For a connected virtual body with synthetic media and faults, use the separate
[simulator](SIMULATION.md) on port 8792. Offline mode on 8791 remains deliberately
disconnected; simulated capabilities are never presented as real camera support.

## Validation and remaining work

`make test` uses a native portal running only on loopback, fake HTTP bodies and an
offline gateway. New tests verify the outbound-request guard, disconnected 503,
authentication, static asset routing, token exclusion, startup and browser control
states. Node, when available, runs a minimal DOM harness for the JavaScript;
there is no screenshot or full browser layout acceptance test yet. Node is not a
runtime dependency for the desktop panel. No test contacts a camera endpoint.

The next hardware-dependent steps remain home-LAN media, measured microphone and
speaker behavior, a bounded real gimbal command, and screen-write ownership. These
are required before wiring a resident agent to actual sensing and movement. Further
PC work can then add a media broker, provider adapters, worker supervision, voice
processing and MCP/ROS bindings against confirmed capabilities.

## v0.3 source additions — backend routes require a service restart

A v0.3 body advertises metadata probes and preview status. The desktop now offers
explicit catalogue/run/cancel controls and a bounded current-job display, with
owner-aware cancellation. It polls only a selected running job; evicted/missing
results are shown as unavailable without marking the body disconnected. Readiness
is displayed separately from preview-control acceptance and never invents a fresh
camera frame. Old bodies hide/disable unsupported operations by capability.
Stop remains clickable during an ordinary pending refresh/read. Tokens and probe
results stay in page memory and are cleared when forgotten/disconnected.
The local DOM harness covers these states; no browser or camera was opened for
this validation. Restart the gateway from reviewed source during TF acceptance
before claiming these additions are live on the resident desktop service.

On2026-11-14 the resident gateway was restarted after the v0.3 TF boot. Its new
probe/readiness routes passed actual-camera checks. Preview reported RTSP
unreachable; a browser video feed is still unavailable. Simulator/offline services
were not restarted. See [current status](STATUS.md).
