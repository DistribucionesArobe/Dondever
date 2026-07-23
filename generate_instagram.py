#!/usr/bin/env python3
"""
DondeVer.app — Instagram Daily Image Generator
Fetches today's real games from ESPN API and generates a branded
Instagram image (1080x1350) for the "Juegos de Hoy" daily post.

Usage:
    python generate_instagram.py              # Today's games
    python generate_instagram.py 2026-07-23   # Specific date
    python generate_instagram.py --stories    # 1080x1920 Stories format

Output: instagram_juegos_YYYY-MM-DD.png in current directory
"""

import asyncio
import sys
import os
import json
import locale
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("Installing httpx...")
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing Pillow...")
    os.system(f"{sys.executable} -m pip install Pillow -q")
    from PIL import Image, ImageDraw, ImageFont


# ── Config ──────────────────────────────────────────────────

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
TZ_MX = timezone(timedelta(hours=-6))  # CST

# Leagues to include on Instagram (prioritized order)
INSTAGRAM_LEAGUES = [
    ("liga-mx",         "soccer",     "mex.1",              "Liga MX",          "⚽"),
    ("world-cup",       "soccer",     "fifa.world",         "Mundial 2026",     "🏆"),
    ("champions",       "soccer",     "uefa.champions",     "Champions League", "⚽"),
    ("premier-league",  "soccer",     "eng.1",              "Premier League",   "⚽"),
    ("la-liga",         "soccer",     "esp.1",              "La Liga",          "⚽"),
    ("mls",             "soccer",     "usa.1",              "MLS",              "⚽"),
    ("nfl",             "football",   "nfl",                "NFL",              "🏈"),
    ("nba",             "basketball", "nba",                "NBA",              "🏀"),
    ("mlb",             "baseball",   "mlb",                "MLB",              "⚾"),
    ("nhl",             "hockey",     "nhl",                "NHL",              "🏒"),
    ("ufc",             "mma",        "ufc",                "UFC",              "🥊"),
    ("serie-a",         "soccer",     "ita.1",              "Serie A",          "⚽"),
    ("bundesliga",      "soccer",     "ger.1",              "Bundesliga",       "⚽"),
    ("ligue-1",         "soccer",     "fra.1",              "Ligue 1",          "⚽"),
    ("europa-league",   "soccer",     "uefa.europa",        "Europa League",    "⚽"),
    ("copa-america",    "soccer",     "conmebol.america",   "Copa América",     "⚽"),
]

# Max games to show on the image
MAX_GAMES = 7

# Default channels per league (Mexico)
DEFAULT_CHANNELS = {
    "liga-mx": "TUDN / Canal 5",
    "world-cup": "Canal 5 / Azteca 7 / ViX",
    "champions": "Max / TNT",
    "premier-league": "ESPN MX",
    "la-liga": "ESPN MX",
    "mls": "Apple TV+",
    "nfl": "ESPN MX / Fox Sports",
    "nba": "ESPN MX",
    "mlb": "ESPN MX",
    "nhl": "ESPN MX",
    "ufc": "Fox Sports",
    "serie-a": "ESPN MX",
    "bundesliga": "ESPN MX",
    "ligue-1": "ESPN MX",
    "europa-league": "ESPN MX",
    "copa-america": "TUDN / Canal 5",
}

# Channel normalization
CHANNEL_NORMALIZE = {
    "TUDN": "TUDN", "UniMás": "TUDN", "Univision": "TUDN",
    "ESPN": "ESPN", "ESPN2": "ESPN 2", "ESPNEWS": "ESPN",
    "ESPN Deportes": "ESPN MX", "ESPNDeportes": "ESPN MX",
    "ABC": "ESPN / ABC", "ESPN+": "ESPN+",
    "FOX": "FOX", "FS1": "Fox Sports", "FS2": "Fox Sports",
    "Fox Sports 1": "Fox Sports", "Fox Sports 2": "Fox Sports",
    "TNT": "Max / TNT", "TBS": "TNT / TBS",
    "CBS": "Paramount+", "CBSSN": "Paramount+",
    "NBC": "Peacock / NBC", "NBCSN": "Peacock",
    "Peacock": "Peacock", "Paramount+": "Paramount+",
    "Apple TV+": "Apple TV+", "Apple TV": "Apple TV+",
    "DAZN": "DAZN", "NFL Network": "NFL Network",
    "NHL Network": "NHL Network", "MLB Network": "MLB Network",
    "NBA TV": "NBA TV", "Canal 5": "Canal 5",
    "Azteca 7": "Azteca 7", "ViX": "ViX",
    "Max": "Max",
}

