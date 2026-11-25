"""Fin and Fur face pack: pixel-art renderer (PC only, PIL). Adam, 2026-11-16.

Fin, a blue axolotl with pink gills, and Fur, an orange tabby with a blue collar and a
heart tag, side by side with a small overlap. In the idle look they take turns being in
front. Every mood of the Pikachu pack (cammy_face.EXPRESSIONS), eyes open and closed.

Shapes are drawn supersampled, reduced to a 119x134 pixel grid with each character's own
palette and a 1-pixel outline, then scaled 3x to the 356x400 screen.
"""
import math
import numpy as np
from PIL import Image, ImageDraw

W, H, PX = 119, 134, 3          # pixel grid; 3x = 357x402, cropped to 356x400
SCREEN = (356, 400)
SS = 8                          # supersampling per grid pixel
MOODS = ('neutral', 'happy', 'angry', 'sad', 'doubt', 'sleepy')
FRONT = ('fin', 'fur')

FIN = dict(body=(58, 160, 232), shade=(40, 122, 202), light=(118, 200, 245), belly=(158, 234, 223),
           gill=(226, 51, 122), gill2=(255, 128, 176), line=(23, 58, 110), blush=(247, 132, 190),
           iris=(138, 21, 80), pupil=(42, 10, 30), tail=(236, 84, 150))
FUR = dict(body=(243, 154, 46), shade=(210, 116, 24), light=(255, 189, 92), cream=(251, 227, 184),
           stripe=(196, 98, 24), line=(106, 50, 12), ear=(242, 160, 160), nose=(240, 128, 150),
           iris=(47, 111, 208), pupil=(16, 32, 74), collar=(47, 101, 216), gold=(240, 196, 64),
           heart=(216, 48, 74))
WHITE, MOUTH, TONGUE, TEAR = (255, 255, 255), (122, 26, 58), (255, 122, 154), (120, 200, 255)


class Pen:
    """Draws in grid units around an anchor (ox, oy) at scale k; remembers its colours."""

    def __init__(self, ox, oy, k=1.0):
        self.img = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.ox, self.oy, self.k, self.colours = ox, oy, k, set()

    def p(self, x, y):
        return ((self.ox + x * self.k) * SS, (self.oy + y * self.k) * SS)

    def _c(self, c):
        self.colours.add(c[:3])
        return c

    def ell(self, cx, cy, rx, ry, fill):
        (x0, y0), (x1, y1) = self.p(cx - rx, cy - ry), self.p(cx + rx, cy + ry)
        self.d.ellipse((x0, y0, x1, y1), fill=self._c(fill))

    def poly(self, pts, fill):
        self.d.polygon([self.p(x, y) for x, y in pts], fill=self._c(fill))

    def line(self, pts, fill, w=1.0):
        pts = [self.p(x, y) for x, y in pts]
        r = w * self.k * SS / 2
        self.d.line(pts, fill=self._c(fill), width=max(1, int(round(2 * r))))
        for x, y in pts:   # round joints and caps
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def curve(self, f, x0, x1, fill, w=1.0, n=16):
        self.line([(x0 + (x1 - x0) * i / n, f(x0 + (x1 - x0) * i / n)) for i in range(n + 1)], fill, w)


