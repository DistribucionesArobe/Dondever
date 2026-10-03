"""Daily Instagram Reel, with durable checkpoints to prevent duplicate posts."""
import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from generate_instagram import get_todays_games, TZ_MX
from generate_reel_stadium import render

BASE = os.getenv('DONDEVER_URL', 'https://dondever.app').rstrip('/')
GRAPH = 'https://graph.facebook.com/v21.0'
CREDIT = 'All This — Kevin MacLeod (incompetech.com). Licensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/ (extracto editado).'

def eligible(games, now):
    result = []
    for game in games:
        try:
            starts = datetime.fromisoformat(game['starts_at'].replace('Z', '+00:00'))
            if game['status'] == 'STATUS_SCHEDULED' and starts.astimezone(TZ_MX).date() == now.date() and starts > now + timedelta(minutes=30) and game.get('channel_verified'):
                result.append(game)
        except (KeyError, ValueError):
            continue
    priorities = {'nfl':0,'liga-mx':1,'mlb':2,'champions':3,'nba':4}
    return sorted(result, key=lambda g:(not bool(g.get('mx_channels')),priorities.get(g['league_slug'],5),g['starts_at']))

def checked(response):
    response.raise_for_status()
    data = response.json()
    if 'error' in data:
        raise RuntimeError('Meta rejected request: ' + data['error'].get('message','unknown error'))
    return data

async def main(dry_run=False):
    now = datetime.now(TZ_MX); day = now.strftime('%Y-%m-%d')
    if dry_run and os.getenv('INTERNAL_API_KEY'):
        with httpx.Client(timeout=30) as client:
            state = checked(client.get(f'{BASE}/api/internal/reels/{day}',headers={'x-internal-api-key':os.environ['INTERNAL_API_KEY']}))
        print('Private storage verified: '+state['status'])
        if not os.getenv('INSTAGRAM_ACCESS_TOKEN') or not os.getenv('INSTAGRAM_USER_ID'):
            raise RuntimeError('Instagram credentials missing')
        print('Instagram credentials present (not published)')
    games = eligible(await get_todays_games(now.strftime('%Y%m%d')), now)
    if not games:
        print('No verified upcoming game today; skipped.'); return
    game = games[0]
    caption = f"{game['away']['name']} vs {game['home']['name']} · {game['time']} h CDMX.\nMéxico: {' / '.join(game['mx_channels']) or 'Canal por confirmar'}.\nEE. UU.: {' / '.join(game['us_channels']) or 'Canal por confirmar'}.\nConsulta la agenda en dondever.app. Enlace en el perfil.\n#DondeVer #Deportes\n\n{CREDIT}"
    if dry_run:
        print(json.dumps({'date':day,'game':game,'caption':caption,'preview':True},ensure_ascii=False)); return
    key = os.environ['INTERNAL_API_KEY']; token = os.environ['INSTAGRAM_ACCESS_TOKEN']; user = os.environ['INSTAGRAM_USER_ID']
    private = {'x-internal-api-key':key}
    endpoint = f'{BASE}/api/internal/reels/{day}'
    with httpx.Client(timeout=120) as client:
        state = checked(client.get(endpoint,headers=private))
        if state['status'] != 'new':
            print(f"Already {state['status']}; skipped to prevent duplicates."); return
        checked(client.post(endpoint+'/reserve',headers=private))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'reel.mp4'
            render({'date':day,'game':game,'preview':False,'caption':caption},output)
            with output.open('rb') as stream:
                hosted = checked(client.put(endpoint+'/video',headers={**private,'content-type':'video/mp4'},content=stream))
        # Recheck match time after rendering and upload.
        if not eligible([game],datetime.now(TZ_MX)):
            raise RuntimeError('Game starts too soon; publication stopped')
        container = checked(client.post(f'{GRAPH}/{user}/media',data={'media_type':'REELS','video_url':hosted['video_url'],'caption':caption,'share_to_feed':'true','access_token':token}))['id']
        checked(client.post(endpoint+'/checkpoint',headers=private,json={'status':'container','container':container}))
        for _ in range(60):
            status = checked(client.get(f'{GRAPH}/{container}',params={'fields':'status_code','access_token':token}))['status_code']
            if status == 'FINISHED': break
            if status in ('ERROR','EXPIRED'): raise RuntimeError('Meta could not process Reel')
            time.sleep(5)
        else: raise RuntimeError('Meta processing timed out; inspect container before retrying')
        # Save intent BEFORE the publish call: uncertain network outcomes must not retry automatically.
        checked(client.post(endpoint+'/checkpoint',headers=private,json={'status':'publishing'}))
        media = checked(client.post(f'{GRAPH}/{user}/media_publish',data={'creation_id':container,'access_token':token}))['id']
        checked(client.post(endpoint+'/checkpoint',headers=private,json={'status':'published','media':media}))
        print('Instagram Reel published: '+media)

def render_test():
    """Exercise the full NFL renderer without storage, credentials or publication."""
    game = {'league':'NFL','league_slug':'nfl', 'time':'18:15',
            'away':{'name':'Equipo visitante','short':'VIS'},
            'home':{'name':'Equipo local','short':'LOC'},
            'mx_channels':['Canal de muestra'], 'us_channels':['Canal de muestra']}
    with tempfile.TemporaryDirectory() as folder:
        output = render({'date':'2026-10-02','game':game,'preview':True},Path(folder)/'test.mp4')
        print(f'Render test completed: 720x1280, 20 seconds, {output.stat().st_size} bytes',flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--render-test',action='store_true')
    args = parser.parse_args()
    if args.render_test:
        render_test()
    else:
        asyncio.run(main(args.dry_run))
