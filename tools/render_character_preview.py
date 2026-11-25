"""Export the actual ParamFace character to panel-size PNGs and silent MP4.

Offline export only. Mouth/gaze in the reel are scripted demo inputs, not audio
lip sync, vision tracking, robot motion or a physical camera LCD recording.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from cammy_face import FaceRenderer,preset,EXPRESSIONS


def main():
    from PIL import Image,ImageDraw,ImageFont
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'build/character-face')
    parser.add_argument('--idle',action='store_true',help='Quiet24second repeating expression-loop export')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    face=FaceRenderer();face.load(preset('pikachu'))
    face.drive(mouth=0,gaze=(0,0))
    sheet=Image.new('RGB',(1068,880),'#f4f0e8');draw=ImageDraw.Draw(sheet)
    names=dict(neutral='Neutral / listening',happy='Happy',angry='Angry',sad='Sad',doubt='Curious',sleepy='Sleepy')
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    except OSError:font=ImageFont.load_default()
    for i,expression in enumerate(EXPRESSIONS):
        face.set_expression(expression)
        im=Image.frombytes('RGB',(356,400),face.frame(0))
        im.save(args.out/f'{expression}.png')
        # Future native trial input only; no executable/camera changes here.
        if expression=='happy':
            bgra=im.convert('RGBA').tobytes('raw','BGRA')
            (args.out/'happy.bgra').write_bytes(bgra)
        x=i%3*356;y=i//3*440;sheet.paste(im,(x,y))
        draw.text((x+16,y+408),names[expression],font=font,fill='#241d29')
    sheet.save(args.out/'expression-sheet.png')
    face.close()
    face=FaceRenderer();face.load(preset('pikachu'))
    fps=20;scene_seconds=4 if args.idle else 3;frames=fps*scene_seconds*6;hashes=set();render_ms=[]
    video_name='pikachu-idle-loop.mp4' if args.idle else 'pikachu-expressions.mp4'
    process=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
        '-s','356x400','-r',str(fps),'-i','pipe:0','-an','-c:v','libx264','-crf','18',
        '-pix_fmt','yuv420p','-movflags','+faststart',str(args.out/video_name)],stdin=subprocess.PIPE)
    try:
        for i in range(frames):
            t=i/fps;scene=int(t/scene_seconds)
            face.set_expression(EXPRESSIONS[scene])
            # First half of each mood is quiet; second half demonstrates mouth drive.
            mouth=(.5+.5*math.sin(t*17)) if not args.idle and t%3>1.5 and scene!=5 else 0
            face.drive(mouth=mouth,gaze=None if args.idle else (.7*math.sin(t*.8),.35*math.sin(t*.55)))
            started=time.monotonic();rgb=face.frame(1000/fps)
            render_ms.append((time.monotonic()-started)*1000)
            hashes.add(hashlib.sha256(rgb).digest());process.stdin.write(rgb)
        process.stdin.close()
        if process.wait(timeout=20):raise RuntimeError('Video export failed')
    finally:
        face.close()
        if process.poll() is None:process.kill();process.wait()
        if not process.stdin.closed:process.stdin.close()
    report=dict(preset='pikachu',schema='ParamFace2',width=356,height=400,fps=fps,
                frames=frames,duration_seconds=frames/fps,distinct_frames=len(hashes),
                animation='actual ParamFace blinks/gaze; quiet mouth; angry sparks/sleepy Zzz' if args.idle else 'actual ParamFace blink; scripted demo mouth/gaze; static authored art',
                lip_sync_verified=False,physical_display_verified=False,camera_access=False,
                host_render_ms=dict(mean=sum(render_ms)/len(render_ms),max=max(render_ms)),
                scene_seconds=scene_seconds,scene_order=list(EXPRESSIONS),voice_work='paused-by-user',video=video_name)
    image=args.out/'happy.bgra'
    report['native_image_candidate']=dict(file=image.name,format='BGRA8888',
        bytes=image.stat().st_size,sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        candidate_library_built=False,hardware_displayed=False)
    (args.out/('idle-preview-report.json' if args.idle else 'preview-report.json')).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