def pixelate(pen, outline):
    """Supersampled layer -> grid RGBA: hard alpha, palette colours, 1-pixel outline."""
    small = pen.img.resize((W, H), Image.BOX)
    pal = sorted(pen.colours)
    out = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    src, dst = small.load(), out.load()
    mask = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r, g, b, a = src[x, y]
            if a < 128:
                continue
            r, g, b = (r * 255 // a, g * 255 // a, b * 255 // a)
            dst[x, y] = min(pal, key=lambda c: (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2) + (255,)
            mask[y][x] = True
    for y in range(H):
        for x in range(W):
            if not mask[y][x] and any(0 <= x + dx < W and 0 <= y + dy < H and mask[y + dy][x + dx]
                                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                dst[x, y] = outline + (255,)
    return out


# ---------------------------------------------------------------- shared features

def eye(pen, cx, cy, rx, ry, c, kind, side, gaze=(0, 0), lid=None):
    """kind: open | closed | happy | half | narrow | sad. side -1 viewer's left, +1 right."""
    line = c['line']
    if kind == 'closed':      # relaxed closed eye: a soft downward curve
        pen.curve(lambda x: cy + 1.2 * (1 - ((x - cx) / rx) ** 2), cx - rx, cx + rx, line, 1.3)
        return
    if kind == 'happy':       # ^ arcs
        pen.curve(lambda x: cy + 1 - 3.2 * (1 - ((x - cx) / rx) ** 2), cx - rx, cx + rx, line, 1.4)
        return
    gx, gy = gaze
    pen.ell(cx, cy, rx + 1, ry + 1, line)
    pen.ell(cx, cy, rx, ry, WHITE)
    pen.ell(cx + gx, cy + gy + 0.3, rx - 0.9, ry - 0.8, c['iris'])
    pen.ell(cx + gx, cy + gy + 0.6, rx * 0.55, ry * 0.58, c['pupil'])
    pen.ell(cx + gx - rx * 0.32, cy + gy - ry * 0.38, 1.6, 1.8, WHITE)
    pen.ell(cx + gx + rx * 0.35, cy + gy + ry * 0.35, 0.9, 0.9, WHITE)
    skin = lid or c['body']
    if kind == 'half':        # sleepy: upper half covered by the lid
        pen.poly([(cx - rx - 2, cy - ry - 3), (cx + rx + 2, cy - ry - 3), (cx + rx + 2, cy - 0.5), (cx - rx - 2, cy - 0.5)], skin)
        pen.line([(cx - rx - 0.5, cy - 0.5), (cx + rx + 0.5, cy - 0.5)], line, 1.2)
    elif kind == 'narrow':    # angry: lid slanting down towards the nose
        inner, outer = cy - ry * 0.05, cy - ry - 2
        xi, xo = cx - side * (rx + 2), cx + side * (rx + 2)
        pen.poly([(xi, inner), (xo, outer), (xo, cy - ry - 4), (xi, cy - ry - 4)], skin)
        pen.line([(cx - side * (rx + 0.5), inner + 0.3), (cx + side * (rx + 0.5), outer + 1.6)], line, 1.3)
    elif kind == 'sad':       # lid slanting down away from the nose
        inner, outer = cy - ry - 2, cy - ry * 0.2
        xi, xo = cx - side * (rx + 2), cx + side * (rx + 2)
        pen.poly([(xi, inner), (xo, outer), (xo, cy - ry - 4), (xi, cy - ry - 4)], skin)
        pen.line([(cx - side * (rx + 0.3), inner + 1.4), (cx + side * (rx + 0.5), outer + 0.2)], line, 1.2)


def brow(pen, cx, cy, side, tilt, c, w=6):
    """tilt > 0: inner end low (angry); tilt < 0: inner end high (sad)."""
    xi, xo = cx - side * w / 2, cx + side * w / 2
    pen.line([(xi, cy + tilt), (xo, cy - tilt)], c['line'], 1.4)


def mouth(pen, mx, my, kind, c, w=6):
    line = c['line']
    if kind == 'smile':
        pen.curve(lambda x: my + 2.6 * (1 - ((x - mx) / w) ** 2), mx - w, mx + w, line, 1.2)
    elif kind in ('open', 'yawn'):
        if kind == 'open':
            top, depth, half = my - 0.5, 7.5, w + 1.5
            pts = [(mx + half * math.cos(t), top + depth * math.sin(t)) for t in [i * math.pi / 14 for i in range(15)]]
        else:
            pts = [(mx + 3.2 * math.cos(t), my + 3 + 4.2 * math.sin(t)) for t in [i * math.pi / 10 for i in range(20)]]
        pen.poly(pts, line)
        inner = [(mx + (x - mx) * 0.84, my + (y - my) * 0.84 + (0.3 if kind == 'open' else 0.5)) for x, y in pts]
        pen.poly(inner, MOUTH)
        ty = max(y for _, y in inner)
        pen.ell(mx, ty - 2, 3.4 if kind == 'open' else 2.2, 2.2, TONGUE)
    elif kind == 'frown':
        pen.curve(lambda x: my + 2.4 - 2.4 * (1 - ((x - mx) / (w - 1)) ** 2), mx - w + 1, mx + w - 1, line, 1.2)
    elif kind == 'grit':
        pen.poly([(mx - w, my - 1.6), (mx + w, my - 1.6), (mx + w - 1, my + 2.6), (mx - w + 1, my + 2.6)], line)
        pen.poly([(mx - w + 1, my - 0.8), (mx + w - 1, my - 0.8), (mx + w - 1.6, my + 1.8), (mx - w + 1.6, my + 1.8)], WHITE)
        pen.line([(mx - w + 1.5, my + 0.5), (mx + w - 1.5, my + 0.5)], line, 0.7)
    elif kind == 'wavy':
        pen.curve(lambda x: my + 1 + 1.1 * math.sin((x - mx) * 1.2), mx - w + 1, mx + w - 1, line, 1.2)
    elif kind == 'small':
        pen.ell(mx, my + 1.2, 2, 1.6, line)
        pen.ell(mx, my + 1.3, 1.1, 0.8, MOUTH)


def tear(pen, x, y):
    pen.poly([(x, y - 3), (x + 1.8, y + 0.6), (x - 1.8, y + 0.6)], TEAR)
    pen.ell(x, y + 0.8, 1.8, 1.8, TEAR)
    pen.ell(x - 0.5, y + 0.4, 0.5, 0.6, WHITE)


def vein(pen, x, y, c=(232, 40, 60)):
    pen.line([(x - 1, y - 3), (x - 1, y - 1), (x - 3, y - 1)], c, 1.1)
    pen.line([(x + 1, y - 3), (x + 1, y - 1), (x + 3, y - 1)], c, 1.1)
    pen.line([(x - 1, y + 3), (x - 1, y + 1), (x - 3, y + 1)], c, 1.1)
    pen.line([(x + 1, y + 3), (x + 1, y + 1), (x + 3, y + 1)], c, 1.1)


def question(pen, x, y, c):
    pen.curve(lambda u: y - 2.4 - math.sqrt(max(0, 6.25 - (u - x) ** 2)) * 0.9, x - 2.5, x + 2.5, c, 1.4, 10)
    pen.line([(x + 2.5, y - 2.4), (x + 1.8, y - 0.6), (x, y + 0.4), (x, y + 1.6)], c, 1.4)
    pen.ell(x, y + 4, 0.9, 0.9, c)


# ---------------------------------------------------------------- the characters
# Each pose: eyes, brows (tilt or None), mouth, extras. side=-1 Fin (left), +1 Fur (right).

POSE = {
    'neutral': dict(eyes='open', brows=None, mouth='smile', gaze=(0, 0)),
    'happy': dict(eyes='open', closed='happy', brows=None, mouth='open', gaze=(0, -0.3)),
    'angry': dict(eyes='narrow', brows=1.6, mouth='grit', gaze=(0, 0.4)),
    'sad': dict(eyes='sad', brows=-1.6, mouth='frown', gaze=(0, 0.6)),
    'doubt': dict(eyes='open', brows='doubt', mouth='wavy', gaze=None),
    'sleepy': dict(eyes='half', brows=None, mouth='small', gaze=(0, 0.6)),
}


def eyes_for(mood, closed, which):
    pose = POSE[mood]
    if closed:
        return pose.get('closed', 'closed')
    if mood == 'doubt':   # one eye squints, the other looks at the friend
        return ('narrow', 'open') if which == 'fin' else ('open', 'narrow')
    return pose['eyes'], pose['eyes']


def gaze_for(mood, which):
    g = POSE[mood]['gaze']
    if g is None:         # doubt: a sideways look at the other one
        return (1.4, 0) if which == 'fin' else (-1.4, 0)
    return g


# ---------------------------------------------------------------- bodies (chibi: big head, small body)
# Arm poses: (elbow, hand) for the arm on side s (-1 viewer's left, +1 right), around the
# head centre. Each mood gives each character a pose; their outer arms do the waving.
ARM = {
    'down':  ((17, 31), (18, 40)),
    'wave':  ((24, 22), (29, 11)),
    'hip':   ((24, 31), (15, 37)),
    'front': ((15, 34), (5, 41)),
    'chin':  ((17, 33), (7, 17)),
    'yawn':  ((16, 30), (4, 12)),
}
ARMS = {   # mood -> (Fin's left, Fin's right), (Fur's left, Fur's right)
    'neutral': (('down', 'down'), ('down', 'down')),
    'happy':   (('wave', 'down'), ('down', 'wave')),
    'angry':   (('hip', 'hip'), ('hip', 'hip')),
    'sad':     (('front', 'front'), ('front', 'front')),
    'doubt':   (('down', 'chin'), ('chin', 'down')),
    'sleepy':  (('yawn', 'down'), ('down', 'yawn')),
    'asleep':  (('front', 'front'), ('front', 'front')),
}


def outlined_ell(pen, cx, cy, rx, ry, fill, line, w=1.0):
    pen.ell(cx, cy, rx + w, ry + w, line)
    pen.ell(cx, cy, rx, ry, fill)


def limb(pen, pts, width, fill, line, w=1.0):
    pen.line(pts, line, width + 2 * w)
    pen.line(pts, fill, width)


def arm(pen, side, pose, c, paw, hand_r=3.6):
    (ex, ey), (hx, hy) = ARM[pose]
    shoulder, elbow, hand = (side * 13, 25), (side * ex, ey), (side * hx, hy)
    limb(pen, [shoulder, elbow, hand], 6.2, c['body'], c['line'])
    outlined_ell(pen, hand[0], hand[1], hand_r, hand_r, paw, c['line'])
    return hand


def leg(pen, side, c, foot, toes):
    limb(pen, [(side * 7, 42), (side * 8, 52)], 8.5, c['body'], c['line'])
    outlined_ell(pen, side * 9.5, 55, 6.8, 3.8, foot, c['line'])
    for t in (-2.6, 0, 2.6):   # toe lines
        pen.line([(side * 9.5 + t, 53.6), (side * 9.5 + t, 55.6)], toes, 0.7)


def draw_fin(pen, mood, closed, breath=0.0, asleep=False):
    c, pose = FIN, POSE[mood]
    hb = breath * 0.7
    # tail, behind: a curl to the left with a pink fin along its top
    tail = [(-10, 46), (-18, 50), (-25, 47), (-28.5, 40), (-27.5, 33.5)]
    limb(pen, tail, 7.5, c['body'], c['line'])
    pen.line([(x, y - 3.4) for x, y in tail[1:]], c['tail'], 2.2)
    pen.line(tail[:3], c['shade'], 3)
    for side in (-1, 1):
        leg(pen, side, c, c['body'], c['shade'])
    outlined_ell(pen, 0, 33, 15.5, 17, c['body'], c['line'])
    pen.ell(4, 36, 11, 13, c['shade'])
    pen.ell(-1, 32, 13, 15, c['body'])
    pen.ell(0, 35, 9.5, 11.5, c['belly'])
    pen.ell(0, 18.5, 11, 3.2, c['shade'])                    # shadow under the chin
    arms = ARMS['asleep' if asleep else mood][0]
    late = []
    for side, p in zip((-1, 1), arms):
        if p in ('chin', 'yawn'):   # hands in front of the face: drawn after it
            late.append((side, p))
        else:
            arm(pen, side, p, c, c['body'])
    # gills: three feathery fronds a side
    spread = dict(happy=-8, angry=-14, sad=16, sleepy=10).get(mood, 0)
    for side in (-1, 1):
        for i, (ax, ay, ang, ln) in enumerate(((19, -12 + hb, -60, 10.5), (23, -4 + hb, -20, 11), (22, 5 + hb, 18, 9.5))):
            a = math.radians(ang + spread * (1 if i == 0 else 0.7))
            if side > 0:   # the side towards Fur: shorter, so Fur's eye stays clear
                ln, a = ln * 0.58, a - math.radians(14)
            x0, y0, n = side * ax, ay, 10
            pts = [(x0 + side * math.cos(a) * ln * j / n, y0 + math.sin(a) * ln * j / n) for j in range(n + 1)]
            nx, ny = -math.sin(a), math.cos(a)
            for j, (x, y) in enumerate(pts):
                if j % 2 == 0 and j > 0:
                    f = 3.8 - 1.7 * j / n
                    for sgn in (-1, 1):
                        pen.line([(x, y), (x + side * (nx * sgn * f + math.cos(a) * 1.0), y + ny * sgn * f + math.sin(a) * 1.0)],
                                 c['gill'] if j % 4 else c['gill2'], 1.8)
            for j, (x, y) in enumerate(pts):
                r = 2.9 - 1.3 * j / n
                pen.ell(x, y, r, r, c['gill'])
            for x, y in pts[2::3]:
                pen.ell(x + side * 0.4, y - 0.8, 0.8, 0.8, c['gill2'])
    # head
    outlined_ell(pen, 0, hb, 24, 19, c['body'], c['line'])
    pen.ell(2, 4 + hb, 22, 15, c['body'])
    pen.ell(-7, -11 + hb, 7.5, 3.2, c['light'])
    face(pen, 'fin', mood, closed, hb, eye_x=10, eye_y=0, mouth_y=9)
    for side in (-1, 1):
        pen.ell(side * 16.5, 7.5 + hb, 4, 2, c['blush'])
    for side, p in late:
        arm(pen, side, p, c, c['body'])
    if mood == 'angry':
        vein(pen, -16, -12 + hb)


def draw_fur(pen, mood, closed, breath=0.0, asleep=False):
    c, pose = FUR, POSE[mood]
    hb = breath * 0.7
    # tail, behind: curling up on the right, striped
    tail = [(10, 47), (19, 47), (25, 40), (26.5, 31), (23.5, 24.5)]
    limb(pen, tail, 6.5, c['body'], c['line'])
    for x, y in tail[1:4]:
        pen.ell(x + 0.5, y, 2.4, 2.4, c['stripe'])
    pen.ell(23.5, 25, 3, 3, c['cream'])
    for side in (-1, 1):
        leg(pen, side, c, c['cream'], c['stripe'])
    outlined_ell(pen, 0, 33, 15.5, 17, c['body'], c['line'])
    pen.ell(-4, 36, 11, 13, c['shade'])
    pen.ell(1, 32, 13, 15, c['body'])
    for i in range(3):   # side stripes
        pen.line([(9.5, 30 + i * 6), (14, 28 + i * 6)], c['stripe'], 1.4)
        pen.line([(-9.5, 30 + i * 6), (-14, 28 + i * 6)], c['stripe'], 1.4)
    pen.ell(0, 34, 8.5, 12, c['cream'])
    pen.ell(0, 18.5, 11, 3.2, c['shade'])
    arms = ARMS['asleep' if asleep else mood][1]
    late = []
    for side, p in zip((-1, 1), arms):
        if p in ('chin', 'yawn'):
            late.append((side, p))
        else:
            arm(pen, side, p, c, c['cream'])
    # ears
    tilt = dict(angry=(28, 28), sad=(36, 36), doubt=(0, 22), sleepy=(14, 14)).get(mood, (0, 0))
    for side, deg in zip((-1, 1), tilt):
        base = (side * 13, -13 + hb)
        a = math.radians(deg) * side

        def rot(x, y, base=base, a=a):
            x, y = x - base[0], y - base[1] + hb
            return (base[0] + x * math.cos(a) - y * math.sin(a), base[1] + x * math.sin(a) + y * math.cos(a))
        ear = [rot(side * 23, -5), rot(side * 19, -31), rot(side * 3, -16)]
        pen.poly([(x + side * 0.0, y) for x, y in ear], c['line'])
        pen.poly([rot(side * 21.8, -6), rot(side * 18.6, -29.3), rot(side * 4.6, -16.4)], c['body'])
        pen.poly([rot(side * 19, -9), rot(side * 17.6, -25.5), rot(side * 8.5, -16)], c['ear'])
    # head with cheek tufts
    for side in (-1, 1):
        pen.poly([(side * 20, 2 + hb), (side * 28.2, 9.4 + hb), (side * 22.2, 9.6 + hb), (side * 26, 14.4 + hb), (side * 15, 16.4 + hb)], c['line'])
    outlined_ell(pen, 0, hb, 23, 19, c['body'], c['line'])
    for side in (-1, 1):
        pen.poly([(side * 20, 2 + hb), (side * 27, 9.5 + hb), (side * 21.5, 9.5 + hb), (side * 24.8, 13.8 + hb), (side * 15, 15.6 + hb)], c['body'])
    pen.ell(-6, -12 + hb, 6.5, 3, c['light'])
    for x, top, bot in ((-5, -18.5, -11.5), (0, -19, -10), (5, -18.5, -11.5)):
        pen.poly([(x - 1.9, top + hb), (x + 1.9, top + hb), (x, bot + hb)], c['stripe'])
    pen.ell(0, 12.5 + hb, 7.5, 5, c['cream'])
    pen.ell(-4.2, 9.5 + hb, 5.4, 4.2, c['cream'])
    pen.ell(4.2, 9.5 + hb, 5.4, 4.2, c['cream'])
    face(pen, 'fur', mood, closed, hb, eye_x=9.5, eye_y=-1, mouth_y=9.5)
    for side, p in late:
        arm(pen, side, p, c, c['cream'])
    if mood == 'angry':
        vein(pen, 16, -13 + hb)
    # collar and heart tag
    pen.curve(lambda x: 19.5 + 2.4 * (1 - (x / 13) ** 2), -13, 13, c['line'], 4.4)
    pen.curve(lambda x: 19.5 + 2.4 * (1 - (x / 13) ** 2), -13, 13, c['collar'], 2.8)
    pen.ell(0, 23.6, 1.2, 1.2, c['gold'])
    pen.ell(-1.4, 26, 1.9, 1.9, c['heart'])
    pen.ell(1.4, 26, 1.9, 1.9, c['heart'])
    pen.poly([(-3.2, 26.5), (3.2, 26.5), (0, 29.8)], c['heart'])
    pen.ell(-1.6, 25.6, 0.5, 0.5, WHITE)


def face(pen, which, mood, closed, hb, eye_x, eye_y, mouth_y):
    c, pose = (FIN if which == 'fin' else FUR), POSE[mood]
    kinds = eyes_for(mood, closed, which)
    g = gaze_for(mood, which)
    for side, kind in zip((-1, 1), kinds if isinstance(kinds, tuple) else (kinds, kinds)):
        eye(pen, side * eye_x, eye_y + hb, 5.4, 6.2, c, kind, side, g)
    b = pose['brows']
    if b == 'doubt':
        lo, hi = (-1, 1) if which == 'fin' else (1, -1)
        brow(pen, lo * eye_x, eye_y - 9 + hb, lo, 0.9, c, 5)
        brow(pen, hi * eye_x, eye_y - 11.5 + hb, hi, -1.2, c, 5)
    elif b is not None:
        for side in (-1, 1):
            brow(pen, side * eye_x, eye_y - 9.5 + hb, side, b * 0.85, c, 5)
    if which == 'fur':
        pen.poly([(-1.9, 6 + hb), (1.9, 6 + hb), (0, 8 + hb)], c['nose'])
        m = 'open' if pen.talking else pose['mouth']
        if m == 'smile':   # cat mouth: a little w under the nose
            pen.line([(0, 8 + hb), (0, 9.2 + hb)], c['line'], 0.9)
            pen.curve(lambda x: 9.2 + hb + 1.4 * math.sin(-x / 2.8 * math.pi), -2.8, 0, c['line'], 1.0, 8)
            pen.curve(lambda x: 9.2 + hb + 1.4 * math.sin(x / 2.8 * math.pi), 0, 2.8, c['line'], 1.0, 8)
        else:
            pen.line([(0, 8 + hb), (0, 9 + hb)], c['line'], 0.9)
            mouth(pen, 0, mouth_y + 0.6 + hb, m, c, 4.2)
        for side in (-1, 1):   # whiskers
            for dy, sl in ((-0.5, -1.0), (1.8, 0.5)):
                pen.line([(side * 8.5, 9.5 + dy + hb), (side * 20.5, 9.5 + dy + sl * 2 + hb)], WHITE, 0.55)
    else:
        mouth(pen, 0, mouth_y + hb, 'open' if pen.talking else pose['mouth'], c, 5)
    if mood == 'sad':
        tear(pen, (-14 if which == 'fin' else 14), (7 if not closed else 5) + hb)


# ---------------------------------------------------------------- scene

BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def gradient(stops, y):
    t = y / (H - 1) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    return [tuple(int(a + (b - a) * f) for a, b in zip(stops[i], stops[i + 1]))][0]


def background(night=False, twinkle=0):
    img = Image.new('RGB', (W, H))
    px = img.load()
    bands = ([(24, 30, 64), (40, 52, 104), (64, 70, 128)] if night else
             [(255, 236, 170), (252, 222, 160), (214, 236, 176)])
    hills = [(42, 66, 84), (34, 56, 70)] if night else [(150, 210, 120), (118, 192, 104)]
    for y in range(H):
        for x in range(W):
            # dithered bands: 6 steps between the stops
            t = y / (H - 1) * 6
            lo = int(t)
            f = t - lo
            col_lo, col_hi = gradient(bands, lo / 6 * (H - 1)), gradient(bands, min(6, lo + 1) / 6 * (H - 1))
            px[x, y] = col_hi if f * 16 > BAYER[y % 4][x % 4] else col_lo
            h1 = 92 + 6 * math.sin(x / 19 + 1.0)
            h2 = 102 + 5 * math.sin(x / 13 + 3.0)
            if y > h2:
                px[x, y] = hills[1]
            elif y > h1:
                px[x, y] = hills[0]
    d = ImageDraw.Draw(img)
    stars = ((10, 9), (30, 18), (58, 6), (88, 14), (110, 7), (100, 26), (6, 30), (70, 24))
    for i, (x, y) in enumerate(stars if night else stars[:4]):
        on = not night or (i + twinkle) % 3 != 0
        col = (255, 250, 210) if night else (255, 255, 236)
        if on:
            d.point((x, y), fill=col)
            if (i % 2 == 0) or not night:
                d.point([(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)], fill=col)
    return img


# Placement (grid units). The front one sits lower and a touch bigger.
PLACE = {'fin': (36, 44), 'fur': (84, 44)}
FRONT_K, BACK_K, BACK_LIFT = 1.1, 1.03, -3


def character(which, front, mood, closed, breath=0.0, asleep=False, talking=False):
    x, y = PLACE[which]
    k = FRONT_K if front else BACK_K
    pen = Pen(x, y + (0 if front else BACK_LIFT), k)
    pen.talking = talking
    (draw_fin if which == 'fin' else draw_fur)(pen, mood, closed, breath, asleep)
    layer = pixelate(pen, FIN['line'] if which == 'fin' else FUR['line'])
    if not front:   # slightly dimmer behind
        r, g, b, a = layer.split()
        layer = Image.merge('RGBA', [ch.point(lambda v: int(v * 0.9)) for ch in (r, g, b)] + [a])
    return layer


def scene(mood, closed, front='fin', breath=(0.0, 0.0), night=False, twinkle=0, extra=None, asleep=False, talking=False):
    """One 356x400 RGB screen. front: 'fin' or 'fur' (which is drawn in front)."""
    img = background(night, twinkle).convert('RGBA')
    back = 'fur' if front == 'fin' else 'fin'
    bi = FRONT.index(back)
    img.alpha_composite(character(back, False, mood, closed, breath[bi], asleep))
    img.alpha_composite(character(front, True, mood, closed, breath[1 - bi], asleep, talking))
    if extra:
        extra(img)
    a = np.asarray(img.convert('RGB')).repeat(PX, 0).repeat(PX, 1)  # Pillow 12.1 NEAREST resize fails
    big = Image.fromarray(a)
    return big.crop((0, 1, SCREEN[0], 1 + SCREEN[1]))


def zzz(phase):
    """Two small z's drifting up over the sleepers (grid units), phase 0..1."""
    def draw(img):
        d = ImageDraw.Draw(img)
        for i, (x0, y0) in enumerate(((52, 22), (64, 14))):
            t = (phase + i * 0.5) % 1.0
            x, y, s = x0 + 6 * t, y0 - 10 * t, 3 + i
            a = 255 if t < 0.6 else int(255 * (1 - (t - 0.6) / 0.4))
            col = (255, 255, 255, a)
            d.line([(x, y), (x + s, y), (x, y + s), (x + s, y + s)], fill=col, width=1)
    return draw


def sleep_frames(n=8):
    """Both asleep under a night sky, breathing out of step, z's drifting up."""
    out = []
    for i in range(n):
        t = i / n
        breath = (math.sin(2 * math.pi * t), math.sin(2 * math.pi * t + 1.4))
        out.append(scene('sleepy', True, 'fin', breath=tuple(0.8 * b for b in breath), night=True,
                         twinkle=i // 3, extra=zzz(t), asleep=True))
    return out
