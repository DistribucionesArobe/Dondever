"""Stadium art direction for faceless DondeVer Reels. Local render only."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps
from generate_reel import ROOT, W, H, FPS, DURATION, load_badge, music, ffmpeg_binary

WHITE='#f5f7f5'
GREEN='#70f0aa'
GRAY='#aab5b2'

def face(size, condensed=False):
    font=ImageFont.truetype(str(ROOT/'static/fonts'/('Anton.ttf' if condensed else 'Manrope.ttf')),size)
    if not condensed:
        font.set_variation_by_name('Bold')
    return font


def write(d, value, y, size, color=WHITE, x=540, width=860, condensed=False):
    f=face(size,condensed)
    lines=[]
    for paragraph in value.split('\n'):
        line=''
        for word in paragraph.split():
            proposed=(line+' '+word).strip()
            if line and d.textlength(proposed,font=f)>width:
                lines.append(line); line=word
            else:
                line=proposed
        lines.append(line)
    for line in lines:
        while d.textlength(line,font=f)>width:
            size-=2; f=face(size,condensed)
        d.text((x,y),line,font=f,anchor='mt',fill=color,stroke_width=0)
        y+=int(size*1.23)
    return y


def layer(game, index, badges, date, preview=False):
    img=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(img)
    d.rounded_rectangle((80,170,348,220),radius=25,fill=GREEN)
    d.text((214,179),'DONDEVER.APP',font=face(25),anchor='mt',fill='#07231a')
    d.text((990,181),game['league'],font=face(30),anchor='rt',fill=WHITE)
    if index==0:
        write(d,'QUE NO SE TE\nPASE EL PARTIDO.',420,112,condensed=True)
        d.rectangle((405,770,675,780),fill=GREEN)
        write(d,'Encuentra dónde verlo.',880,49)
        write(d,'EQUIPOS · HORARIO · CANALES',1050,28,GREEN)
    elif index==1:
        write(d,'EL ENFRENTAMIENTO',340,39,GREEN)
        for key,x,color in [('away',305,'#ffd263'),('home',775,'#ff8243')]:
            d.ellipse((x-177,585,x+177,939),fill=(10,14,18,215),outline=color,width=4)
            badge=badges[key]
            if badge:
                badge=badge.copy(); badge.thumbnail((282,282))
                img.alpha_composite(badge,(x-badge.width//2,762-badge.height//2))
            else:
                write(d,game[key].get('short','?'),720,72,color,x=x,width=340,condensed=True)
            write(d,game[key]['name'].upper(),1010,47,color,x=x,width=390,condensed=True)
        write(d,'VS',722,50,condensed=True)
        write(d,date,1320,35)
        write(d,'HORARIO Y CANALES →',1450,32,GREEN)
    elif index==2:
        write(d,'AQUÍ ESTÁ EL HORARIO',350,43,GREEN)
        write(d,game['time'],455,195,condensed=True)
        write(d,'HORA CDMX · UTC−6',720,32)
        write(d,date,800,33,GRAY)
        d.rounded_rectangle((100,920,980,1400),radius=34,fill=(6,13,16,226),outline=(116,144,131,130),width=2)
        y=975
        for country,key in [('MÉXICO','mx_channels'),('ESTADOS UNIDOS','us_channels')]:
            write(d,country,y,26,GREEN)
            value=' · '.join(game.get(key) or []) or 'Canal por confirmar'
            y=write(d,value,y+55,44,width=760)+60
        write(d,'Fuente: agenda ESPN',1480,23,GRAY)
    else:
        write(d,'MENOS BUSCAR.\nMÁS VER.',430,140,condensed=True)
        write(d,'Consulta la agenda\ny elige tus equipos.',850,46)
        d.rounded_rectangle((120,1100,960,1245),radius=24,fill=GREEN)
        write(d,'dondever.app',1134,69,'#07231a',width=760,condensed=True)
        write(d,'ENLACE EN EL PERFIL',1340,30)
    write(d,('MUESTRA · ' if preview else 'AGENDA · ')+date,1640,21,GRAY)
    return img


def render(metadata,output):
    game=metadata['game']
    date_parts=metadata['date'].split('-')
    months=['ENE','FEB','MAR','ABR','MAY','JUN','JUL','AGO','SEP','OCT','NOV','DIC']
    date=f'{date_parts[2]} {months[int(date_parts[1])-1]} {date_parts[0]}'
    bg=ImageOps.fit(Image.open(ROOT/'static/reels/stadium-v2.png').convert('RGB'),(W+60,H+108)) if game['league_slug']=='nfl' else Image.new('RGB',(W+60,H+108),'#10251d')
    bg=bg.convert('RGBA')
    # Dark veil allows the literal data to remain readable over cinematic lighting.
    veil=Image.new('RGBA',bg.size,(0,4,8,58)); bg=Image.alpha_composite(bg,veil)
    badges={k:load_badge(game[k]) for k in ('home','away')}
    layers=[layer(game,i,badges,date,metadata.get("preview",False)) for i in range(4)]
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    for i,l in enumerate(layers):
        still=bg.crop((30,54,30+W,54+H)); still.alpha_composite(l)
        still.convert('RGB').save(output.with_name(output.stem+f'-scene-{i+1}.jpg'),quality=93)
    bounds=[(0,3),(3,6),(6,14),(14,20)]
    with tempfile.TemporaryDirectory(prefix='dv-stadium-') as tmp:
        audio=Path(tmp)/'music.wav'
        subprocess.run([ffmpeg_binary(),'-y','-loglevel','error','-ss','96.79','-i',str(ROOT/'static/reels/audio/all-this-kevin-macleod.mp3'),'-t','20','-af','loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.25,afade=t=out:st=19:d=1',str(audio)],check=True)
        p=subprocess.Popen([ffmpeg_binary(),'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24',
                            '-s',f'{W}x{H}','-r',str(FPS),'-i','-','-i',str(audio),'-c:v','libx264',
                            '-threads','1','-preset','fast','-crf','21','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-ar','48000',
                            '-movflags','+faststart','-t',str(DURATION),str(output)],stdin=subprocess.PIPE)
        try:
            for n in range(FPS*DURATION):
                t=n/FPS; index=next(i for i,(a,b) in enumerate(bounds) if a<=t<b)
                start,end=bounds[index]; elapsed=t-start
                x=int(30+18*math.sin(t*.2)); y=int(54-32*t/DURATION)
                frame=bg.crop((x,y,x+W,y+H))
                # Text enters in 0.35 seconds, synchronized to major musical beats.
                ease=1-(1-min(1,elapsed/.35))**3
                shifted=Image.new('RGBA',(W,H),(0,0,0,0))
                shifted.alpha_composite(layers[index],(0,int((1-ease)*85)))
                if ease<1:
                    shifted.putalpha(shifted.getchannel('A').point(lambda a:int(a*ease)))
                frame=Image.alpha_composite(frame,shifted)
                d=ImageDraw.Draw(frame)
                # Small sequence markers leave the action area clear.
                for dot in range(4):
                    d.rounded_rectangle((430+dot*60,1570,470+dot*60,1577),radius=3,
                                        fill=GREEN if dot==index else '#50645a')
                if 0<elapsed<.09 and index:
                    flash=Image.new('RGBA',(W,H),(255,255,255,int(70*(1-elapsed/.09))))
                    frame=Image.alpha_composite(frame,flash)
                p.stdin.write(frame.convert('RGB').tobytes())
        finally:
            p.stdin.close()
        if p.wait(): raise RuntimeError('Video encoding failed')
    metadata.update({'design':'stadium-v2','preview':metadata.get('preview',False),'duration':20,'music':'All This — Kevin MacLeod (CC BY 4.0)'})
    output.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--metadata',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(render(json.loads(Path(args.metadata).read_text()),args.output))
