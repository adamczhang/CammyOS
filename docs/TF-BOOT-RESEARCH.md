# M105 TF boot investigation — 2026-11-13

## Current state

The camera-formatted 32 GB TF card has been inspected, its existing directory tree and first 4 MiB backed up, and a minimal upstream FEL stub written/read back in an unused FAT32 reserved region. It was unmounted and the USB reader safely powered off. **Adam inserted it and powered on the camera: TF code execution and USB FEL enumeration succeeded.** The camera has a black screen and the host sees `1f3a:efe8`. No internal camera flash write was performed.

The administrator-run `xfel version` handshake returned `AWUSBFEX ID=0x00188600(V851/V853) dflag=0x44 dlength=0x08 scratchpad=0x00040400`. `xfel spinor` detected 16,777,216 bytes of SFDP SPI NOR. The first complete 16 MiB read through FEL succeeded. All partitions except writable rootfs_data match the earlier live backup; the second complete read matches the first byte-for-byte. The full-image SHA256 is `85472f2b297c0f61315ad1eb948acbcd51f166605c5beca15d25310f18244ae2`. This is an offline, quiescent 16 MiB snapshot, unlike the earlier live per-partition capture. This is a confirmed entry/read path, not yet a tested restore or full CammyOS boot. USB add events were captured in `evidence/tf-probe-usb-events.log`. User-mode USB access was denied, so the working operations used administrator privileges.

Adam confirmed that the camera formatted this card. It is a FAT32 superfloppy (filesystem on the whole device, no partition table):

| Property | Measured value |
|---|---|
| Capacity | 31,266,439,168 bytes (29.1 GiB) |
| Volume | UDISK, C87D-17EA |
| Reader | Generic MassStorageClass, serial 000000002961, LUN 0 |
| Sector / cluster | 512 bytes / 128 sectors |
| Reserved region | Sectors 0–31 (16 KiB) |
| FAT allocation table | Begins at sector 32; one FAT, 3,728 sectors |
| FSInfo / backup VBR / backup FSInfo | Sectors 1 / 2 / 3 |
| Probe write | Sectors 16–31, 8 KiB at offset 8 KiB |

The write region was entirely zero and does not overlap the VBR, FSInfo, backup boot metadata, FAT, or file/directory clusters. The top-level DCIM and .data directories had no regular files. `fsck.fat -n` reported five allocated directory entries/clusters, with identical findings before/after: boot-sector dirty status byte65 differs from backup (01/00). This pre-existing state was not repaired or changed. The first 4 MiB readback matched the expected result exactly, including all bytes outside the 8 KiB change. This is a targeted boot-region backup, **not a full 32 GB raw card image**.

## Why this route is plausible

The captured camera device tree enables SDC0 (`sdmmc@4020000`) on PF0–PF5 with PF6 card detect. Live Linux mounted the inserted card at `/mnt/extsd`. That establishes the storage path, not reset-time boot priority.

Allwinner's V853 reference documentation shows boot selections which put SDC0 first, and fast-boot selections that omit SDC0. GPIO straps or eFuse select the policy. We have not measured this camera's reset straps or decoded its eFuse boot policy. The running `boot_type=3` reports current SPI-NOR boot medium; it does **not** establish the complete search order.

The stock boot0 starts with an `eGON.BT0` header, length 0x9000, and its checksum verifies. Live `/sys/class/sunxi_info/sys_info` reports `sunxi_secure: normal`. These are favorable compatibility observations, not a complete secure-boot/eFuse audit.

The saved U-Boot environment has bootdelay=0 and directly reads the internal boot partition through `sunxi_flash`. Its binary contains MMC and FAT-loading commands; no automatic boot.scr/uEnv/extlinux search was established. Placing ordinary files on FAT alone therefore has no demonstrated power-on autorun effect. No boot environment was modified.

## The prepared test

Payload: upstream linux-sunxi/sunxi-tools `bin/fel-sdboot.sunxi`, revision `34dfa20f8284273085666436e4cac09af19646c8`.

- Length: 8,192 bytes; verified eGON magic, length and checksum.
- SHA256: `2ccec08a026840571f3804671ba72afae840db27112a43f2b1f122075bc808bc`.
- Code reads the ARM control register and branches to the low/high BROM FEL entry. It contains no flash-writer, storage driver, GPIO changes or DRAM initialization.
- Loading this stub and entering FEL is now verified on this particular camera.

The stock kernel is not needed for this test: BROM accepted the card, loaded the stub into SRAM, and the stub entered ROM USB recovery. The measured host identity is `1f3a:efe8`. The dark screen was corroborated by enumeration and a successful protocol handshake. This establishes an independent entry route; successful enumeration alone does not prove full flash read/restore or boot of CammyOS.

First-4-MiB hashes:

- Original: `d69539ef7e752a4d8694341729eb35ddbef4c4f5babfd820d7514a1c58b0d45b`.
- Prepared: `e4c0f4dfaa360754b8922531d062bf91cbcbfb6e2d0f94d51d8444611bc30c91`.

