# Status-only loader: device-ABI and lifecycle proof (WP2A, 2026-11-14)

**Offline proof only.** Nothing here ran on the camera, used ADB or the camera network,
opened a device node, or executed the original or patched `sdvcam`. The library ran
under `qemu-arm` against the **saved camera libc**, inside synthetic hosts. The patched
executable is a private candidate (mode 0600, `deployed: false`), not part of any TF
release. Every physical capability stays **unavailable**.

Package report: [WP2A-ABI-LOADER.md](../work-packages/WP2A-ABI-LOADER.md). Contract:
[NATIVE-ABI.md](NATIVE-ABI.md) §3–§5 and §7. Source: [`native/loader/`](../../native/loader/).

## 1. Identities

| Item | SHA-256 |
| --- | --- |
| Stock `sdvcam` (input, unchanged) | `8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec` |
| Saved camera `/lib/libc.so` (musl 1.1.24, linked against, never replaced) | `5fea26c4e562809794d33fd9c62304cef68b2db6233a8372bedd31e0baac7f51` |
| musl 1.1.24 source (headers + `crt1/crti/crtn` only) | `1370c9a812b2cf2a7d92802510cca0058cc37e66a7bedd70051f0a34015022a3` |
| Release library `libcammy_native_status.so` (21,456 B) | `db34fa035a26c4af63cb5854d85926cdf38b5cd055f38cedc62db19bf1e55065` |
| Candidate `sdvcam-status-db34fa035a26` (private) | `a9dedc985cc79d933601390df36b4458784e8dd4807b867cd53cfe0a8a9caba0` |
| Stub bytes (360 B at VA 0x8E2E40) | `f1991e175dafdf000775e025d386be42e8704a6fe9ac7d2a289a7b29df7263b6` |
| WP2 core `cammy_bridge.c` (compiled in, read-only) | `4677bc99ed96eee464b47621f1a4422a9fed412c7a5e4ed5228f3f0a6f1560d5` |

**musl source provenance:** `https://musl.libc.org/releases/musl-1.1.24.tar.gz`. It has a good
GPG signature from the musl release key, fingerprint
`8364 8929 0BB6 B70F 99FF DA05 56BC DB59 3020 450F` (checked in a throwaway keyring).
The signature is the authoritative check. The SHA-256 above is now pinned in the
script; it also matched the Buildroot 1.1.24 value I recalled, but I did not re-fetch
Buildroot's hash file to confirm that. `native/loader/fetch_musl_headers.sh` repeats the
fetch and both checks, and installs headers only under `build/native-loader/`.

**Reproducibility:** a clean rebuild gives byte-identical release and fixture libraries.
The library path baked into the candidate is
`/tmp/cammy-native-status/db34fa03…5065/libcammy_native_status.so`. The library was built
and hashed first, then its hash was baked in, so there is no fixed point.

## 2. Device-ABI build profile

The release library is built from the WP2 core plus `cammy_native_status.c` with these
settings:
- the existing gcc 14.3 cross compiler, with `-nostdinc` and **musl 1.1.24 headers only**
- `-marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard`
- no stack protector, fortify or unwind tables; hidden visibility by default
- linked `-nostdlib -shared -z defs -z now` against the **saved** `libc.so` plus static
  `libgcc.a` (helpers made local)

`-z defs` makes the link fail if any import is missing from the saved libc. A version
script exports exactly the two v1 symbols.

There is no second libc: the only `DT_NEEDED` is `libc.so`, and musl resolves it to the
already-loaded C library.

The ARM RTABI hooks `__aeabi_idiv0`/`__aeabi_ldiv0` are defined locally. libgcc's
default handlers raise `SIGFPE`; the local ones just return. The only 64-bit divisors
are non-zero constants, so the hooks are unreachable, and defining them keeps `raise`
out of the import table.

**Audit** (`native/loader/abi_audit.py`, mechanical, all pass):

