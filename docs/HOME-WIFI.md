# CammyOS wireless desk profile

Verified on Adam's AT-M105 / Allwinner V853, 2026-11-13 America/Chicago (2026-11-14 UTC). This runtime uses the stock Linux kernel and camera application, with CammyOS network/agent services. No replacement-kernel boot or raw firmware installation is implied.

## Connection and PC service

| Item | State |
|---|---|
| Home network | <home-wifi>, 2.4GHz WPA2-PSK/CCMP station mode |
| Camera MAC | `<camera-mac>` |
| Camera home address | `<camera-ip>/22`; gateway `<home-network>` |
| Stable address | Adam reports saving Deco reservation MAC → `<camera-ip>` |
| Direct fallback | SSID `CammyOS`, camera `192.168.10.1` |
| Camera agent portal | Authenticated HTTP on port8787 |
| PC agent endpoint | `http://127.0.0.1:8790`, authenticated gateway |
| PC service | Enabled `cammy-host.service`; reconnects to home or fallback endpoint |
| PC fallback radio | NetworkManager `CammyOS-camera`, autoconnect, never default route |
| Hostname | `cammyos`; DHCP announces `CammyOS`; mDNS is not implemented |
| Sensing | Off until an agent explicitly requests supported operations |

Managed/client and AP modes are verified, but concurrent combinations are not supported. The fallback and home connection therefore use the same radio in different modes. The PC retains its Ethernet default route when attached to the camera AP. The gateway polls identity only; it starts no sensing or model server.

## Boot implementation

The original blank8KiB TF boot-stub region was restored and verified; this card now selects normal internal boot. `/mnt/extsd/CammyOS/` holds credential-free static binaries and scripts. The stock-init hook copies tools into tmpfs; it does not replace PID1 or the kernel. The earlier FEL stub can be reapplied independently with the guarded recovery tool. No formatting was done.

1. `S00cammy-power`, gated by `/etc/cammy/desk.enabled`, disables the native idle-poweroff setting before `S01app` reads it.
2. Optional development `S97cammy-usb-debug`, gated by `/etc/cammy/usb-debug.enabled`, configures USB ADB. It does not require a PC for networking. Home TCP5555 was closed during the charger-only check.
3. `S98cammy-name` restores hostname `cammyos` after stock init sets its default.
4. `S99cammy-tf` checks for private configuration and payload. A persistent `/etc/cammy/startup.pending` latch is written before launch.
5. The runtime verifies hashes, copies tools to RAM, waits30s for stock initialization to settle, then loads the exact vendor ECR6600S driver if absent. It switches to managed mode, starts the supplicant, and obtains DHCP.
6. It starts the portal and monitors association. Brief link loss uses supplicant reassociation; failed initial association/DHCP or about40s sustained association loss selects the CammyOS AP.
7. After60s of portal survival the startup latch is cleared. A reset before that leaves it present; the next boot skips the failed runtime path instead of cycling indefinitely.

The latch is deliberately conservative: intentional power loss during startup also leaves it set. An operator should inspect its phase before clearing it. AP fallback currently remains AP until runtime restart; automatic AP-to-home retry is not implemented. The PC gateway can still use the fallback. Address discovery currently uses configured addresses, supported here by Adam's DHCP reservation.

## Hardware acceptance

| Check | Evidence |
|---|---|
| Station join, DHCP, wired-PC ARP/ping and camera HTTP | `evidence/home-wifi-trial.json`; earlier USB readbacks |
| Actual short link loss and autonomous reassociation | `evidence/home-wifi-trial.json` |
| Runtime AP fallback, broadcast name/MAC and authenticated control | `evidence/tf-ap-fallback.json` |
| Guarded stop/start and return home | Same fallback test and structured checkpoint |
| Same TF boots stably with the automatic hook disabled | `private/coldboot/baseline-evidence.json` and USB baseline |
| Fresh driver start after stock boot settles | `evidence/post-boot-radio-start.json` |
| Physical cold boot, no WiFi/USB controls touched | `evidence/guarded-coldboot.json` |
| PC automatically reattaches to the native AP | Disabled-hook comparison; no new connection-up command |
| Unauthenticated API requests rejected, camera and gateway | `evidence/wireless-auth-check.json` |
| Sustained USB-power-only idle run | `evidence/power-only-12min.json` — passed, over720s continuous connection |

