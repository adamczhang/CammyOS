/* SPDX-License-Identifier: MIT
 * Register-level harness for the WP2A stub fixture (ARM mode, VFPv3-D16).
 * run_site enters the candidate's patched instruction at 0x31DB0 with chosen
 * r0-r11/d8-d15 and sp; probe_entry is reached when control returns to 0x31DB4.
 * fake_original and the wrappers clobber every caller-saved register they may.
 */
    .syntax unified
    .arm
    .fpu vfpv3-d16
    .text

    .global run_site
run_site:
    push    {r4-r11, lr}
    vpush   {d8-d15}
    ldr     r12, =driver_sp
    str     sp, [r12]
    ldr     r12, =site_din
    vldmia  r12, {d8-d15}
    ldr     r12, =main_sp
    ldr     sp, [r12]
    ldr     r12, =site_in + 16
    ldmia   r12, {r4-r11}
    ldr     r12, =site_in
    ldmia   r12, {r0-r3}
    ldr     pc, =0x31DB0            @ execute the candidate's call-site instruction
    .ltorg

    .global probe_entry
probe_entry:                        @ via trampoline at 0x31DB4 (ldr pc: no register change)
    ldr     r12, =probe_out
    stmia   r12, {r0-r11}
    ldr     r12, =probe_sp
    str     sp, [r12]
    ldr     r12, =probe_dout
    vstmia  r12, {d8-d15}
    ldr     r12, =driver_sp
    ldr     sp, [r12]
    vpop    {d8-d15}
    pop     {r4-r11, pc}
    .ltorg

    .macro CLOBBER
    movw    r2, #0xc0d2
    movw    r3, #0xc0d3
    movw    r12, #0xc0dc
    vmov    d0, r2, r3
    vmov    d1, r2, r3
    vmov    d2, r2, r3
    vmov    d3, r2, r3
    vmov    d4, r2, r3
    vmov    d5, r2, r3
    vmov    d6, r2, r3
    vmov    d7, r2, r3
    .endm

    .global fake_original
fake_original:                      @ via trampoline at 0xA4294
    ldr     r12, =orig_in
    stmia   r12!, {r0-r3}
    str     sp, [r12], #4
    str     lr, [r12]
    push    {r4, lr}
    bl      fake_original_c         @ uint64_t -> r0:r1
    CLOBBER
    pop     {r4, pc}
    .ltorg

    .macro WRAP name, cfn
    .global \name
\name:
    push    {r4, lr}
    bl      \cfn
    movw    r1, #0xbad1
    CLOBBER
    pop     {r4, pc}
    .endm

    WRAP wrap_dlopen, fake_dlopen_c
    WRAP wrap_dlsym, fake_dlsym_c
    WRAP wrap_attach, fake_attach_c
    WRAP wrap_detach, fake_detach_c

    .section .note.GNU-stack,"",%progbits