# Brand colors
BG_DARK = (18, 18, 36)          # #121224
BG_CARD = (30, 32, 50)          # #1e2032
BG_CARD_ALT = (26, 28, 44)     # #1a1c2c
GREEN = (16, 185, 129)          # #10b981
GREEN_DARK = (5, 150, 105)      # #059669
WHITE = (248, 249, 251)         # #f8f9fb
GRAY = (156, 163, 175)          # #9ca3af
LIGHT_GRAY = (209, 213, 219)    # #d1d5db
ACCENT_LIME = (163, 230, 53)    # #a3e635

# League accent colors (for card left border)
LEAGUE_COLORS = {
    "liga-mx":        (0, 180, 80),      # green
    "world-cup":      (218, 165, 32),    # gold
    "champions":      (0, 82, 155),      # UEFA blue
    "premier-league": (55, 0, 130),      # EPL purple
    "la-liga":        (255, 87, 34),     # orange
    "mls":            (0, 45, 114),      # dark blue
    "nfl":            (1, 51, 105),      # NFL blue
    "nba":            (200, 16, 46),     # NBA red
    "mlb":            (0, 51, 160),      # MLB blue
    "nhl":            (0, 0, 0),         # black
    "ufc":            (213, 0, 0),       # UFC red
    "serie-a":        (0, 140, 72),      # green
    "bundesliga":     (220, 0, 50),      # red
    "ligue-1":        (15, 80, 22),      # dark green
    "europa-league":  (252, 76, 2),      # orange
    "copa-america":   (0, 51, 153),      # blue
}


# ── ESPN API ────────────────────────────────────────────────

async def fetch_scoreboard(sport: str, league: str, date_str: str) -> dict:
    """Fetch scoreboard from ESPN API."""
    url = f"{ESPN_BASE}/{sport}/{league}/scoreboard"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params={"dates": date_str})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  Warning: could not fetch {sport}/{league}: {e}")
            return {"events": []}


def parse_channel(broadcasts: list) -> str:
    """Extract the best channel name from ESPN broadcast data."""
    for b in broadcasts:
        market = b.get("market", {})
        # Prefer Mexico/international market
        market_type = market.get("type", "") if isinstance(market, dict) else ""
        names = b.get("names", [])
        if names:
            raw = names[0]
            return CHANNEL_NORMALIZE.get(raw, raw)
    return ""


def parse_events(data: dict, league_slug: str, league_name: str, emoji: str) -> list:
    """Parse ESPN events into our format."""
    games = []
    for event in data.get("events", []):
        competitions = event.get("competitions", [{}])
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = away = None
        for c in competitors:
            team_data = {
                "name": c.get("team", {}).get("displayName", "TBD"),
                "short": c.get("team", {}).get("abbreviation", "???"),
                "logo": c.get("team", {}).get("logo", ""),
                "score": c.get("score", ""),
            }
            if c.get("homeAway") == "home":
                home = team_data
            else:
                away = team_data

        if not home or not away:
            continue

        # Parse time
        date_utc = event.get("date", "")
        try:
            dt = datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
            dt_mx = dt.astimezone(TZ_MX)
            time_str = dt_mx.strftime("%H:%M")
        except Exception:
            time_str = "TBD"

        # Parse channel
        broadcasts = comp.get("broadcasts", [])
        channel = parse_channel(broadcasts)
        if not channel:
            channel = DEFAULT_CHANNELS.get(league_slug, "")

        # Status
        status = event.get("status", {}).get("type", {}).get("name", "STATUS_SCHEDULED")

        games.append({
            "league": league_name,
            "league_slug": league_slug,
            "emoji": emoji,
            "home": home,
            "away": away,
            "time": time_str,
            "channel": channel,
            "status": status,
            "season": str(event.get("season", {}).get("year", "")),
        })

    return games


