# CammyOS 0.1 validation

Verified 2026-11-13 America/Chicago (2026-11-14 UTC). This is a stock-derived userspace fork, tested on the actual AT-M105 / Allwinner V853. It retains the vendor Linux 4.9.191 kernel and media application.

## Verified results

| Check | Result | Local evidence |
|---|---|---|
| Complete original flash layout | Seven partition dumps total 16,777,216 bytes; manifest hashes rechecked | Original backup manifest; `dist/manifest.json` |
| Candidate image integrity | Every byte outside rootfs matches the backup; rootfs content and remaining erased padding checked | `dist/validation.json` |
| Rootfs partition fit | 9,572,352 bytes in a 10,354,688-byte partition | `dist/manifest.json` |
| Native protocol/gateway tests | 15 passed | `evidence/test-host.log` |
| ARM user-mode portal tests | 11 passed | `evidence/test-arm-emulation.log` |
| Real hardware execution | Cammy portal root is `/tmp/cammyos-ramtest`; CammyOS marker visible | `evidence/ram-process-proof.txt` |
| Agent coordination | Second agent rejected with 409; ownership handed off successfully | `evidence/ram-test.json` |
| Preview | Stock media backend produced one decoded 640×480 JPEG, 10,653 bytes | `evidence/ram-test.json`, private sensor frame |
| Test aftermath | Preview stopped, owner released, portal left running in RAM | `evidence/ram-test.json` |
| No reboot during RAM test | Kernel boot ID unchanged | `evidence/ram-test.json` |
| Recovery-mode flash backup | Two complete 16 MiB reads, byte-identical; fixed OS partitions match the earlier backup | `private/tf-card/fel-read-validation.json` |
| Later TF boot/recovery entry | Card stub executed; USB1f3a:efe8, xfel version and 16 MiB SPI detection succeeded | `docs/TF-BOOT-RESEARCH.md`, `evidence/tf-probe-usb-events.log` |

The historical RAM deployment was rechecked after testing: its process root and command name still matched. Subsequent image updates added hostname/AP naming and interface-aware preview URLs; the earlier complete RAM-root test does not validate every byte of the updated candidate. The current TF portal binary matches the rebuilt image portal hash. Native and emulated tests cover authentication, malformed requests, truthful capabilities, control leases, expiry/stop, events, image identity, and daemon startup/termination.

The later TF FEL experiment ended that temporary RAM chroot deployment. Normal stock boot was subsequently restored. The original blank card boot region is now restored; the card hosts a smaller runtime loaded into tmpfs by a stock-init hook. Warm startup, home DHCP, authenticated portal/gateway, native CammyOS AP naming, sustained-loss fallback and return to home Wi-Fi have been verified. Normal reboot acceptance is recorded separately. See HOME-WIFI.md and evidence/tf-ap-fallback.json. Recovery entry/read is verified; restoring flash and fully booting an alternate OS are not implied.

The original backup is a live partition snapshot. Later ROM FEL reads captured the full chip twice without running the stock kernel; both were byte-identical, with immutable partitions matching the original. Mutable overlay changes explain the full-image difference. Flash restoration/programming remains untested.

## Artifacts

- `dist/cammyos-m105-rootfs.squashfs`: candidate rootfs, not installed.
- `dist/cammyos-m105-spi16MiB.bin`: private raw SPI image, not a PhoenixSuit/PhoenixCard package.
- `dist/stock-spi16MiB.bin`: private reconstructed original full-flash image.
- `dist/cammyos-rootfs-ramtest.tar.gz`: rootfs archive used for RAM bring-up.
- `dist/cammyos-source-v0.1.0.tar.gz`: original source, SDK, build scripts, overlay and documentation; no credentials, vendor dumps or sensor evidence.
- `dist/manifest.json`, `dist/validation.json`: artifact hashes and validation status.

The source package builds the original portal and tools. Building the private fork additionally requires this unit's stock backup. It does not contain all vendor source or confer redistribution rights on the stock-derived images.

## Outstanding gates

1. Complete independent recovery read/restore verification. The TF stub now boots to FEL with a working handshake and SPI detection. Two complete 16 MiB FEL reads are now byte-identical; restoration remains untested. The previous software efex reboots did not enumerate recovery; TF application-update filenames/triggers remain unverified.
2. Prove a full alternate-root/cold boot, including PID1 initialization and hardware operation. A RAM chroot proves userspace execution while sharing the existing kernel and stock media service; it does not prove the boot chain.
3. Implement and verify face, bounded gimbal, microphone and speaker adapters behind the existing API. They currently report unavailable and return 501.
4. Extend the working resident host gateway with home-network video, perception/audio, encrypted transport and distinct agent identities. It currently handles reconnect and authenticated control; model providers remain interchangeable.
5. Only after restoration is verified, install/read back the candidate rootfs and test rollback. No flash writer or automatic updater is supplied in v0.1.

No raw firmware partition erase/program, replacement-kernel boot, always-on microphone, face replacement or gimbal adapter is claimed. The writable internal filesystem does contain the restored diagnostic wrapper, naming/network hooks and private configuration; those filesystem writes must not be described as zero internal storage changes.

## Wireless desk acceptance completed — 2026-11-13

Clean normal physical startup with no WiFi/USB-mode selection passed under the stock kernel. The delayed startup/failure latch avoided the early-start reset seen in the first version. Charger-only startup initially exposed the native3minute idle shutdown; S00cammy-power now disables that setting before S01app loads it. Repeated charger-only startup stayed on home WiFi for over720s with authenticated gateway control at the beginning/end, no PC USB device and no open home TCP5555. See HOME-WIFI.md, evidence/guarded-coldboot.json and evidence/power-only-12min.json. Adam reports DHCP reservation and high priority saved in Deco.

Native protocol/gateway checks15 and focused guard tests4 pass; earlier ARM portal checks11 passed. This is bounded operational evidence, not a24hour endurance test. AP fallback was hardware-tested, and its function is unchanged in the guarded revision; it remains AP until restart. The source-only package excludes private credentials/vendor dumps. The camera is left running on its separate USB supply, with the resident PC gateway active and no preview/owner. Full replacement OS boot, raw flashing/rollback, home-video integration and robot hardware adapters remain separate work.
