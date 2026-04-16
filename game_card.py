"""
Game card image generator for DondeVer.app Twitter bot.
Generates matchup cards with team logos, time, channel, and pick.
Uses Pillow to create images and urllib to fetch ESPN team logos.
"""

import io
import logging
import os
import hashlib
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("dondever.gamecard")

# ── Config ───────────────────────────────────────────────
CARD_WIDTH = 1200
CARD_HEIGHT = 675  # Twitter recommended 1.91:1 ratio
CACHE_DIR = Path("/tmp/dondever_logos")
CACHE_DIR.mkdir(exist_ok=True)

# Colors
BG_COLOR = "#0F172A"       # dark navy
ACCENT_COLOR = "#10B981"   # emerald green (DondeVer brand)
TEXT_WHITE = "#FFFFFF"
TEXT_GRAY = "#94A3B8"
TEXT_LIGHT = "#E2E8F0"
PICK_BG = "#059669"        # darker green for pick badge
DIVIDER_COLOR = "#1E293B"  # subtle divider


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if system fonts aren't available."""
    font_paths = [
        # Linux (Render)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFPro.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fetch_logo(logo_url: str, size: int = 120) -> Image.Image | None:
    """
    Fetch team logo from URL, cache locally, resize to square.
    ESPN logo URLs: https://a.espncdn.com/i/teamlogos/...
    """
    if not logo_url:
        return None

    # Cache by URL hash
    url_hash = hashlib.md5(logo_url.encode()).hexdigest()
    cache_path = CACHE_DIR / f"{url_hash}_{size}.png"

    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGBA")
        except Exception:
            pass

    try:
        req = urllib.request.Request(logo_url, headers={"User-Agent": "DondeVer/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            img_data = resp.read()
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img = img.resize((size, size), Image.Resampling.LANCZOS)
        img.save(cache_path, "PNG")
        return img
    except Exception as e:
        logger.warning(f"Logo fetch failed for {logo_url}: {e}")
        return None


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: str):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _draw_team_block(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    team_name: str,
    logo_url: str,
    x_center: int,
    y_top: int,
    font_name: ImageFont.FreeTypeFont,
):
    """Draw a team logo + name centered at x_center."""
    # Logo
    logo = _fetch_logo(logo_url, size=100)
    if logo:
        logo_x = x_center - 50
        img.paste(logo, (logo_x, y_top), logo)
    else:
        # Placeholder circle
        draw.ellipse(
            [x_center - 45, y_top + 5, x_center + 45, y_top + 95],
            fill="#334155",
        )

    # Team name (below logo)
    name_y = y_top + 110
    bbox = draw.textbbox((0, 0), team_name, font=font_name)
    tw = bbox[2] - bbox[0]
    # Truncate if too wide
    display_name = team_name
    if tw > 380:
        display_name = team_name[:18] + "..."
        bbox = draw.textbbox((0, 0), display_name, font=font_name)
        tw = bbox[2] - bbox[0]
    draw.text((x_center - tw // 2, name_y), display_name, fill=TEXT_WHITE, font=font_name)


def generate_game_card(
    home_name: str,
    away_name: str,
    home_logo_url: str = "",
    away_logo_url: str = "",
    league_name: str = "",
    emoji: str = "",
    time_str: str = "",
    channels: str = "",
    pick_team: str = "",
    pick_reason: str = "",
    sport: str = "soccer",
    home_left: bool = True,
) -> bytes:
    """
    Generate a game card image as PNG bytes.

    Returns PNG image bytes ready for Twitter media upload.
    """
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Fonts
    font_league = _get_font(22)
    font_team = _get_font(28, bold=True)
    font_vs = _get_font(36, bold=True)
    font_time = _get_font(40, bold=True)
    font_channel = _get_font(20)
    font_pick = _get_font(22, bold=True)
    font_pick_reason = _get_font(18)
    font_brand = _get_font(18, bold=True)

    # Determine display order
    if home_left:
        left_name, left_logo = home_name, home_logo_url
        right_name, right_logo = away_name, away_logo_url
    else:
        left_name, left_logo = away_name, away_logo_url
        right_name, right_logo = home_name, home_logo_url

    # ── Top bar (accent stripe) ──────────────────────────
    draw.rectangle([0, 0, CARD_WIDTH, 6], fill=ACCENT_COLOR)

    # ── League badge ─────────────────────────────────────
    league_text = f"{emoji} {league_name}" if emoji else league_name
    league_bbox = draw.textbbox((0, 0), league_text, font=font_league)
    league_w = league_bbox[2] - league_bbox[0]
    _draw_rounded_rect(
        draw,
        (CARD_WIDTH // 2 - league_w // 2 - 16, 24,
         CARD_WIDTH // 2 + league_w // 2 + 16, 58),
        radius=14,
        fill="#1E293B",
    )
    draw.text(
        (CARD_WIDTH // 2 - league_w // 2, 28),
        league_text, fill=ACCENT_COLOR, font=font_league,
    )

    # ── Team blocks ──────────────────────────────────────
    team_y = 80
    left_center = CARD_WIDTH // 4
    right_center = 3 * CARD_WIDTH // 4

    _draw_team_block(img, draw, left_name, left_logo, left_center, team_y, font_team)
    _draw_team_block(img, draw, right_name, right_logo, right_center, team_y, font_team)

    # ── VS ───────────────────────────────────────────────
    vs_bbox = draw.textbbox((0, 0), "vs", font=font_vs)
    vs_w = vs_bbox[2] - vs_bbox[0]
    draw.text(
        (CARD_WIDTH // 2 - vs_w // 2, team_y + 50),
        "vs", fill=TEXT_GRAY, font=font_vs,
    )

    # ── Time ─────────────────────────────────────────────
    if time_str:
        time_display = f"{time_str} MX"
        time_bbox = draw.textbbox((0, 0), time_display, font=font_time)
        tw = time_bbox[2] - time_bbox[0]
        draw.text(
            (CARD_WIDTH // 2 - tw // 2, 290),
            time_display, fill=ACCENT_COLOR, font=font_time,
        )

    # ── Channels ─────────────────────────────────────────
    if channels:
        ch_text = f"📺 {channels}"
        ch_bbox = draw.textbbox((0, 0), ch_text, font=font_channel)
        cw = ch_bbox[2] - ch_bbox[0]
        draw.text(
            (CARD_WIDTH // 2 - cw // 2, 345),
            ch_text, fill=TEXT_GRAY, font=font_channel,
        )

    # ── Divider ──────────────────────────────────────────
    draw.rectangle([80, 385, CARD_WIDTH - 80, 387], fill=DIVIDER_COLOR)

    # ── Pick section ─────────────────────────────────────
    if pick_team:
        # Pick badge
        pick_text = f"🎯 Pick: {pick_team}"
        pick_bbox = draw.textbbox((0, 0), pick_text, font=font_pick)
        pw = pick_bbox[2] - pick_bbox[0]
        badge_x = CARD_WIDTH // 2 - pw // 2 - 20
        _draw_rounded_rect(
            draw,
            (badge_x, 405, badge_x + pw + 40, 445),
            radius=12,
            fill=PICK_BG,
        )
        draw.text(
            (CARD_WIDTH // 2 - pw // 2, 410),
            pick_text, fill=TEXT_WHITE, font=font_pick,
        )

        # Reason
        if pick_reason:
            reason_bbox = draw.textbbox((0, 0), pick_reason, font=font_pick_reason)
            rw = reason_bbox[2] - reason_bbox[0]
            draw.text(
                (CARD_WIDTH // 2 - rw // 2, 460),
                pick_reason, fill=TEXT_GRAY, font=font_pick_reason,
            )

    # ── Bottom bar ───────────────────────────────────────
    # Background strip
    draw.rectangle([0, CARD_HEIGHT - 60, CARD_WIDTH, CARD_HEIGHT], fill="#0B1120")

    # Brand left
    brand = "dondever.app"
    draw.text((30, CARD_HEIGHT - 45), brand, fill=ACCENT_COLOR, font=font_brand)

    # WhatsApp CTA right
    wa_text = "📲 Picks gratis: wa.me/15715463202"
    wa_bbox = draw.textbbox((0, 0), wa_text, font=font_brand)
    wa_w = wa_bbox[2] - wa_bbox[0]
    draw.text(
        (CARD_WIDTH - wa_w - 30, CARD_HEIGHT - 45),
        wa_text, fill=TEXT_LIGHT, font=font_brand,
    )

    # Export to PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


def generate_live_card(
    home_name: str,
    away_name: str,
    home_score: str,
    away_score: str,
    home_logo_url: str = "",
    away_logo_url: str = "",
    league_name: str = "",
    emoji: str = "",
    event_type: str = "goal",
    channels: str = "",
    sport: str = "soccer",
    home_left: bool = True,
) -> bytes:
    """
    Generate a live event card (goal, final, etc.) with scores.
    """
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_league = _get_font(22)
    font_team = _get_font(26, bold=True)
    font_score = _get_font(72, bold=True)
    font_event = _get_font(28, bold=True)
    font_brand = _get_font(18, bold=True)

    if home_left:
        left_name, left_logo, left_score = home_name, home_logo_url, home_score
        right_name, right_logo, right_score = away_name, away_logo_url, away_score
    else:
        left_name, left_logo, left_score = away_name, away_logo_url, away_score
        right_name, right_logo, right_score = home_name, home_logo_url, home_score

    # Top accent
    draw.rectangle([0, 0, CARD_WIDTH, 6], fill=ACCENT_COLOR)

    # Event type banner
    event_labels = {
        "goal": "⚽ GOOOL!",
        "score_change": "🔔 ANOTACIÓN",
        "started": "🟢 EN VIVO",
        "halftime": "⏸️ MEDIO TIEMPO",
        "final": "🏁 FINAL",
    }
    event_text = event_labels.get(event_type, "EN VIVO")
    event_color = "#EF4444" if event_type == "goal" else ACCENT_COLOR
    event_bbox = draw.textbbox((0, 0), event_text, font=font_event)
    ew = event_bbox[2] - event_bbox[0]
    _draw_rounded_rect(
        draw,
        (CARD_WIDTH // 2 - ew // 2 - 20, 20, CARD_WIDTH // 2 + ew // 2 + 20, 60),
        radius=14,
        fill=event_color,
    )
    draw.text(
        (CARD_WIDTH // 2 - ew // 2, 24),
        event_text, fill=TEXT_WHITE, font=font_event,
    )

    # League
    league_text = f"{emoji} {league_name}" if emoji else league_name
    lb = draw.textbbox((0, 0), league_text, font=font_league)
    lw = lb[2] - lb[0]
    draw.text((CARD_WIDTH // 2 - lw // 2, 75), league_text, fill=TEXT_GRAY, font=font_league)

    # Teams + logos
    team_y = 120
    left_center = CARD_WIDTH // 4
    right_center = 3 * CARD_WIDTH // 4

    _draw_team_block(img, draw, left_name, left_logo, left_center, team_y, font_team)
    _draw_team_block(img, draw, right_name, right_logo, right_center, team_y, font_team)

    # Scores (big, centered)
    score_text = f"{left_score}  -  {right_score}"
    sb = draw.textbbox((0, 0), score_text, font=font_score)
    sw = sb[2] - sb[0]
    draw.text(
        (CARD_WIDTH // 2 - sw // 2, 280),
        score_text, fill=TEXT_WHITE, font=font_score,
    )

    # Channels (if started)
    if event_type == "started" and channels:
        ch_text = f"📺 {channels}"
        cb = draw.textbbox((0, 0), ch_text, font=font_league)
        draw.text((CARD_WIDTH // 2 - (cb[2] - cb[0]) // 2, 380), ch_text, fill=TEXT_GRAY, font=font_league)

    # Bottom bar
    draw.rectangle([0, CARD_HEIGHT - 60, CARD_WIDTH, CARD_HEIGHT], fill="#0B1120")
    draw.text((30, CARD_HEIGHT - 45), "dondever.app", fill=ACCENT_COLOR, font=font_brand)
    wa = "📲 Picks gratis: wa.me/15715463202"
    wb = draw.textbbox((0, 0), wa, font=font_brand)
    draw.text((CARD_WIDTH - (wb[2] - wb[0]) - 30, CARD_HEIGHT - 45), wa, fill=TEXT_LIGHT, font=font_brand)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()
