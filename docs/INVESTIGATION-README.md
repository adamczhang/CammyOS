# Cammy: investigation and progress history

This is the story of how Adam's M105 camera became the current CammyOS wireless prototype. It records what was tried, why the direction changed, what the evidence actually proves, and what remains to be built.

The work began with M105 hardware research on **November 5, 2026**, followed by live investigation and development on **November 13 local time**. Some evidence uses November 14 UTC timestamps. Historical reports are snapshots: their statements about what was unknown at the time can be superseded by later tests described here.

## Where we are now

Latest status: [canonical handoff](STATUS.md). Runtime 0.3.0 is now boot/probe
verified; home-network video still fails readiness. An [offline native media
candidate](NATIVE-MEDIA-CANDIDATE-2026-11-14.md) passed local guard and synthetic
ARM checks. The [guarded TF launch plan](archive/GUARDED-TF-LAUNCH-PLAN.md) specifies the
next lifecycle, supervisor, recovery and frame-acceptance gates; it is not a
deployed launcher. The sections below preserve earlier investigation history.

Subsequent [lifecycle mapping](NATIVE-LIFECYCLE-2026-11-14.md), including bounded
read-only USB confirmation, found that native watchdog and shutdown ownership
must be resolved before a handoff. The [go-forward assessment](archive/GO-FORWARD-OPTIONS-2026-11-14.md)
compares native hooks, boot selection, SDK media, USB and full-OS routes and records
new public source leads. No app restart, card update or firmware change occurred.

Cammy can start the stock camera operating system, load a small CammyOS runtime from its TF card, join Adam's **<home-wifi>** home Wi-Fi, and accept authenticated agent-control requests. A resident service on RED-LINUX connects to it. The camera's own fallback hotspot is named **CammyOS**.

The earlier charger-only endurance acceptance used a separate USB charger or power bank, no PC USB data connection, untouched Wi-Fi controls, and more than **12 minutes of continuous home-network operation**. The camera was still running at the end. A gateway check during that investigation also returned connected with sensing off; a health response does not by itself prove the earlier endurance test or future uptime. The launch-plan commit was documentation-only; the later lifecycle report separately identifies its read-only camera checks.

This is a working **network and control foundation**. It is not yet a talking, looking robot: home-network video, continuous audio, a screen face, gimbal control and the resident conversational agent remain to be built. It runs the **stock Linux kernel and vendor camera application** with CammyOS services; a full replacement OS has not been booted or flashed.

## 1. The original goal and the camera

Adam wanted to turn an existing pocket/gimbal camera into a Reachy Mini Wireless–like companion: USB for power, Wi-Fi to the local computer, an agent that can watch and listen, natural conversation, a face on the camera screen, and a gimbal the agent can move. The local computer needs persistent software because an ordinary chat turn alone cannot continuously own the camera or listen between conversations.

The investigation began with the existing project material and model names associated with **Ourlife/M105/N105** and **Ausek AT-M105**. Earlier hardware research identified an Ausek AT-M105 pocket/vlog camera, examined the supplied documents and public certification material, and produced a board map and functional diagram. It did not establish an exact motherboard schematic or every component. The camera has a TF/microSD slot and offers USB charging, storage and webcam modes.

Sources: [hardware and setup report](../../Cammy%20Bot/M105%20investigation/outputs/ausek-m105-hardware-and-setup.md), [hardware diagrams and investigation files](../../Cammy%20Bot/M105%20investigation/outputs/), and the [current companion roadmap](archive/FULL-FEATURE-ROADMAP.md). Links into the sibling `Cammy Bot` folder refer to Adam's local investigation workspace; those files and private evidence are not bundled with the source repository.

## 2. Learning the phone app and control protocol

The camera uses the **HDV CAM** phone app. Decoding the QR link in the M105 manual gave a stronger connection to the actual app than a name-only search. We examined the app package, decompiled Java and native-library symbols, and traced how its different camera backends connect. The shared app supports several device families, so a feature in the app was treated as a lead rather than proof that this particular camera supports it.