| Check | Result |
| --- | --- |
| ELF | ARM ET_DYN, `e_flags` 0x5000400 (EABI5 hard-float); no PT_TLS, no PT_INTERP; GNU_STACK not executable |
| Dynamic | `NEEDED` = [`libc.so`]; no RPATH/RUNPATH/TEXTREL; no INIT/FINI/INIT_ARRAY/FINI_ARRAY/PREINIT_ARRAY (no constructors or destructors) |
| Exports | exactly `cammy_native_attach_v1`, `cammy_native_detach_v1` (core symbols hidden, checked again at runtime with `dlsym`) |
| Imports | 42, all exported by the saved libc. No `*time64*`. None forbidden: no exit, signal installation, exec, ioctl, kill, `dlclose`, mmap, libstdc++/libgcc_s/unwinder or TLS symbols |
| Interposition | In musl's global lookup order for `sdvcam` (the executable, then 11 NEEDED objects, then `libc.so` 13th), the **first** definer of every import and of the stub's `dlopen`/`dlsym` is `libc.so`. The earlier objects shadow only linker symbols, `rint` (libawnn) and long-double math (libstdc++), none of them imported |
| Relocations | `R_ARM_RELATIVE` and `R_ARM_JUMP_SLOT` only |
| Instructions | no VFP or NEON instruction in the library |
| Time-bearing imports | `clock_gettime` (`struct timespec`), `lstat`/`fstat` (`struct stat`): see the runtime layout probe |

**Runtime layout probe** (synthetic host under qemu, saved libc):
- `time_t` is 4 bytes and `timespec` is 8. `clock_gettime` writes no byte past the
  struct and agrees with the raw 32-bit `clock_gettime` syscall.
- musl's `struct stat` is 104 bytes. Its `st_mode` (offset 16), `st_uid`, `st_size` (48),
  `st_mtim` (80) and `st_ino` (96) equal the kernel ARM `stat64` filled by a raw
  `fstatat64` syscall, using offsets written out independently of libc. No byte is
  written past the struct.
- Recorded sizes: `sigset_t` 128, `pthread_attr_t` 36, `pthread_mutex_t` 24.

**Negative control:** the same sources built with the ordinary musl 1.2.5 (time64)
toolchain fail the audit. Under qemu, the saved libc's `dlopen(RTLD_NOW)` refuses that
library with a `time64` symbol error, and the stub fixture falls back to the original.

## 3. Library behaviour (status v1)

| Rule (NATIVE-ABI) | Implementation | Proof |
| --- | --- | --- |
| Attach on the main thread, bounded, no native call, never dereference `mm` | Validates `abi`/`size` first, then profile and hash. Opens the WP2 core with a status-only native table and creates one 64 KiB thread | Every host passes an inaccessible (PROT_NONE) page as `main_module`; the stub fixture passes an unmapped sentinel |
| Single-shot | First call wins; later calls return `-EALREADY` (even after a rejected record) | qemu: second attach and retry-after-reject |
| Record rejection | `-EINVAL` (NULL) or `-EPROTO` (abi/size/profile/hash). Nothing is served, no descriptor is left | qemu, 5 cases |
| Signals | All blockable signals masked around `pthread_create`; the caller's mask is restored | qemu: mask restored exactly. A process-directed SIGUSR1 stays pending while main blocks it (the bridge thread never takes it) and runs on main once unblocked |
| CLOEXEC | Every new descriptor, including accepted peers, has `FD_CLOEXEC` | qemu: `/proc/self/fd` difference after attach and with a peer connected |
| Status | `READY`/`NONE` while serving; `UNAVAILABLE`/`NATIVE_EXITING` once detach begins; every physical capability `unavailable`/`not_in_protocol_v1`; no hardware-actions flag | qemu: hello/status/capabilities |
| Detach | Idempotent; a no-op before or without a successful attach. Sets `EXITING`, wakes the core under a lock, then polls `pthread_tryjoin_np` with a monotonic deadline (no `CLOCK_REALTIME`). Close (socket unlink, `fini`) only after a completed join | qemu: about 1–2 ms, second call about 4 µs, socket removed, no descriptors left |
| Abandon | After 200 ms: no join, close, free, unlink, unmap or `dlclose`. The core handle, all state (static) and the library stay valid until process death | qemu: next section |
| Fatal step error | `cammy_bridge_step() == -1` (for example `EMFILE` on accept) → `SERVING→FAILED`. The bridge thread itself releases its descriptors and socket, and stops. The host continues; detach later joins | qemu: real `EMFILE` from `RLIMIT_NOFILE`; the host keeps running |
| Exit without detach | `exit()` with the bridge thread live: no library destructor, clean status 0; peers see EOF | qemu |
| Kill while serving | Peers see EOF; the stale socket is reclaimed by the next attach | qemu: SIGKILL, then a new host attaches and serves |

