"""Private cron storage and durable publication checkpoints."""
import hmac
import os
import re
import sqlite3
import time
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
ROOT = Path(os.getenv('REEL_STORAGE_DIR', '/data/reels'))

def db():
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ROOT / 'publications.sqlite', timeout=30)
    connection.execute('CREATE TABLE IF NOT EXISTS posts (day TEXT PRIMARY KEY, status TEXT NOT NULL, container TEXT, media TEXT)')
    return connection

def auth(request):
    expected = os.getenv('INTERNAL_API_KEY', '')
    if not expected or not hmac.compare_digest(request.headers.get('x-internal-api-key', ''), expected):
        raise HTTPException(403, 'Forbidden')

def day_check(day):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise HTTPException(400, 'Invalid day')

@router.get('/api/internal/reels/{day}')
async def status(day: str, request: Request):
    auth(request); day_check(day)
    with db() as connection:
        row = connection.execute('SELECT status,container,media FROM posts WHERE day=?', (day,)).fetchone()
    return dict(zip(('status','container','media'), row)) if row else {'status':'new'}

@router.post('/api/internal/reels/{day}/reserve')
async def reserve(day: str, request: Request):
    auth(request); day_check(day)
    with db() as connection:
        result = connection.execute('INSERT OR IGNORE INTO posts(day,status) VALUES (?,?)', (day,'reserved'))
        if result.rowcount != 1:
            raise HTTPException(409, 'Already reserved; inspect publication before retrying')
    return {'status':'reserved'}

@router.put('/api/internal/reels/{day}/video')
async def upload(day: str, request: Request):
    auth(request); day_check(day)
    target = ROOT / f'{day}.mp4'
    temporary = target.with_suffix('.upload')
    total = 0
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        with temporary.open('wb') as stream:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 50_000_000:
                    raise HTTPException(413, 'Video too large')
                stream.write(chunk)
        with temporary.open('rb') as stream:
            if stream.read(12)[4:8] != b'ftyp':
                raise HTTPException(400, 'Expected MP4')
        temporary.replace(target)
        for old in ROOT.glob('*.mp4'):
            if old.stat().st_mtime < time.time() - 14*86400:
                old.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return {'video_url':f'https://dondever.app/reels/{day}.mp4'}

@router.post('/api/internal/reels/{day}/checkpoint')
async def checkpoint(day: str, request: Request):
    auth(request); day_check(day)
    data = await request.json()
    transitions = {'container':'reserved', 'publishing':'container', 'published':'publishing'}
    state = data.get('status')
    if state not in transitions:
        raise HTTPException(400, 'Invalid state')
    with db() as connection:
        result = connection.execute('UPDATE posts SET status=?, container=COALESCE(?,container), media=COALESCE(?,media) WHERE day=? AND status=?',
            (state,data.get('container'),data.get('media'),day,transitions[state]))
        if result.rowcount != 1:
            raise HTTPException(409, 'Invalid transition')
    return {'status':state}

@router.get('/reels/{filename}')
async def video(filename: str):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.mp4', filename) or not (ROOT / filename).is_file():
        raise HTTPException(404, 'Video not found')
    return FileResponse(ROOT / filename, media_type='video/mp4')
