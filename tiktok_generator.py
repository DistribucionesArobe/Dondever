"""
TikTok/Reels/Shorts video generator for DondeVer.app
Generates vertical slideshow videos (1080x1920) with today's picks.
Auto-generates daily — Alejandro just uploads to TikTok/IG/YT.
"""

import os
import asyncio
import logging
import random
import subprocess
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import TZ_MX, AFFILIATES, HOME_LEFT_SPORTS
from sports_api import get_todays_games

logger = logging.getLogger("dondever.tiktok")

# ── Config ──────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
BG_COLOR = (10, 10, 15)  # Near-black
ACCENT = (255, 107, 0)    # DondeVer orange
WHITE = (255, 255, 255)
GRAY = (160, 160, 170)
GREEN = (0, 200, 100)
DARK_CARD = (25, 25, 35)

SLIDE_DURATION = 4  # seconds per slide
FPS = 1  # 1 frame per second (slideshow style, small file)

OUTPUT_DIR = Path(os.getenv("TIKTOK_OUTPUT_DIR", "static/tiktok"))

# ── Font helpers ────────────────────────────────────────

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if custom not available."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Drawing helpers ─────────────────────────────────────

def draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.pieslice([x0, y0, x0 + 2*radius, y0 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x1 - 2*radius, y0, x1, y0 + 2*radius], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2*radius, x0 + 2*radius, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2*radius, y1 - 2*radius, x1, y1], 0, 90, fill=fill)


