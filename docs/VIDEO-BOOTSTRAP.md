# Native video initialization without the Wi-Fi screen

Adam requested that CammyOS initialize the required media services at boot,
completely bypassing the stock Wi-Fi screen. CammyOS0.4.1 already starts its
home network, shared-audio profile and restoration guardian automatically.
[Cold-start microphone transport is now acoustically verified](RELEASE-0.4.1.md).
Video-server initialization is the remaining dependency.

## Why the screen behaves strangely

There are two network managers. CammyOS's TF runtime loads the radio and runs
its own station supplicant/DHCP client, connecting the camera to home Wi-Fi.
The stock app's NetManager does not learn its internal remote-camera mode from
that external setup. Its Wi-Fi UI runs `SwitchToSoftAp`: it attempts hotspot
configuration, sets192.168.10.1, starts native networking, marks internal mode3,
and synchronously notifies SOFTAP_ENABLE. The presenter then creates/starts
native RTSP and its sender. Consequently the UI both duplicates network setup
and performs media initialization that ordinary home association does not.

The RTSP server is inside the already-running `sdvcam`, rather than a separate
installed service binary. Ordinary client9999/4016 events can start native
microphone capture even before8554 exists; the audio and video prerequisites
are therefore different. Starting listeners must remain distinct from granting
an agent a media lease or forwarding microphone data.

## Existing trigger under investigation

Static analysis of exact stock SHA256 `8771961f…637d62b0ec` found:

- Native HTTP POST command3003 (`str=SSID`) is the phone app's SetWifiSsid path.
  It calls0xAFA5C → `SwitchToSoftAp`0x901EC → Notify0x8F8 → presenter media-init
  0xAC44C, which calls RTSP create0xADB90/start0xAD90C/sender0xAE210.
- That notification body initializes media without showing a Wi-Fi dialog.
  The trigger still invokes stock AP configuration and rewrites net_config.lua;
  it is not a pure media-only API.
- The currently stored AP prefix is CammyOS. A same-value request could invoke
  initialization without renaming it or changing its passphrase. A warm command/recovery test has passed; cold boot remains unproven.
- The separate restart helper0xAE3C8 is called by work-mode window-load paths,
  guarded by native NetManager mode3. No unguarded public media-only entry has
  been established. No internal-address calls or generic GUI/FIFO injection are
  used in this investigation.

Candidate transaction: verify idle view/no recording and no native AP helpers;
arm independent recovery; acquire a bounded control lease; send exactly one
same-prefix request through the existing native HTTP handler; stop only newly
identified native DHCP helper(s); renew the verified owned DHCP client; verify
home station and fresh video; release and verify idle capture. A local prototype
has PID/start-time/executable guards and a bounded recovery loop. It pauses only
the CammyOS network supervisor during this transaction and resumes it on exit;
it never signals the native app or its watchdog owner. Production integration
must use current identities and execute locally from TF startup, without USB.

## Current experiment status — 2026-11-15

The warm existing-API trial acknowledged command3003 and restored the home IP.
The initial guard incorrectly treated repeated TERM as successful DHCP cleanup;
we found the same stock helper still running. An exact identity/argv guarded
KILL removed it. The new production guard requires no live AP helper before it
can report restored. The same-prefix operation changed native configuration
bytes; it must be described as a stock settings write, even though no firmware,
code, startup file or network name was replaced.

Normal mode reload then yielded9.012s of fresh changing video alongside3s PCM,
with lease/session release and microphone CLOSED afterward. A listener was
already present before the trigger because the user had operated camera controls:
this proves warm command/recovery/media behavior, **not a cold boot bypass**.

[CammyOS0.5.0](RELEASE-0.5.0.md) now packages the fixed local HTTP client, one-shot
TF startup coordinator and independent network recovery. It is opt-in, card-updated and now
[accepted at normal boot with live A/V](COLD-BOOT-AV-2026-11-15.md). The actual ARM client passed
read-only native preflight on the camera; both scripts passed the camera shell's
syntax parser. No native-app restart, candidate binary or raw-memory path is used.
Exact evidence/checkpoints remain in `private/video-bootstrap/`.
