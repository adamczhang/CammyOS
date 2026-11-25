"""Cammy face pack (stable ID "cammy", pack index 2): CCC-only, never a boot or physical
choice. PC only (PIL). Adam, 2026-11-17.

Inputs (kept in private/, never copied into git):
  assets/faces/cammy-adult-atlas.png  3 x 3 cells, row-major:
      neutral   happy         surprise
      doubt     sad           sleepy
      blink     speech-closed speech-open
  assets/faces/cammy-adult-sleep.png  one tile: the approved Z-eyed sleeping pose,
      the base of the eight-frame sleep loop.
Any square-ish cell size works; each cell is scaled to 400 px tall and centre-cropped to
356 px wide (1254 x 1254 atlas: 418 px cells, 22 px trimmed from each side).

Output: one CPK1 blob (native/face_service/face_pack_codec.h) holding 16 frames
losslessly. The native decoder rebuilds BGRA8888 exactly as bgra_preview() makes it.
"""
import hashlib,struct
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ATLAS=ROOT/'assets/faces/cammy-adult-atlas.png'
SLEEP=ROOT/'assets/faces/cammy-adult-sleep.png'
W,H=356,400
FRAME_BGR=W*H*3
CELLS=('neutral','happy','surprise','doubt','sad','sleepy','blink','speech-closed','speech-open')
# Engine moods (cammy_face.EXPRESSIONS / CFS order) -> atlas cell. Mood 2 is "angry" in the
# control API; this pack shows its surprise cell there.
MOOD_CELLS=('neutral','happy','surprise','sad','doubt')
SLEEP_FRAMES,SLEEP_PERIOD=8,3.5          # CFS_SLEEP_FRAMES, CFS_SLEEP_PERIOD_MS
# Blob frame order (face_pack_codec.h CPK_*): 5 awake moods, blink, 2 talk, 8 sleep.
FRAMES=MOOD_CELLS+('blink','talk-closed','talk-open')+tuple('sleep-%d'%i for i in range(SLEEP_FRAMES))
MAGIC,VERSION,MAX_FRAMES=0x314b5043,1,32
F_KEY_SUB,F_KEY_UP,F_DELTA=1,2,3
NONE=0xffffffff
# Library budget: 40 MiB (tools/rc06_build.py) minus the 0.7.10 library (36,471,508 bytes)
# leaves 5.2 MiB; the pack also needs 1.49 MiB of decode buffers in sdvcam, so it may use
# at most 3.5 MiB of the rest. The build fails beyond that.
BUDGET=3_670_016

def fit(cell):
    """Cover-fit one cell to 356 x 400 (scale to 400 tall, crop the sides)."""
    from PIL import Image
    w,h=cell.size
    scale=max(W/w,H/h)
    sw,sh=max(W,round(w*scale)),max(H,round(h*scale))
    big=cell.resize((sw,sh),Image.LANCZOS)
    x,y=(sw-W)//2,(sh-H)//2
    return big.crop((x,y,x+W,y+H))

