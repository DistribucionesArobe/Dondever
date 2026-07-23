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
            "season": event.get("season", {}).get("type", {}).get("name", ""),
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


def generate_image(
    games: list,
    date_str: str,
    is_stories: bool = False,
    logos: dict = None,
) -> Image.Image:
    """Generate the Instagram image."""
    W = 1080
    H = 1920 if is_stories else 1350

    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Fonts
    f_title = get_font(64, bold=True)
    f_subtitle = get_font(52, bold=True)
    f_date = get_font(28, bold=True)
    f_date_sm = get_font(20)
    f_league = get_font(22, bold=True)
    f_season = get_font(16)
    f_time = get_font(36, bold=True)
    f_team = get_font(26, bold=True)
    f_vs = get_font(20)
    f_channel = get_font(20, bold=True)
    f_footer = get_font(22, bold=True)
    f_footer_sm = get_font(18)
    f_brand = get_font(32, bold=True)
    f_tag = get_font(18, bold=True)

    y = 40 if not is_stories else 100

    # ── Header ──────────────────────────────────────────

    # Brand name top-left
    draw.text((50, y), "DondeVer", fill=GREEN, font=f_brand)
    draw.text((50 + draw.textlength("DondeVer", font=f_brand), y), ".app", fill=WHITE, font=f_brand)

    # "TV ABIERTA EN MÉXICO" badge top-right
    badge_text = "TV ABIERTA"
    badge_sub = "EN MÉXICO"
    bw = draw.textlength(badge_text, font=f_tag) + 30
    bx = W - 50 - int(bw)
    draw_rounded_rect(draw, (bx, y, W - 50, y + 50), 8, GREEN)
    draw.text((bx + 15, y + 5), badge_text, fill=BG_DARK, font=f_tag)
    draw.text((bx + 15, y + 28), badge_sub, fill=BG_DARK, font=get_font(14, bold=True))

    y += 70

    # Title: "JUEGOS DE HOY"
    draw.text((50, y), "JUEGOS", fill=WHITE, font=f_title)
    y += 60
    draw.text((50, y), "DE HOY", fill=WHITE, font=f_subtitle)

    # Date box on the right
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

    date_box_x = W - 280
    date_box_y = y - 50
    draw_rounded_rect(draw, (date_box_x, date_box_y, W - 50, date_box_y + 100), 10, BG_CARD)
    # Green border
    draw.rectangle((date_box_x, date_box_y, date_box_x + 4, date_box_y + 100), fill=GREEN)

    draw.text((date_box_x + 20, date_box_y + 10), f"{day_num} {month_str}", fill=GREEN, font=f_date)
    draw.text((date_box_x + 20, date_box_y + 45), weekday, fill=WHITE, font=f_date)
    draw.text((date_box_x + 20, date_box_y + 75), "TIEMPO DEL CENTRO (CDMX)", fill=GRAY, font=get_font(12))

    y += 70

    # Divider line
    draw.line([(50, y), (W - 50, y)], fill=(40, 44, 60), width=2)
    y += 15

    # ── Game rows ───────────────────────────────────────

    num_games = len(games)
    if is_stories:
        row_h = min(150, (H - y - 150) // max(num_games, 1))
    else:
        row_h = min(130, (H - y - 150) // max(num_games, 1))
    gap = 8

    for i, game in enumerate(games):
        ry = y + i * (row_h + gap)
        card_bg = BG_CARD if i % 2 == 0 else BG_CARD_ALT

        # Card background
        draw_rounded_rect(draw, (30, ry, W - 30, ry + row_h), 12, card_bg)

        # Left section: League info
        league_x = 50
        league_y = ry + 15

        # League name
        draw.text((league_x, league_y), game["league"].upper(), fill=WHITE, font=f_league)

        # Season/type below league
        season_text = ""
        if game.get("season"):
            s = game["season"]
            if "Regular" in s:
                season_text = "TEMPORADA 2026"
            elif "Post" in s:
                season_text = "PLAYOFFS"
            elif "Pre" in s:
                season_text = "PRETEMPORADA"
            elif "All" in s:
                season_text = "ALL-STAR"
            else:
                season_text = s.upper()
        if season_text:
            draw.text((league_x, league_y + 28), season_text, fill=GRAY, font=f_season)

        # Vertical separator after league
        sep_x = 260
        draw.line([(sep_x, ry + 15), (sep_x, ry + row_h - 15)], fill=(50, 55, 70), width=2)

        # Time
        time_x = sep_x + 25
        time_color = GREEN if game["status"] == "STATUS_SCHEDULED" else ACCENT_LIME
        if game["status"] in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME"):
            time_text = "EN VIVO"
            time_color = (239, 68, 68)  # Red
        elif game["status"] in ("STATUS_FINAL", "STATUS_FULL_TIME"):
            time_text = "FINAL"
            time_color = GRAY
        else:
            time_text = game["time"]

        draw.text((time_x, ry + (row_h // 2) - 20), time_text, fill=time_color, font=f_time)

        # Vertical separator after time
        sep2_x = sep_x + 140
        draw.line([(sep2_x, ry + 15), (sep2_x, ry + row_h - 15)], fill=(50, 55, 70), width=2)

        # Teams section
        teams_x = sep2_x + 20

        # Team logos (if available)
        logo_size = 32
        logo_x = teams_x

        # Away team logo
        away_logo_key = game["away"].get("logo", "")
        if logos and away_logo_key in logos and logos[away_logo_key]:
            logo_img = logos[away_logo_key].resize((logo_size, logo_size), Image.LANCZOS)
            try:
                img.paste(logo_img, (logo_x, ry + 12), logo_img)
            except Exception:
                img.paste(logo_img, (logo_x, ry + 12))

        # Home team logo
        home_logo_key = game["home"].get("logo", "")
        if logos and home_logo_key in logos and logos[home_logo_key]:
            logo_img = logos[home_logo_key].resize((logo_size, logo_size), Image.LANCZOS)
            try:
                img.paste(logo_img, (logo_x, ry + row_h - logo_size - 12), logo_img)
            except Exception:
                img.paste(logo_img, (logo_x, ry + row_h - logo_size - 12))

        # Team names — always try full name; only shorten if > 22 chars
        name_x = logo_x + logo_size + 10
        max_name_chars = 22
        away_name = game["away"]["name"] if len(game["away"]["name"]) <= max_name_chars else game["away"]["short"]
        home_name = game["home"]["name"] if len(game["home"]["name"]) <= max_name_chars else game["home"]["short"]

        # Layout: AWAY on top, "vs." in green small, HOME on bottom
        draw.text((name_x, ry + 12), away_name.upper(), fill=WHITE, font=f_team)
        draw.text((name_x, ry + 40), "vs.", fill=GREEN, font=f_vs)
        draw.text((name_x, ry + row_h - 40), home_name.upper(), fill=WHITE, font=f_team)

        # Scores (if in progress or final)
        if game["status"] in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_FINAL", "STATUS_FULL_TIME"):
            score_x = name_x + 250
            if game["away"].get("score"):
                draw.text((score_x, ry + 15), str(game["away"]["score"]), fill=WHITE, font=f_team)
            if game["home"].get("score"):
                draw.text((score_x, ry + row_h - 42), str(game["home"]["score"]), fill=WHITE, font=f_team)

        # Channel (right side)
        channel = game.get("channel", "")
        if channel:
            ch_w = draw.textlength(channel, font=f_channel)
            ch_x = W - 60 - int(ch_w)
            ch_y = ry + (row_h // 2) - 12
            draw.text((ch_x, ch_y), channel, fill=GREEN, font=f_channel)

    # ── Footer ──────────────────────────────────────────

    footer_y = H - 100 if not is_stories else H - 160
    draw.line([(50, footer_y - 20), (W - 50, footer_y - 20)], fill=(40, 44, 60), width=2)

    # Footer text
    draw.text((50, footer_y), "TODOS LOS HORARIOS,", fill=GRAY, font=f_footer_sm)
    draw.text((50, footer_y + 22), "CANALES Y RESULTADOS EN:", fill=GRAY, font=f_footer_sm)

    # DondeVer.app button
    btn_text = "DondeVer.app"
    btn_w = draw.textlength(btn_text, font=f_footer) + 40
    btn_x = W - 60 - int(btn_w)
    btn_y = footer_y + 5
    draw_rounded_rect(draw, (btn_x, btn_y, btn_x + int(btn_w), btn_y + 40), 8, GREEN)
    draw.text((btn_x + 20, btn_y + 5), btn_text, fill=BG_DARK, font=f_footer)

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