async def get_todays_games(date_str: str) -> list:
    """Fetch all games for a given date."""
    tasks = []
    league_info = []

    for slug, sport, league, name, emoji in INSTAGRAM_LEAGUES:
        tasks.append(fetch_scoreboard(sport, league, date_str))
        league_info.append((slug, name, emoji))

    print(f"Fetching games from {len(tasks)} leagues...")
    results = await asyncio.gather(*tasks)

    all_games = []
    for (slug, name, emoji), result in zip(league_info, results):
        games = parse_events(result, slug, name, emoji)
        if games:
            print(f"  {name}: {len(games)} games")
        all_games.extend(games)

    # Sort by time
    all_games.sort(key=lambda g: g["time"])
    return all_games


def pick_best_games(games: list, max_games: int = MAX_GAMES) -> list:
    """Pick the most interesting/diverse games for Instagram."""
    if len(games) <= max_games:
        return games

    # Priority scoring
    LEAGUE_PRIORITY = {
        "liga-mx": 100, "world-cup": 99, "champions": 95,
        "nfl": 90, "nba": 85, "mlb": 80, "premier-league": 75,
        "la-liga": 70, "mls": 65, "serie-a": 60, "ufc": 55,
        "europa-league": 50, "bundesliga": 45, "ligue-1": 40,
        "nhl": 35, "copa-america": 90,
    }

    for g in games:
        g["_priority"] = LEAGUE_PRIORITY.get(g["league_slug"], 20)

    # Sort by priority (highest first), then time
    games.sort(key=lambda g: (-g["_priority"], g["time"]))

    # Pick top games, ensuring league diversity
    selected = []
    seen_leagues = set()

    # First pass: one per league
    for g in games:
        if g["league_slug"] not in seen_leagues and len(selected) < max_games:
            selected.append(g)
            seen_leagues.add(g["league_slug"])

    # Second pass: fill remaining slots with highest priority
    if len(selected) < max_games:
        for g in games:
            if g not in selected and len(selected) < max_games:
                selected.append(g)

    # Sort final selection by time
    selected.sort(key=lambda g: g["time"])
    return selected


# ── Image Generation ────────────────────────────────────────

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a system font. Falls back gracefully."""
    font_paths = []
    if bold:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]

    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue

    return ImageFont.load_default()


def draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.pieslice([x0, y0, x0 + 2*radius, y0 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x1 - 2*radius, y0, x1, y0 + 2*radius], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2*radius, x0 + 2*radius, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2*radius, y1 - 2*radius, x1, y1], 0, 90, fill=fill)


async def download_logo(url: str) -> Optional[Image.Image]:
    """Download a team logo and return as PIL Image."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                from io import BytesIO
                img = Image.open(BytesIO(resp.content))
                img = img.convert("RGBA")
                return img
    except Exception:
        pass
    return None


def draw_gradient_bg(img, color_top, color_bottom):
    """Draw a vertical gradient background."""
    W, H = img.size
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def draw_decorative_elements(draw, W, H):
    """Draw subtle decorative circles and lines in background."""
    import random
    random.seed(42)  # Consistent pattern
    for _ in range(25):
        cx = random.randint(0, W)
        cy = random.randint(0, H)
        r = random.randint(20, 120)
        opacity = random.randint(8, 20)
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(255, 255, 255, opacity) if hasattr(draw, '_image') else (30, 35, 55),
            outline=None,
        )
    # Diagonal accent lines
    for i in range(3):
        x_offset = 200 + i * 350
        draw.line([(x_offset, 0), (x_offset - 200, H)], fill=(40, 45, 65), width=1)


