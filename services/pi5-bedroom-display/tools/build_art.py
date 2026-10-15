#!/usr/bin/env python3
"""Original code-native geometric artwork; no downloaded images or photo access."""
import math
from pathlib import Path
from PIL import Image,ImageDraw

DEST=Path(__file__).resolve().parents[1]/'assets'
W,H=1920,1080


def gradient(top,bottom):
    im=Image.new('RGB',(W,H));d=ImageDraw.Draw(im)
    for y in range(H):
        t=y/(H-1);color=tuple(round(a*(1-t)+b*t) for a,b in zip(top,bottom))
        d.line((0,y,W,y),fill=color)
    return im


def build():
    images={}
    im=gradient((35,33,44),(142,98,79));d=ImageDraw.Draw(im)
    d.ellipse((1210,180,1450,420),fill='#c7a984')
    for k,color in enumerate(['#816052','#654f4b','#4a3c41','#302c36']):
        pts=[(x,round(610+k*95+85*math.sin(x/470+k))) for x in range(0,W+10,10)]
        d.polygon(pts+[(W,H),(0,H)],fill=color)
    images['dunes']=im
    im=gradient((11,24,35),(36,53,61));d=ImageDraw.Draw(im)
    for k in range(8):
        margin=k*33;d.arc((480+margin,80+margin,1440-margin,1040-margin),30,320,fill='#637e81',width=3)
    d.ellipse((825,405,1095,675),fill='#c0ab80')
    d.ellipse((850,390,1095,640),fill='#243b46');images['orbit']=im
    im=gradient((27,57,59),(135,151,133));d=ImageDraw.Draw(im)
    d.ellipse((350,190,540,380),fill='#d2cab1')
    for k,color in enumerate(['#607f76','#42685f','#2a4e4d','#18383e']):
        pts=[(x,round(550+k*135+90*math.sin(x/390+k*1.3))) for x in range(0,W+10,10)]
        d.polygon(pts+[(W,H),(0,H)],fill=color)
    images['hills']=im
    im=gradient((19,34,54),(60,96,107));d=ImageDraw.Draw(im)
    for k in range(16):
        pts=[(x,round(150+k*60+45*math.sin(x/240+k*.45))) for x in range(0,W+10,6)]
        d.line(pts,fill=(90+k*3,129+k*2,135+k*2),width=2)
    images['tide']=im
    DEST.mkdir(exist_ok=True)
    for name,im in images.items():im.save(DEST/('art-'+name+'.png'),optimize=True)


if __name__=='__main__':build()
