# WP2A: device-ABI status library and offline loader (2026-11-14)

**Result: the status-only bridge can be built device-compatible and loaded in-process,
and the loader works offline.** The proof uses `qemu-arm` with the saved camera libc,
synthetic hosts, and the candidate's exact stub bytes. **Nothing was deployed. No
hardware capability is enabled.** The native backend reports status only.

Proof record: [LOADER-PROOF.md](../native-bridge/LOADER-PROOF.md). Contract:
[NATIVE-ABI.md](../native-bridge/NATIVE-ABI.md). Prior packages:
[WP1](WP1-NATIVE-OWNERSHIP.md), [WP2](WP2-STATUS-BRIDGE.md).

## What was built

- **Device-ABI profile, without a second libc.** The library is compiled against the
  pinned, GPG-verified musl 1.1.24 headers and linked against the **saved** camera
  `libc.so` (`-z defs`). No time-free shim or out-of-process core was needed: the audit
  proves every import, layout and lookup.
- **`libcammy_native_status.so`** (release `db34fa03…5065`):
  - the WP2 core (`cammy_bridge.c` `4677bc99…`, unmodified) plus a status-only native
    table and the v1 attach/detach entry points
  - exports exactly those two names; one `NEEDED` (`libc.so`), 42 imports; no
    constructors, destructors or TLS
- **Lifecycle:**
  - attach: single-shot, signals masked, `CLOEXEC` throughout
  - detach: idempotent; bounded monotonic tryjoin, 200 ms
  - abandon: pins the core handle, all state and the library until process death
  - a fatal core step error is a bridge failure (`unavailable`), never a native exit
- **Candidate** `sdvcam-status-db34fa035a26` (`a9dedc98…caba0`, private, mode 0600):
  - built from **pristine** stock, with zero media-event modifications
  - one call-site word, a 360-byte stub in the cave, and the text LOAD size changed
- **Library path:** baked SHA-256-scoped
  `/tmp/cammy-native-status/<library-sha256>/libcammy_native_status.so` (manager
  contract, underscore name).
- **Fixtures:**
  - synthetic host (one source for the device profile and for host ASan/TSan)
  - ARM stub fixture with inert or real `dlopen`/`dlsym`
  - a time64 negative control

## Changed paths (all new; nothing outside the owned list)

- `native/loader/`:
  - `cammy_native_host.h`, `cammy_native_status.c`
  - `exports.map`, `exports-hooks.map`
  - `status_stub.S`
  - `synth_host.c`, `stub_fixture.c`, `stub_fixture_asm.S`
  - `elfinfo.py`, `abi_audit.py`, `fetch_musl_headers.sh`, `Makefile`
- `tools/build_native_status_candidate.py`
- `tests/test_native_status_loader.py`
- `docs/work-packages/WP2A-ABI-LOADER.md`, `docs/native-bridge/LOADER-PROOF.md`
- Ignored output:
  - `build/native-loader/`: sysroot, third-party tarball, builds, namespace `/tmp`
  - `private/ownership-program/wp2a/`: candidate and manifest, library copy, audits,
    fixture evidence, `checkpoint.json`, test work

`native/bridge/`, `tools/native_trial/`, the WP1 contract and top-level files were not
edited.

## Commands and results

| Command | Result |
| --- | --- |
| `sh native/loader/fetch_musl_headers.sh` | One-time, pinned and verified; installs only under `build/native-loader/` |
| `make -C native/loader device time64-control host-asan host-tsan` | Clean under the WP2 warning set with `-Werror`; release and hooks libraries rebuild byte-identically |
| `python3 -I native/loader/abi_audit.py --library build/native-loader/release/libcammy_native_status.so --rootfs build/native-loader/device-rootfs` | pass (the time64 control fails, as intended) |
| `python3 tools/build_native_status_candidate.py --library private/ownership-program/wp2a/library/libcammy_native_status.so` | candidate and manifest, mode 0600, `deployed: false` |
| `python3 -m unittest tests.test_native_status_loader` | **27/27 OK**, 3 consecutive runs, about 6 s each |

