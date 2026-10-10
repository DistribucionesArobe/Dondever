"""Daily Reel using the approved Canva cover and matching data scenes."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps
from generate_reel import ROOT, W, H, FPS, DURATION, load_badge, music, ffmpeg_binary

WHITE='#f8f6f1'
GREEN='#def22c'
GRAY='#a0aebe'
CANVA_COVER = ROOT / 'static/reels/canva-approved-cover.png'

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
    if index == 0:
        img = Image.open(CANVA_COVER).convert('RGBA')
        if preview:
            d = ImageDraw.Draw(img)
            d.rectangle((70,1630,700,1690),fill='#031421')
            d.text((90,1640),'MUESTRA · '+date,font=face(24),fill=WHITE)
        return img
    img=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(img)
    # Canva's angular lime border carried into the live data scenes.
    d.line((520,160,1000,160,935,1500),fill=GREEN,width=3)
    d.polygon([(950,160),(1000,160),(996,215)],fill=GREEN)
    d.text((80,179),'DONDEVER.APP',font=face(27),fill=WHITE)
    d.text((990,181),game['league'],font=face(26),anchor='rt',fill=WHITE)
    if index==0:
        write(d,'QUE NO SE TE\nPASE EL PARTIDO.',420,112,condensed=True)
        d.rectangle((405,770,675,780),fill=GREEN)
        write(d,'Encuentra dónde verlo.',880,49)
        write(d,'EQUIPOS · HORARIO · CANALES',1050,28,GREEN)
    elif index==1:
        write(d,'EL ENFRENTAMIENTO',340,39,GREEN)
        for key,x,color in [('away',305,GREEN),('home',775,WHITE)]:
            d.ellipse((x-177,585,x+177,939),fill=(3,20,33,215),outline=color,width=4)
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
        d.rounded_rectangle((100,920,980,1400),radius=34,fill=(3,20,33,245),outline=(160,174,190,100),width=2)
        y=975
        for country,key in [('MÉXICO','mx_channels'),('ESTADOS UNIDOS','us_channels')]:
            write(d,country,y,26,GREEN)
            value=' · '.join(game.get(key) or []) or 'Canal por confirmar'
            y=write(d,value,y+55,44,width=760)+60
        write(d,'Fuente: agenda ESPN',1480,23,GRAY)
    else:
        write(d,'TU PARTIDO.\nTU CANAL.',430,140,condensed=True)
        write(d,'Consulta la agenda\ny elige tus equipos.',850,46)
        d.polygon([(120,1100),(960,1100),(900,1245),(120,1245)],fill=GREEN)
        write(d,'dondever.app',1134,69,'#031421',width=760,condensed=True)
        write(d,'ENLACE EN EL PERFIL',1340,30)
    write(d,('MUESTRA · ' if preview else 'AGENDA · ')+date,1640,21,GRAY)
    return img


def render(metadata,output):
    print("Reel: preparing artwork",flush=True)
    game=metadata['game']
    width, height = 720, 1280
    scale = width / W
    date_parts=metadata['date'].split('-')
    months=['ENE','FEB','MAR','ABR','MAY','JUN','JUL','AGO','SEP','OCT','NOV','DIC']
    date=f'{date_parts[2]} {months[int(date_parts[1])-1]} {date_parts[0]}'
    bg=Image.new('RGB',(W+60,H+108),'#031421')
    backdrop=ImageDraw.Draw(bg)
    for offset in range(0,1800,180):
        backdrop.line((0,1420+offset,W+60,980+offset),fill='#142b41',width=2)
    bg=bg.resize((width+40,height+72), Image.Resampling.LANCZOS).convert('RGBA')
    # Dark veil allows the literal data to remain readable over cinematic lighting.
    veil=Image.new('RGBA',bg.size,(0,4,8,58)); bg=Image.alpha_composite(bg,veil)
    badges={k:load_badge(game[k]) for k in ('home','away')}
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    bounds=[(0,3),(3,6),(6,14),(14,20)]
    with tempfile.TemporaryDirectory(prefix='dv-stadium-') as tmp:
        audio=Path(tmp)/'music.wav'
        print('Reel: preparing audio',flush=True)
        subprocess.run([ffmpeg_binary(),'-y','-loglevel','error','-threads','1','-filter_threads','1','-ss','96.79','-i',str(ROOT/'static/reels/audio/all-this-kevin-macleod.mp3'),'-t','20','-af','aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,afade=t=in:d=0.25,afade=t=out:st=19:d=1','-ar','48000',str(audio)],check=True,timeout=90)
        print('Reel: encoding video',flush=True)
        silent=Path(tmp)/'silent.mp4'
        p=subprocess.Popen([ffmpeg_binary(),'-y','-loglevel','error','-threads','1','-filter_threads','1','-f','rawvideo','-pix_fmt','rgb24',
                            '-s',f'{width}x{height}','-r',str(FPS),'-i','-','-c:v','libx264',
                            '-threads','1','-preset','ultrafast','-tune','zerolatency','-x264-params','rc-lookahead=0:sync-lookahead=0','-crf','21','-pix_fmt','yuv420p','-an',
                            '-movflags','+faststart','-t',str(DURATION),str(silent)],stdin=subprocess.PIPE)
        current_index = None
        overlay = None
        try:
            for n in range(FPS*DURATION):
                t=n/FPS; index=next(i for i,(a,b) in enumerate(bounds) if a<=t<b)
                if index != current_index:
                    if overlay is not None:
                        overlay.close()
                    print(f'Reel: scene {index+1}/4',flush=True)
                    full = layer(game,index,badges,date,metadata.get('preview',False))
                    overlay = full.resize((width,height),Image.Resampling.LANCZOS)
                    full.close()
                    still = bg.crop((20,36,20+width,36+height))
                    still.alpha_composite(overlay)
                    still.convert('RGB').save(output.with_name(output.stem+f'-scene-{index+1}.jpg'),quality=93)
                    still.close()
                    current_index = index
                start,end=bounds[index]; elapsed=t-start
                x=int((30+18*math.sin(t*.2))*scale); y=int((54-32*t/DURATION)*scale)
                frame=bg.crop((x,y,x+width,y+height))
                # Text enters in 0.35 seconds, synchronized to major musical beats.
                ease=1-(1-min(1,elapsed/.35))**3
                shifted=Image.new('RGBA',(width,height),(0,0,0,0))
                shifted.alpha_composite(overlay,(0,int((1-ease)*85*scale)))
                if ease<1:
                    shifted.putalpha(shifted.getchannel('A').point(lambda a:int(a*ease)))
                frame=Image.alpha_composite(frame,shifted)
                d=ImageDraw.Draw(frame)
                # Small sequence markers leave the action area clear.
                for dot in range(4):
                    d.rounded_rectangle(tuple(int(v*scale) for v in (430+dot*60,1570,470+dot*60,1577)),radius=2,
                                        fill=GREEN if dot==index else '#344353')
                if 0<elapsed<.09 and index:
                    flash=Image.new('RGBA',(width,height),(255,255,255,int(70*(1-elapsed/.09))))
                    frame=Image.alpha_composite(frame,flash)
                p.stdin.write(frame.convert('RGB').tobytes())
        finally:
            p.stdin.close()
            if overlay is not None:
                overlay.close()
        if p.wait(timeout=90): raise RuntimeError('Video encoding failed')
        print('Reel: adding music',flush=True)
        subprocess.run([ffmpeg_binary(),'-y','-loglevel','error','-threads','1',
                        '-i',str(silent),'-i',str(audio),'-map','0:v:0','-map','1:a:0',
                        '-c:v','copy','-c:a','aac','-b:a','128k','-ar','48000',
                        '-movflags','+faststart','-t',str(DURATION),str(output)],check=True,timeout=90)
    metadata.update({'design':'canva-approved-v1','canva_source':'DAHXo0VXJ2s','width':width,'height':height,'preview':metadata.get('preview',False),'duration':20,'music':'All This — Kevin MacLeod (CC BY 4.0)'})
    output.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--metadata',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(render(json.loads(Path(args.metadata).read_text()),args.output))
