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
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    os.system(f"{sys.executable} -m pip install jinja2 -q")
    from jinja2 import Environment, FileSystemLoader

try:
    from playwright.async_api import async_playwright
except ImportError:
    os.system(f"{sys.executable} -m pip install playwright -q")
    from playwright.async_api import async_playwright


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


# ── Image Generation (HTML → PNG via Playwright) ──────────

# League color hex for HTML template
LEAGUE_COLORS_HEX = {
    "liga-mx":        "#00b450",
    "world-cup":      "#daa520",
    "champions":      "#00529b",
    "premier-league": "#37003c",
    "la-liga":        "#ff5722",
    "mls":            "#002d72",
    "nfl":            "#013369",
    "nba":            "#c8102e",
    "mlb":            "#0033a0",
    "nhl":            "#000000",
    "ufc":            "#d50000",
    "serie-a":        "#008c48",
    "bundesliga":     "#dc0032",
    "ligue-1":        "#0f5016",
    "europa-league":  "#fc4c02",
    "copa-america":   "#003399",
}

# Short league display names for Instagram
LEAGUE_SHORT_NAMES = {
    "Champions League": "CHAMPIONS",
    "Premier League": "PREMIER",
    "Europa League": "EUROPA",
    "Copa América": "COPA AME",
}


def prepare_template_data(games: list, date_str: str, is_stories: bool = False):
    """Prepare game data for the HTML template."""
    # Parse date
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        day_num = dt.strftime("%d")
        month_names = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                       "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
        month_str = month_names[dt.month - 1]
        day_names = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES",
                     "VIERNES", "SÁBADO", "DOMINGO"]
        weekday = day_names[dt.weekday()]
    except Exception:
        day_num, month_str, weekday = "??", "???", "---"

    # Process games for template
    template_games = []
    for game in games:
        slug = game["league_slug"]
        league_name = game["league"]
        league_display = LEAGUE_SHORT_NAMES.get(league_name, league_name).upper()
        if len(league_display) > 12:
            league_display = league_display[:12]

        # Season display
        season = game.get("season", "")
        if season:
            if "Regular" in str(season) or season.isdigit():
                season_display = f"Temporada {season}" if season.isdigit() else "Temporada 2026"
            elif "Post" in str(season):
                season_display = "Playoffs"
            elif "Pre" in str(season):
                season_display = "Pretemporada"
            elif "All" in str(season):
                season_display = "All-Star"
            else:
                season_display = str(season)
        else:
            season_display = ""

        # Status
        status = game["status"]
        is_live = status in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME")
        is_final = status in ("STATUS_FINAL", "STATUS_FULL_TIME")
        show_score = is_live or is_final

        # Channel display (truncate if too long)
        channel = game.get("channel", "")
        channel_display = channel[:14] if len(channel) > 14 else channel

        # Team display names (use full if short enough, otherwise abbreviation)
        max_name = 20
        away_display = game["away"]["name"].upper() if len(game["away"]["name"]) <= max_name else game["away"]["short"].upper()
        home_display = game["home"]["name"].upper() if len(game["home"]["name"]) <= max_name else game["home"]["short"].upper()

        template_games.append({
            "league_display": league_display,
            "league_color": LEAGUE_COLORS_HEX.get(slug, "#10b981"),
            "season_display": season_display,
            "time": game["time"],
            "is_live": is_live,
            "is_final": is_final,
            "show_score": show_score,
            "away": {
                "display_name": away_display,
                "logo": game["away"].get("logo", ""),
                "score": game["away"].get("score"),
            },
            "home": {
                "display_name": home_display,
                "logo": game["home"].get("logo", ""),
                "score": game["home"].get("score"),
            },
            "channel": channel,
            "channel_display": channel_display,
        })

    # Calculate card sizing for logo-centric layout
    num_games = len(template_games)
    height = 1920 if is_stories else 1350
    header_footer = 240  # header + title + footer
    available = height - header_footer
    card_h = min(160, available // max(num_games, 1) - 12)
    card_h = max(card_h, 130)  # minimum height for logos
    gap = max(6, min(14, (available - card_h * num_games) // max(num_games - 1, 1)))

    return {
        "games": template_games,
        "day_num": day_num,
        "month_str": month_str,
        "weekday": weekday,
        "height": height,
        "card_h": card_h,
        "gap": gap,
    }


async def generate_image(
    games: list,
    date_str: str,
    is_stories: bool = False,
    output_path: str = "instagram.png",
) -> str:
    """Generate the Instagram image using HTML template + Playwright.

    Returns the output file path.
    """
    # Prepare template data
    data = prepare_template_data(games, date_str, is_stories)

    # Render HTML from template
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("instagram_image.html")
    html_content = template.render(**data)

    # Render HTML to PNG with Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = await browser.new_page(
            viewport={"width": 1080, "height": data["height"]},
            device_scale_factor=1,
        )

        await page.set_content(html_content, wait_until="networkidle")

        # Wait for Google Fonts to load
        await page.wait_for_timeout(2000)

        # Screenshot the full page
        await page.screenshot(path=output_path, full_page=False)
        await browser.close()

    print(f"  Image saved: {output_path}")
    return output_path


# ── Main ────────────────────────────────────────────────────

async def main():
    # Parse args
    date_input = None
    is_stories = False

    for arg in sys.argv[1:]:
        if arg == "--stories":
            is_stories = True
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

    print(f"\n  DondeVer Instagram Generator (Playwright)")
    print(f"  Date: {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
    print(f"  Format: {'Stories (1080x1920)' if is_stories else 'Feed (1080x1350)'}")
    print()

    # Fetch games
    games = await get_todays_games(date_str)

    if not games:
        print("  No games found for this date.")
        sys.exit(0)

    print(f"\n  Total games found: {len(games)}")

    # Pick best games
    selected = pick_best_games(games, MAX_GAMES)
    print(f"  Selected {len(selected)} games for image:")
    for g in selected:
        ch = g.get('channel', 'N/A')
        print(f"   {g['time']} | {g['league']:20s} | {g['away']['name']} vs {g['home']['name']} | {ch}")

    # Generate image
    print("\n  Generating image with Playwright...")
    date_nice = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    suffix = "_stories" if is_stories else ""
    filename = f"instagram_juegos_{date_nice}{suffix}.png"

    await generate_image(selected, date_str, is_stories, filename)

    output_path = Path(filename)
    print(f"\n  Image saved: {output_path}")
    print(f"   File: {output_path.stat().st_size / 1024:.0f} KB")

    return str(output_path)


if __name__ == "__main__":
    result = asyncio.run(main())
