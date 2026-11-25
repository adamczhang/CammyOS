"""Build a hand-drawn cel-shaded character skin in the existing ParamFace schema.

No downloads, camera, browser or display writes. Palette sprites are generated
from code; ParamFace itself supplies rendering, blinking, gaze and mouth drive.
"""
import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLORS = ['#241D29', '#E84E38', '#692A35', '#EF8995', '#FFFAE8', '#FFF19B',
          '#E7BD49', '#6B4636', '#23262D', '#F9DB65', '#AD322A', '#FF8C51']
INK, RED, INSIDE, TONGUE, WHITE, LIGHT, SHADE, BROWN, BG, YELLOW, REDSHADE, CORAL = range(1, 13)
EXPRESSIONS = ('neutral', 'happy', 'angry', 'sad', 'doubt', 'sleepy')


def curve(draw, points, fill=INK, width=3):
    a,b,c,d = points
    coords=[]
    for i in range(31):
        t=i/30;u=1-t
        coords.append((u**3*a[0]+3*u*u*t*b[0]+3*u*t*t*c[0]+t**3*d[0],
                       u**3*a[1]+3*u*u*t*b[1]+3*u*t*t*c[1]+t**3*d[1]))
    draw.line(coords, fill=fill, width=width, joint='curve')


def sprite(image):
    pixels=image.tobytes()
    encoded=bytearray();i=0
    while i<len(pixels):
        j=i+1
        while j<len(pixels) and pixels[j]==pixels[i] and j-i<16:j+=1
        encoded.append(((j-i-1)<<4)|pixels[i]);i=j
    return dict(w=image.width,h=image.height,palette=COLORS,
                data=base64.b64encode(encoded).decode())


def eye(expression, closed=False, right=False):
    from PIL import Image, ImageDraw
    image=Image.new('L',(48,48),0);d=ImageDraw.Draw(image)
    if closed or expression in ('sleepy','happy'):
        if expression=='happy':
            curve(d,[(7,30),(17,12),(30,12),(41,30)],width=4)
        else:
            curve(d,[(7,24),(16,32),(31,32),(41,23)],width=3)
        return sprite(image)
    d.ellipse((8,6,40,40),fill=INK)
    d.ellipse((11,24,37,38),fill=BROWN)
    d.ellipse((11,8,25,22),fill=WHITE)
    d.ellipse((28,29,33,34),fill=LIGHT)
    if expression=='angry':
        pts=[(3,0),(45,0),(45,9),(3,23)] if right else [(3,0),(45,0),(45,23),(3,9)]
        d.polygon(pts,fill=YELLOW)
        d.line([pts[-1],pts[-2]],fill=INK,width=3)
    elif expression in ('sad','angry'):
        pts=[(4,0),(45,0),(45,12),(4,7)] if right else [(4,0),(45,0),(45,7),(4,12)]
        d.polygon(pts,fill=YELLOW)
    elif expression=='doubt' and right:
        d.rectangle((3,0,45,18),fill=YELLOW)
        d.line((8,18,41,18),fill=INK,width=3)
    return sprite(image)


def mouth(expression, opened=False):
    from PIL import Image, ImageDraw
    image=Image.new('L',(48,36),0);d=ImageDraw.Draw(image)
    if opened and expression!='sleepy':
        box=(9,3,39,34) if expression=='happy' else (14,5,34,31)
        d.ellipse(box,fill=INK)
        d.ellipse((box[0]+2,box[1]+2,box[2]-2,box[3]-2),fill=INSIDE)
        d.ellipse((box[0]+5,box[3]-12,box[2]-4,box[3]-2),fill=TONGUE)
    elif expression=='sad':
        curve(d,[(10,24),(17,14),(31,14),(38,24)],width=3)
    elif expression=='doubt':
        curve(d,[(12,24),(20,21),(28,26),(37,20)],width=3)
    elif expression=='sleepy':
        d.ellipse((20,17,29,23),outline=INK,width=2)
    else:
        curve(d,[(7,17),(11,26),(20,26),(24,19)],width=3)
        curve(d,[(24,19),(28,26),(37,26),(41,17)],width=3)
    return sprite(image)