def text_center_x(draw, text, font, y, fill, img_width=WIDTH):
    """Draw text centered horizontally."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (img_width - tw) // 2
    draw.text((x, y), text, font=font, fill=fill)
    return y + (bbox[3] - bbox[1]) + 10


def get_team_order(game: dict) -> tuple:
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


def get_pick_team(game: dict) -> str:
    home = game["home"]["name"]
    away = game["away"]["name"]
    return home if random.random() < 0.6 else away


def format_time_mx(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


# ── Slide generators ────────────────────────────────────

def create_intro_slide(games: list, date_str: str) -> Image.Image:
    """Slide 1: Hook slide — '5 JUEGOS QUE NO TE PUEDES PERDER HOY'"""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to add logo
    logo_path = Path("static/logo.png")
    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((200, 200), Image.LANCZOS)
            logo_x = (WIDTH - 200) // 2
            img.paste(logo, (logo_x, 300), logo)
        except Exception:
            pass

    font_big = get_font(72, bold=True)
    font_med = get_font(48, bold=True)
    font_small = get_font(36)

    y = 560
    y = text_center_x(draw, f"{len(games)}", font_big, y, ACCENT)
    y += 10
    y = text_center_x(draw, "JUEGOS QUE NO TE", font_med, y, WHITE)
    y = text_center_x(draw, "PUEDES PERDER HOY", font_med, y, WHITE)
    y += 40
    y = text_center_x(draw, date_str, font_small, y, GRAY)
    y += 60

    # Orange accent line
    line_w = 200
    draw.rectangle([(WIDTH - line_w)//2, y, (WIDTH + line_w)//2, y + 4], fill=ACCENT)
    y += 40

    y = text_center_x(draw, "DondeVer.app", font_small, y, ACCENT)

    # Bottom CTA
    font_cta = get_font(30)
    text_center_x(draw, "Sigue para ver los picks", font_cta, HEIGHT - 200, GRAY)

    return img


def create_game_slide(game: dict, index: int, total: int) -> Image.Image:
    """Individual game slide with pick."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_counter = get_font(28)
    font_league = get_font(36, bold=True)
    font_team = get_font(56, bold=True)
    font_vs = get_font(40)
    font_time = get_font(36)
    font_channel = get_font(32)
    font_pick_label = get_font(32, bold=True)
    font_pick = get_font(48, bold=True)
    font_brand = get_font(28)

    first, second = get_team_order(game)
    time_str = format_time_mx(game["date"])
    league = game.get("league_name", "")
    emoji = game.get("emoji", "")
    channels = [b["channel"] for b in game.get("broadcasts", [])[:3]]
    channels_text = " / ".join(channels) if channels else "Por confirmar"
    pick_team = get_pick_team(game)

    # Counter top right
    draw.text((WIDTH - 120, 80), f"{index}/{total}", font=font_counter, fill=GRAY)

    # League name
    y = 300
    y = text_center_x(draw, f"{emoji} {league}", font_league, y, ACCENT)
    y += 50

    # Team 1
    y = text_center_x(draw, first, font_team, y, WHITE)
    y += 20

    # VS
    y = text_center_x(draw, "VS", font_vs, y, GRAY)
    y += 20

    # Team 2
    y = text_center_x(draw, second, font_team, y, WHITE)
    y += 60

    # Time card
    draw_rounded_rect(draw, (WIDTH//2 - 200, y, WIDTH//2 + 200, y + 70), 15, DARK_CARD)
    text_center_x(draw, f"HOY {time_str} (MX)", font_time, y + 15, WHITE)
    y += 110

    # Channels
    y = text_center_x(draw, f"Donde verlo:", font_channel, y, GRAY)
    y = text_center_x(draw, channels_text, font_channel, y, WHITE)
    y += 60

    # DondeVer Pick — the money part
    draw_rounded_rect(draw, (80, y, WIDTH - 80, y + 180), 20, (30, 50, 30))
    draw.rectangle([80, y, 90, y + 180], fill=GREEN)  # Green left accent
    text_center_x(draw, "DONDEVER PICK", font_pick_label, y + 20, GREEN)
    text_center_x(draw, pick_team, font_pick, y + 70, WHITE)
    y += 220

    # Brand footer
    text_center_x(draw, "DondeVer.app", font_brand, HEIGHT - 150, ACCENT)
    text_center_x(draw, "Picks gratis diarios por WhatsApp", font_brand, HEIGHT - 100, GRAY)

    return img


def create_cta_slide() -> Image.Image:
    """Final CTA slide — follow + WhatsApp."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_big = get_font(56, bold=True)
    font_med = get_font(40, bold=True)
    font_small = get_font(32)
    font_brand = get_font(36, bold=True)

    # Logo
    logo_path = Path("static/logo.png")
    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((180, 180), Image.LANCZOS)
            logo_x = (WIDTH - 180) // 2
            img.paste(logo, (logo_x, 350), logo)
        except Exception:
            pass

    y = 580
    y = text_center_x(draw, "RECIBE GRATIS", font_big, y, WHITE)
    y = text_center_x(draw, "POR WHATSAPP", font_big, y, ACCENT)
    y += 40

    # Feature list
    features = [
        ("Picks diarios", "escribe PICKS"),
        ("Alertas 1h antes", "escribe ALERTA + equipo"),
        ("Goles en tiempo real", "automatico"),
    ]
    for feat, desc in features:
        draw_rounded_rect(draw, (100, y, WIDTH - 100, y + 70), 12, (0, 80, 40))
        text_center_x(draw, feat, font_med, y + 8, WHITE)
        text_center_x(draw, desc, font_brand, y + 42, GREEN)
        y += 85

    y += 20
    y = text_center_x(draw, "+1 (571) 546-3202", font_small, y, GREEN)
    y += 40

    # Follow CTA
    draw_rounded_rect(draw, (100, y, WIDTH - 100, y + 80), 20, DARK_CARD)
    text_center_x(draw, "@dondeverapp", font_med, y + 18, ACCENT)
    y += 110

    y = text_center_x(draw, "Siguenos para picks diarios", font_small, y, GRAY)
    y += 80

    text_center_x(draw, "DondeVer.app", font_brand, y, ACCENT)

    return img


# ── Video assembly ──────────────────────────────────────

def slides_to_video(slides: list[Image.Image], output_path: str) -> str:
    """
    Convert PIL slides to MP4 via ffmpeg concat demuxer.
    Memory efficient: guarda cada slide UNA vez como JPEG,
    ffmpeg la repite por duracion en vez de duplicar archivos.
    """
    import tempfile
    import shutil

    tmp_dir = Path(tempfile.mkdtemp(prefix="dondever_"))
    slide_paths = []
    concat_lines = []

    # 1) Guardar cada slide UNA vez como JPEG (mucho mas pequeño que PNG)
    for i, slide in enumerate(slides):
        fp = tmp_dir / f"slide_{i:02d}.jpg"
        if slide.mode != "RGB":
            slide = slide.convert("RGB")
        slide.save(fp, "JPEG", quality=82, optimize=True)
        slide_paths.append(fp)
        concat_lines.append(f"file '{fp}'")
        concat_lines.append(f"duration {SLIDE_DURATION}")

    # ffmpeg concat requiere repetir el ultimo archivo sin duration
    if slide_paths:
        concat_lines.append(f"file '{slide_paths[-1]}'")

    # Liberar memoria PIL inmediatamente
    try:
        slides.clear()
    except Exception:
        pass

    concat_file = tmp_dir / "concat.txt"
    concat_file.write_text("\n".join(concat_lines))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vsync", "vfr",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-1500:]
            logger.error(f"ffmpeg error (rc={result.returncode}): {stderr_tail}")
            raise RuntimeError(f"ffmpeg rc={result.returncode}: {stderr_tail}")
        # Verify the output file actually exists and has content
        out_size = 0
        try:
            out_size = os.path.getsize(output_path)
        except Exception:
            pass
        if out_size < 1000:
            raise RuntimeError(
                f"ffmpeg rc=0 but output file is missing/empty (size={out_size}). "
                f"stderr tail: {(result.stderr or '')[-500:]}"
            )
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout")
        raise RuntimeError("ffmpeg timeout (90s)")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return output_path


# ── Main generator ──────────────────────────────────────

async def generate_daily_video() -> str:
    """
    Generate today's TikTok/Reels video.
    Returns path to the generated video file.
    """
    logger.info("Generating daily TikTok video...")

    games = await get_todays_games()
    if not games:
        logger.info("No games today — skipping video generation")
        return ""

    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d de %B, %Y")

    # Select top 5 most interesting games (prioritize popular leagues)
    priority_leagues = [
        "Liga MX", "Champions League", "NFL", "NBA", "Premier League",
        "La Liga", "MLB", "Serie A", "MLS", "UFC", "Formula 1",
    ]

    def game_priority(g):
        league = g.get("league_name", "")
        try:
            return priority_leagues.index(league)
        except ValueError:
            return 99

    games_sorted = sorted(games, key=game_priority)
    top_games = games_sorted[:5]

    # Build slides
    slides = []
    slides.append(create_intro_slide(top_games, date_str))

    for i, game in enumerate(top_games, 1):
        slides.append(create_game_slide(game, i, len(top_games)))

    slides.append(create_cta_slide())

    # Generate video
    date_tag = now.strftime("%Y%m%d")
    output_path = str(OUTPUT_DIR / f"dondever_picks_{date_tag}.mp4")

    # slides_to_video raises RuntimeError on failure — deja que propague
    video_path = slides_to_video(slides, output_path)

    logger.info(f"TikTok video generated: {video_path}")
    return video_path


# ── Also generate individual slides as images ───────────

async def generate_daily_images() -> list[str]:
    """
    Generate today's picks as individual images (for Instagram carousel or Stories).
    Returns list of image paths.
    """
    games = await get_todays_games()
    if not games:
        return []

    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d de %B, %Y")
    date_tag = now.strftime("%Y%m%d")

    priority_leagues = [
        "Liga MX", "Champions League", "NFL", "NBA", "Premier League",
        "La Liga", "MLB", "Serie A", "MLS", "UFC", "Formula 1",
    ]

    def game_priority(g):
        league = g.get("league_name", "")
        try:
            return priority_leagues.index(league)
        except ValueError:
            return 99

    games_sorted = sorted(games, key=game_priority)
    top_games = games_sorted[:5]

    img_dir = OUTPUT_DIR / "images" / date_tag
    img_dir.mkdir(parents=True, exist_ok=True)

    paths = []

    intro = create_intro_slide(top_games, date_str)
    intro_path = str(img_dir / "00_intro.png")
    intro.save(intro_path, "PNG")
    paths.append(intro_path)

    for i, game in enumerate(top_games, 1):
        slide = create_game_slide(game, i, len(top_games))
        slide_path = str(img_dir / f"{i:02d}_game.png")
        slide.save(slide_path, "PNG")
        paths.append(slide_path)

    cta = create_cta_slide()
    cta_path = str(img_dir / "99_cta.png")
    cta.save(cta_path, "PNG")
    paths.append(cta_path)

    logger.info(f"Generated {len(paths)} TikTok images in {img_dir}")
    return paths


if __name__ == "__main__":
    path = asyncio.run(generate_daily_video())
    if path:
        print(f"Video saved to: {path}")
    else:
        print("No video generated")
