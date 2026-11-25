# CammyOS v1.0-alpha1

2026-11-24. First tagged alpha of CammyOS and Cammy Control Center, for technical
testers. The camera runtime in this release is **0.9.12** (release id
`f895a29aaa17cd14f2ddf3c6`); the PC side is Cammy Control Center (C3) at the same
commit.

> **Alpha.** CammyOS has run on one camera, the author's AT-M105, with one PC. Nothing in
> this release has been tried on another unit. Read [Who can install it](#who-can-install-it)
> before anything else: a camera with factory firmware cannot be set up from this
> release alone yet.

## What CammyOS is

Cammy is a desk companion made from the AT-M105 gimbal camera. Its screen is a face,
the gimbal is its gaze, and its camera, microphone and speaker are its senses and voice.

- **On the camera:** CammyOS is a runtime on the TF card. It keeps the stock kernel,
  bootloader, drivers and camera app (`sdvcam`). Installing it writes the TF card and a
  few boot and policy files in the camera's flash overlay; it never writes raw flash.
- **On the PC:** Cammy Control Center (C3) connects to the camera over Wi-Fi, runs the
  voice conversation through Gemini Live, and offers a desktop window and a local API
  for agents (an MCP server).

## What it does

- **Boot chooser.** At power-on the camera offers CammyOS or the normal camera. A
  loading screen shows each startup stage, then Cammy's face.
- **Faces.** Three face packs: Cammy, Fin & Fur and a third character pack. The face
  pack also sets Cammy's voice and personality.
- **Conversation.** Talk to Cammy through the camera's own microphone and speaker. A
  talk gate decides what reaches the cloud: speech close to the camera, follow-ups in
  an open conversation, or anything that says "Cammy". Quiet or distant speech that does
  not name Cammy is checked by a small local Whisper model and never leaves the PC.
- **Senses and motion.** Cammy can look (live preview frames), let you take a photo or
  video with its camera, turn around, follow a face and look around the room.
- **Cammy Control Center window.** Conversation, activity, connection status, Brain
  profiles, providers, look-back history, agent instructions and skills, family
  controls (Kids/Adult), calendar and an OS tab. A **Start/Stop** button starts and stops
  the background service that holds Cammy's connection.
- **Agents.** An MCP server lets coding agents and bots see and talk through Cammy via
  C3, which holds the camera's single control lease.
- **Recovery.** A resident release starts CammyOS on every boot (0.9.14). Records
  left by a power-off or a crash after the menu are archived, not obeyed. Only
  starts that fail before the menu count: three in a row give one normal-camera
  boot, then CammyOS is tried again.

## After the alpha1 tag (main branch): runtime 0.9.14

- **Restarts no longer fall back to the normal camera.** Switching the camera off
  stopped its camera app while CammyOS's health check still ran, so every power-off
  was recorded as a crash; that spent the one automatic retry and left the camera in
  normal mode until a manual repair. A resident release now always starts CammyOS
  (see Recovery above). Tested on the author's camera with restarts after 3 minutes,
  before the menu, and by long press.
- `tools/rc_resident.py stage <candidate>` makes a newly installed release resident
  before its first boot.

## Changes in the 0.9.8 to 0.9.12 runtime and C3 (2026-11-23 and 24)

Faster start:
- **Loading screen held about 2 minutes on every boot.** The screen's parser rejected
  the startup record's new release-bound form and waited for the 120 s health window.
  Menu now at about 50 to 70 s after power-on (was 204 s). (0.9.10)
- **Startup wait measured from power-on.** The fixed 30 s pause after the boot chooser
  now ends at 35 s of uptime, about 12 s sooner. (0.9.11)
- **C3 reconnects sooner after a camera reboot**: 10 s guard instead of 45 s. C3 was
  connected 64 s after power-on in the last test (was 121 s).
- **Boot chooser over a busy USB prompt.** The chooser no longer waits for the stock
  app's mode change to settle when the USB prompt is showing. (0.9.9)

Steadier conversation:
- **Talk gate** (above). Cammy no longer answers TV and cross-room talk with "Did you
  say something?".
- **Wi-Fi stalls no longer cut the microphone.** The camera's Wi-Fi driver queues up to
  1.7 s under load; the portal now tolerates 5 s and marks the audio stream for priority.
  C3 reopens a stream that worked at once. (0.9.8)
- **Camera preview pulls video only**, not the unused audio track.

Robustness:
- **Photo wait could reset the camera.** While waiting for the record button, C3 read
  the camera's state and full media listing several times a second; at the end of a
  2-minute wait the camera's watchdog reset it. Polling is now once a second, the
  listing every 3 s, and the photo window lasts 60 s. Gemini may only open it when
  someone asks for a photo.
- **The camera's address can change.** C3 now finds Cammy on the home network itself
  and follows it if the router moves it (`host/c3/discover.py`).
- **Unexpected camera modes.** C3 switches the camera's app back to video mode when it
  is left in a mode other than video or photo, instead of waiting forever.
- **The face comes back after a camera reboot**, even if the camera view or a halted
  capture had hidden it.

## Who can install it

**A camera running factory firmware cannot be set up from this release alone.** These
steps have no tool or public procedure yet:

1. **Root access on the camera.** Stock firmware gives file access over ADB, not a root
   shell. The author's camera was opened by hand; that procedure is not written up.
2. **Camera credentials.** Home Wi-Fi settings and the portal token must be placed on
   the camera (`/etc/cammy/home.conf`, `/etc/cammy/portal.token`); nothing writes them.
3. **Building the runtime** needs the camera's own stock app and boot script, extracted
   from its firmware. They cannot be redistributed, and only one firmware version
   (`sdvcam` SHA-256 `8771961f...`) is accepted.
4. **The install tools assume the author's PC**: its drive paths, the camera's USB
   serial number and an ADB server port, and approval steps that name the author.

Testers who already have root ADB, provisioned credentials and the matching firmware
can follow [RC06-INSTALL-REVIEW.md](RC06-INSTALL-REVIEW.md) and the first-device path
in `tools/cammy_lifecycle.py` (`enroll`, then `apply first-device <candidate>`), on Linux,
after adjusting the paths above. Removing these four blockers is the work for alpha 2.

**Running Cammy Control Center** is possible for anyone whose camera runs CammyOS:
on Linux (see [C3.md](C3.md)), or on Windows 11 through WSL2
(see [WINDOWS.md](WINDOWS.md), not yet tried on Windows).

## Requirements

- AT-M105 with the accepted stock firmware, a TF card the camera has formatted (its
  first 8 KiB must be blank), a USB data cable for installation and recovery.
- PC: Linux x86-64 (Ubuntu 24.04 or later), or Windows 11 22H2 or later with WSL2.
  Python 3.12 (`host/requirements-c3.txt`), ffmpeg, GTK 4 and libadwaita for the window.
- A Gemini API key for the voice. Optional: the Whisper tiny.en model for the talk gate
  (`setup-c3-wsl.sh` fetches and checks it).
- Camera and PC on the same 2.4 GHz home network. A DHCP reservation for the camera is
  recommended.

## Known issues

- **Abrupt resets are not fully explained.** Whole-system resets have been seen on
  battery power and during media preparation, and one Wi-Fi driver oops. CammyOS
  recovers (the next boot is the normal camera, or one automatic retry), but the cause
  is open. Keep the camera on USB power.
- **After a crash with USB power connected, the camera can stop at its charging
  screen.** Press the power button to start it.
- **Gemini sometimes stops mid-reply** (about 1 in 10 turns); C3 reconnects and the
  turn is lost.
- **Full duplex talks to itself** in open air; half duplex is the default.
- **Reboots are manual.** The camera may switch off on `adb reboot`.
- **The boot chooser has no confirmed timeout.**
- If home Wi-Fi fails, the camera starts its own `CammyOS` access point at
  192.168.10.1 and does not retry home Wi-Fi until the next boot.
- **Not yet tested:** another camera unit, other firmware versions, the Windows guide,
  battery-only boots.

## Returning the camera to normal

- **Once:** choose the normal camera on the boot chooser.
- **Stop CammyOS starting:** remove the TF card, or roll back with
  `tools/rc_install.py <candidate> rollback` (or `tools/cammy_lifecycle.py apply rollback`).
  Both restore the files saved before installing, disable the selector and need one
  manual power cycle.
- A rollback needs the camera to boot far enough for ADB. Restoring the whole flash
  from a backup has not been verified.

## Files in this release

| Path | What it is |
| --- | --- |
| `tools/rc06_build.py`, `tools/rc_install.py`, `tools/rc_resident.py` | Build, install and make-resident tools for the camera runtime |
| `scripts/`, `native/`, `src/` | Camera runtime, boot scripts, face service and loading screen, portal |
| `host/c3/` | Cammy Control Center |
| `host/requirements-c3.txt` | C3's Python packages |
| `tools/windows/` | Windows setup (`Setup-CammyWSL.ps1`, `setup-c3-wsl.sh`) |
| `docs/` | This note, [WINDOWS.md](WINDOWS.md), [C3.md](C3.md), [HOME-WIFI.md](HOME-WIFI.md), [RC06-INSTALL-REVIEW.md](RC06-INSTALL-REVIEW.md), [STATUS.md](STATUS.md) |

Built camera candidates, the stock firmware, credentials and the author's test
evidence are not part of the repository.

## Reporting problems

Open an issue with: what you did, what the camera screen showed, the time, and the
last 50 lines of `journalctl --user -u cammy-c3`. If you have USB ADB on the camera,
add `/tmp/cammy-runtime/ui.log` and the files in `/mnt/extsd/CammyOS-faults/`.
Never include `private/` files, tokens or Wi-Fi details.

## License

Original CammyOS code is MIT licensed (`LICENSE`). Third-party components keep their
own licenses. CammyOS is not affiliated with the camera's maker.
