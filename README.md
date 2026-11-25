# CammyOS

Cammy is a desk companion built from the M105: the screen is its face, the gimbal
its gaze, and the camera, microphone and speaker its senses and voice. CammyOS is a
TF-card userspace runtime with an agent portal. It keeps the stock kernel,
bootloader, drivers and camera app. The checked install writes the TF card and
a small set of boot and policy files. It does not program raw flash.

**Release:** [v1.0-alpha1 release notes](docs/RELEASE-1.0-ALPHA1.md) (for technical
testers: what works, who can install it, known issues). Windows users:
[Cammy Control Center on Windows](docs/WINDOWS.md).

**Start here:** [docs/STATUS.md](docs/STATUS.md) (where things stand now), then
[docs/PLAN.md](docs/PLAN.md) ("Now": the ordered work list). Those two are the only
current-state and plan documents; everything else describes a part or records evidence.

- **Talking to Cammy:** [docs/C3.md](docs/C3.md), Cammy Control Center on the PC
  (family controls, voice, Brain profiles and the local API).
- **All documents:** [docs/README.md](docs/README.md).
- **Hardware and system design:** [architecture.md](architecture.md).
- **History:** [docs/INVESTIGATION-README.md](docs/INVESTIGATION-README.md).

## Run Cammy

[Cammy Control Center](docs/C3.md) on the PC owns the camera lease and provides
the desktop and default agent controls. The camera runs its display, buttons,
media and network runtime. See [STATUS.md](docs/STATUS.md) for the installed
release and the latest verified hardware results.

Home Wi-Fi and the camera's fallback access point are documented in
[HOME-WIFI.md](docs/HOME-WIFI.md). The TF release contains runtime code.
Wi-Fi credentials and the portal token live on the camera.

## Local simulator

Run and tune the desktop against an in-process simulated camera with virtual gaze,
face states, synthetic media and injectable faults. No camera connection is made.
See [simulation setup and limits](docs/SIMULATION.md).

## Source and images

Original CammyOS code is MIT licensed (`LICENSE`); the face renderer builds on ParamFace (MIT, `native/paramface/LICENSE-ParamFace`, `native/paramface/THIRD-PARTY.md`). Private stock firmware, board dumps, build downloads, runtime credentials, images and sensor evidence are excluded from git. Existing third-party/vendor components keep their own licensing; extracting a binary does not supply its source or redistribution permission.

A local build writes `dist/manifest.json` (image-build hashes and retained partitions) and `dist/validation.json`; `dist/` is not in the repository. The full raw image is a raw 16 MiB SPI layout, **not** a PhoenixSuit or PhoenixCard container.

## Build

Use a Linux x86_64 build machine. To make the build scripts refuse to run unless a work drive is mounted, set `CAMMY_WORK_MOUNT` to its mount point. Needed tools: C compiler, make, Python3, curl, tar/xz, squashfs-tools. No administrator access is required for normal builds.

```sh
make test
make tools
python3 scripts/generate-api.py
make image
```

The ARM build downloads a pinned Bootlin toolchain and verifies its SHA256. Image construction requires this unit's original seven-partition backup. Set `CAMMY_STOCK_BACKUP` if it is not in the sibling `Cammy Bot/M105 investigation/work/firmware-backup` folder. Keep the backup and generated images private.

To explicitly rebuild this tool's generated artifacts after reviewing an existing result:

```sh
python3 scripts/build-fork.py --rebuild
```

The image adds `/usr/bin/cammy-portal`, an OS marker, API schema, init scripts, and `cammyos` hostname. It changes the diagnostic ADB wrapper and the native AP prefix to `CammyOS`; every other original file is checked for unchanged content/mode/link target. Existing directory modes and all non-rootfs partitions are preserved. The separate live TF networking hook/private configuration described below are not provisioned by this private candidate image.

## RAM validation on the actual camera

Prerequisites: camera normal and connected over Wi-Fi, root ADB working, `/tmp` verified tmpfs. No recovery command is used. The script refuses an existing test directory.

```sh
python3 scripts/ram-test.py
```

It loads the fork under `/tmp/cammyos-ramtest`, starts its portal on camera port8787, and verifies the process root plus OS marker. A temporary credential is kept in `private/ram-test.token`; no credential is embedded in the image or printed. The portal is temporarily reachable on the private camera network. The test requests preview and decodes a frame, then releases the lease and stops preview. The portal is left running in RAM for further agent connections and disappears on camera reboot. It does not replace the installed OS or start an always-on microphone.

The isolated process shares the host camera kernel and existing media service. That dependency is intentional for the fork prototype and is reported in capabilities.

## Connect an agent

The default Cammy MCP server calls the Control Center API. See the
[tool list and setup](docs/C3.md#agent-and-bot-access). The direct SDK and
finite-session legacy controls are described in the
[control implementation notes](docs/T3-CONTROL-IMPLEMENTATION.md).

## Development layout

- `src/`: on-device service and ARM legacy-stat compatibility shim.
- `sdk/`: agent client and camera HTTP transport.
- `host/`, `systemd/`: resident PC gateway and service unit. The unit files are templates; `python3 tools/c3/install_units.py` installs them for your checkout.
- `tests/`: host and emulated ARM protocol tests.
- `overlay/`: new rootfs files.
- `scripts/`: verified ARM build, image fork and RAM validation.
- `docs/`: protocol, architecture and boot route.
- `private/`, `evidence/`, `downloads/`, `build/`, `dist/`: local-only inputs and outputs.

## Vendor-kernel compatibility

The real camera kernel returns EINVAL for the statx syscall, while legacy ARM
fstatat64 works. New musl consequently failed file-status operations instead of
falling back. `src/compat/arm_legacy_stat.c` converts the documented Linux ARM
stat64 UAPI to musl's public structure and is linked into the ARM portal and the
source-built BusyBox helper. This is a userspace workaround; the installed kernel
is unchanged. The legacy timestamps retain the old kernel's time32 limitations.
The measured before/after probe is in `evidence/stat-compat-real-camera.txt`.

`make tools` builds pinned BusyBox1.38.0 with just the needed utilities; the source
archive hash is checked. Its GPLv2 license is included in the private image.
`configs/cammy-tools.config` records the configuration with `@CAMMY_ROOT@` standing
for the local checkout; the build script regenerates local paths. Upstream code
and the original MIT portal are separate programs.
