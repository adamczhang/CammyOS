# M105 CammyOS loading and boot strategy

## What the v0.1 artifacts prove

`dist/cammyos-m105-rootfs.squashfs` is a real stock-derived root filesystem with the open portal added. `dist/cammyos-m105-spi16MiB.bin` preserves every byte outside the original rootfs partition, including this unit's bootloader, boot resources, kernel and pre-test settings. The original rootfs geometry is retained. The generated archive permits a **chroot test in the actual camera's RAM** without a firmware write or reboot.

A chroot test changes the process filesystem root while sharing the running stock kernel, network and system services. It is strong evidence that the new ARM userspace can load and run on this hardware; it does not demonstrate boot ROM -> bootloader -> kernel -> replacement PID1 startup. `evidence/ram-test.json` records whether the real test succeeded. Host ARM user-mode emulation is a separate test, not an emulator of the complete V853 board.

## Exact boot inputs captured

The backup Android boot image has:

- zImage address `0x40008000`, kernel length 2,832,512 bytes;
- page size 2,048 bytes;
- a 12-byte ramdisk placeholder containing `ramdisk.img\n`, not a usable initramfs;
- the kernel command line selecting rootfs at `/dev/mtdblock3`;
- the running board DTB copied through `/sys/firmware/fdt` (131,072 bytes).

A source-derived stock kernel configuration has not been recovered. `/proc/config.gz`, `/proc/kallsyms` and `/sys/kernel/kexec_loaded` are absent. The decompressed kernel contains no searched kexec symbol strings. This supports the conclusion that no ready kexec route has been found; it is not a complete kernel configuration audit.

Private extracted artifacts are under `private/board/`; they are not source-licensed or a generic board-support package. Avoid substituting developer-board DRAM/power/display configuration.

## Route progression

1. **RAM chroot bring-up — implemented:** extract our fork to `/tmp`, run its static ARM portal with a rootfs marker, authenticate two agent clients, exercise capabilities/control events and stock preview. Leave existing PID1/kernel/flash intact. No `/dev`, `/proc`, `/sys` bind mount is needed for portal/preview integration; the stock media daemon remains outside the chroot.
2. **TF recovery entry — verified 2026-11-13:** the camera booted the minimal upstream 8 KiB FEL stub from a reserved area on its camera-formatted TF card. USB `1f3a:efe8`, the xfel version handshake, and 16 MiB SPI-NOR detection succeeded. Full read/restore verification is tracked separately; no internal flash write was performed. Normal and forced `reboot efex` had previously failed and are not needed for this route. See [TF experiment details](TF-BOOT-RESEARCH.md).
3. **Alternate-root full boot:** after a verified external/recovery route, use the captured stock kernel and board config with a prepared rootfs/boot command, or a compatible initramfs. Prove initialization and media/power operation. A normal FAT card with the rootfs copied onto it is not automatically bootable.
4. **Partition-only installation:** once restoration is tested, replace only the original rootfs partition with the verified artifact, retaining the bootloader/kernel/layout. Provision a device-specific API credential separately. Perform read-back and boot tests, and verify rollback. No flash writer is provided by this prototype.
5. **Source-built kernel/BSP:** later replace vendor components where source and matching board configuration can be established. Track binary dependencies explicitly.

## Scope of the initial fork

The current fork preserves the vendor `sdvcam` process. It supplies preview through the recovered event/RTSP path. The portal's face, gimbal, speech and microphone-adapter endpoints explicitly return 501 and capabilities mark them unavailable. Existing low-level hardware probes have shown some of those functions can work; they have not yet been implemented behind this API.

This avoids falsely representing a rebuilt image as a complete new robot application. The published original portal code, SDK, schemas and build tooling can be open source now. The private stock-derived image includes vendor components and device settings and is not offered as an openly redistributable image.
