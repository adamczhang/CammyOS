# Cammy documentation index

Start with **[PLAN.md](PLAN.md)** (the one forward plan) and **[STATUS.md](STATUS.md)**
(current state). Hardware and system design: [../architecture.md](../architecture.md).

## Current: operations and runbooks

| Doc | Use |
|---|---|
| [PLAN.md](PLAN.md) | Acceptance work, parked milestones and release rules |
| [STATUS.md](STATUS.md) | Running and installed release, CCC state and verification evidence |
| [C3.md](C3.md) | Control Center, family and Calendar controls, Look Back, voice and agent tools |
| [BRAIN.md](BRAIN.md) | Resident Brain profiles and the opt-in legacy T3 path |
| [CAMMY-BRAIN-FOUNDATION.md](CAMMY-BRAIN-FOUNDATION.md) | Cammy's technical foundation, OS lifecycle and first-device setup |
| [HOME-WIFI.md](HOME-WIFI.md) | Network profiles, credentials, ports and AP fallback |
| [T3-CONTROL-IMPLEMENTATION.md](T3-CONTROL-IMPLEMENTATION.md) | Finite-session legacy agent service and tools |
| [DESKTOP.md](DESKTOP.md), [SIMULATION.md](SIMULATION.md) | Gateway panel and local simulator |
| [NATIVE-PHOTO-SNAPSHOTS.md](NATIVE-PHOTO-SNAPSHOTS.md) | Gateway snapshot path |
| [MEDIA-BUDGET.md](MEDIA-BUDGET.md) | 15 Mb/s combined media budget |
| [M105-CONTROL-INVENTORY.md](M105-CONTROL-INVENTORY.md) | Physical controls, speed menu, tracking button |
| [SPEAKER-ROUTING-2026-11-15.md](SPEAKER-ROUTING-2026-11-15.md) | Speaker path facts RC2 depends on |
| [RELEASE-1.0-ALPHA1.md](RELEASE-1.0-ALPHA1.md) | v1.0-alpha1 for testers: features, changes, install limits, known issues, recovery |
| [WINDOWS.md](WINDOWS.md) | Cammy Control Center on Windows 11 through WSL2 |
| [RELEASE-0.5.0.md](RELEASE-0.5.0.md) | 0.5.0 release and its rollback base |
| [RELEASE-0.6.0-CANDIDATE.md](RELEASE-0.6.0-CANDIDATE.md), [RELEASE-0.6.0-RC2.md](RELEASE-0.6.0-RC2.md), [RC06-INSTALL-REVIEW.md](RC06-INSTALL-REVIEW.md), [RC2-STAGING-2026-11-15.md](RC2-STAGING-2026-11-15.md) | Preserved 0.6 release and installer evidence |

The five RC2/control docs above and `NATIVE-TRIAL-REVIEW.md` are hashed into
release packages by `tools/rc06_build.py` and `tools/trial_package/package.py`.
**Do not edit or move them**; supersede them with a new document instead.

## Reference: hardware evidence (still true, not plans)
- **2026-11-22 resilience review:** [Crash domains and resource budget](RESILIENCE-REVIEW-2026-11-22.md)

- **Device resources and release review:** [0.7.9 device resource audit](DEVICE-RESOURCE-AUDIT-2026-11-17.md)

- **Video and the reset blocker:** [VIDEO-BOOTSTRAP](VIDEO-BOOTSTRAP.md),
  [NATIVE-LIFECYCLE](NATIVE-LIFECYCLE-2026-11-14.md),
  [NATIVE-MEDIA-CANDIDATE](NATIVE-MEDIA-CANDIDATE-2026-11-14.md),
  [MEDIA-STARTUP-TRACE](MEDIA-STARTUP-TRACE-2026-11-14.md),
  [RTSP-BLOCKER-RESEARCH](RTSP-BLOCKER-RESEARCH-2026-11-14.md),
  [FACE-BOOT-TRIAL](FACE-BOOT-TRIAL-2026-11-15.md)
- **Live media proofs:** [LIVE-VISION](LIVE-VISION-2026-11-14.md),
  [LIVE-AV](LIVE-AV-2026-11-15.md), [WIFI-AV](WIFI-AV-2026-11-15.md),
  [COLD-BOOT-AV](COLD-BOOT-AV-2026-11-15.md),
  [LIVE-CONVERSATION](LIVE-CONVERSATION-2026-11-15.md)
- **Audio, buttons, gimbal:** [AUDIO-TESTS](AUDIO-TESTS-2026-11-14.md),
  [OWNED-AUDIO](OWNED-AUDIO-2026-11-14.md), [ASYNC-AUDIO](ASYNC-AUDIO-2026-11-14.md),
  [RECORD-BUTTON-AUDIO](RECORD-BUTTON-AUDIO-2026-11-15.md),
  [FEEDBACK-TEST](FEEDBACK-TEST-2026-11-14.md),
  [STOCK-CONTROL-BASELINE](STOCK-CONTROL-BASELINE-2026-11-14.md)
- **Face/LCD:** [NATIVE-FACE-ABI](NATIVE-FACE-ABI.md), [NATIVE-FACE-TRIAL](NATIVE-FACE-TRIAL.md),
  [ANIMATED-CHARACTER-FACE](ANIMATED-CHARACTER-FACE.md),
  [DESKTOP ROBOT FACE LOOP RESEARCH](FACE-LOOP-RESEARCH-2026-11-17.md)
- **Boot, recovery and probes:** [BOOT-STRATEGY](BOOT-STRATEGY.md),
  [TF-BOOT-RESEARCH](TF-BOOT-RESEARCH.md), [VALIDATION](VALIDATION.md),
  [STOCK-BOOTSTRAP-TEST](STOCK-BOOTSTRAP-TEST.md),
  [BOOTSTRAP-HARDWARE](BOOTSTRAP-HARDWARE-2026-11-14.md),
  [PREVIEW-READINESS](PREVIEW-READINESS.md), [PROBE-SUPERVISOR](PROBE-SUPERVISOR.md),
  [PROBE](PROBE-2026-11-14.md), [TARGETED-PROBES](TARGETED-PROBES-2026-11-14.md),
  [PCCAM-AND-APP](PCCAM-AND-APP-2026-11-14.md),
  [CAMERA-COMPATIBILITY](CAMERA-COMPATIBILITY-2026-11-14.md)
- **Earlier releases and results:** [RELEASE-0.4.1](RELEASE-0.4.1.md),
  [RC06-LOCAL-RESULTS](RC06-LOCAL-RESULTS.md)
- **Native bridge and work packages:** [native-bridge/](native-bridge/),
  [work-packages/](work-packages/) (WP1–WP3 and the RAM smoke test are evidence;
  CHUNK1–6, WP4 and WP5 are a disarmed activation path kept in place because code
  and tests refer to them)
- **History:** [INVESTIGATION-README](INVESTIGATION-README.md),
  [archive/STATUS-history-2026-11-16.md](archive/STATUS-history-2026-11-16.md),
  [archive/architecture-history-2026-11-16.md](archive/architecture-history-2026-11-16.md)

## Archive: superseded direction

[archive/](archive/) holds the older roadmaps and milestone lists, the Reachy and
GPT-Live/T3-brain plans, the voice and subscription research, and release notes
for 0.2–0.4.0. They are kept for history only. Everything in them that still holds
was carried into [PLAN.md](PLAN.md).