For the relevant QZ-style branch, the app identified a direct-camera network at `192.168.10.1`, an HTTP interface on port `8082`, an event channel on `9999`, and an RTSP preview path on `8554`. It also contained movement and tracking commands. Some recovered motor request encoding remained ambiguous, and app buttons for multiple axes did not prove that this unit has those powered axes.

A deeper examination of `libaw_net_client` found real TUTK/IOTC networking and generic functions for audio receive/send and speaker operations. Those generic library functions and their IO-control identifiers were not evidence that this M105's QZ backend implemented two-way audio. A separate JNI registration-table examination likewise did not establish a working M105 speaker protocol.

**Why it mattered:** the app gave concrete discovery, preview and motor leads, while preventing us from assuming that an Android permission, an advertised codec, or a generic SDK symbol proved a working camera feature.

Sources: [HDV CAM reverse engineering](../../Cammy%20Bot/M105%20investigation/outputs/HDV-CAM-reverse-engineering.md), [firmware and native-library follow-up](../../Cammy%20Bot/M105%20investigation/outputs/M105-firmware-native-followup.md), and [microphone investigation](../../Cammy%20Bot/M105%20investigation/outputs/M105-microphone-investigation.md).

## 3. Researching firmware and preparing tools

A bounded search of manufacturer downloads, camera-family material and Allwinner hacking write-ups did not identify a matching M105 firmware image or a verified TF update filename/procedure. Similar cameras and their write-ups were useful for learning about Linux, ADB and Allwinner recovery; their firmware images were not established as suitable for this board.

Before live identification, V316 was considered because of SDK clues. It was a hypothesis, not a chip identification. We built upstream `sunxi-fel` locally and prepared Wi-Fi probing tools. A built host tool alone did not prove the camera could enter recovery or that its flash could be restored. Adam confirmed the TF slot was available, which opened a second line of investigation: card-based recovery or startup.

Source: [firmware research and tool builds](../../Cammy%20Bot/M105%20investigation/outputs/M105-firmware-native-followup.md).

## 4. Joining the camera and obtaining root

The camera's Wi-Fi screen showed a direct network, originally **AT-M105**. The PC's Wi-Fi driver was installed, the PC joined the camera, and live checks identified the QZ-style HTTP/event/RTSP paths. This established that a normal computer could join the camera's own access point without relying on the phone's QR scanner.

The camera's TCP ADB service allowed file access, but its stock `/bin/adb_shell` called a login option that did not work. After reading the exposed partitions and saving the original wrapper, we installed a small replacement wrapper in the writable JFFS2 overlay. A shell then reported UID 0, and a custom static ARM program executed successfully. The original wrapper was retained for restoration. The diagnostic wrapper later survived a normal boot.

Root was also established over **USB ADB** for development. The host ADB client uses a project-local configuration and a separate server port. USB debugging became a useful management and recovery route while Wi-Fi modes changed; it is not required for the intended desk operation.

Live identification corrected the earlier V316 hypothesis: the device tree identifies **Allwinner V853**, with a Cortex-A7-class ARM CPU, vendor Tina/Linux **4.9.191**, **512 MiB physical DRAM**, and **16 MiB SPI-NOR**. The stock media application is `sdvcam`. The running kernel reports an `imx298_mipi` driver; that observation is not a physical inspection of the sensor marking.

Source: [live access and firmware investigation](../../Cammy%20Bot/M105%20investigation/outputs/M105-live-access-and-firmware.md).

## 5. What the actual camera, microphone, speaker and screen could do

With root and the real device available, we moved from app-code inference to bounded hardware tests:

