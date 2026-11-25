# RTSP blocker: application integration before custom drivers

Date:2026-11-14. Research combines saved firmware analysis, the v0.3 acceptance
records, fresh read-only USB metadata, and primary platform documentation. No
camera code, driver, settings, media stream or firmware was changed for this pass.

## Finding

**There is no current evidence that this RTSP blocker requires a new kernel driver.**
The stronger working explanation remains a missing native application lifecycle
step when Cammy starts managed Wi-Fi independently of the stock AP UI. That
explanation is not yet a proved fix. A custom user-space media backend is a credible
fallback; it is different work from writing a sensor/ISP/encoder kernel driver.

## What this camera already has

Fresh read-only `/proc/modules` inspection found `imx298_mipi`, `vin_io`,
`vin_v4l2`, `videobuf2_dma_contig` and `dw9714_act` loaded. Sysfs lists eight video
nodes and44V4L subdevices; their existence does not prove a free capture channel.
The native `sdvcam` process holds `/dev/video0`, `/dev/video4`, `/dev/video8`,
`/dev/cedar_dev`, `/dev/ion` and `/dev/disp`. Only descriptor targets and module/node
metadata were read; no device was opened or ioctl issued by this research.

Adam reported normal live camera view during v0.3 acceptance. Together, that view
and loaded video drivers are evidence that capture/display already operate. They
do not independently prove the encoder will deliver a new network stream.

Saved `etc/init.d/S00mpp` explicitly loads the IMX298/VIN modules. Saved `sdvcam`
contains MPP initialization messages, IMX298/ISP600 tuning names, EyeseeCamera/
EyeseeRecorder symbols and RtspServer class metadata. The earlier exact-binary
analysis locates RTSP creation/sender calls in a presenter soft-AP-related branch;
see [targeted findings](TARGETED-PROBES-2026-11-14.md) and the
[QZ app trace](MEDIA-STARTUP-TRACE-2026-11-14.md).

The v0.3 live check established event TCP and wrote4016, but no8554 listener was
observed during the five-second RTSP budget. This places the observed failure
before SDP/frame decoding. Changing a video codec or PC decoder cannot make an
absent listener accept a TCP connection. It does not rule out an encoder-init
failure causing the app to skip network startup; native return/error paths still
need investigation.

The saved ELF is ARMv7/Cortex-A7, hard-float/NEON. Its dynamic dependencies do not
include the usual standalone MPP media library, and a saved-rootfs search found
no `libmedia_mpp.so`, `libaw_mpp.a`, `libisp.so` or `libvencoder.so`. **Inference:**
important media/ISP/RTSP code appears embedded in the monolithic application,
rather than ready to relink from a separate installed SDK library. This does not
establish that every component is static or that a callable stable ABI is exposed.

Private fresh evidence: `private/rtsp-research/summary.json`,
`live-modules-video.txt`, `native-resource-fds.txt`. Existing live readiness evidence:
`private/tf-updates/20261114T151950Z/preview-accepted.json`.

## What the platform documentation supports

Allwinner's [V853 Camera documentation](https://docs.aw-ol.com/v853/soft/soft_camera/)
describes the kernel VIN framework built on V4L2, with ISP support partly in user
space. It distinguishes sensor/VIN modules, the media pipeline and ISP tuning.
This is a platform reference, not the M105 board configuration.

The board vendor's [MPP sample manual, sample_rtsp](https://docs.100ask.net/TinaSDK/SdkModule/Linux_MPP_Sample_Instructions-05/#8115-sample_rtsp)
lists video-input, video-encoding and RTSP components and describes an executable
plus configuration run from SD. Its network interface and sensor/stream parameters
are configurable. This supports a user-space capture/encode/network-service route;
it does not make a development-board binary compatible with this IMX298-configured firmware.

The [V85X RTSP setup FAQ](https://bbs.aw-ol.com/topic/1828/faq-%E5%85%A8%E5%BF%97v853%E8%8A%AF%E7%89%87-tina%E4%B8%8Brtsp%E7%8E%AF%E5%A2%83%E6%90%AD%E5%BB%BA%E6%96%B9%E6%B3%95)
also uses a user-space demo to provide RTSP. It was not executed here. Older forum
links to `YuzukiHD/Yuzukilizard/Software/sunxi-mpp` returned404 through both GitHub
API and public fetch on this date, so that pointer is not a verified available SDK.
No matching M105 build-ready SDK was obtained in this pass.

## Engineering recommendation

1. **Continue the native integration route first.** Trace how the presenter
   soft-AP path is reached and whether a narrow supported command can initialize
   RTSP while preserving managed Wi-Fi. Follow error/return paths around server,
   encoder and sender creation. Do not assume4016 creates all of those objects,
   or blindly inject the broader soft-AP/udev event.
2. **Prepare a user-space fallback in parallel with the design work.** Locate
   matching V853/sun8iw21 MPP headers/libraries/toolchain and IMX298 ISP tuning;
   validate kernel ioctl/buffer ABI compatibility, selected channels and cleanup.
   A standalone Cammy media service could own capture/encoding and serve frames,
   RTSP or another transport through the existing authenticated control/lease API.
   This route is plausible, not yet built or validated. Standard V4L2 nodes alone
   do not establish that generic ffmpeg/GStreamer can drive this vendor pipeline.
3. **Resolve ownership before running it.** The stock app already owns camera,
   encoder-memory and display resources. An unreviewed MPP demo can initialize
   global media resources; running it alongside `sdvcam` is not a proven benign
   test. First design either a supported extra stream channel or an explicit,
   reversible ownership handoff. Current constraints still exclude ad-hoc app
   restarts, mixer writes, framebuffer/process patches and new camera uploads.
4. **Consider kernel changes only when evidence requires them.** Examples: a
   replacement kernel lacks this sensor/ISP support, an actual driver defect or
   incompatible ioctl prevents the required capture path, or necessary hardware
   functionality is absent from the installed driver. No such cause is established
   by the current missing8554 listener. A full new Linux/kernel port would require
   sensor, ISP, memory, encoder and board integration work beyond this RTSP issue.

Immediate next research deliverable: a verified native-start control boundary or
an inventory of matching SDK components and ownership requirements. Neither a
new kernel nor a custom sensor driver is the justified first implementation step.