Private backups, layout and write report: `private/tf-card/`. Apply/restore tool: sibling `Cammy Bot/tools/tf_fel_probe.py`. It pins the reader, card geometry, original prefix and upstream payload hashes; requires an unmounted removable USB block device; writes only the recorded 8 KiB; and verifies the prefix readback. Its offline self-test verified write boundaries and refusal of overlapping FAT, occupied reserved space, and corrupted payload. The updated restore guard validates card identity/BPB and the reserved region while allowing normal FAT metadata changes from runtime files. Pre-write prefixes are backed up; it changes only the recorded8KiB and preserves card files.

Physical procedure: turn the camera fully off, insert the prepared card, reconnect USB data to the PC, and power on normally once. Keep the cable attached for USB enumeration. If there is no useful result, power off and remove the card before restoring normal operation. Do not select format/update during this probe. No software efex reboot is required.

## Other concrete options

1. **TF userspace under the stock kernel:** a prepared fallback bundle `dist/cammyos-tf-userspace-fallback-v0.1.0.tar.gz` stores the validated rootfs as an archive on FAT. A manual root-ADB launcher copies the utility into tmpfs, verifies hashes, extracts to `/tmp/cammyos-tf`, then starts the portal in that root. This avoids requiring FAT to store Linux symlinks/modes. It defaults to loopback port8789, requires a separately provisioned token, and contains no boot sector/update image. Its shell syntax and bundle hashes are verified; this TF launcher has not yet run on hardware. The existing rootfs/portal RAM bring-up was already verified separately. This route retains the existing PID1/kernel/media service and is not a full alternate OS boot.
2. **Board-specific FEL switcher:** a primary write-up for an F800 V85x-family device describes a working TF-to-FEL SPL and the importance of its PH0 power hold. The source is checked out at `downloads/F800-linux-tools`, revision `780872218ecba454de434c7da5dafc37689b1bcf`. Its 16 KiB stub would extend from 8 KiB to 24 KiB and overlap this card's FAT, and it changes PH0/clock/RTC registers. It was **not** written or run on this camera. This camera's DT exposes AXP2101 PMIC nodes; the F800's power-hold circuit has not been established here.
3. **Full TF OS boot:** requires an SD-specific first stage with exact board DRAM/power configuration, compatible U-Boot/kernel and root-device parameters. Reusing SPI-NOR boot0 or the full raw SPI image by merely writing it onto the card is not a validated conversion. A generic development-board PhoenixCard image is not a matching camera BSP.
4. **FEL RAM load with exact DRAM settings:** the camera stock boot0 header provides DDR3 parameters at offset0x38, including 912 MHz, consistent with the running DT. xfel’s generic V853 DDR preset uses 936 MHz. The stock parameter block has been extracted to `private/board/stock-dram-parameters.json`; the generic DDR command has not been run. RAM initialization, RAM image loading and a complete kernel/rootfs handoff remain to be implemented and verified.
5. **UART-controlled load:** stock U-Boot contains MMC/FAT commands, making an interactive RAM load plausible if UART is accessible. Pads, console interaction and board wiring remain unverified. The saved bootdelay=0 means automatic interruption cannot be assumed.

Live stock `/proc/filesystems` exposes vfat, squashfs, jffs2 and overlay, with no ext4 listed; no loop devices were present. That favors FAT-hosted archives extracted into RAM for the first fallback rather than assuming an ext4 TF root or loop-mounted SquashFS. This is the observed loaded/built-in set, not proof an additional module cannot be supplied.

## Primary references

- [Allwinner V853 boot-selection table](https://docs.aw-ol.com/v853/en/hard/hard_3boardsch/).
- [Allwinner PhoenixCard boot versus production-card modes](https://docs.aw-ol.com/v853/en/study/study_5flash/).
- [Pinned upstream minimal FEL stub source](https://github.com/linux-sunxi/sunxi-tools/blob/34dfa20f8284273085666436e4cac09af19646c8/fel-sdboot.S).
- [V85x F800 investigator's primary account](https://hackaday.io/project/207172-reverse-engineering-another-carplayaa-device/details).
- [F800 switcher implementation and board-specific caveats](https://github.com/Gamadril/F800-linux-tools/tree/780872218ecba454de434c7da5dafc37689b1bcf/fel_switcher).
- Local original partition dumps, captured running device tree, card header/readback, `private/tf-card/apply-result.json`, and `Cammy Bot/M105 investigation/work/followup-checkpoint.json`.


## Later tests and current card state — 2026-11-13

Two complete16MiB SPI reads through ROM FEL matched byte for byte. A V85x DDR initializer patched with the camera complete128-byte DRAM parameter block passed two64KiB pattern readbacks. Two stock-kernel RAM handoffs then failed to produce a USB shell; the second removed PSCI compatibility. Uploaded kernel/DTB/trampoline readbacks matched, but boot failure remains unlocalized without logs. No flash erase/program was attempted. See evidence/fel-dram-test.json and evidence/ram-boot-no-psci/result.json.

The original blank8KiB reserved boot region was restored and verified before copying the credential-free CammyOS/ runtime onto FAT. The card is now inserted in the running normal camera; warm startup of WPA/DHCP/portal and sustained-loss CammyOS AP fallback succeeded. The current card no longer forces FEL. Restoring/reapplying the stub is distinct from the stock-init TF runtime described in HOME-WIFI.md. Cold/full alternate-root boot and flash restoration remain unverified.