def path_points(commands):
    result=[];current=(0,0)
    for command in commands:
        if command[0] in ('M','L'):
            current=command[1:3];result.append(current)
        elif command[0]=='C':
            a=current;b=command[1:3];c=command[3:5];end=command[5:7]
            for step in range(1,25):
                t=step/24;u=1-t
                result.append((u**3*a[0]+3*u*u*t*b[0]+3*u*t*t*c[0]+t**3*end[0],
                               u**3*a[1]+3*u*u*t*b[1]+3*u*t*t*c[1]+t**3*end[1]))
            current=end
    return result


HEAD=[('M',96,134),('C',119,114,191,114,218,131),
      ('C',251,145,261,170,268,197),('C',277,220,294,240,291,266),
      ('C',287,299,260,323,231,337),('C',194,355,124,355,87,337),
      ('C',57,323,30,300,27,267),('C',24,242,40,218,49,196),
      ('C',56,168,68,148,96,134),('Z',)]
LEFT_EAR=[('M',69,163),('C',53,139,33,85,18,29),
          ('C',13,7,25,10,40,27),('C',75,65,97,111,111,143),('Z',)]
RIGHT_EAR=[('M',208,141),('C',228,101,260,52,292,22),
           ('C',307,8,307,25,300,45),('C',282,97,263,144,248,166),('Z',)]


def hand_art():
    from PIL import Image,ImageDraw
    image=Image.new('L',(318,360),BG);draw=ImageDraw.Draw(image)
    def shape(commands,fill,outline=False):
        pts=path_points(commands);draw.polygon(pts,fill=fill)
        if outline:draw.line(pts+[pts[0]],fill=INK,width=3,joint='curve')
    shape(LEFT_EAR,YELLOW,True);shape(RIGHT_EAR,YELLOW,True)
    shape([('M',21,45),('C',35,58,45,65,52,71),('L',40,27),
           ('C',25,10,13,7,18,29),('Z',)],INK)
    shape([('M',267,55),('C',279,58,288,64,292,68),('L',300,45),
           ('C',307,25,307,8,292,22),('Z',)],INK)
    shape([('M',64,151),('C',51,119,39,90,32,66),
           ('C',59,106,72,129,82,153),('Z',)],SHADE)
    shape([('M',226,149),('C',251,107,267,80,284,63),
           ('C',270,106,253,142,242,160),('Z',)],SHADE)
    shape(HEAD,YELLOW,True)
    mask=Image.new('L',image.size,0);ImageDraw.Draw(mask).polygon(path_points(HEAD),fill=255)
    shade=Image.new('L',image.size,0);sd=ImageDraw.Draw(shade)
    pts=path_points([('M',274,202),('C',277,263,253,304,210,329),
         ('C',178,347,136,346,93,336),('C',148,374,249,350,283,293),
         ('L',307,243),('Z',)])
    sd.polygon(pts,fill=SHADE)
    from PIL import ImageChops
    image.paste(shade,(0,0),ImageChops.multiply(mask,shade.point(lambda x:255 if x else 0)))
    draw=ImageDraw.Draw(image)
    curve(draw,[(95,145),(111,133),(140,131),(153,132)],fill=LIGHT,width=5)
    # Cheek circles now lie INSIDE the head, with a visible yellow margin.
    for x in (44,233):
        draw.ellipse((x,257,x+41,300),fill=RED,outline=INK,width=2)
        draw.arc((x+2,258,x+39,298),5,140,fill=REDSHADE,width=3)
        draw.arc((x+5,261,x+34,291),200,270,fill=CORAL,width=2)
    draw.polygon([(154,243),(164,243),(159,249)],fill=INK)
    # Keep authored facial features as separate driven ParamFace parts.
    windows=Image.new('L',image.size,0);wd=ImageDraw.Draw(windows)
    for box in [(52,166,125,242),(192,166,265,242),(124,259,195,310)]:
        wd.rectangle(box,fill=255)
    from PIL import ImageFilter
    image.paste(0,(0,0),ImageChops.multiply(mask.filter(ImageFilter.MinFilter(9)),windows))
    return image


def overlay(expression):
    # Full-resolution immutable static art; expression/animation stays in the parts.
    from PIL import Image
    image=hand_art().resize((356,400),Image.Resampling.NEAREST)
    from PIL import ImageDraw
    draw=ImageDraw.Draw(image)
    # Effect slots lie on flat skin, so transparent windows reveal the live sprites.
    draw.rectangle((133,158,184,209),fill=0)
    draw.rectangle((211,163,261,214),fill=0)
    return sprite(image)


