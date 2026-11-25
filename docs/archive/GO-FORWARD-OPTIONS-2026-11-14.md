# CammyOS go-forward assessment — 2026-11-14

## Recommendation

Keep the stock kernel, drivers and known camera tuning while making application
ownership explicit. Keep the PC gateway/API and simulator. **Do not make a generic
late kill/restart launcher the next hardware experiment.** The [lifecycle evidence](../NATIVE-LIFECYCLE-2026-11-14.md)
shows why the media patch alone does not make that launch recoverable.

The next local decision should compare a cooperative native-app extension with a
boot-time selector that starts the chosen app once. Seek matching SDK components
in parallel as a replacement-media fallback. This is a research recommendation,
not approval to change the current TF-only update workflow or installed init code.

The product is a Wi-Fi desktop companion: screen face, bounded gimbal gaze,
camera/microphone/speaker and a persistent host-run agent. Owning a new kernel is
not itself a product milestone. Another simulator feature, protocol wrapper or
successful process launch also does not establish usable physical media.

## Project surfaces and what is actually missing

| Surface | Current foundation | Remaining boundary |
| --- | --- | --- |
| PC service and agent API | Resident authenticated gateway, Python SDK, desktop controls and local simulator | Real media ingestion, persistent companion and speech providers; preserve current interfaces. |
| Body control | TF runtime 0.3.0 boot/probe verified, home station and leases | Narrow native bridge with truthful ownership and readiness; no trial-launch API exists. |
| Camera media | Stock LCD capture/display, historical direct-AP decoded frame; offline native RTSP patch | Fresh home-network frames and repeatable start/stop/recovery. |
| Audio, gimbal and face | Low-level and static leads; no finished physical adapters | Native ownership of PCM, ttyS3 and GUI must be resolved before adding competing writers. |
| Boot and updates | Ordinary TF userspace updates and rollback; full NOR read backup and TF-to-FEL entry | Alternate native-app selection, tested restoration and complete alternate OS boot are distinct unproved gates. |

The central issue is **who owns the hardware lifecycle**, across all body
features. The vendor app owns the watchdog and camera resources; it also holds
the motor UART and display. Solving RTSP launch while ignoring those owners would
leave the face, speech and motion work blocked by the same problem later.

## Routes compared

| Route | What it could unlock | What it does not solve | Current recommendation |
| --- | --- | --- | --- |
| Cooperative native-app extension | Reuse stock sensor/ISP/encoder, add structured media/GUI/motor hooks in the existing owner | Needs native-thread integration, stable IPC and a way to activate the exact offline copy | First candidate for application integration; analyze locally before more binary changes. |
| Early boot app selector | Start stock or TF candidate once, preserving its ordinary watchdog startup; avoid warm ownership transfer | Current TF loader is late; no early TF selector exists; rollback still needs physical boot acceptance | Compare closely with warm handoff. Requires a separate bootstrap/workflow decision. |
| Guarded late handoff | Use the existing late TF entry point | Needs cooperative shutdown, watchdog handling, storage/USB/radio continuity and warm restore | Retain the plan, but keep active launch gated until these are real contracts. |
| Separate vendor-SDK media service | A maintainable capture/encode/audio backend, eventually decoupled from sdvcam | Compatible ISP/IMX298 tuning, libraries and exclusive device ownership; does not automatically supply face or gimbal | Credible fallback; start with an SDK/ABI inventory rather than writing a kernel driver. |
| Stock AP plus PC media bridge | A reference path for stock media, with host software serving clients | Changes the network topology and does not prove Cammy's home-station stream | Useful bounded diagnostic baseline; not a replacement for the chosen home-Wi-Fi goal. |
| USB PCCAM transport | Potential host-controlled media on a data cable | Existing UVC negotiation fails and microphone samples were zero; changes USB-power-only operating model | Secondary diagnostic route, not the primary companion architecture. |
| New rootfs/kernel or OpenIPC port | Broad control after board support/recovery are solved | Full board boot, tuning, LCD, PMIC, radio, motor MCU, watchdog and tested restoration | Separate long-term track; no generic image is suitable to flash now. |

These are engineering judgments based on the evidence below, not measured effort
estimates or claims that a chosen path will work on the M105.

### Cooperative native integration versus early selection

The current private patch only adds media initialization/destruction at existing
client callbacks. A fuller native bridge could expose state/readiness and invoke
supported native operations on the correct thread. It must be a versioned custom
hook, not a debugger, preload injection or broad event-replay interface. Map the
GUI task/event dispatcher and error returns first; do not call internal C++ methods
from a signal handler or an unrelated external process.

A boot-time selector would avoid transferring a live watchdog/camera between two
apps. It could choose the exact verified TF app before S01app launches stock,
consume a one-shot selection and return to stock on the next boot. **This is a
proposal, not an existing TF feature.** The installed S00 power hook contains no
such selection, S99 is too late, and `.sdvcam` is a backup rather than a loader.
A selector may require a one-time change to an internal writable-overlay init
hook, or a working alternate boot route. Both differ from the currently authorized
ordinary TF payload update; neither should be smuggled into that updater.

Early selection avoids the warm-stop problem, but not bad native startup, radio
effects, media bugs, power loss or the need to restore stock after a failed boot.
Its failure policy must be a disarmed next boot, not an automatic restart loop.

### SDK and driver evidence: promising leads, no matching BSP yet

