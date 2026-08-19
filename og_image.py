"""
Dynamic Open Graph image generator for DondeVer.app
Generates branded 1200×630 PNGs with team logos and match info.
"""

import io
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont
from cachetools import TTLCache

logger = logging.getLogger("og_image")

# Cache generated images for 2 hours (key = full URL path)
_og_cache: TTLCache = TTLCache(maxsize=20, ttl=1800)  # 20 images, 30min — PNGs are ~100KB each

# Brand colors
BG_COLOR = (26, 26, 46)       # #1a1a2e
ACCENT = (16, 185, 129)       # #10b981
WHITE = (255, 255, 255)
GRAY = (156, 163, 175)        # #9ca3af
LIGHT_BG = (22, 33, 62)       # #16213e

# Image dimensions (Facebook/Twitter recommended)
W, H = 1200, 630


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if custom not available."""
    try:
        # Try common system fonts
        for name in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]:
            try:
                return ImageFont.truetype(name, size)
            except (OSError, IOError):
                continue
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


async def _download_logo(url: str, size: int = 120) -> Optional[Image.Image]:
    """Download and resize a team logo from ESPN."""
    if not url:
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; DondeVerBot/1.0)"}
        async with httpx.AsyncClient(timeout=5, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            logo = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            logo.thumbnail((size, size), Image.LANCZOS)
            return logo
    except Exception as e:
        logger.warning(f"Logo download failed: {e}")
        return None


def _draw_rounded_rect(draw: ImageDraw.Draw, xy: tuple, radius: int, fill: tuple):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _truncate_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Truncate text with ellipsis if too wide."""
    bbox = font.getbbox(text)
    if bbox[2] - bbox[0] <= max_width:
        return text
    while len(text) > 3:
        text = text[:-1]
        bbox = font.getbbox(text + "…")
        if bbox[2] - bbox[0] <= max_width:
            return text + "…"
    return text


async def generate_game_og(
    home_name: str,
    away_name: str,
    home_logo_url: str = "",
    away_logo_url: str = "",
    league_name: str = "",
    date_str: str = "",
) -> bytes:
    """Generate a 1200×630 OG image for a game page."""
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Gradient-like effect: lighter stripe at top
    _draw_rounded_rect(draw, (0, 0, W, 180), radius=0, fill=LIGHT_BG)

    # Brand bar at very top
    draw.rectangle((0, 0, W, 6), fill=ACCENT)

    # Fonts
    font_brand = _get_font(28, bold=True)
    font_team = _get_font(42, bold=True)
    font_vs = _get_font(32, bold=True)
    font_league = _get_font(24)
    font_sub = _get_font(20)
    font_small = _get_font(16)

    # Brand name top-left
    draw.text((50, 30), "DondeVer.app", fill=ACCENT, font=font_brand)

    # "Dónde ver en vivo" top-right
    draw.text((W - 350, 30), "Dónde ver en vivo", fill=GRAY, font=font_sub)

    # League pill centered
    if league_name:
        league_text = league_name.upper()
        lbbox = font_small.getbbox(league_text)
        lw = lbbox[2] - lbbox[0] + 40
        lx = (W - lw) // 2
        _draw_rounded_rect(draw, (lx, 120, lx + lw, 155), radius=12, fill=ACCENT)
        draw.text((lx + 20, 122), league_text, fill=WHITE, font=font_small)

    # Download logos in parallel
    import asyncio as _aio
    home_logo, away_logo = await _aio.gather(
        _download_logo(home_logo_url, 130),
        _download_logo(away_logo_url, 130),
    )

    # Layout: Home [logo] --- VS --- [logo] Away
    center_y = 310
    logo_y = center_y - 65

    # Home side (left)
    if home_logo:
        logo_x = 160
        img.paste(home_logo, (logo_x, logo_y), home_logo)

    home_text = _truncate_text(home_name, font_team, 380)
    hbbox = font_team.getbbox(home_text)
    hw = hbbox[2] - hbbox[0]
    draw.text((160 + 65 - hw // 2, center_y + 85), home_text, fill=WHITE, font=font_team)

    # VS in the center
    draw.text((W // 2 - 25, center_y - 20), "vs", fill=GRAY, font=font_vs)

    # Away side (right)
    if away_logo:
        logo_x = W - 290
        img.paste(away_logo, (logo_x, logo_y), away_logo)

    away_text = _truncate_text(away_name, font_team, 380)
    abbox = font_team.getbbox(away_text)
    aw = abbox[2] - abbox[0]
    draw.text((W - 290 + 65 - aw // 2, center_y + 85), away_text, fill=WHITE, font=font_team)

    # Date at bottom
    if date_str:
        draw.text((50, H - 70), date_str, fill=GRAY, font=font_sub)

    # Footer tagline
    draw.text((W - 450, H - 70), "Canales, horarios y streaming", fill=GRAY, font=font_sub)

    # Bottom accent bar
    draw.rectangle((0, H - 6, W, H), fill=ACCENT)

    # Export to PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def generate_team_og(
    team_name: str,
    team_logo_url: str = "",
    league_name: str = "",
) -> bytes:
    """Generate a 1200×630 OG image for a team page."""
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Top accent bar
    draw.rectangle((0, 0, W, 6), fill=ACCENT)
    _draw_rounded_rect(draw, (0, 0, W, 180), radius=0, fill=LIGHT_BG)

    font_brand = _get_font(28, bold=True)
    font_team = _get_font(52, bold=True)
    font_league = _get_font(26)
    font_sub = _get_font(22)

    # Brand
    draw.text((50, 30), "DondeVer.app", fill=ACCENT, font=font_brand)

    # Team logo centered
    logo = await _download_logo(team_logo_url, 160)
    center_y = 260
    if logo:
        lx = (W - logo.width) // 2
        img.paste(logo, (lx, center_y - 80), logo)
        center_y += 100

    # Team name centered
    team_text = _truncate_text(team_name, font_team, W - 100)
    tbbox = font_team.getbbox(team_text)
    tw = tbbox[2] - tbbox[0]
    draw.text(((W - tw) // 2, center_y), team_text, fill=WHITE, font=font_team)

    # League below
    if league_name:
        lbbox = font_league.getbbox(league_name)
        lw = lbbox[2] - lbbox[0]
        draw.text(((W - lw) // 2, center_y + 65), league_name, fill=GRAY, font=font_league)

    # Footer
    draw.text((50, H - 65), "Partidos, canales y horarios", fill=GRAY, font=font_sub)
    draw.text((W - 300, H - 65), "México, USA y LATAM", fill=GRAY, font=font_sub)
    draw.rectangle((0, H - 6, W, H), fill=ACCENT)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
