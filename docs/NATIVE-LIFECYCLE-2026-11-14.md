# Native lifecycle and handoff evidence — 2026-11-14

**The late handoff is not hardware-ready.** Mapping found a watchdog owner and
signal behavior that make an ordinary terminate/restart unsafe to assume. The
offline media candidate does not implement a cooperative process-only shutdown.
No signal, app restart, device ioctl, radio change, TF update or camera code write
was performed here. Static tracing was followed by bounded read-only USB metadata
and a portal state query; the latter reports 0.3.0, owner null and preview off.

## Source identity and evidence

Live read-only hashes match the preserved files:

| File | SHA256 |
| --- | --- |
| `/usr/bin/sdvcam` | `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` |
| `/usr/lib/libglog.so.0.3.5` | `9a66ba0f6c148fea200e45b8e17cfdb9756ecc0a69d3f5df144d1c0556681237` |

The live process maps the glog library. Addresses below are link-time virtual
addresses within the named file, not process-memory write locations. Analysis
uses ELF load segments, PLT relocations, ARM disassembly, exception-table bounds,
GOT entries and C++ vtables. A nearby symbol or a shared logging-string prefix
alone does not identify a function; several initial labels needed correction.

Private evidence: `private/native-lifecycle/`, especially `source-hashes.json`,
`native-app-main.txt`, `watchdog.txt`, `glog-signals.txt`, `logging-config.txt`,
`module-destructor.txt`, `model-teardown.txt`, `network-constructors.txt`,
`live-metadata-accepted.json`, `live-library-identity.json`,
`watchdog-platform-sysfs.txt` and `final-state.json`. The first metadata attempt
used unavailable BusyBox applets; its errors are retained, and it is not acceptance
evidence. The accepted snapshot uses native cat/ls with host-side filtering.

## Startup and launch context

The ELF entry at 0x334A8 reaches `__libc_start_main` with main at 0x318C8.
Main creates a watchdog object before the MainModule singleton, sets FB_SYNC to
1 and SCREEN_INFO to 356x400-32bpp, and constructs native media/UI components and
threads. This is whole-app initialization, not an isolated RTSP service.

The ELF interpreter is `/lib/ld-musl-armhf.so.1`. There are 12 dynamic dependencies,
including glog, ALSA, the vendor neural/VIP libraries, libstdc++ and musl libc.
Copying `sdvcam` to RAM still depends on those installed libraries and resources.
Do not launch a second copy beside the original to test dependency availability.

Saved S01app starts it without extra arguments, after changing directory to
`/sys/class/axp`. The live snapshot instead has cwd `/tmp/log`: the native log
maintenance routine at 0xA40E8 calls chdir there. A current cwd is therefore not
automatically the correct original launch cwd.

The snapshot's initial environment includes LD_LIBRARY_PATH=/usr/lib/eyesee-mpp:
and the S01app FB_SYNC, SCREEN_INFO, TERM, TZ and LD_HWCAP_MASK values. `/proc`
environ describes the original environment area and need not reflect later
setenv changes. Capture a launch contract, not a blind replay of current state.
Stdin points to `/dev/null`; stdout/stderr point to `/dev/console`. The live app
has parent PID1 and 109 threads at this observation; these are not fixed profile
requirements. The trial must choose bounded log/core behavior deliberately rather
than inheriting S01app's unlimited core limit and TF core-dump path.

## Watchdog: concrete ownership, unknown release safety

- Constructor 0x99564 stores a feeder interval of 3 from main and timeout 10 in
  the object. Main calls RunWatchDog 0x99580 at 0x31914.
- RunWatchDog opens `/dev/watchdog`, asks to disable it, requests a timeout via
  WDIOC_SETTIMEOUT, reads it back, enables it, then creates WatchDogThread.
- The feeder at 0x999FC calls FeedDog 0x99940 and sleeps for the object's interval.
  FeedDog issues WDIOC_KEEPALIVE. Thus the code requests a ten-second timeout and
  approximately three-second feeding; **the actual driver-returned timeout was
  not read or measured**.
- StopWatchDog at 0x99850 explicitly issues WDIOC_SETOPTIONS/DISABLECARD and closes
  the descriptor on success. Main's orderly cleanup calls it at 0x31DFC.
- Fresh metadata confirms the running native app holds `/dev/watchdog`. The
  bound driver is `sunxi-wdt` at platform device 20500a0.watchdog.
- The platform watchdog0 directory lacks timeout, nowayout and state attributes;
  sunxi_wdt exposes no parameters directory in this snapshot. `/proc/config.gz`
  could not be read. No watchdog device was opened by our probe and no ioctl was
  sent. Close-on-death, nowayout, effective timeout and stop success stay unknown.

The proposed 120-second overall trial ceiling is **not** a permissible watchdog
handoff interval. There is no demonstrated restart margin. Do not stop or suspend
the feeder, race a new app to reopen the watchdog, or introduce a guessed external
feeder. Obtain matching-driver evidence and/or a cooperative native acknowledgement
of successful watchdog handling before any stop action is eligible.

