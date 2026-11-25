/* SPDX-License-Identifier: MIT
 * Reference source of the WP2A status-only loader stub. The candidate builder
 * (tools/build_native_status_candidate.py) encodes the same instructions itself;
 * tests assemble this file with GNU as, link it at 0x8E2E40 and require identical
 * bytes, so the two encoders check each other.
 *
 * Build: cpp -DLIB_PATH='"/tmp/cammy-native-status/<sha>/libcammy_native_status.so"'
 * Entry: from `bl` at 0x31DB0 in main, r0 = main's [sp,#16], lr = 0x31DB4.
 */
    .syntax unified
    .arm
    .text
    .equ DLOPEN_PLT, 0x2CA70
    .equ DLSYM_PLT, 0x2D574
    .equ ORIGINAL, 0xA4294
    .global status_stub
status_stub:
    push    {r0-r6, lr}             @ r0-r3 kept for the original; 32 bytes keeps sp 8-aligned
    add     r0, pc, #(path - . - 8)
    mov     r1, #2                  @ RTLD_NOW (musl)
    bl      DLOPEN_PLT
    subs    r5, r0, #0
    beq     fallback
    add     r1, pc, #(sym_attach - . - 8)
    bl      DLSYM_PLT
    subs    r6, r0, #0
    beq     fallback
    mov     r0, r5
    add     r1, pc, #(sym_detach - . - 8)
    bl      DLSYM_PLT
    subs    r5, r0, #0
    beq     fallback
    sub     sp, sp, #48             @ struct cammy_native_host_v1
    mov     r0, #1                  @ abi_version
    mov     r1, #48                 @ struct_size
    mov     r2, #1                  @ CAMMY_PROFILE_STATUS_ONLY
    stm     sp, {r0-r2}
    add     r1, pc, #(src_hash - . - 8)
    add     r0, sp, #12
    ldm     r1!, {r2, r3, ip, lr}
    stm     r0!, {r2, r3, ip, lr}
    ldm     r1!, {r2, r3, ip, lr}
    stm     r0!, {r2, r3, ip, lr}
    ldr     r2, [sp, #48]           @ caller's r0 from the push frame
    str     r2, [r0]                @ main_module (opaque)
    mov     r0, sp
    blx     r6                      @ attach(&host)
    add     sp, sp, #48
    cmp     r0, #0
    bne     fallback
    ldm     sp, {r0-r3}             @ original arguments, unchanged
    bl      ORIGINAL
    stm     sp, {r0, r1}            @ keep the original's r0:r1 across detach
    mov     r0, #1                  @ CAMMY_NATIVE_EXIT_QUEUE_CLOSED
    blx     r5                      @ detach
    pop     {r0-r6, pc}             @ return to 0x31DB4
fallback:
    pop     {r0-r6, lr}             @ exact entry state, lr = 0x31DB4
    b       ORIGINAL                @ stock tail call
    .balign 4
src_hash:
    .byte 0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e
    .byte 0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec
    .balign 4
path:
    .asciz LIB_PATH
    .balign 4
sym_attach:
    .asciz "cammy_native_attach_v1"
    .balign 4
sym_detach:
    .asciz "cammy_native_detach_v1"
    .balign 4
    .section .note.GNU-stack,"",%progbits
