"""Render a 20-second, faceless DondeVer reel. Never publishes anything.

python generate_reel.py --date 2026-10-01 --league nfl --preview --output previews/reels/nfl.mp4
For production, omit --preview: only future scheduled games are eligible.
"""
import argparse
import asyncio
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import wave
from datetime import datetime

import httpx
from PIL import Image, ImageDraw, ImageFont
from generate_instagram import get_todays_games, TZ_MX

ROOT = Path(__file__).resolve().parent
W, H, FPS, DURATION = 1080, 1920, 30, 20
BG, INK, GREEN, MUTED = '#f3f6f0', '#11251d', '#087b4c', '#54645b'
FONT = ROOT / 'static/fonts/Manrope.ttf'


def font(size):
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_name('Bold')
    return f


def text(draw, value, y, size=64, color=INK, width=900):
    """Center wrapped text inside the mobile-safe area; return bottom edge."""
    f = font(size)
    lines = []
    for paragraph in str(value).split('\n'):
        line = ''
        for word in paragraph.split():
            if draw.textlength(word, font=f) > width:
                while draw.textlength(word, font=f) > width and size > 20:
                    size -= 2
                    f = font(size)
            proposed = (line + ' ' + word).strip()
            if line and draw.textlength(proposed, font=f) > width:
                lines.append(line)
                line = word
            else:
                line = proposed
        lines.append(line)
    for line in lines:
        draw.text((W/2, y), line, font=f, fill=color, anchor='mt')
        y += int(size * 1.3)
    return y


def load_badge(team):
    try:
        url = team.get('logo', '')
        if url.startswith('https://a.espncdn.com/'):
            response = httpx.get(url, timeout=15)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert('RGBA')
            image.thumbnail((230, 230))
            return image
    except (httpx.HTTPError, OSError):
        pass
    return None


def base(game, date, preview):
    image = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((78, 240, 1002, 1570), radius=56, fill='white')
    d.rounded_rectangle((90, 160, 990, 178), radius=8, fill=GREEN)
    text(d, 'DONDEVER.APP', 200, 30, GREEN)
    label = f"{game['league']}  /  {date}"
    text(d, label, 275, 30, MUTED)
    text(d, 'MUESTRA · NO PUBLICADA' if preview else 'AGENDA DEPORTIVA', 1640, 25, MUTED)
    return image