The cold-boot verifier records a new kernel boot ID, completed home state, authenticated lease acquire/release and60s latch clearance. An earlier assisted boot is explicitly excluded because Adam enabled WiFi manually. The power-only verifier checks no camera USB serial on this PC, continuous authenticated home control, and no preview. It uses a12minute window because the native idle options include3,5and10minutes.

## Two failures found and addressed

The first immediate automatic start caused resets roughly every19s, visible independently in USB logs. Removing TF restored stability; the same card booted stably after disabling S99. Fresh radio initialization after stock boot settled worked. The delayed/guarded revision then passed clean physical startup. The precise early reset trigger remains unlocalized; this does not establish a particular driver defect. See `evidence/tf-coldboot-usb-cycling.log` and private boot diagnostics.

The first charger-only start joined home and passed65s, then powered off without operator input. That short test was insufficient. Native `ml_camera_timedshutdown=1` selects3minutes. Both the original English UI and executable table at0x4ddf40 map indices to `[0,3,5,10]` minutes; zero stops the timer. The early desk hook now sets0 before the app caches settings. Only that value changed against a private backup. Screensaver and standby were already off. See `evidence/idle-shutdown-analysis.json`; the later12minute result is the sustained-operation gate.

## HTTP receive workaround

On home WiFi, quick separate header/body writes from Python HTTP arrived with altered short-body bytes. USB-forwarded API calls passed10/10; combined header/body writes passed50/50 over WiFi. Disabling GRO did not help and was reverted. The receive fault remains unlocalized.

`sdk/cammy_http.py` writes small JSON requests with headers in one send, preserving standard HTTP/1.1. The SDK and PC gateway both use it. Agents using ordinary HTTP to the PC gateway do not need to know this device detail. Direct third-party device clients may need the same framing workaround. Native protocol/gateway tests15 and focused startup-guard tests4 passed; earlier ARM portal tests11 passed.

## Credentials and reversal

Cammy keeps its provisioned station profiles in root-owned
`/etc/cammy/home.conf` (0600 under a 0700 directory). The startup script copies
the config into a private RAM directory for the running supplicant. Provision
or rotate a profile separately from firmware installation. Both WPA2-derived
keys remain usable credentials, so read back only profile metadata and keep
the config and key material out of logs, source and firmware artifacts.

The integrated release builder and validator check the TF file allowlist and
reject Wi-Fi credential assignments in text payloads. Before copying, the
installer checks the actual TF tree and accepts only the fixed bootstrap
destinations in its plan. Firmware upgrades leave the camera's home and
hotspot profiles in place. The fallback AP config and portal token also stay
in private camera files.

To stop automatic networking, power off and remove TF, or move `S99cammy-tf` outside `/etc/init.d/`. This reversal was tested. A normal camera can still use its native CammyOS AP. Naming, diagnostic root wrapper and desk-power configuration are separate persistent filesystem changes. To restore native idle poweroff, remove the desk-profile flag/early hook and use the camera's Auto Power Off setting (original3minutes). Keep backups private. No raw partition erase/program has been performed.

## Separate companion work

The complete robot still needs home-network media integration and face/gimbal/audio adapters. In an earlier warm test the vendor RTSP listener remained on the old AP address, so home frame decoding failed; preview was stopped and its lease released. Earlier decoded video was verified on the direct AP in the RAM chroot test. None of this network acceptance proves always-on perception, a replacement OS boot or flash restoration. Two RAM-kernel handoffs failed; independent FEL entry and full-chip reads succeeded, while restoration/programming remains untested.

Rebuild helpers: `scripts/build-wifi-client.sh`, `scripts/build-network-tools.sh`, `scripts/build-arm.sh`. Physical checks: `scripts/verify-boot.py --previous-boot UUID`, `scripts/verify-power-only.py --seconds 720`. They observe startup and acquire/release a short control lease; they do not reboot the camera or start sensing.
