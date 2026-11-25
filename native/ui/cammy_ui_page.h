/* SPDX-License-Identifier: MIT. Shared page between cammy-ui (menu process) and the
 * face service inside sdvcam. cammy-ui renders whole screens into one of two buffers;
 * the face service only displays the published buffer and forwards taps (through its
 * own status page). Nothing here is ever freed while sdvcam runs. */
#ifndef CAMMY_UI_PAGE_H
#define CAMMY_UI_PAGE_H
#include <stdint.h>
#include <stdatomic.h>
#define CUI_FILE "/tmp/cammy-face-service/ui"
#define CUI_MAGIC 0x31495543u /* "CUI1" */
#define CUI_ABI 1u
#define CUI_HEADER_BYTES 4096u
#define CUI_W 356u
#define CUI_H 400u
#define CUI_FRAME_BYTES (CUI_W*CUI_H*4u)
#define CUI_FILE_BYTES (CUI_HEADER_BYTES+2u*CUI_FRAME_BYTES)
#define CUI_STALE_MS 3000u /* no heartbeat for this long: the face ignores the menu */
/* cammy-ui starts per boot: the first start plus supervisor restarts (UI_STARTS in
 * tools/rc06_build.py). Each start publishes a new page file. The face service maps at
 * most this many and never unmaps them (the audited import set has no munmap), so this
 * must cover every allowed start: at most 5 x CUI_FILE_BYTES stay mapped in sdvcam. */
#define CUI_MAX_STARTS 5u
struct cui_header {
    _Atomic uint32_t magic, abi, pid;
    _Atomic uint32_t heartbeat_ms;  /* cammy-ui CLOCK_MONOTONIC ms, low 32 bits */
    _Atomic uint32_t show;          /* 1: display the published buffer instead of the face */
    _Atomic uint32_t frame_seq;     /* bumped after a buffer is fully written */
    _Atomic uint32_t frame_index;   /* 0 or 1: buffer holding frame_seq */
    _Atomic uint32_t ack_seq;       /* face service: frame_seq it handed to the GUI */
    _Atomic uint32_t camera_mode;   /* 1: give the screen to the stock camera this boot */
    _Atomic uint32_t face_pack;     /* 1 + index of the chosen pack (cui_pack_id); 0: not set */
    _Atomic uint32_t live_view;     /* 1: hide the face so the stock live view shows, staying in
                                       CammyOS mode (0.7.4, DOWN / "show the camera"); additive */
    /* 0.7.11, additive: */
    _Atomic uint32_t pack_seq;      /* cammy-ui: bumped on every physical pack pick (even of the
                                       pack already saved), so the face service sees the pick */
    _Atomic uint32_t face_shown;    /* face service: 1 + the pack on screen; 0 unknown */
    /* 0.7.12 candidate, additive: */
    _Atomic uint32_t boot;          /* cammy-ui: CUI_BOOT_* for the face service's loading gate
                                       (face_service.h). 0 is a cammy-ui without the gate. */
};
/* LOADING: still waiting (or showing a startup failure with its stock camera choice);
 * READY: this boot's records show its services ready, so the face may show. */
#define CUI_BOOT_LOADING 1u
#define CUI_BOOT_READY 2u
/* Face packs in bank order. A physical choice is saved as an ID in /etc/cammy/face-pack.
 * The first two packs are raw child banks. Cammy uses the optional packed bank. */
#define CUI_CHILD_PACKS 2u
#define CUI_PACK_CAMMY 2u
#define CUI_PACK_COUNT 3u
static inline const char *cui_pack_id(uint32_t i) {
    return i == 0u ? "pikachu" : i == 1u ? "fin-fur" : i == CUI_PACK_CAMMY ? "cammy" : 0;
}
/* Index of a pack ID (n bytes, no terminator needed), or -1 if unknown. */
static inline int cui_pack_index(const char *id, unsigned n) {
    for (uint32_t i = 0; i < CUI_PACK_COUNT; i++) {
        const char *k = cui_pack_id(i);
        unsigned j = 0;
        while (j < n && k[j] && k[j] == id[j]) j++;
        if (j == n && !k[j]) return (int)i;
    }
    return -1;
}
_Static_assert(sizeof(struct cui_header)<=CUI_HEADER_BYTES,"ui header");
static inline unsigned char *cui_frame(void *base,uint32_t index) {
    return (unsigned char *)base+CUI_HEADER_BYTES+(index&1u)*CUI_FRAME_BYTES;
}
#endif