def delta(value, base):
    """ParamFace expression numbers ADD to the base; sprite frames are absolute."""
    result={}
    for key,item in value.items():
        old=base.get(key)
        if key=='frames':result[key]=item
        elif isinstance(item,dict):
            nested=delta(item,old if isinstance(old,dict) else {})
            if nested:result[key]=nested
        elif item!=old:
            result[key]=item-old if type(item) in (int,float) and type(old) in (int,float) else item
    return result


def effect(name):
    from PIL import Image,ImageDraw
    image=Image.new('L',(48,48),0);draw=ImageDraw.Draw(image)
    if name=='sparks':
        for points in [[(8,2),(14,12),(9,13),(18,25),(12,9),(17,8)],
                       [(35,4),(27,15),(32,16),(25,29),(40,13),(34,12)],
                       [(19,31),(14,39),(21,38),(24,47),(28,32),(23,34)]]:
            draw.polygon(points,fill=LIGHT)
            draw.line(points+[points[0]],fill=INK,width=1)
    else:
        for x,y,size in [(3,30,8),(16,17,11),(32,2,13)]:
            points=[(x,y),(x+size,y),(x,y+size),(x+size,y+size)]
            draw.line(points,fill=INK,width=5)
            draw.line(points,fill=WHITE,width=2)
    return sprite(image)


def build():
    doc=dict(version=2,meta=dict(name='Pikachu / hand-drawn cel',author='CammyOS',
             description='Procedural cel-shaded character skin; existing ParamFace controls'),
             canvas=dict(width=356,height=400),
             palette=dict(primary=COLORS[INK-1],secondary=COLORS[RED-1],background=COLORS[YELLOW-1]),
             parts={},animation=dict(blink=dict(interval=3.8,duration=150),
                 saccade=dict(interval=2.4,amplitude=.4),breath=dict(period=3.5,depth=.4)),
             expressions={},overlay=dict(smooth=False,frames=dict(open=overlay('neutral'))))
    for expression in EXPRESSIONS:
        parts={}
        for key,x,right in [('eyeL',88,False),('eyeR',230,True)]:
            parts[key]=dict(pos=dict(x=round(x*356/318),y=230),shape='pixel',scale=1.375,smooth=True,
                frames=dict(open=eye(expression,right=right),closed=eye(expression,closed=True,right=right)),
                upperLid=dict(angle=0,cover=0),lowerLid=dict(angle=0,cover=0))
        parts['mouth']=dict(pos=dict(x=178,y=313),shape='pixel',scale=1.5,smooth=True,
                           frames=dict(open=mouth(expression,True),closed=mouth(expression)))
        for key,x,right in [('browL',88,False),('browR',230,True)]:
            angle={'angry':20,'sad':-18,'doubt':-12}.get(expression,0)*(1 if not right else -1)
            parts[key]=dict(pos=dict(x=round(x*356/318),y=188-(6 if expression=='doubt' and not right else 0)),
                           shape='arc',width=30 if expression in ('angry','sad','doubt') else 0,
                           thickness=3,curve=.2,angle=angle,color=COLORS[BROWN-1])
            if expression in ('angry','sad','doubt'):parts[key]['shape']='rect'
        if expression=='angry':
            parts['browL']=dict(pos=dict(x=158,y=183),shape='pixel',scale=1,smooth=True,
                frames=dict(open=effect('sparks')))
        elif expression=='sleepy':
            parts['browR']=dict(pos=dict(x=236,y=188),shape='pixel',scale=1,smooth=True,
                frames=dict(open=effect('zzz')))
        if expression=='neutral':doc['parts']=parts
        else:
            doc['expressions'][expression]=dict(parts={key:delta(part,doc['parts'][key]) for key,part in parts.items()})
            if expression=='sleepy':
                doc['expressions'][expression]['animation']=dict(blink=dict(interval=2,duration=300))
    return doc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'assets/faces/pikachu.json')
    args=parser.parse_args()
    doc=build();raw=json.dumps(doc,separators=(',',':')).encode()
    if len(raw)>65536:raise ValueError('Exceeds existing ParamFace admission limit')
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_bytes(raw+b'\n')
    print(json.dumps(dict(path=str(args.out),bytes=len(raw),schema=2,hardware_displayed=False)))


if __name__=='__main__':main()
