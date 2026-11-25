# Chunk 2 Review — Adapter Static Verifier Check AO2 (`PlaySound` `g_audio_mutex` trylock)

Date: 2026-11-14
Base commit: `1d8face`
Scope: `native/adapters/verify_static_evidence.py`, `native/adapters/README.md`, `tests/test_adapter_static_verifier.py`
Saved ELF inspected (data only): `<projects>/Cammy Bot/M105 investigation/work/firmware-rootfs/usr/bin/sdvcam`
SHA256: `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`
Evidence class: Read-only static ELF32/ARM inspection of the saved stock executable and synthetic unit tests (`tests.test_adapter_static_verifier`). No vendor binary execution, no vendor binary slices in Git, and no camera, ADB, network, TF, or hardware interaction.

## 1. AO2 Diagnosis: Claim vs. Verifier

- **Check:** `AO2` — `"PlaySound trylocks g_audio_mutex at 0x80E8C (GOT load at 0x80E80)"`
- **Predicate (`verify_static_evidence.py:238-240`):**
  ```python
  c.bl_plt(0x80E8C, 'pthread_mutex_trylock')[0] and
  e.got_loads(0x80E0C, 0x80E90).get(0x80E80) == 'g_audio_mutex'
  ```
- **Conclusion:** **The AO2 address/instruction claim is right; the verifier's GOT symbol initialization (`Elf.__init__`) was wrong (incomplete for non-PIE `ET_EXEC` executables).** No change to the AO2 predicate was needed or made.

### Bounded Reproducible Instruction Evidence

In the saved stock `sdvcam` ELF (`ET_EXEC`, non-PIE ARM32):

| Virtual Address | Word | Instruction / Datum | Static Meaning |
| :--- | :--- | :--- | :--- |
| `0x00080E1C` | `0xE59FA404` | `ldr sl, [pc, #0x404]` | Loads literal at `0x80E1C + 8 + 0x404 = 0x81228`, which equals `0x0087A300`. |
| `0x00080E28` | `0xE08FA00A` | `add sl, pc, sl` | Computes `sl = (0x80E28 + 8 + 0x0087A300) = 0x008FB130` (`.got` base address). |
| `0x00080E7C` | `0xE59F33B4` | `ldr r3, [pc, #0x3B4]` | Loads literal at `0x80E7C + 8 + 0x3B4 = 0x81238`, which equals `0x00000F34`. |
| `0x00080E80` | `0xE79A3003` | `ldr r3, [sl, r3]` | Loads from `.got` slot `0x008FB130 + 0xF34 = 0x008FC064`. |
| `0x00080E84` | `0xE58D300C` | `str r3, [sp, #0xC]` | Spills the loaded `g_audio_mutex` pointer (`0x00AEF9C8`) to `[sp, #0xC]`. |
| `0x00080E88` | `0xE1A00003` | `mov r0, r3` | Places `0x00AEF9C8` into `r0` (first argument). |
| `0x00080E8C` | `0xEBFEAF3C` | `bl 0x2CB84` | Calls `.plt` entry `0x2CB84` (`pthread_mutex_trylock`, `.rel.plt` index 215 -> `r_offset = 0x8FB498`). |
| `0x00080E90` | `0xE2509000` | `subs sb, r0, #0` | Tests `pthread_mutex_trylock` return value (`0` = acquired). |
| `0x00080E94` | `0x0A00000E` | `beq 0x80ED4` | Branches to `0x80ED4` when `r0 == 0`; falls through to `0x80E98` (`"audio device is busy, can not play keytone"`) when non-zero. |

### Why the Verifier Failed

- `.got` is at `0x008FB130` (file offset `0x008DB130`, size `0x1ED0`, 1,972 slots).
- At `.got` slot `0x008FC064` (`0x008FB130 + 0xF34`), the pre-linked 32-bit word is `0x00AEF9C8`.
- In `.dynsym`, `g_audio_mutex` has `st_value = 0x00AEF9C8` and `st_size = 24` (located in `.bss` `[0x00AEF070, 0x00AFE098)`).
- Because `sdvcam` is a fixed-address executable (`ET_EXEC`), `.rel.dyn` contains only 33 dynamic relocations (264 bytes) for external symbols; the remaining 1,353 symbol-backed `.got` slots hold their resolved `.dynsym` addresses directly in `.got`.
- `Elf.__init__` in `verify_static_evidence.py` previously populated `self.got_sym` exclusively from `.rel.dyn`, leaving `0x008FC064` unmapped and causing `e.got_loads(0x80E0C, 0x80E90)` to return `{}`.