**The 27 tests cover:**
- builder guards: 5
- ABI audit: 5
- qemu device scenarios: 11
- stub fixture: 4 (the inert run holds 7 register-level cases)
- host ASan/UBSan and TSan: 2

The same fixtures were rerun against the official candidate file
(`private/ownership-program/wp2a/evidence-fixtures.json`).

## Answers to the manager's questions

- **Device-compatible library plus WP2 core?** Yes. Time-bearing imports are only
  `clock_gettime`, `lstat` and `fstat`, and their 32-bit layouts were verified at
  runtime against raw kernel syscalls. No libstdc++, libgcc_s, initial-exec TLS or
  constructors.
- **Identity mismatch.** Implemented as NATIVE-ABI §4 says: attach returns `-EPROTO`,
  nothing is served, and the stub runs stock. NATIVE-ABI §5 now also lists a served
  `ERROR`/`NATIVE_IDENTITY_MISMATCH` status. Under this loader design that row is
  unreachable, because a mismatched library never serves. Recommendation: keep the
  fallback, and treat reason 7 as reserved for a future out-of-process client.
- **Abandon.** Proven in the synthetic host: the thread was stalled inside the core
  mid-request, then detach abandoned it, and the late completion was still delivered
  correctly against pinned state.
  - ASan/UBSan: clean.
  - TSan: clean apart from the deliberate un-joined thread.
  - The library is never unloaded; musl 1.1.24 `dlclose` is a no-op anyway, and
    `dlclose` is not imported.

## Unresolved gates (not proven here)

1. **Hardware stage 5.** Unproven on the board:
   - attach latency against the ≤ 50 ms target (qemu: 9–26 ms, emulator only)
   - real scheduling of the 200 ms detach bound
   - boot-time `/tmp` state at S01
   - whether `dlopen` at 0x31DB0 contends with the GUI, debug and log threads already
     running
2. **Staging integrity (WP4).** The baked path is content-addressed but not verified by
   `dlopen`. WP4 must create `/tmp/cammy-native-status/<sha>/` and `/tmp/cammy-bridge/`
   root-owned with mode 0700, and stage the exact `db34fa03…` bytes. WP4's
   **unapproved** selector prototype uses an MD5 directory and a hyphenated file name;
   it must be aligned to this contract. With nothing staged, the candidate behaves as
   stock: proven fallback.
3. **musl internals.** The saved libc is assumed to be unpatched 1.1.24 in the paths
   used (version string, exports and runtime probes; no binary diff). musl's internal
   `dlopen`/thread-exit locking is reasoned from source, not measured.
4. **Exceptions and unwinding.** That a C++ exception escaping 0xA4294 terminates in
   both stock and candidate is static analysis: `main` has no catch clause, and the
   cave is covered by CANTUNWIND.
5. **Native exit paths.** The 7 `exit()` sites, `abort`, watchdog reset and power-off
   are covered only as abrupt disappearance. They are not traced (WP1 U4).

## Suggested architecture/status text (for the manager)

"WP2A (offline, 2026-11-14): a device-ABI status library (musl 1.1.24 headers, linked
to the saved camera libc, 2 exports, 42 audited imports, no time64, no interposition)
embeds the unmodified WP2 core with a status-only native table. A separate private
candidate from pristine `sdvcam` replaces only the 0x31DB0 call with a 360-byte cave
stub that dlopens
`/tmp/cammy-native-status/<lib-sha256>/libcammy_native_status.so`, attaches, runs the
original, and detaches once. It falls back exactly to stock on any load, symbol or
attach failure.

Proven under qemu-arm with the saved libc, in synthetic hosts only:
- register, stack and return-value preservation, and fallback
- detach cardinality
- serve and detach
- the 200 ms abandon path with pinned state
- signal masking and CLOEXEC
- EMFILE handled as a bridge failure
- kill/exit recovery

Not deployed; every physical capability unavailable. Hardware status-only acceptance
and WP4 staging remain gated."
