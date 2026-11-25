"""Actual pinned ParamFace renderer in RAM. Never opens a display/device node."""
import ctypes
import json
import math
from pathlib import Path
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
EXPRESSIONS=('neutral','happy','angry','sad','doubt','sleepy')
PRESETS=('default','cat','ke','mochi','moon','pixel-demo','pikachu')
MAX_JSON=65536


def _pairs(items):
    out={}
    for k,v in items:
        if k in out:raise ValueError('duplicate face key')
        out[k]=v
    return out


def validate_face(raw):
    if isinstance(raw,str):raw=raw.encode()
    if not isinstance(raw,bytes) or len(raw)>MAX_JSON or b'\0' in raw:raise ValueError('bounded face JSON required')
    try:
        value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite number')))
    except (json.JSONDecodeError,UnicodeDecodeError,RecursionError) as e:raise ValueError('invalid face JSON') from e
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version'] not in (1,2):raise ValueError('ParamFace schema1 or2 required')
    if not isinstance(value.get('parts'),dict) or not value['parts']:raise ValueError('face parts required')
    canvas=value.get('canvas',{})
    if not isinstance(canvas,dict):raise ValueError('invalid canvas')
    for key in ('width','height'):
        n=canvas.get(key,320 if key=='width' else 240)
        if type(n) not in (int,float) or not math.isfinite(n) or not 16<=n<=1024:raise ValueError('canvas16..1024 required')
    count=[0]
    def visit(item,depth):
        count[0]+=1
        if depth>10 or count[0]>4096:raise ValueError('face complexity limit')
        if isinstance(item,dict):
            for k,v in item.items():
                if len(k)>80:raise ValueError('face key too long')
                visit(v,depth+1)
        elif isinstance(item,list):
            if len(item)>256:raise ValueError('face array too large')
            for v in item:visit(v,depth+1)
        elif type(item) in (float,int):
            if not math.isfinite(item) or abs(item)>4096:raise ValueError('face numeric range')
        elif isinstance(item,str):
            if len(item)>32768:raise ValueError('face string too large')
        elif item is not None and not isinstance(item,bool):raise ValueError('unsupported face value')
    visit(value,0)
    return json.dumps(value,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def preset_path(name):
    if name not in PRESETS:raise ValueError('unknown preset')
    directory=ROOT/'assets/faces' if name=='pikachu' else ROOT/'downloads/stackchan-face-editor/faces'
    return directory/f'{name}.json'


def preset(name):
    return validate_face(preset_path(name).read_bytes())


class FaceRenderer:
    def __init__(self,width=356,height=400,library=None):
        if type(width) is not int or type(height) is not int or not 16<=width<=1024 or not 16<=height<=1024:raise ValueError('dimensions16..1024')
        self.width,self.height=width,height
        self.lock=threading.RLock();self.closed=False;self.json=None;self.expression='neutral';self.sequence=0
        self.lib=ctypes.CDLL(str(library or ROOT/'build/paramface/libcammy_paramface.so'))
        self.lib.cm_pf_create.argtypes=[ctypes.c_int,ctypes.c_int];self.lib.cm_pf_create.restype=ctypes.c_void_p
        self.lib.cm_pf_destroy.argtypes=[ctypes.c_void_p];self.lib.cm_pf_destroy.restype=None
        self.lib.cm_pf_load.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_size_t]
        self.lib.cm_pf_expression.argtypes=[ctypes.c_void_p,ctypes.c_int]
        self.lib.cm_pf_drive.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_float,ctypes.c_int,ctypes.c_float,ctypes.c_float]
        self.lib.cm_pf_frame.argtypes=[ctypes.c_void_p,ctypes.c_float,ctypes.c_void_p,ctypes.c_size_t]
        self.lib.cm_pf_error.argtypes=[ctypes.c_void_p];self.lib.cm_pf_error.restype=ctypes.c_char_p
        self.ctx=self.lib.cm_pf_create(width,height)
        if not self.ctx:raise RuntimeError('ParamFace allocation failed')
        self.pixels=(ctypes.c_uint8*(width*height*3))()
        self.load(preset('default'))

    def _open(self):
        if self.closed:raise RuntimeError('renderer closed')

    def load(self,raw):
        validated=validate_face(raw)
        with self.lock:
            self._open()
            if self.lib.cm_pf_load(self.ctx,validated,len(validated)):raise ValueError(self.lib.cm_pf_error(self.ctx).decode(errors='replace'))
            self.json=validated;self.sequence+=1

    def set_expression(self,expression):
        if expression not in EXPRESSIONS:raise ValueError('unknown expression')
        with self.lock:
            self._open()
            if self.lib.cm_pf_expression(self.ctx,EXPRESSIONS.index(expression)):raise RuntimeError('expression refused')
            self.expression=expression;self.sequence+=1

    def drive(self,mouth=None,gaze=None):
        m=0 if mouth is None else mouth
        h,v=(0,0) if gaze is None else gaze
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (m,h,v)) or not 0<=m<=1 or not -1<=h<=1 or not -1<=v<=1:raise ValueError('invalid mouth/gaze')
        with self.lock:
            self._open()
            if self.lib.cm_pf_drive(self.ctx,mouth is not None,m,gaze is not None,h,v):raise RuntimeError('drive refused')
            self.sequence+=1

    def frame(self,dt_ms=33.333):
        if type(dt_ms) not in (int,float) or not math.isfinite(dt_ms) or not 0<=dt_ms<=250:raise ValueError('dt0..250 required')
        with self.lock:
            self._open();start=time.monotonic()
            if self.lib.cm_pf_frame(self.ctx,dt_ms,self.pixels,len(self.pixels)):raise RuntimeError('render failed')
            self.last_render_ms=(time.monotonic()-start)*1000
            return bytes(self.pixels)

    def ppm(self,dt_ms=33.333):return f'P6\n{self.width} {self.height}\n255\n'.encode()+self.frame(dt_ms)

    def state(self):
        with self.lock:return {'renderer':'ParamFace','expression':self.expression,'width':self.width,'height':self.height,'sequence':self.sequence,'target':'host-preview','physical_display_verified':False,'render_ms':getattr(self,'last_render_ms',None)}

    def close(self):
        with self.lock:
            if not self.closed:self.lib.cm_pf_destroy(self.ctx);self.ctx=None;self.closed=True