- **Video:** opening the event channel and sending `cmd_id 4016`, then keeping that connection open, allowed an RTSP H.264 preview on the direct AP. FFmpeg decoded a **640×480 JPEG**. An RTSP description by itself had not proved usable frames.
- **Microphone:** capture initially failed because the MIC1 mixer switch was off. Enabling it produced three seconds of **16 kHz mono PCM**. This proves sample capture, not speech intelligibility or continuous streaming. The mixer was restored after the test.
- **Speaker:** a short, low-volume test waveform was accepted by ALSA playback. A successful return code did not establish that Adam heard it or that speech quality is acceptable.
- **RTSP audio:** the stream advertised AAC audio, but the bounded attempt did not deliver usable audio packets. The ALSA capture path is a separate, demonstrated alternative.
- **Display:** the framebuffer was readable, including a measured **356×400** mode and a larger virtual buffer. Reading it did not prove ownership of the screen or rendering an animated face.
- **Gimbal:** the camera has visible startup movement and motor-related application/UART paths, including `/dev/ttyS3`. Agent-controlled movement, limits, actual feedback and safe stop behavior were not demonstrated.

Sources: [live hardware findings](../../Cammy%20Bot/M105%20investigation/outputs/M105-live-access-and-firmware.md) and [CammyOS validation](VALIDATION.md).

## 6. Backing up stock firmware and building a fork

All seven exposed MTD partitions were saved, covering the complete **16 MiB** SPI-NOR layout. Bootloader and rootfs were independently reread and matched. Those initial partition captures were made from the running system, so they were a live snapshot rather than a single atomic offline read.

The project assembled a private raw stock SPI image and built a stock-derived CammyOS candidate: the existing board kernel, bootloader, drivers and vendor application remained the starting point, with our own small ARM portal and integration files added. The candidate rootfs was checked against the partition capacity and validated by extraction; its raw image preserves non-rootfs partition bytes from the backup. These images contain vendor software and device-specific data and remain private.

A real-hardware **RAM chroot** ran the Cammy portal, enforced a control lease between agents, and produced a decoded preview frame using the stock media service. This was useful evidence that the new ARM userspace can run on the device. It shared the existing kernel and services: it was not a replacement-kernel or replacement-PID1 boot.

One compatibility problem was traced to the vendor kernel returning `EINVAL` for newer `statx` behavior. An ARM legacy-stat compatibility shim let the newly built static utilities perform the required filesystem operations on this kernel.

Sources: [validation](VALIDATION.md), [boot strategy](BOOT-STRATEGY.md), and the private partition manifest referenced by the local checkpoint.

## 7. Recovery: failed software entry, then TF-to-FEL success

Software attempts to enter Allwinner recovery using `reboot efex` left the screen black and did not enumerate a usable FEL device. Physical power/reset steps restored the normal camera. Those failures did not establish that FEL was impossible; they showed that this software entry route was unreliable on this unit.

We then inspected Adam's **camera-formatted 32 GB TF card**, backed up its directory state and first 4 MiB, and checked the FAT layout. A minimal upstream **8 KiB** TF boot stub was written at byte offset `8192`, in reserved sectors before the FAT. The write and readback were checked; the card was not reformatted.

With that card inserted at power-on, the camera entered USB ROM FEL, enumerated as `1f3a:efe8`, and identified the V851/V853-family device. The recovery tool detected the 16 MiB SPI-NOR. **Two complete offline flash reads were byte-identical.** Fixed OS partitions matched the earlier live backups; the writable overlay could legitimately differ because the camera had been changed and used in between.

This was a major result: the camera really can execute a TF boot stub and enter a recovery interface independent of the running stock Linux. It did **not** prove a successful flash write, a restored chip, or a complete alternate OS boot. Flash programming and restoration remain untested.

Source: [TF boot research](TF-BOOT-RESEARCH.md); private card/flash backups and the `tf_fel_probe.py` tool are in the local workspaces.

## 8. Trying a full RAM-kernel handoff

To investigate an OS path without programming SPI, we extracted this unit's complete DRAM parameters from its stock boot0 and used them to adapt an Allwinner DDR initializer. Separated 64 KiB write/readback checks passed at two RAM addresses. The captured kernel, device tree and launch trampoline also matched their upload readbacks.

