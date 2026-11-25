/* SPDX-License-Identifier: MIT
 * FEL RAM handoff to the captured ARM zImage. No storage operations.
 * Linux ARM boot protocol: r0=0, r1=~0 for DT boot, r2=DTB.
 */
.syntax unified
.arm
.global _start
_start:
    cpsid if
    mrc p15, 0, r0, c1, c0, 0
    movw r1, #0x1807
    bic r0, r0, r1
    mcr p15, 0, r0, c1, c0, 0
    dsb sy
    isb
    mov r0, #0
    mcr p15, 0, r0, c7, c5, 0
    mcr p15, 0, r0, c7, c5, 6
    dsb sy
    isb
    mvn r1, #0
    ldr r2, =0x4a000000
    ldr r3, =0x44000000
    bx r3