def load(atlas=ATLAS,sleep=SLEEP):
    from PIL import Image
    a=Image.open(atlas).convert('RGB');s=Image.open(sleep).convert('RGB')
    if a.width%3 or a.height%3 or min(a.size)<300:raise ValueError('atlas must be 3 x 3 equal cells, at least 300 px')
    cw,ch=a.width//3,a.height//3
    if not 0.8<=cw/ch*H/W<=1.35:raise ValueError('atlas cells %dx%d too far from the 356x400 panel'%(cw,ch))
    cells={name:fit(a.crop(((i%3)*cw,(i//3)*ch,(i%3+1)*cw,(i//3+1)*ch))) for i,name in enumerate(CELLS)}
    return cells,fit(s)

def sleep_loop(base):
    """Eight frames over one 3.5 s breath: the approved Z-eyed tile, a slow glow (whole
    picture darkened by 0-2 levels) and a small z drifting up from the head's right edge."""
    import math
    from PIL import ImageDraw,ImageFont
    from tools.build_face_service_assets import FONT
    ink=(128,246,204)
    font=ImageFont.truetype(FONT,20);out=[]
    for i in range(SLEEP_FRAMES):
        t=i/SLEEP_FRAMES
        dim=round(1-math.cos(2*math.pi*t))
        im=base.point(lambda v,d=dim:max(0,v-d))
        a=1.0 if t<0.5 else max(0.0,1-(t-0.5)*2)
        if a>0.15:
            bg=im.getpixel((316,92))
            fill=tuple(round(bg[k]+(ink[k]-bg[k])*a) for k in range(3))
            ImageDraw.Draw(im).text((300+int(12*t),96-int(44*t)),'z',font=font,fill=fill)
        out.append(im)
    return out

def frames(atlas=ATLAS,sleep=SLEEP):
    """The 16 RGB images in blob order."""
    from tools.build_face_service_assets import speaking_frame
    from PIL import Image
    cells,base=load(atlas,sleep)
    def talk(name):  # the balloon from the other packs, bottom right, below the mouth
        bgra=speaking_frame(cells[name].tobytes(),False,compact=True)
        rgb=bytearray(FRAME_BGR);rgb[0::3]=bgra[2::4];rgb[1::3]=bgra[1::4];rgb[2::3]=bgra[0::4]
        return Image.frombytes('RGB',(W,H),bytes(rgb))
    return [cells[n] for n in MOOD_CELLS]+[cells['blink'],talk('speech-closed'),talk('speech-open')]+sleep_loop(base)

# --- lossless codec ------------------------------------------------------------------
def bgr(im):
    raw=im.tobytes();out=bytearray(FRAME_BGR)
    out[0::3]=raw[2::3];out[1::3]=raw[1::3];out[2::3]=raw[0::3];return bytes(out)

def residual(data,mode,key=None):
    if mode==F_DELTA:return bytes((a-b)&255 for a,b in zip(data,key))
    step=3 if mode==F_KEY_SUB else W*3
    return data[:step]+bytes((data[i]-data[i-step])&255 for i in range(step,len(data)))

def lz(data):
    """LZ4-style block: token (literal len << 4 | match len - 4), 255-run length extensions,
    literals, 16-bit little-endian offset. The last sequence is literals only."""
    n=len(data);out=bytearray();table={};i=anchor=0
    def length(v):
        while v>=255:out.append(255);v-=255
        out.append(v)
    while i+4<=n:
        k=data[i:i+4];j=table.get(k);table[k]=i
        if j is None or i-j>65535:i+=1;continue
        m=4
        while i+m<n:
            step=min(256,n-i-m)
            if data[j+m:j+m+step]==data[i+m:i+m+step]:m+=step;continue
            while i+m<n and data[j+m]==data[i+m]:m+=1
            break
        lit=i-anchor
        out.append((min(lit,15)<<4)|min(m-4,15))
        if lit>=15:length(lit-15)
        out+=data[anchor:i];out+=struct.pack('<H',i-j)
        if m-4>=15:length(m-4-15)
        for p in range(i+1,min(i+m,n-3),max(1,m//64)):table[data[p:p+4]]=p
        i+=m;anchor=i
    lit=n-anchor;out.append(min(lit,15)<<4)
    if lit>=15:length(lit-15)
    out+=data[anchor:];return bytes(out)

def unlz(blob,n):
    out=bytearray();p=0
    while True:
        t=blob[p];p+=1;lit=t>>4
        if lit==15:
            while True:
                b=blob[p];p+=1;lit+=b
                if b!=255:break
        out+=blob[p:p+lit];p+=lit
        if p==len(blob):break
        off=blob[p]|blob[p+1]<<8;p+=2;m=(t&15)+4
        if (t&15)==15:
            while True:
                b=blob[p];p+=1;m+=b
                if b!=255:break
        for _ in range(m):out.append(out[-off])
    assert len(out)==n;return bytes(out)

def encode(images):
    """CPK1 blob. Each frame: the smallest of a Sub- or Up-filtered key frame, or a byte
    delta against an earlier key frame."""
    return encode_planes([bgr(im) for im in images])

def encode_planes(data):
    entries=[];payload=bytearray();keys=[]
    for i,d in enumerate(data):
        options=[(lz(residual(d,F_KEY_SUB)),F_KEY_SUB,NONE),(lz(residual(d,F_KEY_UP)),F_KEY_UP,NONE)]
        for k in keys:options.append((lz(residual(d,F_DELTA,data[k])),F_DELTA,k))
        best=min(options,key=lambda o:len(o[0]))
        if best[1]!=F_DELTA:keys.append(i)
        entries.append((len(payload),len(best[0]),best[1],best[2]));payload+=best[0]
        while len(payload)%4:payload.append(0)
    head=8*4+len(entries)*16
    blob=struct.pack('<8I',MAGIC,VERSION,W,H,len(entries),head+len(payload),0,0)
    blob+=b''.join(struct.pack('<4I',head+o,n,f,r) for o,n,f,r in entries)+payload
    return blob

def decode(blob):
    """Reference decoder (tests): list of BGRA frames, as bgra_preview() would make them."""
    magic,ver,w,h,count,total=struct.unpack_from('<6I',blob)
    assert (magic,ver,w,h,total)==(MAGIC,VERSION,W,H,len(blob))
    planes=[]
    for i in range(count):
        o,n,f,r=struct.unpack_from('<4I',blob,32+16*i)
        res=bytearray(unlz(blob[o:o+n],FRAME_BGR))
        if f==F_DELTA:res=bytearray((a+b)&255 for a,b in zip(res,planes[r]))
        else:
            step=3 if f==F_KEY_SUB else W*3
            for j in range(step,FRAME_BGR):res[j]=(res[j]+res[j-step])&255
        planes.append(bytes(res))
    out=[]
    for p in planes:
        b=bytearray(W*H*4);b[0::4]=p[0::3];b[1::4]=p[1::3];b[2::4]=p[2::3];b[3::4]=b'\xff'*(W*H);out.append(bytes(b))
    return out

def empty():
    """No inputs: a valid header with no frames. The face service then reports the pack
    unavailable and never offers it."""
    return struct.pack('<8I',MAGIC,VERSION,W,H,0,32,0,0)

def build(out,atlas=ATLAS,sleep=SLEEP):
    """Writes out/cammy.cpk; returns the asset report entry."""
    if not Path(atlas).is_file() or not Path(sleep).is_file():
        blob=empty();(out/'cammy.cpk').write_bytes(blob)
        return dict(available=False,bytes=len(blob),reason='inputs missing')
    images=frames(atlas,sleep);blob=encode(images)
    if len(blob)>BUDGET:raise ValueError('cammy pack %d bytes exceeds its %d byte share of the face library budget'%(len(blob),BUDGET))
    (out/'cammy.cpk').write_bytes(blob)
    for name,im in (('cammy-preview.png',images[0]),):im.save(out/name)
    return dict(available=True,bytes=len(blob),frames=list(FRAMES),raw_bgra_bytes=len(images)*W*H*4,
                sha256=hashlib.sha256(blob).hexdigest(),
                atlas_sha256=hashlib.sha256(Path(atlas).read_bytes()).hexdigest(),
                sleep_sha256=hashlib.sha256(Path(sleep).read_bytes()).hexdigest(),
                frame_sha256=[hashlib.sha256(f).hexdigest() for f in decode(blob)])