Two attempts to hand off to the captured Linux kernel from RAM did **not** produce the expected USB shell; FEL stopped responding after each handoff. The second attempt removed a PSCI compatibility entry after reviewing the kernel's early firmware handling. Neither attempt established where boot stopped. Without a boot log or UART evidence, there was no basis to claim that the replacement kernel/root filesystem had booted.

Normal stock operation was restored. The practical direction became: retain the working stock kernel and hardware services, run the new portal alongside them, and continue investigating full replacement separately.

Sources: [TF boot research](TF-BOOT-RESEARCH.md), [boot strategy](BOOT-STRATEGY.md), and local `evidence/fel-dram-test.json` / `evidence/ram-boot-no-psci/result.json`.

## 9. Making the camera join home Wi-Fi

The original screen exposed the camera's own hotspot, not a home-network selection menu. Adam's desired desk setup required the camera to join the existing router instead of requiring the PC to stay connected only to the camera AP.

Live radio inspection showed an **ECR6600S** device with 2.4 GHz managed/client and AP modes, but no supported simultaneous AP/STA combination. Stock components did not provide a verified usable station daemon for this setup. We built static ARM `wpa_supplicant`/`wpa_cli` and a compact BusyBox network/DHCP helper, including the kernel compatibility work, and used the existing vendor driver loader.

The camera successfully joined **<home-wifi>** with WPA2, obtained `<camera-ip>` from DHCP, and responded from the wired PC. The camera MAC is `<camera-mac>`. A real interface down/up test showed autonomous reassociation. A controlled loss test restored the direct AP, and the PC connected to it. The AP name was changed to **CammyOS** and verified in a radio scan and on the camera's own screen. Adam reported assigning high priority and saving a DHCP reservation for that MAC/address in Deco.

The home network and API credentials were provisioned privately. The TF runtime files do not contain the Wi-Fi password or API token. Camera private configuration is under a restricted directory, and host-private data and backups are excluded from the source repository.

A network-specific issue also appeared: on home Wi-Fi, small HTTP bodies sent immediately after headers in separate writes sometimes arrived altered. The same API worked over USB forwarding. Combining headers and body into one send worked in repeated Wi-Fi checks. The SDK and desktop gateway now use that HTTP transport workaround; the underlying receive-path cause remains unlocalized.

Source: [wireless implementation and evidence](HOME-WIFI.md).

## 10. Boot loops, the three-minute shutdown and the guarded runtime

For normal use, we restored the TF card's original blank boot-stub region and copied a credential-free `CammyOS/` runtime onto its FAT filesystem. This is a different use of the same card from the earlier FEL stub: the current runtime card selects normal internal boot, after which a stock-init hook loads its tools into RAM.

The first automatic-start revision caused on/off cycling. Adam observed the camera reach live video and then reset; the PC's USB logs independently showed repeated normal-device enumerations about 19 seconds apart. The same card booted stably with the automatic hook disabled, and starting the radio after the stock app had settled worked. The exact early-start trigger was not isolated.

The revised startup waits about **30 seconds** before radio initialization and uses a persistent failure latch. A failed or interrupted start leaves the latch set so the next boot skips the runtime rather than repeating the same path indefinitely. A clean physical boot with the Wi-Fi and USB-mode controls untouched then joined the home network, passed authenticated control, and survived the latch's **60-second** healthy period.

The first charger-only test passed a short connection check, but Adam then observed that the camera powered off and did not restart. Investigation found the native **Auto Power Off** setting enabled at three minutes. The desk profile now changes that setting to off before the vendor app loads it; comparison against a private backup verified that only the intended setting value changed. A repeated charger/power-bank test passed over **720 seconds** of continuous authenticated home connectivity, with no PC USB data connection. This is a meaningful bounded test, not a claim of 24-hour endurance.

