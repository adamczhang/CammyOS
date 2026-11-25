"""Build-time actual ParamFace pixels, immutable inside the trial library.

No camera or devices. The image is a generated artifact, not a vendor asset.
"""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from cammy_face import FaceRenderer,PRESETS,preset
from tools.native_image_abi import bgra_preview


def build(preset_name='default',delay_ms=3000):
    if preset_name not in PRESETS:raise ValueError('Unknown face preset')
    if type(delay_ms) is not int or not 0<=delay_ms<=90000:raise ValueError('Delay0..90000ms required')
    out=ROOT/'build/native-face';out.mkdir(parents=True,exist_ok=True)
    renderer=FaceRenderer()
    try:
        renderer.load(preset(preset_name))
        renderer.set_expression('happy')
        for _ in range(30):rgb=renderer.frame(33.333)
    finally:renderer.close()
    pixels=bgra_preview(rgb)
    lines=['/* Generated from actual ParamFace, happy, 30 ticks. */',
           f'#define CMF_TRIAL_DELAY_MS {delay_ms}u',
           'static const unsigned char trial_pixels[] = {']
    lines.extend(','.join(str(b) for b in pixels[i:i+32])+',' for i in range(0,len(pixels),32))
    lines.append('};')
    (out/'trial_pixels.h').write_text('\n'.join(lines)+'\n')
    (out/'trial.ppm').write_bytes(b'P6\n356 400\n255\n'+rgb)
    report={'width':356,'height':400,'format':'BGRA8888','bytes':len(pixels),
            'sha256':hashlib.sha256(pixels).hexdigest(),'immutable':True,
            'expression':'happy','preset':preset_name,'delay_ms':delay_ms,'duration_ms':10000,'hardware_displayed':False}
    (out/'asset.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset',choices=PRESETS,default='default')
    parser.add_argument('--delay-ms',type=int,default=3000)
    args=parser.parse_args()
    print(json.dumps(build(args.preset,args.delay_ms),indent=2))
