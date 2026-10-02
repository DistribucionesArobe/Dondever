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
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader
try:
    from playwright.async_api import async_playwright
except ModuleNotFoundError:
    # Keep data preparation importable on lightweight local preview/test setups.
    async_playwright = None

from config import CHANNEL_ALIASES, ESPN_CHANNEL_NORMALIZE


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

# Max games to show on the image (new layout groups by league, can handle more)
MAX_GAMES = 10

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


OPEN_TV_CHANNELS = {"canal 5", "azteca 7", "las estrellas", "canal nueve"}


def parse_channels(comp: dict) -> tuple[list[str], list[str], list[str]]:
    """Return country-separated channels and verified Mexican open TV.

    Open TV is marked only when the event itself includes a broadcast on a
    known Mexican over-the-air network. League defaults never count as proof.
    """
    mx, us, open_tv = [], [], []
    broadcasts = comp.get("geoBroadcasts", [])
    for item in broadcasts:
        media = item.get("media") or {}
        raw = media.get("shortName") or media.get("name") or ""
        if not raw:
            names = item.get("names") or []
            raw = names[0] if names else ""
        normalized = ESPN_CHANNEL_NORMALIZE.get(raw, raw).strip()
        alias_info = CHANNEL_ALIASES.get(normalized, {})
        name = alias_info.get("name") or CHANNEL_NORMALIZE.get(normalized, normalized)
        region = item.get("country", {})
        country = ((region.get("code") or region.get("abbreviation") or "") if isinstance(region, dict)
                   else str(region)).upper()
        alias_info = alias_info or CHANNEL_ALIASES.get(name, {})
        if not country:
            country = str(alias_info.get("country", "")).upper()
        market = (item.get("market") or {}).get("type", "")
        if not country and market in ("Home", "Away"):
            country = "US"
        bucket = mx if country in ("MX", "MEXICO", "MÉXICO") else us if country in ("US", "USA", "UNITED STATES") else None
        if bucket is not None and name and name not in bucket:
            bucket.append(name)
        if (bucket is mx and name not in open_tv and
                (name.lower() in OPEN_TV_CHANNELS or
                 (alias_info.get("country") == "MX" and alias_info.get("type") == "broadcast"))):
            open_tv.append(name)
    return mx, us, open_tv


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
            day_names = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
            date_label = f"{day_names[dt_mx.weekday()]} {dt_mx.day:02d}"
        except Exception:
            time_str = ""
            date_label = "Fecha por confirmar"

        # Parse channel
        mx_channels, us_channels, open_channels = parse_channels(comp)
        channel = ", ".join(mx_channels or us_channels)

        # Status
        status = event.get("status", {}).get("type", {}).get("name", "STATUS_UNKNOWN")

        games.append({
            "league": league_name,
            "league_slug": league_slug,
            "emoji": emoji,
            "home": home,
            "away": away,
            "time": time_str,
            "date_label": date_label,
            "starts_at": date_utc,
            "event_id": str(event.get("id", "")),
            "channel": channel,
            "mx_channels": mx_channels,
            "us_channels": us_channels,
            "open_channels": open_channels,
            "channel_verified": bool(mx_channels or us_channels),
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


def _process_game(game: dict) -> dict:
    """Prepare one real game for the reusable Instagram card."""
    slug = game["league_slug"]
    def team(which):
        data = game.get(which) or {}
        name = data.get("name") or data.get("displayName") or "Equipo por confirmar"
        initials = (data.get("short") or "").strip() or "?"
        return {"name": name, "initial": initials[:3].upper(), "logo": data.get("logo") or ""}
    date_label = game.get("date_label") or ""
    mx = game.get("mx_channels")
    us = game.get("us_channels")
    if mx is None and game.get("channel"):
        # Legacy defaults may be shown as a Mexico listing, but are never
        # eligible for the TV ABIERTA badge.
        mx = [part.strip() for part in str(game["channel"]).split("/") if part.strip()]
    return {
        "league": game.get("league") or "Partido",
        "league_slug": slug,
        "day_label": date_label or game.get("weekday") or "",
        "time": game.get("time") or "",
        "timezone": game.get("timezone") or "Hora CDMX (UTC−6)",
        "away": team("away"), "home": team("home"),
        "mx_channels": mx or [], "us_channels": us or [],
        "open_channels": game.get("open_channels", []),
    }


def prepare_template_data(games: list, date_str: str, is_stories: bool = False,
                          headline: str = "", page_index: int = 1, page_total: int = 1):
    """Prepare one slide; fit no more than three games on a slide."""
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

    league = games[0].get("league", "") if games else ""
    one_league = bool(league) and all(g.get("league") == league for g in games)
    try:
        is_weekend = datetime.strptime(date_str, "%Y%m%d").weekday() >= 5
    except ValueError:
        is_weekend = False
    if headline:
        title = headline
    elif len(games) == 1:
        title = f"Dónde ver a {games[0].get('home', {}).get('name')}"
    elif one_league and is_weekend:
        title = f"{league} este fin de semana"
    elif one_league:
        title = f"Agenda de {league} hoy"
    else:
        title = "Partidos para ver hoy"
    try:
        logo_path = Path(__file__).parent / "static" / "logo-dondever-sm.png"
        import base64
        brand_logo = "data:image/png;base64," + base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except Exception:
        brand_logo = ""
    date_label = f"{weekday.title()} {day_num} {month_str.title()}"
    return {
        "games": [_process_game(g) for g in games[:3]],
        "date_label": date_label,
        "eyebrow": (("VISTA PREVIA · " if games and games[0].get("preview") else "")
                    + (games[0].get("league", "Agenda deportiva") if games else "Agenda deportiva")),
        "title": title,
        "subtitle": f"{len(games)} partido" + ("s" if len(games) != 1 else "") + " · Horarios en hora del centro de México",
        "brand_logo": brand_logo,
        "slide_label": f"LÁMINA {page_index} DE {page_total}" if page_total > 1 else "",
        "height": 1350,
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
    paths = await generate_images(games, date_str, output_path, headline="", is_stories=is_stories)
    return paths[0]


async def generate_images(games: list, date_str: str, output_path: str,
                          headline: str = "", is_stories: bool = False) -> list[str]:
    """Render same-league slides, at most three games per slide."""
    if async_playwright is None:
        raise RuntimeError("Image rendering needs Playwright; install the project requirements first.")
    leagues = {}
    for game in games:
        leagues.setdefault(game.get("league_slug", game.get("league", "")), []).append(game)
    pages = []
    for league_games in leagues.values():
        pages.extend(league_games[i:i + 3] for i in range(0, len(league_games), 3))
    pages = pages or [[]]
    paths = []
    base = Path(output_path)
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("instagram_image.html")
    async with async_playwright() as p:
        chrome_bin = os.getenv("CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
        if Path(chrome_bin).exists():
            launch_args["executable_path"] = chrome_bin
        browser = await p.chromium.launch(**launch_args)
        for index, page_games in enumerate(pages, 1):
            path = str(base) if index == 1 else str(base.with_name(f"{base.stem}_{index}{base.suffix}"))
            page_headline = headline
            is_weekend = False
            try:
                is_weekend = datetime.strptime(date_str, "%Y%m%d").weekday() >= 5
            except ValueError:
                pass
            if not page_headline and is_weekend and len(page_games) == 3 and page_games[0].get("league"):
                page_headline = f"{page_games[0]['league']} este fin de semana"
            data = prepare_template_data(page_games, date_str, headline=page_headline,
                                         page_index=index, page_total=len(pages))
            data["height"] = 1920 if is_stories else 1350
            html_content = template.render(**data)
            page = await browser.new_page(viewport={"width": 1080, "height": data["height"]}, device_scale_factor=1)
            await page.set_content(html_content, wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            await page.evaluate("Promise.all([...document.images].map(i => i.decode().catch(() => null)))")
            await page.screenshot(path=path, full_page=False)
            await page.close()
            paths.append(path)
        await browser.close()
    for path in paths:
        print(f"  Image saved: {path}")
    return paths


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

    outputs = await generate_images(selected, date_str, filename, is_stories=is_stories)
    for output in outputs:
        path = Path(output)
        print(f"  Image: {path} ({path.stat().st_size / 1024:.0f} KB)")
    return outputs


if __name__ == "__main__":
    result = asyncio.run(main())
