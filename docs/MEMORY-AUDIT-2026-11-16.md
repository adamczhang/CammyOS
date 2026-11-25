# Memory and storage audit (2026-11-16)

Read-only check of every place CammyOS keeps bytes, done on the running 0.7.0 camera
(adb `cat`/`ls` only) and the PC. M = measured, E = estimated.

| Resource | Use | Limit | Risk | Verdict |
|---|---|---|---|---|
| Camera RAM | 399 MB free of 508 MB, no swap (M). sdvcam RSS 22.5 MB; cammy-ui, portal, wpa_supplicant, udhcpc under 1 MB each (M) | 508 MB | 0.7.2's face library adds ~21 MB (E), see below | watch |
| Camera tmpfs (`/tmp`, `/run`) | 25.6 MB (M): sdvcam copy 11.3 MB, face library 12.0 MB, cammy-ui page 1.1 MB | no size cap, so ~50% of RAM | the adbd debug log grew ~9 KB per adb command with no bound (M) | **fixed in repo** |
| TF card | **31.27 GB** (61,067,264 sectors, M): the 32 GB card. 3 release slots, 6 old rollback/failed folders, 10 media files (59 MB) | 31 GB | release slots are never pruned (~46 MB each from 0.7.2), stock photos/videos accumulate | watch |
| Internal flash (`/etc/cammy`) | 8 KB in 19 files; overlay 67 KB used (M) | **512 KB JFFS2 on NOR (mtd4)** | every `/etc/cammy` write is a NOR write: volume, face pack, Wi-Fi | **fixed (volume)** |
| PC disk (LAB) | 3.4 TB free; `private/rc06` 303 MB (11 candidates); `build/` 2.7 GB; captures new in `private/captures/` | 3.7 TB | none worth acting on | ok |

## Findings and what changed

1. **The face library lives in RAM.** The boot selector copies `face.so` and `sdvcam` from
   the TF slot into tmpfs before sdvcam maps them, so the camera keeps running if the TF
   card misbehaves. Every byte of the library is RAM: 12 MB in 0.7.0, 33 MB in 0.7.2 (raw
   356x400 BGRA frames, two packs). Free RAM goes from ~399 MB to ~378 MB (E): fine, but
   each new pack costs ~20 MB. **Changed:** `tools/rc06_build.py` refuses a library over
   40 MB (`FACE_LIBRARY_BUDGET`). Next step if more packs come: run-length-compress the
   frames (pixel art shrinks ~20x) and decode the shown frame into two RAM buffers.
2. **`/etc/cammy` is NOR flash.** It is the JFFS2 overlay on the 512 KB `rootfs_data`
   partition. JFFS2 spreads wear, and these files are tiny, but runtime writes should be
   rare. **Changed:** cammy-ui saved the volume on every tap; it now saves once, 1.5 s
   after the last tap or when Controls closes (15 taps = 1 write). The face pack is
   written only when it changes; Wi-Fi only on a successful join.
3. **adbd log in RAM.** `S97cammy-usb-debug` (developer USB access, flag-gated) sent adbd's
   output to `/tmp/cammy-adbd-boot.log`: 526 KB after a short session, about 30 MB an hour
   under heavy adb use, never freed. **Changed in the repo** (`>/dev/null`); the installed
   copy in the camera's `/etc/init.d` was replaced the same day with Adam's approval (takes
   effect at the next boot; the running log was emptied).
4. **TF card.** Plenty of room (31 GB). Not pruned: old release slots and the
   `CammyOS-rollback-0.1.0`..`0.4.1` and `CammyOS-native.failed-*` folders (each install
   adds ~46 MB). Cleaned 2026-11-16 with Adam's approval (archive in `private/tf-archive-2026-11-16/`).
   Frame compression (measured): run-length coding takes the 33 MB of frames to ~3.7 MB.
   Stock photos and videos stay on the card; captures are also copied to the PC.
5. **Logs.** Runtime logs restart empty each boot; cammy-ui's log is bounded by its 5
   restarts. The opt-in DIAG_LOG writes to TF every 2 s with no cap: keep it off unless a
   trace is needed.
6. **PC.** Frames for `look` overwrite one file and are deleted at session end;
   transcripts stay in memory (last 12,000 characters go to the brain). Captures go to
   `private/captures/<date>/` (git-ignored) and are kept until deleted.
