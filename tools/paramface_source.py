"""Pinned MIT ParamFace source preparation; no firmware/device actions."""
import os
from pathlib import Path
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
COMMIT='5ef10f1da988dad7be001dd23a8bc5743866e93f'
SOURCE=ROOT/'downloads/stackchan-face-editor'

def prepare():
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['git','init',str(SOURCE)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(SOURCE),'remote','add','origin','https://github.com/zziying/stackchan-face-editor.git'],check=True)
        subprocess.run(['git','-C',str(SOURCE),'fetch','--depth','1','origin',COMMIT],check=True,capture_output=True)
        subprocess.run(['git','-C',str(SOURCE),'checkout','--detach','FETCH_HEAD'],check=True,capture_output=True)
    if subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()!=COMMIT:
        raise ValueError('ParamFace source commit differs from pin')
    if subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain'],text=True):
        raise ValueError('ParamFace upstream checkout must be clean')
    out=ROOT/'build/paramface';inc=out/'include';inc.mkdir(parents=True,exist_ok=True)
    shim=SOURCE/'lib/ParamFace/host/M5GFX.h';s=shim.read_text()
    old='for (int32_t dy = -r1; dy <= r1; dy++)\n      for (int32_t dx = -r1; dx <= r1; dx++) {'
    new='for (int32_t dy = std::max(-r1, -y); dy <= std::min(r1, h - 1 - y); dy++)\n      for (int32_t dx = std::max(-r1, -x); dx <= std::min(r1, w - 1 - x); dx++) {'
    if s.count(old)!=1:raise ValueError('Pinned arc shim context differs')
    (inc/'M5GFX.h').write_text('// Derived from MIT zziying/stackchan-face-editor, copyright2026 zziying.\n// Cammy change: clip arc raster scan to destination viewport.\n'+s.replace(old,new))
    core=SOURCE/'lib/ParamFace/src/ParamFace.cpp'
    core_text=core.read_text()
    # Same palette/RLE schema; bound one full-panel overlay instead of an80x60 grid.
    # No change to dynamic-part limits or to the upstream source checkout.
    overlay_limit=', 80, 60);'
    if core_text.count(overlay_limit)!=2:raise ValueError('Pinned overlay admission context differs')
    derived=core_text.replace(overlay_limit,', 356, 400);')
    (out/'ParamFace-cammy.cpp').write_text('// Cammy bounded panel-resolution overlay extension; upstream MIT.\n'+derived)
    (out/'LICENSE-ParamFace').write_bytes((SOURCE/'LICENSE').read_bytes())
    sources=['lib/ParamFace/src/ParamFace.cpp','lib/ParamFace/src/ParamFace.h','lib/ParamFace/src/Renderer.cpp','lib/ParamFace/host/M5GFX.h','lib/ParamFace/vendor/ArduinoJson.h']
    (out/'provenance.json').write_text(json.dumps({'source':'https://github.com/zziying/stackchan-face-editor','commit':COMMIT,'license':'MIT','core_modified':True,'core_change':'two overlay bounds80x60 ->356x400; part sprites unchanged; upstream checkout clean','derived_core_sha256':hashlib.sha256((out/'ParamFace-cammy.cpp').read_bytes()).hexdigest(),'host_shim_change':'viewport-clipped arc scan','sha256':{p:hashlib.sha256((SOURCE/p).read_bytes()).hexdigest() for p in sources}},indent=2)+'\n')
    return SOURCE

if __name__=='__main__':print(prepare())
