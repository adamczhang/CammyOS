# Native LCD face: exact image ABI evidence — 2026-11-15

ParamFace pixels can now be packed for the measured M105 colour layout. Offline
emulation also establishes the stock LVGL metadata/image-pointer layout. No face
was drawn on the physical LCD. A [GUI-owned finite surface and exact native
candidate](NATIVE-FACE-TRIAL.md) now pass local fixtures; complete GUI lifetime,
guarded activation and physical display acceptance remain open.

## Real read-only framebuffer metadata

`native/paramface/fb_layout.c` opens only `/dev/fb0` with O_RDONLY/CLOEXEC/NOFOLLOW
and issues the standard Linux GET variable/fixed-info ioctls. It never maps,
reads or writes pixels and performs no set/pan ioctl. A verified static ARM copy
ran once from an exclusive temporary RAM directory; the binary/directory were
removed and absence verified. Camera native/boot/watchdog/hash identity stayed
unchanged. Evidence: `private/reachy-media/fb-metadata-report.json`.

| Field | Observed |
| --- | --- |
| Visible dimensions |356×400|
| Virtual dimensions |356×800|
| Bits per pixel / stride |32 /1424bytes|
| Visual |2 (true colour)|
| Red |offset16, length8, msb_right0|
| Green |offset8, length8, msb_right0|
| Blue |offset0, length8, msb_right0|
| Alpha/transparency |offset24, length8, msb_right0|

On this little-endian ARM target those fields correspond to BGRA byte order.
`tools/native_image_abi.py:bgra_preview` converts bounded RGB888 output accordingly;
a packing test verifies channel ordering. This establishes the reported memory
format, not visible colours or a successful native GUI surface.

## Offline exact-stock primitive emulation

`tools/native_image_abi.py` requires exact saved `sdvcam` SHA256
`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`.
Unicorn2.1.4 maps the saved ELF into emulated RAM. Only four audited pure image
primitives may execute; every other instruction target, interrupt/syscall,
non-return and execution beyond1,000 instructions/50ms is refused. No application
entrypoint, constructor, libc, filesystem or driver code runs. Vendor bytes stay
in private/ignored inputs and are not copied into git.

| Primitive | Address | Observed behavior |
| --- | --- | --- |
| Pixel-size helper |0x498A08|True-colour format4 reports32bits|
| Image-source classifier |0x498A7C|Classifies the synthetic variable descriptor|
| Built-in image info |0x49C2C8|Flags at0, width at4, height at8;12-byte header|
| Built-in variable image open |0x49C8A0|Source data pointer at16 copied into decoder state at32; null pixels refused|

A synthetic native header `(format4,356,400)` is accepted and decoded correctly.
A standard packed four-byte LVGL8.3.11 header followed by its usual size/pointer
is also accepted, but becomes **569600×33558528**. The native info function itself
accepts absurd dimensions. An adapter must therefore validate dimensions/lengths
before native admission; success from this primitive is not sufficient validation.
The reference [upstream8.3.11 header](https://github.com/lvgl/lvgl/blob/v8.3.11/src/draw/lv_img_buf.h)
is a comparison, not proof of the vendor's LVGL release or configuration.

Five emulator/packing tests pass. Exact report:
`private/reachy-media/native-image-abi-report.json`. This is offline emulation of
selected real instructions, not a real camera call or complete LVGL emulator.

## Known, inferred and still unknown

- **Observed:**12-byte metadata header; variable-source pixel pointer at+16;
  decoder source pointer+4, source type+16, header+20 and image data+32;
  true-colour32bits; panel BGRA/stride metadata; GUI mutex/handler bracket from
  [the display audit](../tools/display_owner_audit.py).
- **Inferred:** the conventional source field at+12 is likely `data_size` after
  the expanded header. Its semantics and full descriptor size were not established
  by these primitives; the fixture populates it but does not promote it to proven.
- **Static discrepancy to resolve:** file-info requests12bytes, while the raw
  file-line branch adds4 to its seek offset. Filesystem-adapter semantics were not
  traced. A native `.bin` file path is not accepted as a face solution.
- **Further established offline:** the native canvas APIs populate this padded
  descriptor themselves; its cache-invalidation function is a no-op. Top-layer
  getters and delete-event registration are now mapped/tested in bounded primitive
  emulation with synthetic dependencies. See the [trial evidence](NATIVE-FACE-TRIAL.md).
- **Unknown:** exact vendor LVGL version/config, full image/object/window ABI,
  active-screen parent/transition lifetime, callback
  cleanup fence and safe native activation. No arbitrary native function is
  exposed through the portal or SDK.

## Implemented trial and remaining implementation

A finite immutable image surface and a separate profile2 dual-hook candidate
now implement this path locally. Its GUI callback sits in the observed
mutex/periodic-handler bracket at0xE58B0–0xE5928. It uses native canvas APIs,
registers a delete observer and retains pixel/library storage through exit.
No animation buffer is reused; a real retirement fence remains future work.

The already-tested standalone ARM renderer avoids adding a new C++ runtime inside
`sdvcam`. The new C trial library now uses the saved musl time32 build profile
and exact identity/ABI checks; real lifecycle and activation review remain open.
Status-only loader proof does not by itself authorize drawing or establish a
working native GUI hook on hardware.

Prove the static happy face/restoration in one short guarded native trial, then concurrent
RTSP/PCM, Stop, lease loss and native-window transitions. Physical LCD observation
is required for the visible-face claim. Keep the working0.5.0 media runtime and
stock watchdog owner; this increment made no native app/init/TF changes and
attempted no warm native restart, framebuffer drawing or display-set ioctl.
