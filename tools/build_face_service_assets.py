"""Render a finite immutable mood/blink bank using actual ParamFace. Local only."""
import copy,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from cammy_face import FaceRenderer,preset,EXPRESSIONS
from tools.native_image_abi import bgra_preview

CHOOSER_SPLIT_Y=200  # native_service: a tap above this is Normal, below is CammyOS
FONT='/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf'

SLEEP_FRAMES,SLEEP_PERIOD=8,3.5  # native: CFS_SLEEP_FRAMES, CFS_SLEEP_PERIOD_MS

def sleep_loop(original):
    """Asleep until an agent connects (Adam 2026-11-16): one ParamFace breath period with
    eyes closed, plus a small 'z' drifting up and fading."""
    from PIL import Image,ImageDraw,ImageFont
    doc=copy.deepcopy(original)
    doc['animation']={'blink':{'interval':4000,'duration':150},'saccade':{'interval':4000,'amplitude':0},'breath':{'period':SLEEP_PERIOD,'depth':0.4}}
    for value in doc['expressions'].values():value.pop('animation',None)
    for parts in [doc['parts'],*[v.get('parts',{}) for v in doc['expressions'].values()]]:
        for key in ['eyeL','eyeR']:
            value=parts.get(key,{})
            if 'closed' in value.get('frames',{}):value['frames']['open']=value['frames']['closed']
    r=FaceRenderer();shots=[]
    try:
        r.load(json.dumps(doc).encode());r.set_expression('sleepy');r.drive(mouth=0,gaze=(0,0))
        for _ in range(30):r.frame(33.333)
        step=SLEEP_PERIOD*1000/SLEEP_FRAMES
        for _ in range(SLEEP_FRAMES):r.frame(step/2);shots.append(r.frame(step/2))
    finally:r.close()
    font=ImageFont.truetype(FONT,22);out=bytearray()
    for i,rgb in enumerate(shots):
        im=Image.frombytes('RGB',(356,400),rgb);d=ImageDraw.Draw(im);t=i/SLEEP_FRAMES
        a=int(255*(1-t)) if t>0.5 else 255
        if a>40:d.text((268+int(10*t),150-int(46*t)),'z',font=font,fill=(a,a,a),stroke_width=2,stroke_fill=(24,26,32))
        out.extend(bgra_preview(im.tobytes()))
    return bytes(out)

def chooser(face_rgb,focus=0,crop=(28,40,328,370),label='Pikachu companion'):
    """Boot chooser (Adam 2026-11-16): a simple half split, Normal over CammyOS."""
    from PIL import Image,ImageDraw,ImageFont
    W,H,M,R=356,400,14,26
    img=Image.new('RGB',(W,H),(24,26,32));d=ImageDraw.Draw(img)
    big=ImageFont.truetype(FONT,36);small=ImageFont.truetype(FONT.replace('-B.','-M.'),17)
    top=(M,M,W-M,CHOOSER_SPLIT_Y-6);bottom=(M,CHOOSER_SPLIT_Y+6,W-M,H-M)
    d.rounded_rectangle(top,R,fill=(52,57,68));d.rounded_rectangle(bottom,R,fill=(247,212,60))
    # Normal: a small camera glyph
    cx,cy=80,(top[1]+top[3])//2
    d.rounded_rectangle((cx-34,cy-22,cx+34,cy+26),10,fill=(214,218,226));d.rounded_rectangle((cx-14,cy-31,cx+10,cy-20),4,fill=(214,218,226))
    d.ellipse((cx-17,cy-15,cx+17,cy+19),fill=(52,57,68));d.ellipse((cx-10,cy-8,cx+10,cy+12),fill=(150,158,172))
    d.text((134,cy-26),'Normal',font=big,fill=(255,255,255));d.text((136,cy+16),'Stock camera',font=small,fill=(180,186,198))
    # CammyOS: a little copy of the active pack's real face
    face=Image.frombytes('RGB',(W,H),face_rgb).crop(crop).resize((104,114),Image.LANCZOS)
    by=(bottom[1]+bottom[3])//2
    mask=Image.new('L',face.size,0);ImageDraw.Draw(mask).rounded_rectangle((0,0,face.size[0]-1,face.size[1]-1),18,fill=255)
    img.paste(face,(26,by-57),mask)
    d.text((140,by-26),'CammyOS',font=big,fill=(36,34,30));d.text((142,by+16),label,font=small,fill=(110,86,10))
    # Button focus (up/down arrows, OK picks): a white ring around the focused card.
    if focus:
        box=top if focus==1 else bottom
        d.rounded_rectangle((box[0]-6,box[1]-6,box[2]+6,box[3]+6),R+6,outline=(255,255,255),width=5)
    return img.tobytes()

