/* SPDX-License-Identifier: MIT. See ui_draw.h. */
#include "ui_draw.h"
#include <string.h>
#define W ((int)CUI_W)
#define H ((int)CUI_H)
static void blend(struct ui_canvas *c, int x, int y, uint32_t rgb, unsigned a) {
    if (x < 0 || y < 0 || x >= W || y >= H || !a) return;
    unsigned char *p = c->px + ((unsigned)y * CUI_W + (unsigned)x) * 4u;
    unsigned r = rgb >> 16 & 255u, g = rgb >> 8 & 255u, b = rgb & 255u;
    if (a >= 255u) { p[0] = (unsigned char)b; p[1] = (unsigned char)g; p[2] = (unsigned char)r; p[3] = 255; return; }
    p[0] = (unsigned char)((b * a + p[0] * (255u - a)) / 255u);
    p[1] = (unsigned char)((g * a + p[1] * (255u - a)) / 255u);
    p[2] = (unsigned char)((r * a + p[2] * (255u - a)) / 255u);
    p[3] = 255;
}
void ui_fill(struct ui_canvas *c, int x, int y, int w, int h, uint32_t rgb) {
    for (int j = y < 0 ? 0 : y; j < y + h && j < H; j++)
        for (int i = x < 0 ? 0 : x; i < x + w && i < W; i++) blend(c, i, j, rgb, 255);
}
/* Coverage (0..16) of pixel (i,j) by a w x h rounded rect, from a 4x4 subsample grid:
 * smooth enough at this size, integer only. Outside the rect: 0. */
static unsigned cover(int i, int j, int w, int h, int r) {
    if (i < 0 || j < 0 || i >= w || j >= h) return 0;
    if (r * 2 > w) r = w / 2;
    if (r * 2 > h) r = h / 2;
    int cx = i < r ? r : i >= w - r ? w - r : -1, cy = j < r ? r : j >= h - r ? h - r : -1;
    if (cx < 0 || cy < 0) return 16;
    unsigned hit = 0;
    for (int sy = 0; sy < 4; sy++)
        for (int sx = 0; sx < 4; sx++) {
            int dx = (i * 8 + sx * 2 + 1) - cx * 8, dy = (j * 8 + sy * 2 + 1) - cy * 8;
            if (dx * dx + dy * dy <= r * r * 64) hit++;
        }
    return hit;
}
void ui_rrect(struct ui_canvas *c, int x, int y, int w, int h, int r, uint32_t rgb) {
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) blend(c, x + i, y + j, rgb, cover(i, j, w, h, r) * 255u / 16u);
}
void ui_ring(struct ui_canvas *c, int x, int y, int w, int h, int r, int t, uint32_t rgb) {
    int ri = r > t ? r - t : 0;
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) {
            unsigned o = cover(i, j, w, h, r), n = cover(i - t, j - t, w - 2 * t, h - 2 * t, ri);
            if (o > n) blend(c, x + i, y + j, rgb, (o - n) * 255u / 16u);
        }
}
static int glyph(int ch) { return ch < 32 || ch > 126 ? '?' - 32 : ch - 32; }
int ui_text_width(enum ui_font f, const char *s) {
    int w = 0;
    for (; *s; s++) w += ui_fonts[f].advance[glyph((unsigned char)*s)];
    return w;
}
static int draw_glyph(struct ui_canvas *c, int x, int y, enum ui_font f, int g, uint32_t rgb) {
    const struct ui_font_data *d = &ui_fonts[f];
    const unsigned char *b = d->box + g * 4, *a = d->alpha + d->offset[g];
    for (int j = 0; j < b[3]; j++)
        for (int i = 0; i < b[2]; i++) blend(c, x + b[0] + i, y + b[1] + j, rgb, a[j * b[2] + i]);
    return d->advance[g];
}
int ui_text(struct ui_canvas *c, int x, int y, enum ui_font f, const char *s, uint32_t rgb, int max_w) {
    int full = ui_text_width(f, s), x0 = x;
    if (max_w <= 0 || full <= max_w) {
        for (; *s; s++) x += draw_glyph(c, x, y, f, glyph((unsigned char)*s), rgb);
        return x - x0;
    }
    int dots = 3 * ui_fonts[f].advance['.' - 32];
    for (; *s; s++) {
        int g = glyph((unsigned char)*s);
        if (x - x0 + ui_fonts[f].advance[g] + dots > max_w) break;
        x += draw_glyph(c, x, y, f, g, rgb);
    }
    for (int k = 0; k < 3; k++) x += draw_glyph(c, x, y, f, '.' - 32, rgb);
    return x - x0;
}
void ui_text_center(struct ui_canvas *c, int cx, int y, enum ui_font f, const char *s, uint32_t rgb, int max_w) {
    int w = ui_text_width(f, s);
    if (max_w > 0 && w > max_w) w = max_w;
    (void)ui_text(c, cx - w / 2, y, f, s, rgb, max_w);
}
void ui_icon(struct ui_canvas *c, int x, int y, enum ui_icon id, uint32_t rgb) {
    const struct ui_icon_data *d = &ui_icons[id];
    for (int j = 0; j < d->size; j++)
        for (int i = 0; i < d->size; i++) blend(c, x + i, y + j, rgb, d->alpha[j * d->size + i]);
}
