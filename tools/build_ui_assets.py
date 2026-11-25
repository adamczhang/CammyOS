#!/usr/bin/env python3
"""Font and icon atlas for cammy-ui (0.7 touch menu), rendered on the PC.

Writes build/cammy-ui/ui_assets.c: four Ubuntu font sizes (ASCII 32..126, one alpha
mask per glyph cropped to its inked box, with the advance kept separately) and alpha-mask icons drawn at 4x and
downsampled. The camera never rasterises fonts or vectors.
Usage: python3 -m tools.build_ui_assets
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/cammy-ui'
BOLD = '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf'
MEDIUM = '/usr/share/fonts/truetype/ubuntu/Ubuntu-M.ttf'
# Order must match enum ui_font in native/ui/ui_draw.h.
FONTS = [('small', MEDIUM, 17), ('body', BOLD, 22), ('title', BOLD, 30), ('key', MEDIUM, 21)]
S = 4  # supersampling for icons


def font_cells(path, size):
    font = ImageFont.truetype(path, size)
    ascent, descent = font.getmetrics()
    height = ascent + descent
    glyphs = []
    for code in range(32, 127):
        ch = chr(code)
        adv = max(1, round(font.getlength(ch)))
        im = Image.new('L', (adv + 2, height))
        ImageDraw.Draw(im).text((1, 0), ch, font=font, fill=255)
        glyphs.append((adv, im.crop((1, 0, adv + 1, height)).tobytes()))
    return height, glyphs


def canvas(n):
    im = Image.new('L', (n * S, n * S))
    return im, ImageDraw.Draw(im)


def done(im, n):
    return im.resize((n, n), Image.LANCZOS).tobytes()


def wifi(n, bars=3):
    im, d = canvas(n)
    c, base = n * S / 2, n * S * 0.86
    w = n * S * 0.085
    for i, r in enumerate([0.78, 0.55, 0.32]):
        if 3 - i > bars:
            continue
        rr = n * S * r
        d.arc((c - rr, base - rr, c + rr, base + rr), 225, 315, fill=255, width=round(w))
    dot = n * S * 0.075
    d.ellipse((c - dot, base - dot * 2.2, c + dot, base - dot * 0.2), fill=255)
    return done(im, n)


def agent(n):
    """A small robot head: Cammy's link to the agent on the PC."""
    im, d = canvas(n)
    k = n * S
    d.rounded_rectangle((k * .16, k * .30, k * .84, k * .82), radius=k * .14, fill=255)
    d.ellipse((k * .32, k * .48, k * .44, k * .60), fill=0)
    d.ellipse((k * .56, k * .48, k * .68, k * .60), fill=0)
    d.rounded_rectangle((k * .38, k * .68, k * .62, k * .72), radius=k * .02, fill=0)
    d.line((k * .5, k * .30, k * .5, k * .15), fill=255, width=round(k * .06))
    d.ellipse((k * .43, k * .07, k * .57, k * .21), fill=255)
    d.rectangle((k * .07, k * .48, k * .14, k * .64), fill=255)
    d.rectangle((k * .86, k * .48, k * .93, k * .64), fill=255)
    return done(im, n)


def sliders(n):
    im, d = canvas(n)
    k = n * S
    for y, x in [(.26, .66), (.50, .34), (.74, .58)]:
        d.rounded_rectangle((k * .12, k * (y - .035), k * .88, k * (y + .035)), radius=k * .03, fill=255)
        d.ellipse((k * (x - .1), k * (y - .1), k * (x + .1), k * (y + .1)), fill=255)
        d.ellipse((k * (x - .05), k * (y - .05), k * (x + .05), k * (y + .05)), fill=0)
    return done(im, n)


def camera(n):
    im, d = canvas(n)
    k = n * S
    d.rounded_rectangle((k * .10, k * .28, k * .90, k * .80), radius=k * .1, fill=255)
    d.rounded_rectangle((k * .34, k * .18, k * .62, k * .32), radius=k * .04, fill=255)
    d.ellipse((k * .32, k * .37, k * .68, k * .73), fill=0)
    d.ellipse((k * .40, k * .45, k * .60, k * .65), fill=255)
    d.ellipse((k * .74, k * .36, k * .82, k * .44), fill=0)
    return done(im, n)


def stroke_icon(n, lines, width=.11):
    im, d = canvas(n)
    k = n * S
    for seg in lines:
        pts = [(k * x, k * y) for x, y in seg]
        d.line(pts, fill=255, width=round(k * width), joint='curve')
        r = k * width / 2
        for x, y in pts:
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return done(im, n)


def lock(n):
    im, d = canvas(n)
    k = n * S
    d.arc((k * .28, k * .12, k * .72, k * .62), 180, 360, fill=255, width=round(k * .11))
    d.line((k * .28 + k * .055, k * .37, k * .28 + k * .055, k * .50), fill=255, width=round(k * .11))
    d.line((k * .72 - k * .055, k * .37, k * .72 - k * .055, k * .50), fill=255, width=round(k * .11))
    d.rounded_rectangle((k * .16, k * .46, k * .84, k * .92), radius=k * .1, fill=255)
    return done(im, n)


def pikachu(n):
    """Tiny face-pack thumbnail: yellow handled by the caller's colour."""
    im, d = canvas(n)
    k = n * S
    d.polygon([(k * .18, k * .45), (k * .10, k * .02), (k * .40, k * .30)], fill=255)
    d.polygon([(k * .82, k * .45), (k * .90, k * .02), (k * .60, k * .30)], fill=255)
    d.ellipse((k * .10, k * .22, k * .90, k * .94), fill=255)
    d.ellipse((k * .30, k * .46, k * .40, k * .58), fill=0)
    d.ellipse((k * .60, k * .46, k * .70, k * .58), fill=0)
    d.arc((k * .42, k * .60, k * .58, k * .74), 20, 160, fill=0, width=round(k * .04))
    return done(im, n)



