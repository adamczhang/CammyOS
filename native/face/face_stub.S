/* SPDX-License-Identifier: MIT. Offline-only main + GUI bracket. */
    .syntax unified
    .arm
    .text
    .global face_main_stub, face_gui_stub
    .equ ORIGINAL, 0xA4294
    .equ TIMER, 0x4A7950
    .equ SLOT, 0xAFE098
face_main_stub:
    push {r0-r8,lr}
    adr r0,path
    mov r1,#2
    bl 0x2CA70
    subs r5,r0,#0
    beq fallback
    adr r1,sym_attach
    bl 0x2D574
    subs r6,r0,#0
    beq fallback
    mov r0,r5
    adr r1,sym_detach
    bl 0x2D574
    subs r7,r0,#0
    beq fallback
    mov r0,r5
    adr r1,sym_gui
    bl 0x2D574
    subs r8,r0,#0
    beq fallback
    sub sp,sp,#48
    mov r0,#1
    mov r1,#48
    mov r2,#2
    stm sp,{r0-r2}
    adr r1,src_hash
    add r0,sp,#12
    ldm r1!,{r2,r3,ip,lr}
    stm r0!,{r2,r3,ip,lr}
    ldm r1!,{r2,r3,ip,lr}
    stm r0!,{r2,r3,ip,lr}
    ldr r2,[sp,#48]
    str r2,[r0]
    mov r0,sp
    blx r6
    add sp,sp,#48
    cmp r0,#0
    bne fallback
    ldr r4,slot_address
    dmb ish
    str r8,[r4]
    ldm sp,{r0-r3}
    bl ORIGINAL
    stm sp,{r0,r1}
    @ Keep the never-unloaded callback available so a later GUI tick can
    @ honor detach on the GUI thread under the stock mutex.
    mov r0,#1
    blx r7
    pop {r0-r8,pc}
fallback:
    pop {r0-r8,lr}
    b ORIGINAL
face_gui_stub:
    push {r0-r4,lr}
    ldr r4,slot_address
    ldr r4,[r4]
    dmb ish
    cmp r4,#0
    beq gui_fallback
    mov r0,r5
    mov r1,#0
    blx r4
    ldm sp,{r0-r3}
    bl TIMER
    stm sp,{r0,r1}
    mov r0,r5
    mov r1,#1
    blx r4
    pop {r0-r4,pc}
gui_fallback:
    pop {r0-r4,lr}
    b TIMER
slot_address:
    .word SLOT
src_hash:
    .byte 0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e
    .byte 0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec
path:
    .asciz LIB_PATH
    .balign 4
sym_attach:
    .asciz "cmf_attach_v1"
    .balign 4
sym_detach:
    .asciz "cmf_detach_v1"
    .balign 4
sym_gui:
    .asciz "cmf_gui_v1"
    .balign 4
    .section .note.GNU-stack,"",%progbits
