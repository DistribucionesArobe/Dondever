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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
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
    logo_size: int = 100,
):
    """Draw a team logo or monogram with a centered, wrapping team label."""
    logo = _fetch_logo(logo_url, size=logo_size)
    if logo:
        img.paste(logo, (x_center - logo_size // 2, y_top), logo)
    else:
        radius = logo_size // 2 - 3
        draw.ellipse(
            [x_center - radius, y_top + 3, x_center + radius, y_top + logo_size - 3],
            fill="#E4F3E8",
        )
        initials = "".join(word[0] for word in str(team_name).split()[:2]).upper() or "?"
        initials_font = _get_font(26, bold=True)
        bbox = draw.textbbox((0, 0), initials, font=initials_font)
        draw.text((x_center - (bbox[2] - bbox[0]) // 2, y_top + logo_size // 2 - 15),
                  initials, fill="#17613A", font=initials_font)

    words = str(team_name).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font_name)[2] <= 405:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) >= 2:
                lines[-1] = lines[-1].rstrip("…") + "…"
                current = ""
                break
    if current and len(lines) < 2:
        lines.append(current)
    name_y = y_top + logo_size + 8
    for index, line in enumerate(lines[:2]):
        bbox = draw.textbbox((0, 0), line, font=font_name)
        draw.text((x_center - (bbox[2] - bbox[0]) // 2, name_y + index * 31),
                  line, fill="#1D2A22", font=font_name)


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
    """Generate a polished, Instagram-inspired horizontal X card as PNG bytes."""
    bg = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#F5F7F4")
    glow = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((790, -330, 1430, 310), fill=(205, 239, 216, 170))
    glow = glow.filter(ImageFilter.GaussianBlur(95))
    img = Image.alpha_composite(bg.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img)

    ink, muted = "#18231D", "#647169"
    green, dark_green = "#16834B", "#173B29"
    white, pale, border = "#FFFFFF", "#F0F6F1", "#DFE8E0"
    font_brand = _get_font(21, bold=True)
    font_date = _get_font(18, bold=True)
    font_eyebrow = _get_font(16, bold=True)
    font_title = _get_font(39, bold=True)
    font_subtitle = _get_font(17)
    font_league = _get_font(17, bold=True)
    font_team = _get_font(27, bold=True)
    font_vs = _get_font(17, bold=True)
    font_time = _get_font(23, bold=True)
    font_channel_label = _get_font(13, bold=True)
    font_channel = _get_font(17, bold=True)
    font_footer = _get_font(21, bold=True)
    font_footer_small = _get_font(14)

    shadow = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((51, 237, 1151, 554), radius=25, fill=(24, 49, 34, 34))
    shadow = shadow.filter(ImageFilter.GaussianBlur(13))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, CARD_WIDTH, 7), fill="#10B981")
    draw.rounded_rectangle((54, 28, 280, 82), radius=16, fill=dark_green)
    try:
        logo_path = Path(__file__).parent / "static" / "logo-dondever-sm.png"
        brand_logo = Image.open(logo_path).convert("RGBA")
        brand_logo.thumbnail((188, 38), Image.Resampling.LANCZOS)
        img.paste(brand_logo, (72, 36), brand_logo)
        draw = ImageDraw.Draw(img)
    except Exception:
        draw.text((74, 43), "DondeVer.app", fill=white, font=font_brand)

    date_parts = str(time_str or "").split("·")
    day_label = date_parts[0].strip().upper() if len(date_parts) > 1 else "PRÓXIMO PARTIDO"
    draw.rounded_rectangle((900, 35, 1146, 76), radius=14, fill="#E3F2E7")
    bbox = draw.textbbox((0, 0), day_label, font=font_date)
    draw.text((1023 - (bbox[2] - bbox[0]) / 2, 46), day_label, fill="#526158", font=font_date)
    draw.text((58, 111), f"PARTIDO DESTACADO  ·  {league_name or 'DEPORTES'}".upper(), fill=green, font=font_eyebrow)
    draw.text((55, 137), "¿Dónde verlo?", fill=ink, font=font_title)
    draw.text((58, 190), "Horarios y transmisiones por país", fill=muted, font=font_subtitle)

    draw.rounded_rectangle((50, 230, 1150, 545), radius=25, fill=white, outline=border, width=2)
    draw.rounded_rectangle((76, 248, 295, 281), radius=10, fill=pale)
    league_text = (league_name or "PARTIDO").upper()
    bbox = draw.textbbox((0, 0), league_text, font=font_league)
    if bbox[2] - bbox[0] > 195:
        font_league = _get_font(14, bold=True)
        bbox = draw.textbbox((0, 0), league_text, font=font_league)
    draw.text((185 - (bbox[2] - bbox[0]) / 2, 255), league_text, fill=green, font=font_league)

    if home_left:
        left_name, left_logo = home_name, home_logo_url
        right_name, right_logo = away_name, away_logo_url
    else:
        left_name, left_logo = away_name, away_logo_url
        right_name, right_logo = home_name, home_logo_url
    _draw_team_block(img, draw, left_name, left_logo, 330, 284, font_team, logo_size=84)
    _draw_team_block(img, draw, right_name, right_logo, 870, 284, font_team, logo_size=84)
    draw.text((586, 328), "VS", fill="#89958C", font=font_vs)

    time_display = date_parts[-1].strip() if len(date_parts) > 1 else str(time_str or "")
    if time_display:
        draw.rounded_rectangle((480, 365, 720, 406), radius=13, fill="#E8F5EC")
        bbox = draw.textbbox((0, 0), time_display, font=font_time)
        draw.text((600 - (bbox[2] - bbox[0]) / 2, 373), time_display, fill=dark_green, font=font_time)

    channel_parts = [part.strip() for part in str(channels or "").split("·")]
    confirmed_channels = [
        ("MÉXICO", next((part.split(":", 1)[1].strip() for part in channel_parts if part.startswith("MX:")), "")),
        ("ESTADOS UNIDOS", next((part.split(":", 1)[1].strip() for part in channel_parts if part.startswith("EE.UU.:")), "")),
    ]
    confirmed_channels = [(label, value) for label, value in confirmed_channels if value]
    if len(confirmed_channels) == 2:
        channel_boxes = [(76, 583, *confirmed_channels[0]), (617, 1124, *confirmed_channels[1])]
    elif len(confirmed_channels) == 1:
        channel_boxes = [(347, 853, *confirmed_channels[0])]
    else:
        channel_boxes = []
        info = "Consulta la ficha del partido para más información"
        bbox = draw.textbbox((0, 0), info, font=font_channel)
        draw.text((600 - (bbox[2] - bbox[0]) / 2, 464), info, fill=muted, font=font_channel)

    for x0, x1, label, value in channel_boxes:
        draw.rounded_rectangle((x0, 425, x1, 526), radius=14, fill="#F3F7F3", outline="#E5ECE6", width=1)
        draw.text((x0 + 17, 439), label, fill=green, font=font_channel_label)
        bbox = draw.textbbox((0, 0), value, font=font_channel)
        display_channel = value
        while bbox[2] - bbox[0] > (x1 - x0 - 34) and len(display_channel) > 4:
            display_channel = display_channel[:-2].rstrip() + "…"
            bbox = draw.textbbox((0, 0), display_channel, font=font_channel)
        draw.text((x0 + 17, 464), display_channel, fill="#26352B", font=font_channel)

    draw.rounded_rectangle((50, 570, 1150, 649), radius=18, fill=dark_green)
    draw.text((78, 584), "Consulta dónde ver este partido", fill=white, font=font_footer)
    draw.text((80, 614), "Horarios y canales actualizados para ti", fill="#C5DFCC", font=font_footer_small)
    domain = "DondeVer.app  →"
    bbox = draw.textbbox((0, 0), domain, font=font_footer)
    draw.text((1110 - (bbox[2] - bbox[0]), 597), domain, fill="#A9EDBD", font=font_footer)

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