## Signals do not supply the missing orderly shutdown

Stock logging setup calls glog InstallFailureSignalHandler at sdvcam 0x48A7CC.
The exact bundled glog installer at 0x105FC registers SIGSEGV, SIGILL, SIGFPE,
SIGABRT, SIGBUS and SIGTERM, decoded from its six-entry table. Its handler at
0x10510 logs/flushes, then calls 0x6B3C, which restores the default disposition and
sends the signal to its own process. This does not call the native app teardown.
Other threads entering the failure handler can wait in a sleep loop.

Live SigCgt is 00000000800044e8 and SigIgn is 0000000000001006. They are consistent
with the glog signal set plus a runtime signal, and ignored SIGINT/SIGQUIT/SIGPIPE.
They do not expose a callable graceful-stop API. Do not substitute SIGTERM,
SIGINT, SIGSTOP or SIGKILL for a validated handoff contract. S01app's stop routine
also uses force-kill and writes the hardware clock; it is not that contract.

## Internal orderly exit is broad and not externally exposed

MainModule PrepareExit at 0xA3788 sets a private stop byte and invokes presenter
callbacks. Its vtable entry is present; no verified public process-only request
to reach it was found. It must run in the correct native lifecycle/thread context,
not from an asynchronous signal handler or guessed debug/GUI message.

MainModule destruction at 0xA585C joins a UI thread and calls device teardown
0xA39B4 at 0xA5984. That teardown destroys native RTSP, recorder, camera, media-file,
NetManager, USB gadget and other singleton state, with exit-action-dependent
branches. It includes a storage-manager destruction branch; unmount/restart
effects require tracing before claiming a TF-backed restore can survive it.

Main then stops the watchdog and branches on g_exit_action at 0x31E18:
action1 writes power_en to turn power off, action2 invokes reboot, and action3
invokes the OTA script. Other values reach `_exit(0)` without those three actions.
**This is an internal process-only final branch, not an established external
shutdown API or a safe value to inject.** Earlier native event handling can also
power off before main's final branch. The candidate changes none of these paths.

The saved init scripts launch sdvcam once in the background; the examined
inittab does not have an sdvcam respawn entry. The saved wifi-daemon init body is
commented out. This is narrower than proving no runtime watchdog/respawner exists;
live parent PID1 also does not prove there is no external monitor.

## Radio and shared ownership

Main obtains NetManager through 0xA6E94. Its constructor 0x927A8 constructs native
controllers, sets internal mode1 and loads network configuration at 0x9172C.
The config loader can copy defaults into `/tmp/data`; it is not read-only on all
paths. The checked constructors load configuration, but this trace does **not**
prove a fresh whole-app start unconditionally forces AP mode or preserves managed
mode. Do not promote either claim to a capability.

Native SwitchToSoftAp 0x901EC does use 192.168.10.1, interface down/up commands and
config synchronization. Other native helpers execute broad killall commands for
DHCP clients/servers; embedded radio routines can load/unload the vendor module.
Those are identified mutating paths, not commands to replay. Native destruction
does not constitute an ownership boundary that protects CammyOS networking.

Fresh metadata shows separate wpa_supplicant and cammy-portal processes, alongside
sdvcam holding video0/4/8, ion, cedar_dev, disp and ttyS3. Native UART ownership is
relevant to motor/gimbal startup; no motor data or command was read or written.
Independent portal processes are necessary for recovery but cannot by themselves
guarantee radio, USB debug, TF mount or power continuity through native teardown.

## Handoff contract and next work

The current implementation contract is **preflight/report only**. A future
supervisor may inventory and refuse; its process-stop/launch backend must remain
unavailable until all gates below are satisfied.

| Gate | Current result | Needed before an active backend |
| --- | --- | --- |
| Exact source/library identity | Verified against saved and live files | Recheck each attempt; record candidate hash separately. |
| Exclusive process/resource ownership | Observed; no handoff attempted | Identity-bound resource-release acknowledgements and no respawn. |
| Process-only native shutdown | Internal branch found; external request absent | Cooperative, thread-correct request/ack that avoids power/reboot/OTA paths. |
| Watchdog continuity/disable | Native handling identified; driver behavior unknown | Proven native handling and actual timeout/close semantics; no guessed feeder. |
| Radio/USB/TF continuity | Separate portal exists; native teardown is broad | Demonstrate required access survives teardown and restart; avoid blind radio repair. |
| Original-app restore | Launch context partly mapped; no warm acceptance | Exact launch profile, bounded readiness and one tested recovery path. |

Before choosing additional binary hooks, inspect the preserved kernel for matching
watchdog-driver evidence and trace storage/USB/native event shutdown effects.
Then compare a cooperative lifecycle extension with a boot-time app selector and
replacement-media options. The current patch solves a media call-site problem;
it does not solve process ownership transfer. Hardware activation remains gated.