Allwinner documents VIN/V4L2 with a user-space ISP and MPP RTSP samples. That
supports investigating a user-space backend; it does not make an arbitrary V853
sample compatible with this IMX298/ISP600 camera. [Allwinner camera architecture](https://docs.aw-ol.com/v853/soft/soft_camera/)

The official SDK instructions identify V853Tina_Open and require an eligible
Allwinner account for access. The DongshanPI Lizard repository is a board overlay
on a separately downloaded SDK archive, described as approximately 11 GB; cloning
the overlay is not acquiring the full SDK. No archive/account download was attempted.
[Official SDK acquisition](https://docs.aw-ol.com/v853/study/study_3getsdktoc/),
[board-overlay README](https://github.com/DongshanPI/Yuzukilizard-v851s-TinaSDK/blob/master/README.md)

A newly verified public lead is Lindenis's V853 Linux 4.9 tree. Its watchdog
driver matches the saved DT's `allwinner,sun8i-wdt`, contains the newer register-key
handling, and defines timeout/nowayout parameters with no sysfs permission bits.
That is consistent with the missing parameter directory on the M105; **it is not
a verified source match for the camera kernel**. Compare its watchdog core, driver
and configuration against the saved uncompressed kernel before using its behavior
as a handoff guarantee. [Pinned V853 watchdog source](https://github.com/lindenis-org/lindenis-v853-lichee-linux-4.9/blob/92ac04e8e736c11ce7eb1a204790c6ceef100003/drivers/watchdog/sunxi_wdt.c)

Upstream Linux 4.9's watchdog release checks magic-close/nowayout and can leave a
watchdog running after close. This explains why descriptor closure is not a safe
assumption. It is a reference, not proof of this vendor kernel's configured
behavior. No live watchdog ioctl was issued. [Linux 4.9 watchdog release](https://github.com/torvalds/linux/blob/v4.9/drivers/watchdog/watchdog_dev.c#L778)

### OpenIPC and emulation: useful building blocks, limited compatibility

OpenIPC Divinus explicitly lists sun8iw21p1, including V851S(E)/V853, with all listed
media features marked in development at the checked commit. This is a HAL/source
lead rather than a ready replacement streamer. Its dynamic-library approach also
needs libraries absent as standalone MPP components in our saved rootfs.
[Pinned Divinus support matrix](https://github.com/OpenIPC/divinus/blob/bc714db0e8f30ae9ab09d43fe0353f38131fbb01/README.md)

OpenIPC's qemu-bench has documented V831/V833 board emulation, including synthetic
media modeling for a different sensor/ISP platform. It is evidence that richer
firmware emulation is possible, not an M105/V853 emulator we can already run.
Adapting the board, ISP600/IMX298, display and PMIC would itself be a development
project. Keep current ARM fixtures for narrow branch/ABI checks; do not claim
that they or qemu-bench validate M105 shutdown, gimbal or watchdog behavior.
[Pinned Allwinner emulator scope](https://github.com/OpenIPC/qemu-bench/blob/d5eee17e3f7486717ce8711128ed0a00ae7fa63b/docs/allwinner-v83x.md)

### Reachy is an architecture reference, not a hardware driver

Reachy's documented media architecture uses a daemon to own media, local IPC and
remote WebRTC. Its SDK also coordinates explicit media release/reacquisition.
That ownership contract is the useful model for Cammy. It does not make Reachy's
hardware adapters or release mechanism callable on this camera.
[Reachy media architecture](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/SDK/media-architecture.md)

Keep Cammy's small body API, host gateway and optional ROS 2 interoperability
direction from the [control-plane decision](CONTROL-PLANE.md). Once actual body
frames/audio exist, the host can adapt them to browser/agent transports. Importing
an entire robotics or WebRTC stack onto the camera before proving those streams
would add integration work without resolving the current hardware owner.

## Work order after this assessment

1. **Resolve the launch architecture locally.** Compare a cooperative lifecycle
   extension against a one-shot boot selector, explicitly listing required
   internal-bootstrap changes. Trace the GUI task queue, native shutdown effects
   on TF/USB/radio and the matching watchdog source/configuration. Produce a
   concrete proposal, not another guessed stop command.
2. **Inventory SDK/ABI material.** Identify available vendor headers, libraries,
   versions, sensor tuning and licensing. Distinguish a board overlay, access-gated
   full SDK and this camera's BSP. Use the public V853 kernel and Divinus as source
   leads; avoid an unreviewed multi-gigabyte install or different-board image.
3. **Implement the selected local integration and failure fixtures.** A supervisor
   can be built with a fake process backend and preflight-only hardware backend
   while native shutdown remains unresolved. Native start/stop must stay unavailable
   until the selected lifecycle contract is satisfied.
4. **Stage one bounded hardware acceptance only after that gate.** Require fresh
   frames, explicit stop/expiry behavior and return to normal stock operation.
   Then advance microphone/speaker, bounded gimbal and GUI-owned face hooks.
5. **Build the resident companion above real capabilities.** Add streaming speech,
   interruption/echo control and embodied behaviors after hardware adapters work;
   retain explicit stop, ownership, freshness and disconnect semantics.

PCCAM remains a defined experiment: UVC GET_CUR(PROBE) timed out and the bounded
USB microphone sample was all zero. Earlier AP preview worked, while latest
home-station RTSP was absent. Neither can be replaced by simulation evidence.
See [USB findings](../PCCAM-AND-APP-2026-11-14.md), [current handoff](../STATUS.md),
[launch plan](GUARDED-TF-LAUNCH-PLAN.md) and [full companion roadmap](FULL-FEATURE-ROADMAP.md).

No full SDK, emulator, firmware image or driver was installed by this assessment.
Only three small upstream source/reference files were cached privately, with
commit/hash/fetch records in `private/go-forward-research/`. No candidate was
deployed and no camera process was stopped. This reassessment deliberately leaves
the ordinary 0.3.0 runtime intact while making the next decision explicit.
