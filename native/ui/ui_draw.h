/* SPDX-License-Identifier: MIT. Software drawing for cammy-ui into a 356x400 BGRA frame
 * (the same layout the face service hands to LVGL). No allocation, no floating point. */
#ifndef CAMMY_UI_DRAW_H
#define CAMMY_UI_DRAW_H
#include <stdint.h>
#include "cammy_ui_page.h"
enum ui_font { UI_SMALL, UI_BODY, UI_TITLE, UI_KEY, UI_FONTS };
enum ui_icon { I_WIFI, I_AGENT, I_SLIDERS, I_CAMERA, I_BACK, I_CLOSE, I_LOCK, I_BARS1, I_BARS2, I_BARS3,
               I_CHECK, I_PLUS, I_MINUS, I_BACKSPACE, I_SHIFT, I_PIKACHU, I_FINFUR, UI_ICONS };
/* box: x, y, w, h of each glyph's inked area inside its advance x height cell. */
struct ui_font_data { int height; const unsigned char *alpha; const unsigned short *offset; const unsigned char *advance;
                     const unsigned char *box; };
struct ui_icon_data { int size; const unsigned char *alpha; };
extern const struct ui_font_data ui_fonts[UI_FONTS];
extern const struct ui_icon_data ui_icons[UI_ICONS];
/* 0xRRGGBB */
#define C_BG 0x111318u
#define C_CARD 0x1f232cu
#define C_CARD_HI 0x2b303bu
#define C_LINE 0x343a46u
#define C_TEXT 0xf4f5f7u
#define C_DIM 0x9aa1adu
#define C_YELLOW 0xffcc1fu
#define C_YELLOW_DK 0x3a3112u
#define C_GREEN 0x4cd07du
#define C_RED 0xff6b5eu
#define C_KEY 0x2a2f39u
struct ui_canvas { unsigned char *px; };
void ui_fill(struct ui_canvas *, int x, int y, int w, int h, uint32_t rgb);
void ui_rrect(struct ui_canvas *, int x, int y, int w, int h, int r, uint32_t rgb);
/* Rounded outline of thickness t drawn inside (x,y,w,h). */
void ui_ring(struct ui_canvas *, int x, int y, int w, int h, int r, int t, uint32_t rgb);
int ui_text_width(enum ui_font, const char *);
/* Draws at most max_w pixels wide, ending with "..." when cut. Returns the width drawn. */
int ui_text(struct ui_canvas *, int x, int y, enum ui_font, const char *, uint32_t rgb, int max_w);
void ui_text_center(struct ui_canvas *, int cx, int y, enum ui_font, const char *, uint32_t rgb, int max_w);
void ui_icon(struct ui_canvas *, int x, int y, enum ui_icon, uint32_t rgb);
#endif