### Verifier Repair

In `Elf.__init__` (`native/adapters/verify_static_evidence.py`), after reading `.rel.dyn`, any `.got` 32-bit slot whose static value matches a non-zero `.dynsym` symbol address is added via `self.got_sym.setdefault(slot_va, sym_name)`. Explicit `.rel.dyn` entries retain priority.

## 2. What Static Evidence Proves vs. Runtime Unknowns

- **Proven statically in this build (`8771961f...`):**
  1. The function starting at `0x80E0C` (`PlaySound`) computes `.got` base `0x8FB130` in `sl`, loads the pointer stored at `.got` slot `0x8FC064` (`0xAEF9C8`, symbol `g_audio_mutex`, 24 bytes in `.bss`) at `0x80E80`, moves that pointer into `r0` at `0x80E88`, and calls `pthread_mutex_trylock@plt` (`0x2CB84`) at `0x80E8C`.
  2. Immediately after `pthread_mutex_trylock`, `subs sb, r0, #0` (`0x80E90`) and `beq 0x80ED4` (`0x80E94`) branch to the playback path only when `r0 == 0` and otherwise log `"audio device is busy, can not play keytone"` and return `-1` (`mvn sb, #0`).
- **Unknown / Not Proven by AO2 Static Evidence:**
  1. Which runtime threads call `PlaySound` or otherwise acquire `g_audio_mutex`, and whether every audio output path in `sdvcam` or vendor libraries respects `g_audio_mutex`.
  2. How `g_audio_mutex` is initialized at runtime (e.g., default zero-initialized `PTHREAD_MUTEX_TIMED_NP` in `.bss` vs. explicit `pthread_mutex_init`), and whether unlock discipline holds on all downstream paths after `0x80ED4`.
  3. Actual hardware/alsa/MPP audio behavior, contention frequency, or safe cooperative ownership handoff.

## 3. Changed Paths

- `native/adapters/verify_static_evidence.py`
- `native/adapters/README.md`
- `tests/test_adapter_static_verifier.py`
- `docs/work-packages/CHUNK2-ADAPTER-AO2.md`
- `private/ownership-program/chunk2/checkpoint.json` (ignored)
- `private/ownership-program/chunk2/static-verification-after-ao2.json` (ignored)

## 4. Reproducible Verification & Partial Aggregate Counts

```sh
cd <projects>/Cammy-OS
python3 -m unittest -v tests.test_adapter_static_verifier
python3 native/adapters/verify_static_evidence.py --json
```

- **Focused synthetic unit test (`tests.test_adapter_static_verifier`):**
  - Ran 4 tests in `0.001s` — `OK`.
  - Covers positive static `.got` -> `.dynsym` resolution for `AO2`, `.rel.dyn` precedence, and negative cases (`.got` slot pointing to `pa_flag` or an unknown address, corrupted base register at `0x80E80`, and wrong `bl` target at `0x80E8C`).
  - Manager review strengthened relocation precedence to exercise a slot with
    both a matching static symbol and an explicit relocation; the explicit
    relocation still wins. All four tests passed after this change.
  - The manager separately confirmed the call instruction with GNU objdump,
    the mutex symbol with readelf, and its stored GOT pointer with objdump.
- **Standalone static verifier (`native/adapters/verify_static_evidence.py`):**
  - **Prior baseline:** `9/22` passed, `13/22` failed, exit status `1` (`private/ownership-program/wp6/manager-static-verification.json`).
  - **Current partial result:** **`11/22` passed, `11/22` failed, exit status `1`** (`private/ownership-program/chunk2/static-verification-after-ao2.json`).
    Reproduced by the manager; raw result is saved as
    `private/ownership-program/chunk2/manager-static-verification.json`.
  - **Target check resolved:** `AO2` (`FAIL` -> `PASS`, independently verified).
  - **Incidental change (unreviewed):** `G8` (`FAIL` -> `PASS`) because `G8` also calls `e.got_loads(0x84054, 0x840C8)` for `_ZN11EyeseeLinux10httpServer15postmsg3previewEii` and `g_task_queue`. **`G8` was not independently reviewed in Chunk 2 and is not promoted to a verified claim.**
  - **Remaining failing checks (11, unreviewed in Chunk 2):** `AO3`, `AO4`, `AO5`, `AI1`, `G1`, `G4`, `G5`, `G6`, `G7`, `G9`, `U1`.
