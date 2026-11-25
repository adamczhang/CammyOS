# Body adapters: interrupted research draft

Checkpoint: 2026-11-14. WP6 ended with an Opus 5.5 provider safeguard rejection.
Only `verify_static_evidence.py` was preserved; no adapter implementation,
transport, fixture core or hardware capability was delivered.

The manager initially ran the preserved draft verifier against the exact saved
stock executable (`8771961fdba89280578ce5482a6cbe4ec3f6ec205068944b34f944637d62b0ec`).
**Historical baseline: 9 of 22 checks passed; 13 failed; exit status 1.**
A failed check does not establish whether the claim or the verifier is wrong.
No address claim in this draft should be promoted to a verified integration
contract from an unreviewed check result.

## Chunk 2 bounded review: AO2 (`g_audio_mutex` GOT resolution)

In the [chunk 2 report](../../docs/work-packages/CHUNK2-ADAPTER-AO2.md), check
**AO2** (`PlaySound trylocks g_audio_mutex at 0x80E8C (GOT load at 0x80E80)`)
was independently cross-checked against the saved stock ELF:

- **Diagnosis:** The AO2 instruction/address claim is **correct**, and the
  verifier's `Elf.__init__` GOT symbol table (`self.got_sym`) was **incomplete**
  for non-PIE `ET_EXEC` binaries. `sdvcam` populates `.got` slot `0x8FC064`
  statically at link time with `0xAEF9C8` (`g_audio_mutex` in `.dynsym`,
  24-byte `.bss` object) without emitting a `.rel.dyn` entry. `Elf.__init__`
  now maps static `.got` words matching `.dynsym` values into `self.got_sym`
  when no `.rel.dyn` relocation overrides the slot.
- **Partial verifier status after Chunk 2:** **11 of 22 checks pass; 11 fail;
  exit status 1** (`private/ownership-program/chunk2/static-verification-after-ao2.json`).
  - **AO2:** Reviewed and verified at the static instruction level.
  - **G8:** Turned green **incidentally** because its `got_loads(0x84054, 0x840C8)`
    call shares `self.got_sym`; **G8's claim was not independently reviewed in
    Chunk 2 and is not promoted to verified status.**
  - **11 remaining checks** still fail and remain unreviewed draft claims.

The tool reads a saved ELF only. It runs no vendor executable and accesses no
camera or hardware. Raw analysis and results remain in ignored
`private/ownership-program/wp6/` and `private/ownership-program/chunk2/`. The
expected stock hash is enforced before decoding. This draft is not part of the
build, launch or release acceptance gates.

Resume with independent chunked reviews of the remaining failed checks and
incidental passes, followed by an ownership/lifetime/completion contract for
each supported adapter. Audio, gimbal and face hardware backends remain
unavailable. Do not automatically retry the rejected provider session or
substitute a model without the user's direction.