**Abandon proof** (fixture-only hooks build `745d5866…`; same source plus two test
exports, `-DCNS_TEST_HOOKS`):
1. A client's STATUS request blocks the bridge thread *inside* the status callback,
   mid-step in the core.
2. `detach` returns after about 200 ms with result `ABANDONED`. The handle stays pinned
   and the socket is still present.
3. The host then unmaps its MainModule stand-in and churns the heap.
4. On release, the late thread finishes the step against the pinned handle. The client
   receives `unavailable`/`native_exiting`, the thread exits, and the host exits 0.

The same scenario passes on the build host under ASan/UBSan (no use-after-free) and
TSan (no race). TSan's only report is the abandoned thread itself ("thread leak"),
which is the designed outcome and is suppressed for that one case.

**Syscall trace** (`qemu-arm -strace`, release library):
- The bridge thread and the attach/detach windows make no `ioctl`, `execve`, kill,
  `rt_sigaction`, mount, reboot or `exit_group` call, and touch nothing under `/dev` or
  `/sys`.
- They open only the boot-id file and `/tmp/cammy-bridge/`.
- Attach creates exactly one thread.

## 4. Candidate and stub

`tools/build_native_status_candidate.py` refuses anything but the exact stock hash.
It then checks, before patching:
- the 8 program headers, entry point and flags
- the zero cave from file 0x8D2E1C to 0x8D3030
- `bl 0xA4294` at 0x31DB0, which is the **only** direct branch to 0xA4294
- the 9-word body of 0xA4294: it reads none of r0–r3 or stack arguments, and
  tail-branches to `TaskQueue::Run` 0x57F38
- the `dlopen`/`dlsym` PLT entries 0x2CA70/0x2D574, which go to GOT slots carrying
  `R_ARM_JUMP_SLOT` for exactly those names
- that the last EXIDX entry (0x48A884) is CANTUNWIND and starts before the cave
- that `main`'s LSDA has no type table (no catch clause)

**Patch** (pristine source, **zero media-event modifications**):
- the word at 0x31DB0 becomes `bl 0x8E2E40`
- 360 stub bytes go at 0x8E2E40–0x8E2FA8. This leaves the media candidate's
  0x8E2E20–0x8E2E38 untouched.
- text LOAD `filesz`/`memsz` grows from 0x8D2E1C to 0x8D2FA8. That is still inside the
  already-mapped last text page, so there is no new mapping, and it is below the data
  LOAD.

**Post-patch verification:**
- bytes differ only in the three allowed ranges
- section headers, dynamic section, entry point and other headers are identical
- all stub branch targets are in {dlopen PLT, dlsym PLT, 0xA4294, internal fallback}
- the call sequence is dlopen → dlsym → dlsym → original, and the fallback tail-branches
  to 0xA4294
- the path is embedded

The Python encoder's bytes are **identical** to GNU `as` assembling the readable
[`status_stub.S`](../../native/loader/status_stub.S).