def match_scene(game, date, preview, badges):
    image = base(game, date, preview)
    d = ImageDraw.Draw(image)
    text(d, '¿Dónde pasan\neste partido?', 370, 78)
    for x, key in [(320, 'away'), (760, 'home')]:
        badge = badges[key]
        if badge:
            image.paste(badge, (x-badge.width//2, 700), badge)
        else:
            d.text((x, 795), game[key].get('short', '?')[:4], font=font(78), fill=GREEN, anchor='mm')
    text(d, 'VS', 790, 40, MUTED)
    y = text(d, game['away']['name'], 1010, 54, width=830)
    text(d, 'contra', y+15, 30, MUTED)
    y = text(d, game['home']['name'], y+78, 54, width=830)
    text(d, 'Horario y canales →', max(1430, y+45), 34, GREEN)
    return image


def info_scene(game, date, preview):
    image = base(game, date, preview)
    d = ImageDraw.Draw(image)
    text(d, 'Guarda el horario', 385, 64)
    text(d, game['time'], 510, 138, GREEN)
    text(d, 'Hora CDMX · UTC−6', 705, 35, MUTED)
    y = 860
    for label, key in [('MÉXICO', 'mx_channels'), ('ESTADOS UNIDOS', 'us_channels')]:
        text(d, label, y, 28, GREEN)
        channels = game.get(key) or []
        value = ' · '.join(channels) if channels else 'Canal por confirmar'
        # No league defaults and no inference of Mexican rights from US feeds.
        y = text(d, value, y+65, 48, width=800) + 75
    text(d, 'Fuente: agenda ESPN', 1450, 25, MUTED)
    return image


def end_scene(game, date, preview):
    image = base(game, date, preview)
    d = ImageDraw.Draw(image)
    text(d, 'Tu equipo.\nTu próximo partido.', 440, 78)
    text(d, 'Consulta la agenda\ny elige tus favoritos.', 780, 47, MUTED)
    d.rounded_rectangle((130, 1090, 950, 1245), radius=36, fill=GREEN)
    text(d, 'dondever.app', 1134, 63, 'white', width=770)
    text(d, 'Enlace en el perfil', 1330, 36, MUTED)
    return image


def music(path):
    """Original sports fanfare: synthesized brass, bass and stadium drums."""
    import random
    rng = random.Random(20261001)
    rate, tempo = 44100, 120
    beat_length = 60 / tempo
    chords = ((146.832, 174.614, 220), (130.813, 164.814, 195.998),
              (116.541, 146.832, 174.614), (130.813, 164.814, 195.998))
    # Original rising motif, independent of any broadcast theme.
    motif = (293.665, 0, 349.228, 440, 391.995, 349.228, 293.665, 0,
             261.626, 329.628, 391.995, 523.251, 440, 391.995, 329.628, 0)
    samples = bytearray()
    peak = 0
    for i in range(rate * DURATION):
        t = i / rate
        beat = int(t / beat_length)
        phase = t % beat_length
        eighth = t % (beat_length / 2)
        chord = chords[(beat // 4) % len(chords)]
        brass = 0
        for frequency in chord:
            vibrato = .002 * math.sin(2 * math.pi * 5 * t)
            angle = 2 * math.pi * frequency * (1 + vibrato) * t
            tone = (math.sin(angle) + .35 * math.sin(2 * angle) +
                    .17 * math.sin(3 * angle) + .08 * math.sin(4 * angle))
            envelope = min(1, phase / .025) * math.exp(-phase * 2.8)
            brass += .043 * envelope * tone
        note = motif[beat % len(motif)]
        lead = 0
        if note:
            angle = 2 * math.pi * note * t
            lead = .085 * min(1, phase/.018) * math.exp(-phase*3) * (
                math.sin(angle) + .25*math.sin(2*angle))
        bass = .08 * math.sin(2*math.pi*chord[0]/2*t) * math.exp(-eighth*6)
        kick = .20 * math.exp(-phase*18) * math.sin(
            2*math.pi*(48*phase + 3.0*(1-math.exp(-phase*25))))
        noise = rng.uniform(-1, 1)
        snare = (.12*noise*math.exp(-phase*25) +
                 .07*math.sin(2*math.pi*175*t)*math.exp(-phase*20)) if beat%2 else 0
        hats = .018*noise*math.exp(-eighth*65)
        # Cymbal swells and scene-boundary impacts support the edit.
        crash = sum(.065*noise*math.exp(-(t-hit)*3) for hit in (0,6,14)
                    if 0 <= t-hit < 1.8)
        ramp = min(1, t/.12, max(0,(DURATION-t)/.9))
        sample = ramp * (brass + lead + bass + kick + snare + hats + crash)
        sample = .8 * math.tanh(sample*1.6)
        peak = max(peak, abs(sample))
        samples.extend(struct.pack('<h', int(sample*32767)))
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples)
    return peak


def ffmpeg_binary():
    binary = os.getenv('FFMPEG_BIN') or shutil.which('ffmpeg')
    if binary:
        return binary
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render(game, date, output, preview=False):
    if not game.get('time') or not all((game.get(k) or {}).get('name') for k in ('home','away')):
        raise ValueError('The reel requires both team names and a confirmed schedule time.')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    badges = {k: load_badge(game[k]) for k in ('home','away')}
    scenes = [match_scene(game,date,preview,badges), info_scene(game,date,preview), end_scene(game,date,preview)]
    # Keep inspectable scene stills next to each video.
    for i, scene in enumerate(scenes, 1):
        scene.save(output.with_name(output.stem+f'-scene-{i}.jpg'), quality=92)
    with tempfile.TemporaryDirectory(prefix='dondever-reel-') as temporary:
        audio = Path(temporary)/'music.wav'
        music(audio)
        process = subprocess.Popen([
            ffmpeg_binary(), '-y', '-loglevel', 'error', '-f', 'rawvideo',
            '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
            '-i', str(audio), '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart', '-t', str(DURATION), str(output)
        ], stdin=subprocess.PIPE)
        try:
            for frame in range(FPS*DURATION):
                t = frame/FPS
                index = 0 if t<6 else 1 if t<14 else 2
                start, end = [(0,6),(6,14),(14,20)][index]
                scene = scenes[index]
                # Restrained zoom; all content remains inside generous safe margins.
                progress = (t-start)/(end-start)
                zoom = 1+.015*progress
                enlarged = scene.resize((int(W*zoom), int(H*zoom)), Image.Resampling.BILINEAR)
                x,y = (enlarged.width-W)//2, (enlarged.height-H)//2
                animated = enlarged.crop((x,y,x+W,y+H))
                if index and t-start<.3:
                    animated = Image.blend(scenes[index-1], animated, (t-start)/.3)
                d = ImageDraw.Draw(animated)
                d.rounded_rectangle((90, 1725, 990, 1734), radius=4, fill='#dce5dc')
                d.rounded_rectangle((90, 1725, 90+int(900*t/DURATION), 1734), radius=4, fill=GREEN)
                process.stdin.write(animated.tobytes())
        finally:
            process.stdin.close()
        if process.wait():
            raise RuntimeError('FFmpeg could not encode the reel.')
    metadata = {'date':date, 'preview':preview, 'duration':DURATION,
                'width':W, 'height':H, 'fps':FPS, 'game':game,
                'caption':f"{game['away']['name']} vs {game['home']['name']}: consulta horarios y canales en dondever.app. Enlace en el perfil. #DondeVer #{game['league']}"}
    output.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return str(output)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(TZ_MX).strftime('%Y-%m-%d'))
    parser.add_argument('--league', default='nfl')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', default='previews/reels/dondever.mp4')
    args = parser.parse_args()
    date = datetime.strptime(args.date, '%Y-%m-%d')
    games = await get_todays_games(date.strftime('%Y%m%d'))
    candidates = [g for g in games if g['league_slug']==args.league and g.get('time')]
    if not args.preview:
        now = datetime.now(TZ_MX)
        candidates = [g for g in candidates if g.get('status')=='STATUS_SCHEDULED' and
                      datetime.strptime(args.date+' '+g['time'], '%Y-%m-%d %H:%M').replace(tzinfo=TZ_MX)>now]
    if not candidates:
        raise SystemExit('No eligible games. Nothing generated or published.')
    candidates.sort(key=lambda g: (not bool(g.get('mx_channels')), g['time']))
    print(render(candidates[0], args.date, args.output, args.preview))


if __name__=='__main__':
    asyncio.run(main())
