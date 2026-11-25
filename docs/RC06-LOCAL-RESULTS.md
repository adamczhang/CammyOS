# CammyOS0.6.0-rc1 local milestone — 2026-11-15

The integrated candidate is built and locally validated. Nothing was installed
on the camera or TF card, and running desktop services were not restarted.
The last stable stock camera/fault guard remains untouched. Prior unexplained
resets remain unresolved; physical acceptance has not run.

Private package: `private/rc06/candidate-0.6.0-rc1/`.
Release ID: `bb1881989f1a2774981bf9eb`;87members,20,596,114bytes plus manifest.
Portal reports0.6.0-rc1; generic VERSION remains0.5.0.

| Item | SHA256 |
| --- | --- |
| Exact-stock-derived native candidate | `b9a678f44ab5fbcc67ebc5e55c7a5a33b7b700056a1c70b2b06b1a5ac6ca149b` |
| Saved-libc-compatible face service | `0e4c4fb403872323023cd368862efc0a95aa2a5a01953ee3b1b7ec76487928e8` |
| S01 startup proposal | `35116aef3bf810453aa6d0f1f32b05c3f68fbb430edbb584245c68953d72e9b7` |

Included: six moods/blink/idle and reusable native show/hide/replay; defined256byte
RAM IPC with GUI-thread ownership and fresh acknowledgements; authenticated
boot/random-lease/ordered-intent controls; independently expiring observed-button
input guardian; explicit once-per-boot media preparation; health/fallback policies;
matched SDK/gateway/Reachy app; desktop renderer/editor/presets/license; versioned
TF slot; exact init proposals and original rollback files. No credentials/models
or sensor captures are packaged. The candidate is disarmed and resident opt-in
is absent. GUI/input acknowledgements do not claim physical output or cleanup.

## Verification

- `make test-rc06`:185passed, no skips (42.286s).
- `python3 -m unittest tests.test_boot_launch tests.test_boot_selection tests.test_face_launch_review tests.test_trial_package`:69passed (82.496s).
- Cross-compiled `BootClientTests` under qemu-arm:10passed.
- Static ARM portal under qemu-arm `tests.test_portal`:17passed.
- Final `tests.test_rc06_package`:9passed, including byte-identical repeated builds,
  edited/resealed startup/native/armed rejection and path/link/mode guards.
- Final `python3 -m tools.rc06_validate private/rc06/candidate-0.6.0-rc1`:
  `LOCAL_CANDIDATE_HARDWARE_ACCEPTANCE_PENDING`,87members, not activation-approved.
- C warnings-as-errors, saved musl1.1.24/time32 audit, host/ASan/ARM core fixtures,
  actual-DSO/synthetic-native bindings, ES-module syntax and `git diff --check`
  passed. New shell behavior ran relocated under dash; BusyBox was parsed only.

Detailed local logs are under `build/face-service/`; compact checkpoint is
`private/rc06/checkpoint.json`. No vendor executable ran in these tests.

## New control findings

[Inventory and PTT](M105-CONTROL-INVENTORY.md): Adam reports motor-speed menu,
DOWN face tracking and a faster startup sweep. The saved family manual corroborates
DOWN tracking; saved settings contain motor-speed and two face-follow keys.
These are not current readback, calibrated units or verified remote toggles.
The candidate preserves stock speed and exposes no blind tracking/calibration API.

Maple is recorded as the preferred ChatGPT app voice; no setting was changed and
Cammy's local provider is not relabelled. Desktop PTT selects held1..8second turns
within finite live-mic sessions, with local ASR and explicit attached-agent replies
on P15. Physical PTT, VAD and automatic follow-up/idle are pending.

## Next hardware boundary

Follow [the checked installation review](RC06-INSTALL-REVIEW.md) after TF is in
the PC reader. Confirm live before-hashes and backup state, stage the new slot,
prepare the concrete internal userspace transaction, then one manual Power boot.
Accept only after three cold boots, one hour combined operation, repeated LCD/
head/Stop/disconnect checks, idle ADC CLOSED and a verified stock rollback.
Do not use software reboot, warm native restart or automatic retries.