def finfur(n):
    """Fin and Fur thumbnail: an axolotl head with gills, a cat head overlapping it."""
    im, d = canvas(n)
    k = n * S
    w = round(k * .05)
    for y0, y1 in ((.30, .20), (.42, .40), (.54, .58)):     # Fin's gills (left side)
        d.line((k * .10, k * y0, k * .01, k * y1), fill=255, width=round(k * .07))
    d.ellipse((k * .05, k * .26, k * .55, k * .72), fill=255)
    d.ellipse((k * .17, k * .42, k * .25, k * .52), fill=0)
    d.ellipse((k * .35, k * .42, k * .43, k * .52), fill=0)
    d.polygon([(k * .52, k * .36), (k * .56, k * .08), (k * .72, k * .26)], fill=255)   # Fur's ears
    d.polygon([(k * .96, k * .36), (k * .93, k * .08), (k * .78, k * .26)], fill=255)
    d.ellipse((k * .48, k * .30, k * .98, k * .82), fill=0)                             # gap between heads
    d.ellipse((k * .52, k * .22, k * .98, k * .80), fill=255)
    d.ellipse((k * .62, k * .44, k * .69, k * .54), fill=0)
    d.ellipse((k * .80, k * .44, k * .87, k * .54), fill=0)
    d.line((k * .72, k * .64, k * .77, k * .64), fill=0, width=w)
    return done(im, n)

# Order must match enum ui_icon in native/ui/ui_draw.h.
ICONS = [
    ('wifi', 64, lambda n: wifi(n)), ('agent', 64, agent), ('sliders', 64, sliders), ('camera', 64, camera),
    ('back', 28, lambda n: stroke_icon(n, [[(.62, .18), (.30, .50), (.62, .82)]], .13)),
    ('close', 28, lambda n: stroke_icon(n, [[(.24, .24), (.76, .76)], [(.76, .24), (.24, .76)]], .12)),
    ('lock', 18, lock),
    ('bars1', 26, lambda n: wifi(n, 1)), ('bars2', 26, lambda n: wifi(n, 2)), ('bars3', 26, lambda n: wifi(n, 3)),
    ('check', 26, lambda n: stroke_icon(n, [[(.18, .54), (.42, .76), (.84, .26)]], .13)),
    ('plus', 30, lambda n: stroke_icon(n, [[(.5, .18), (.5, .82)], [(.18, .5), (.82, .5)]], .13)),
    ('minus', 30, lambda n: stroke_icon(n, [[(.18, .5), (.82, .5)]], .13)),
    ('backspace', 26, lambda n: stroke_icon(n, [[(.92, .24), (.36, .24), (.08, .5), (.36, .76), (.92, .76), (.92, .24)],
                                                [(.50, .38), (.74, .62)], [(.74, .38), (.50, .62)]], .08)),
    ('shift', 26, lambda n: stroke_icon(n, [[(.5, .12), (.12, .52), (.32, .52), (.32, .86), (.68, .86), (.68, .52),
                                            (.88, .52), (.5, .12)]], .08)),
    ('pikachu', 44, pikachu),
    ('finfur', 44, finfur),
]


def c_bytes(data):
    return ','.join(str(b) for b in data)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    out = ['/* Generated by tools/build_ui_assets.py. Do not edit. */', '#include "ui_draw.h"']
    fonts = []
    for name, path, size in FONTS:
        height, glyphs = font_cells(path, size)
        offs, boxes, blob = [], [], bytearray()
        for adv, data in glyphs:
            # Keep only the inked box: zero alpha draws nothing, so the render is unchanged.
            box = Image.frombytes('L', (adv, height), data).getbbox() or (0, 0, 0, 0)
            offs.append(len(blob))
            boxes.extend((box[0], box[1], box[2] - box[0], box[3] - box[1]))
            if box[2] > box[0]:
                blob.extend(Image.frombytes('L', (adv, height), data).crop(box).tobytes())
        assert len(blob) < 65536, (name, len(blob))  # offsets are 16-bit
        out.append('static const unsigned char f_%s[]={%s};' % (name, c_bytes(blob)))
        out.append('static const unsigned short f_%s_off[]={%s};' % (name, ','.join(str(o) for o in offs)))
        out.append('static const unsigned char f_%s_adv[]={%s};' % (name, ','.join(str(a) for a, _ in glyphs)))
        out.append('static const unsigned char f_%s_box[]={%s};' % (name, c_bytes(boxes)))
        fonts.append('{%d,f_%s,f_%s_off,f_%s_adv,f_%s_box}' % (height, name, name, name, name))
    icons = []
    for name, n, fn in ICONS:
        out.append('static const unsigned char i_%s[]={%s};' % (name, c_bytes(fn(n))))
        icons.append('{%d,i_%s}' % (n, name))
    out.append('const struct ui_font_data ui_fonts[UI_FONTS]={%s};' % ','.join(fonts))
    out.append('const struct ui_icon_data ui_icons[UI_ICONS]={%s};' % ','.join(icons))
    (OUT / 'ui_assets.c').write_text('\n'.join(out) + '\n')
    print('ui assets: %d fonts, %d icons -> %s' % (len(fonts), len(icons), OUT / 'ui_assets.c'))


if __name__ == '__main__':
    main()