LOADING_BG=(0x11,0x13,0x18)  # native_service CFS_LOADING_BG_*, cammy-ui C_BG
LOADING_YELLOW,LOADING_TEXT,LOADING_DIM=(0xff,0xcc,0x1f),(0xf4,0xf5,0xf7),(0x9a,0xa1,0xad)

def loading_frame():
    """The face library's built-in Loading screen: shown from the CammyOS pick until
    cammy-ui draws the detailed startup status. Returns (alpha plane, row colours, RGB
    preview): one foreground colour per row over LOADING_BG, so the library expands it
    with no colour data per pixel (142 KB instead of a 570 KB frame)."""
    from PIL import Image,ImageDraw,ImageFont
    W,H=356,400;layers=[]
    def layer(colour,draw):
        m=Image.new('L',(W,H),0);draw(ImageDraw.Draw(m));layers.append((colour,m))
    big,body,small=ImageFont.truetype(FONT,46),ImageFont.truetype(FONT,26),ImageFont.truetype(FONT,18)
    def centre(d,y,text,font):
        w=d.textlength(text,font=font);d.text(((W-w)/2,y),text,font=font,fill=255)
    layer(LOADING_YELLOW,lambda d:centre(d,110,'CammyOS',big))
    layer(LOADING_TEXT,lambda d:centre(d,196,'Loading',body))
    layer(LOADING_YELLOW,lambda d:[d.ellipse((150+i*22,258,162+i*22,270),fill=255) for i in range(3)])
    layer(LOADING_DIM,lambda d:(centre(d,318,'Cammy is starting up.',small),centre(d,344,'This can take a minute or two.',small)))
    alpha=bytearray(W*H);rows=bytearray(H*4);colour=[None]*H
    for c,m in layers:
        data=m.tobytes()
        for y in range(H):
            line=data[y*W:(y+1)*W]
            if not any(line):continue
            if colour[y] not in (None,c):raise ValueError('loading frame row %d has two colours'%y)
            colour[y]=c;alpha[y*W:(y+1)*W]=bytes(max(a,b) for a,b in zip(alpha[y*W:(y+1)*W],line))
    for y in range(H):
        r,g,b=colour[y] or LOADING_TEXT;rows[y*4:y*4+4]=bytes((b,g,r,0))
    preview=Image.new('RGB',(W,H),LOADING_BG);px=preview.load()
    for y in range(H):
        r,g,b=colour[y] or LOADING_TEXT
        for x in range(W):
            k=alpha[y*W+x]
            if k:px[x,y]=tuple((bg*(255-k)+fg*k+127)//255 for bg,fg in zip(LOADING_BG,(r,g,b)))
    return bytes(alpha),bytes(rows),preview

def speaking_frame(rgb, left, compact=False):
    """An empty speech balloon in the bottom corner, below the facial features."""
    from PIL import Image, ImageDraw
    image = Image.frombytes('RGB', (356, 400), rgb)
    draw = ImageDraw.Draw(image)
    x = 12 if left else (292 if compact else 266)
    top = 369 if compact else 353
    width = 55 if compact else 77
    ink, paper = (44, 38, 53), (255, 251, 234)
    draw.rounded_rectangle((x, top, x + width, 394), 17, fill=paper, outline=ink, width=3)
    tip = x + (66 if left else 11)
    draw.polygon([(tip - 7, top + 3), (tip, top - 11), (tip + 6, top + 3)], fill=paper)
    draw.line([(tip - 7, top + 1), (tip, top - 11), (tip + 6, top + 1)], fill=ink, width=3)
    return bgra_preview(image.tobytes())


def fin_fur(out):
    """Fin and Fur pack (Adam 2026-11-16): 12 mood/blink frames with Fin in front, the same 12
    with Fur in front (idle turns), the night sleep loop and the pack's chooser."""
    from tools import fin_fur_art as art
    bank=bytearray();frames=[]
    for front in art.FRONT:
        for mood in EXPRESSIONS:
            for closed in [False,True]:
                frame=bgra_preview(art.scene(mood,closed,front).tobytes());bank.extend(frame)
                frames.append(dict(front=front,expression=mood,closed=closed,sha256=hashlib.sha256(frame).hexdigest()))
    talk = b''.join(speaking_frame(art.scene('neutral', False, front, talking=opened).tobytes(),
                                  front == 'fin') for front in art.FRONT for opened in (False, True))
    (out/'finfur-talk.bgra').write_bytes(talk)
    sleep=b''.join(bgra_preview(f.tobytes()) for f in art.sleep_frames(SLEEP_FRAMES))
    neutral=art.scene('neutral',False,'fin').tobytes()
    menu=b''.join(bgra_preview(chooser(neutral,f,crop=(0,10,356,400),label='Fin and Fur companions')) for f in range(3))
    for name,data in (('finfur.bgra',bank),('finfur-sleep.bgra',sleep),('finfur-chooser.bgra',menu)):(out/name).write_bytes(data)
    return dict(turns=2,frames=frames,sha256=hashlib.sha256(bank).hexdigest(),sleep_sha256=hashlib.sha256(sleep).hexdigest(),
                chooser_sha256=hashlib.sha256(menu).hexdigest(),art_sha256=hashlib.sha256((ROOT/'tools/fin_fur_art.py').read_bytes()).hexdigest())

CHILD_FILES = (
    'frames.bgra', 'talk.bgra', 'sleep.bgra', 'chooser.bgra',
    'finfur.bgra', 'finfur-talk.bgra', 'finfur-sleep.bgra', 'finfur-chooser.bgra',
)


def child_bank(out):
    from tools.cammy_pack import encode_planes

    size = 569600
    starts, mapping, frames, planes, seen = {}, [], [], [], {}
    for name in CHILD_FILES:
        group = name.removesuffix('.bgra').replace('-', '_').upper()
        if group == 'FRAMES':
            group = 'FRAMES_START'
        raw = (out / name).read_bytes()
        starts[group] = len(mapping)
        for i in range(len(raw) // size):
            if (group in ('FRAMES_START', 'FINFUR') and i % 12 >= 10 or
                    group.endswith('CHOOSER') and i == 0):
                mapping.append(None)
                continue
            frame = raw[i * size:(i + 1) * size]
            index = seen.get(frame)
            if index is None:
                index = seen[frame] = len(planes)
                if frame[3::4] != b'\xff' * (size // 4):
                    raise ValueError('CPK1 child frames must be opaque')
                plane = bytearray(size // 4 * 3)
                for channel in range(3):
                    plane[channel::3] = frame[channel::4]
                planes.append(bytes(plane))
                frames.append(dict(file=name, frame=i, sha256=hashlib.sha256(frame).hexdigest()))
            mapping.append(index)
    if len(mapping) != 64:
        raise ValueError('Child frame count differs from the native frame table')
    return encode_planes(planes), starts, mapping, frames


def frame_indexes(out, starts, mapping, child_count, loading_size):
    from tools.cammy_pack import FRAMES

    values = ', '.join(str(i) + 'u' if i is not None else 'CPK_NONE' for i in mapping)
    lines = [f'#define CHILD_{name} {start}u' for name, start in starts.items()]
    lines += [
        f'#define CHILD_FRAMES {child_count}u',
        f'#define LOADING_PACKED_BYTES {loading_size}u',
        f'#define ADULT_BLINK {FRAMES.index("blink")}u',
        f'#define ADULT_TALK {FRAMES.index("talk-closed")}u',
        f'#define ADULT_SLEEP {FRAMES.index("sleep-0")}u',
        f'_Static_assert(CPK_FRAMES == {len(FRAMES)}u, "adult frame count mismatch");',
        f'static const uint32_t child_frame_index[] = {{{values}}};',
    ]
    (out/'frame_indexes.h').write_text('\n'.join(lines) + '\n')


def build():
    out=ROOT/'build/face-service';out.mkdir(parents=True,exist_ok=True)
    original=json.loads(preset('pikachu'))
    base=copy.deepcopy(original)
    base['animation']={'blink':{'interval':4000,'duration':150},'saccade':{'interval':4000,'amplitude':0},'breath':{'period':3.5,'depth':0}}
    for value in base['expressions'].values():value.pop('animation',None)
    frames=[];pixels=bytearray()
    for mood in EXPRESSIONS:
        for closed in [False,True]:
            doc=copy.deepcopy(base)
            if closed:
                for parts in [doc['parts'],*[v.get('parts',{}) for v in doc['expressions'].values()]]:
                    for key in ['eyeL','eyeR']:
                        value=parts.get(key,{})
                        if 'closed' in value.get('frames',{}):value['frames']['open']=value['frames']['closed']
            r=FaceRenderer()
            try:
                r.load(json.dumps(doc).encode());r.set_expression(mood);r.drive(mouth=0,gaze=(0,0))
                for _ in range(30):rgb=r.frame(33.333)
                if mood == 'neutral' and not closed:
                    r.drive(mouth=1, gaze=(0,0))
                    for _ in range(10):
                        open_rgb = r.frame(33.333)
                    (out/'talk.bgra').write_bytes(speaking_frame(rgb, False, compact=True) + speaking_frame(open_rgb, False, compact=True))
            finally:r.close()
            if mood==EXPRESSIONS[0] and not closed:neutral_rgb=rgb
            frame=bgra_preview(rgb);pixels.extend(frame)
            frames.append(dict(expression=mood,closed=closed,sha256=hashlib.sha256(frame).hexdigest()))
    (out/'frames.bgra').write_bytes(pixels)
    menu=b''.join(bgra_preview(chooser(neutral_rgb,f)) for f in range(3));(out/'chooser.bgra').write_bytes(menu)
    sleep=sleep_loop(original);(out/'sleep.bgra').write_bytes(sleep)
    finfur=fin_fur(out)
    loading_alpha,loading_rows,loading_preview=loading_frame()
    (out/'loading.alpha').write_bytes(loading_alpha);(out/'loading.rows').write_bytes(loading_rows);loading_preview.save(out/'loading.png')
    from tools import cammy_pack
    cammy=cammy_pack.build(out)  # CCC-only pack; an empty bank when private/ inputs are absent
    child, starts, mapping, child_frames = child_bank(out)
    (out/'children.cpk').write_bytes(child)
    loading_packed = cammy_pack.lz(loading_alpha)
    frame_indexes(out, starts, mapping, len(child_frames), len(loading_packed))
    (out/'loading.lz').write_bytes(loading_packed)
    symbols=[('cfs_loading_alpha_start','loading.lz'),('cfs_loading_rows_start','loading.rows')]
    (out/'frames.S').write_text('.section .rodata\n'+''.join('.balign 4\n.global %s\n%s:\n.incbin "%s"\n'%(sym,sym,out/f) for sym,f in symbols)
        +'.balign 4\n.global cfs_child_start\ncfs_child_start:\n.incbin "%s"\n.global cfs_child_end\ncfs_child_end:\n'%(out/'children.cpk')
        +'.balign 4\n.global cfs_cammy_pack_start\ncfs_cammy_pack_start:\n.incbin "%s"\n.global cfs_cammy_pack_end\ncfs_cammy_pack_end:\n'%(out/'cammy.cpk')
        +'.section .note.GNU-stack,"",%progbits\n')
    report=dict(width=356,height=400,pixel_format='BGRA8888',frame_bytes=569600,total_bytes=len(pixels),frames=frames,sha256=hashlib.sha256(pixels).hexdigest(),skin_sha256=hashlib.sha256(preset('pikachu')).hexdigest(),immutable=True,child_packed_bytes=len(child),child_frames=child_frames,child_frame_map=mapping,hardware_verified=False,loading=dict(alpha_sha256=hashlib.sha256(loading_alpha).hexdigest(),rows_sha256=hashlib.sha256(loading_rows).hexdigest(),bytes=len(loading_packed)+len(loading_rows)),chooser=dict(sha256=hashlib.sha256(menu).hexdigest(),split_y=CHOOSER_SPLIT_Y,font=Path(FONT).name),sleep=dict(frames=SLEEP_FRAMES,period_s=SLEEP_PERIOD,sha256=hashlib.sha256(sleep).hexdigest()),packs={'pikachu':dict(turns=1),'fin-fur':finfur,'cammy':cammy})
    (out/'assets.json').write_text(json.dumps(report,indent=2)+'\n');return report
if __name__=='__main__':print(json.dumps(build(),indent=2))