The current fallback AP remains in AP mode until the runtime is restarted; it does not yet automatically retry the home network from fallback. The failure latch can also require operator inspection/clearing if power is interrupted during startup. Those are prototype limitations, not hidden completed features.

Source: [wireless startup, failures and acceptance](HOME-WIFI.md), including links to `evidence/guarded-coldboot.json`, `power-only-12min.json`, and the boot-loop/idle-shutdown investigation.

## 11. What CammyOS and the PC service provide today

The on-camera portal is a small, original static ARM service with an authenticated HTTP/JSON interface. It reports identity and capabilities, maintains a renewable control lease, records a bounded event history, and supplies a stop operation. Unsupported face, gimbal, speech and microphone-adapter calls report unavailable rather than pretending hardware is working.

On RED-LINUX, `cammy-host.service` is enabled as a user service and the user has lingering enabled. It monitors the configured camera endpoints and exposes a local authenticated gateway at `127.0.0.1:8790`. It can reconnect to the home address or the fallback AP; the saved PC Wi-Fi profile avoids replacing the Ethernet default route. The gateway currently polls identity and proxies supported control calls. It does not itself run an always-listening agent, start a microphone or continuously observe video.

In one home-network preview attempt, the stock RTSP listener remained bound to the old AP address and connecting to the home address was refused. A decoded frame over the direct AP is real evidence of the media pipeline, but it is not proof of a working home-network stream. This is the first media problem in the next implementation slice.

The source project is the sibling [`Cammy-OS`](../README.md) repository. Its initial source commit is `976e08e` (`Initialize CammyOS companion platform`). The original hardware reports, decompiled-app evidence, tools, device backups and structured checkpoint remain in the sibling `Cammy Bot` workspace. Backups, firmware dumps, credentials, sensor samples and generated build artifacts are private or ignored; the source commit is not a publication of the vendor firmware.

## 12. The path from here

The next slice is to make a fresh image available over home Wi-Fi, obtain an intelligible microphone clip, play an audible spoken phrase, and demonstrate a **bounded real gimbal movement and stop**. Screen ownership and simultaneous microphone/speaker operation should be probed early because they can change the design.

The full roadmap then adds a screen face, a resident companion agent, natural two-way speech with echo cancellation and interruption, coordinated gaze/expressions, and a desktop control panel. Daily-use readiness includes a loaded **24-hour** test, reconnection/recovery, updates and rollback. Full firmware replacement stays a separate recovery-gated track.

See the [full-feature roadmap](archive/FULL-FEATURE-ROADMAP.md) and [control-plane decision](archive/CONTROL-PLANE.md).

## Evidence and project map

| Location | Purpose |
|---|---|
| [`Cammy-OS/README.md`](../README.md) | Current source project, API, build and use overview |
| [`HOME-WIFI.md`](HOME-WIFI.md) | Wireless startup, fallback, credentials/reversal and acceptance results |
| [`VALIDATION.md`](VALIDATION.md) | Tested artifacts, RAM chroot, recovery and current limitations |
| [`TF-BOOT-RESEARCH.md`](TF-BOOT-RESEARCH.md) | TF layout, FEL entry and later RAM experiments |
| [`FULL-FEATURE-ROADMAP.md`](archive/FULL-FEATURE-ROADMAP.md) | Remaining gimbal, voice, face, agent and desktop work |
| [Local investigation outputs](../../Cammy%20Bot/M105%20investigation/outputs/) | Original hardware, app, microphone and firmware reports |
| [Local structured checkpoint](../../Cammy%20Bot/M105%20investigation/work/followup-checkpoint.json) | Detailed evidence references and historical state; some nested entries describe superseded stages |
| `Cammy-OS/private/`, `Cammy Bot/M105 investigation/work/` | Private backups and evidence; handle as device data, not public source |

The reports describe when an observation was made. Before acting on any old “current state,” check the running camera and the newest evidence rather than assuming the hardware has stayed in the same state.