**Stub frame and unwinding:** the stub keeps r0–r3 for the original and pushes
`{r0-r6, lr}` (32 bytes, so `sp` stays 8-aligned). The host record (48 bytes) lives
below that frame during attach. The candidate's text LOAD now covers the stub, but its
PC still falls under the last EXIDX entry, which is CANTUNWIND. A C++ exception that
escapes 0xA4294 therefore stops at the stub frame and calls `std::terminate`. Stock
reaches the same outcome, because `main` has no catch clause (its call site at 0x31DB0
is cleanup-only, landing pad 0x31E48), so phase-1 search fails either way. This is
static reasoning, not an exercised exception.

**ARM stub fixture** (`stub_fixture`, qemu, saved libc): the candidate's own word at
0x31DB0 and its exact cave bytes are mapped at their native addresses. Trampolines sit
at both PLT addresses, at 0xA4294 (fake original) and at 0x31DB4 (register probe). The
fakes clobber r1–r3, r12 and d0–d7.

*Inert cases*, 7 of 7 passing:
- `dlopen` NULL; attach symbol NULL; detach symbol NULL; attach `-EPROTO`; attach
  `+1` → exact stock tail: the original sees the entry r0–r3, entry `sp` and
  `lr = 0x31DB4`, and detach runs 0 times
- success, run twice → `dlopen(path, RTLD_NOW=2)`, then `dlsym` attach, then detach on
  the same handle, then attach with the host record. The record has abi 1, size 48,
  profile 1, the exact hash and `main_module = r0`, and it sits in the stub frame.
  Then come the original (`sp` 32 lower and 8-aligned, `lr` inside the stub) and
  detach(1) **exactly once, after** the original.

Every case checks:
- r4–r11, `sp` and d8–d15 at 0x31DB4 equal their entry values
- r0:r1 equal the original's result
- the 64 words above the entry `sp` (main's frame) are untouched

*Real cases*: the trampolines go to the saved libc's real `dlopen`/`dlsym`, the release
library is staged at the baked path, and the fake original blocks like the queue run.
- **Staged:** the bridge serves `native`/`ready` with every physical capability
  unavailable. On "close", the real detach joins and removes the socket. Registers are
  preserved.
- **Not staged, or wrong bytes at the path** (the time64 control): exact stock fallback.

**Namespace note:** device runs use `unshare -rm` with `/tmp` bind-mounted to
`build/native-loader/ns-tmp`. That lets the exact baked path resolve without touching
the build host's `/tmp`. qemu's `-L` supplies the saved rootfs `/lib`.

## 5. What is NOT proven

- **Real hardware.** Board timing, the attach budget, scheduler behaviour, live thread
  names, `/proc` readability as the app's user, and the S01 `/tmp` state are not proven.
  Under qemu, attach measured 9–26 ms, `dlopen` about 1 ms and detach 1–2 ms (5 runs).
  These are emulator numbers, **not** a hard real-time or ≤ 50 ms guarantee.
- **Detach deadline.** The 200 ms is measured with `CLOCK_MONOTONIC` polling. It assumes
  the main thread is scheduled; it is not a real-time guarantee.
- **musl internals.**
  - That `dlopen` at 0x31DB0, concurrent with the already-running GUI, debug and log
    threads, holds musl's loader lock only briefly is inferred from the musl 1.1.24
    source, not measured.
  - The saved libc is assumed to be unpatched upstream 1.1.24 in the paths used. This
    rests on the version string, the export set and the runtime probes, not on a binary
    diff.
- **Untraced native paths.** `exit()` and `abort()` paths, watchdog resets and power-off
  before detach are covered only as "abrupt disappearance" (kill and exit fixtures),
  not traced.
- **Staging integrity.** `dlopen` does not verify the library hash in its path.
  Ownership and permissions of `/tmp/cammy-native-status` and `/tmp/cammy-bridge`
  belong to the activation package (WP4). An existing unsafe socket directory makes
  attach fail (`-EACCES`), which gives the stock fallback.
- **Exceptions.** The C++ exception equivalence (§4) is static.