def generate_image(
    games: list,
    date_str: str,
    is_stories: bool = False,
    logos: dict = None,
) -> Image.Image:
    """Generate the Instagram image with vibrant design."""
    W = 1080
    H = 1920 if is_stories else 1350

    img = Image.new("RGB", (W, H), BG_DARK)

    # ── Gradient background ────────────────────────────
    draw_gradient_bg(img, (12, 10, 40), (22, 28, 52))

    draw = ImageDraw.Draw(img)

    # Decorative background elements
    draw_decorative_elements(draw, W, H)

    # ── Glow accent top ─────────────────────────────
    # Subtle green glow at top-center
    for r in range(200, 0, -2):
        alpha = max(3, int(12 * (1 - r / 200)))
        color = (16, 185, 129, alpha)  # Green-ish
        x = W // 2
        draw.ellipse([x - r, -r, x + r, r], fill=(12 + alpha, 20 + alpha, 40 + alpha // 2))

    # Fonts
    f_title = get_font(72, bold=True)
    f_subtitle = get_font(56, bold=True)
    f_date_big = get_font(40, bold=True)
    f_date = get_font(28, bold=True)
    f_weekday = get_font(24, bold=True)
    f_league = get_font(22, bold=True)
    f_season = get_font(16)
    f_time = get_font(34, bold=True)
    f_team = get_font(24, bold=True)
    f_score = get_font(30, bold=True)
    f_vs = get_font(18)
    f_channel = get_font(18, bold=True)
    f_footer = get_font(24, bold=True)
    f_footer_sm = get_font(18)
    f_brand = get_font(36, bold=True)
    f_tag = get_font(16, bold=True)
    f_live = get_font(22, bold=True)
    f_count = get_font(60, bold=True)

    y = 40 if not is_stories else 100

    # ── Header ──────────────────────────────────────────

    # Brand name top-left
    draw.text((50, y), "DondeVer", fill=GREEN, font=f_brand)
    bname_w = draw.textlength("DondeVer", font=f_brand)
    draw.text((50 + bname_w, y), ".app", fill=WHITE, font=f_brand)

    # Game count badge top-right
    count_str = str(len(games))
    badge_w = 160
    bx = W - 50 - badge_w
    draw_rounded_rect(draw, (bx, y - 5, W - 50, y + 55), 12, GREEN)
    draw.text((bx + 15, y - 2), count_str, fill=BG_DARK, font=f_count)
    count_w = draw.textlength(count_str, font=f_count)
    draw.text((bx + 15 + count_w + 8, y + 5), "JUEGOS", fill=BG_DARK, font=f_tag)
    draw.text((bx + 15 + count_w + 8, y + 24), "EN VIVO", fill=(5, 80, 50), font=f_tag)

    y += 75

    # Title: "JUEGOS DE HOY" with green underline accent
    draw.text((50, y), "JUEGOS", fill=WHITE, font=f_title)
    title_w = draw.textlength("JUEGOS", font=f_title)
    y += 68
    draw.text((50, y), "DE HOY", fill=GREEN, font=f_subtitle)
    y += 55
    # Green accent bar under title
    draw.rectangle([(50, y), (50 + title_w, y + 5)], fill=GREEN)

    # Date box on the right — big and bold
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        day_num = dt.strftime("%d")
        month_names = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                       "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
        month_str = month_names[dt.month - 1]
        day_names = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
        weekday = day_names[dt.weekday()]
    except Exception:
        day_num, month_str, weekday = "??", "???", "---"

    date_box_x = W - 300
    date_box_y = y - 110
    draw_rounded_rect(draw, (date_box_x, date_box_y, W - 50, date_box_y + 115), 14, (25, 30, 55))
    # Green left border
    draw.rectangle((date_box_x, date_box_y + 10, date_box_x + 5, date_box_y + 105), fill=GREEN)

    draw.text((date_box_x + 22, date_box_y + 12), f"{day_num} {month_str}", fill=GREEN, font=f_date_big)
    draw.text((date_box_x + 22, date_box_y + 58), weekday, fill=WHITE, font=f_weekday)
    draw.text((date_box_x + 22, date_box_y + 88), "HORA CDMX (UTC-6)", fill=GRAY, font=get_font(13))

    y += 25

    # ── Game rows ───────────────────────────────────────

    num_games = len(games)
    if is_stories:
        row_h = min(150, (H - y - 180) // max(num_games, 1))
    else:
        row_h = min(130, (H - y - 180) // max(num_games, 1))
    gap = 10

    for i, game in enumerate(games):
        ry = y + i * (row_h + gap)
        card_bg = BG_CARD if i % 2 == 0 else BG_CARD_ALT

        # Card background with rounded corners
        draw_rounded_rect(draw, (30, ry, W - 30, ry + row_h), 14, card_bg)

        # Colored left accent bar by league
        league_color = LEAGUE_COLORS.get(game["league_slug"], GREEN)
        draw.rectangle([(30, ry + 8), (36, ry + row_h - 8)], fill=league_color)

        # ── Left: League + emoji ──────────
        league_x = 55
        league_y = ry + row_h // 2 - 22

        # League colored dot
        dot_r = 6
        draw.ellipse([league_x, league_y + 6, league_x + dot_r * 2, league_y + 6 + dot_r * 2], fill=league_color)
        draw.text((league_x + 18, league_y), game["league"].upper(), fill=WHITE, font=f_league)

        # Season below
        season_text = game.get("season", "")
        if season_text:
            draw.text((league_x + 18, league_y + 26), season_text, fill=GRAY, font=f_season)

        # ── Center: Time/Status ───────────
        sep_x = 260
        draw.line([(sep_x, ry + 15), (sep_x, ry + row_h - 15)], fill=(45, 50, 70), width=1)

        time_x = sep_x + 15

        if game["status"] in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME"):
            # Red "EN VIVO" badge with pulsing dot
            live_bg = (180, 20, 30)
            lx = time_x
            ly = ry + row_h // 2 - 18
            draw_rounded_rect(draw, (lx, ly, lx + 110, ly + 36), 8, live_bg)
            # Red dot
            draw.ellipse([lx + 10, ly + 12, lx + 22, ly + 24], fill=(255, 80, 80))
            draw.text((lx + 28, ly + 5), "EN VIVO", fill=WHITE, font=f_live)
        elif game["status"] in ("STATUS_FINAL", "STATUS_FULL_TIME"):
            draw.text((time_x, ry + row_h // 2 - 18), "FINAL", fill=GRAY, font=f_time)
        else:
            draw.text((time_x, ry + row_h // 2 - 20), game["time"], fill=GREEN, font=f_time)

        # ── Right: Teams ──────────────────
        sep2_x = sep_x + 130
        draw.line([(sep2_x, ry + 15), (sep2_x, ry + row_h - 15)], fill=(45, 50, 70), width=1)

        teams_x = sep2_x + 15
        logo_size = 34
        logo_x = teams_x

        # Away team logo
        away_logo_key = game["away"].get("logo", "")
        if logos and away_logo_key in logos and logos[away_logo_key]:
            logo_img = logos[away_logo_key].resize((logo_size, logo_size), Image.LANCZOS)
            try:
                img.paste(logo_img, (logo_x, ry + 10), logo_img)
            except Exception:
                img.paste(logo_img, (logo_x, ry + 10))

        # Home team logo
        home_logo_key = game["home"].get("logo", "")
        if logos and home_logo_key in logos and logos[home_logo_key]:
            logo_img = logos[home_logo_key].resize((logo_size, logo_size), Image.LANCZOS)
            try:
                img.paste(logo_img, (logo_x, ry + row_h - logo_size - 10), logo_img)
            except Exception:
                img.paste(logo_img, (logo_x, ry + row_h - logo_size - 10))

        # Team names
        name_x = logo_x + logo_size + 10
        max_name_chars = 20
        away_name = game["away"]["name"] if len(game["away"]["name"]) <= max_name_chars else game["away"]["short"]
        home_name = game["home"]["name"] if len(game["home"]["name"]) <= max_name_chars else game["home"]["short"]

        draw.text((name_x, ry + 12), away_name.upper(), fill=WHITE, font=f_team)
        draw.text((name_x, ry + 38), "vs.", fill=GRAY, font=f_vs)
        draw.text((name_x, ry + row_h - 38), home_name.upper(), fill=WHITE, font=f_team)

        # Scores (bold, larger)
        if game["status"] in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_FINAL", "STATUS_FULL_TIME"):
            if game["away"].get("score"):
                score_text = str(game["away"]["score"])
                sw = draw.textlength(score_text, font=f_score)
                draw.text((W - 160 - sw, ry + 10), score_text, fill=WHITE, font=f_score)
            if game["home"].get("score"):
                score_text = str(game["home"]["score"])
                sw = draw.textlength(score_text, font=f_score)
                draw.text((W - 160 - sw, ry + row_h - 40), score_text, fill=WHITE, font=f_score)

        # Channel badge (right side)
        channel = game.get("channel", "")
        if channel:
            # Truncate long channel names
            if len(channel) > 14:
                channel = channel[:12] + ".."
            ch_w = draw.textlength(channel, font=f_channel)
            ch_x = W - 55 - int(ch_w)
            ch_y = ry + (row_h // 2) - 12
            # Channel pill background
            draw_rounded_rect(draw, (ch_x - 10, ch_y - 4, W - 42, ch_y + 22), 6, (35, 40, 60))
            draw.text((ch_x, ch_y), channel, fill=GREEN, font=f_channel)

    # ── Footer ──────────────────────────────────────────

    footer_y = H - 120 if not is_stories else H - 180

    # Gradient bar separator
    for x in range(50, W - 50):
        ratio = (x - 50) / (W - 100)
        r = int(16 + (0 - 16) * ratio)
        g = int(185 + (100 - 185) * ratio)
        b = int(129 + (255 - 129) * ratio)
        draw.line([(x, footer_y - 15), (x, footer_y - 12)], fill=(abs(r), abs(g), abs(b)))

    # Footer text
    draw.text((50, footer_y + 5), "TODOS LOS HORARIOS,", fill=GRAY, font=f_footer_sm)
    draw.text((50, footer_y + 27), "CANALES Y RESULTADOS EN:", fill=GRAY, font=f_footer_sm)

    # DondeVer.app button — bigger, bolder
    btn_text = "DondeVer.app"
    btn_w = draw.textlength(btn_text, font=f_footer) + 50
    btn_x = W - 60 - int(btn_w)
    btn_y = footer_y + 5
    draw_rounded_rect(draw, (btn_x, btn_y, btn_x + int(btn_w), btn_y + 45), 10, GREEN)
    draw.text((btn_x + 25, btn_y + 8), btn_text, fill=BG_DARK, font=f_footer)

    # Social CTA
    cta_y = footer_y + 65
    draw.text((W // 2 - 150, cta_y), "Síguenos  @dondeverapp", fill=GRAY, font=get_font(20, bold=True))

    return img


# ── Main ────────────────────────────────────────────────────

async def main():
    # Parse args
    date_input = None
    is_stories = False
    skip_logos = False

    for arg in sys.argv[1:]:
        if arg == "--stories":
            is_stories = True
        elif arg == "--no-logos":
            skip_logos = True
        else:
            date_input = arg

    # Determine date
    now_mx = datetime.now(TZ_MX)
    if date_input:
        try:
            dt = datetime.strptime(date_input, "%Y-%m-%d")
            date_str = dt.strftime("%Y%m%d")
        except ValueError:
            print(f"Invalid date format: {date_input}. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        date_str = now_mx.strftime("%Y%m%d")

    print(f"\n🏟️  DondeVer Instagram Generator")
    print(f"📅 Date: {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
    print(f"📐 Format: {'Stories (1080x1920)' if is_stories else 'Feed (1080x1350)'}")
    print()

    # Fetch games
    games = await get_todays_games(date_str)

    if not games:
        print("❌ No games found for this date.")
        sys.exit(0)

    print(f"\n📊 Total games found: {len(games)}")

    # Pick best games
    selected = pick_best_games(games, MAX_GAMES)
    print(f"✅ Selected {len(selected)} games for image:")
    for g in selected:
        ch = g.get('channel', 'N/A')
        print(f"   {g['time']} | {g['league']:20s} | {g['away']['name']} vs {g['home']['name']} | {ch}")

    # Download logos
    logos = {}
    if not skip_logos:
        print("\n🎨 Downloading team logos...")
        logo_urls = set()
        for g in selected:
            for team_key in ("home", "away"):
                url = g[team_key].get("logo", "")
                if url:
                    logo_urls.add(url)

        logo_tasks = {url: download_logo(url) for url in logo_urls}
        results = await asyncio.gather(*logo_tasks.values())
        for url, result in zip(logo_tasks.keys(), results):
            if result:
                logos[url] = result
        print(f"   Downloaded {len(logos)}/{len(logo_urls)} logos")

    # Generate image
    print("\n🖼️  Generating image...")
    img = generate_image(selected, date_str, is_stories, logos)

    # Save
    date_nice = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    suffix = "_stories" if is_stories else ""
    filename = f"instagram_juegos_{date_nice}{suffix}.png"
    output_path = Path(filename)
    img.save(output_path, "PNG", quality=95)

    print(f"\n✅ Image saved: {output_path}")
    print(f"   Size: {img.size[0]}x{img.size[1]}")
    print(f"   File: {output_path.stat().st_size / 1024:.0f} KB")

    return str(output_path)


if __name__ == "__main__":
    result = asyncio.run(main())
