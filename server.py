"""
DondeVer.app — Main FastAPI server
Where to watch sports in Mexico & USA
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.twiml.messaging_response import MessagingResponse

from config import AFFILIATES, STREAMING_AFFILIATES, LEAGUES, ALL_LEAGUES, APP_URL, TZ_MX, TZ_ET, TEAM_ALIASES, TEAM_SHOP, MELI_AFF_PARAM, TEAM_SHOP_MELI, POPULAR_TEAMS
from sports_api import (
    get_todays_games, search_games, get_team_stats, get_league_standings,
    fetch_odds, match_odds_to_game, match_full_odds_to_game,
    get_recent_league_results, get_upcoming_league_games, fetch_team_news,
    fetch_meli_product_image, fetch_espn_event_summary,
)
from whatsapp_bot import handle_whatsapp_message
import meta_whatsapp
import email_subscribers
import user_favs
from tiktok_auth import (
    get_tiktok_auth_url, exchange_code_for_token, get_user_info,
    upload_video_to_tiktok, check_publish_status, is_authenticated,
    get_token_info,
)

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dondever")

# ── App ──────────────────────────────────────────────────
app = FastAPI(
    title="DondeVer.app",
    description="Donde ver juegos deportivos en Mexico y USA",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Google Analytics middleware ──────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware


class GAInjectMiddleware(BaseHTTPMiddleware):
    """Inject Google Analytics 4 and Microsoft Clarity snippets into every HTML response.
    Activated per-tool when env vars are set:
      - GA_MEASUREMENT_ID (format: G-XXXXXXXXXX) for GA4
      - CLARITY_PROJECT_ID (format: lowercase alphanumeric) for Microsoft Clarity
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ga_id = os.getenv("GA_MEASUREMENT_ID", "").strip()
        gads_id = os.getenv("GOOGLE_ADS_ID", "").strip()  # format: AW-XXXXXXXXXXX
        clarity_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
        gtm_id = os.getenv("GTM_CONTAINER_ID", "").strip()
        adsense_id = os.getenv("ADSENSE_PUB_ID", "").strip()  # format: ca-pub-XXXXXXXXXXXXXXXX
        onesignal_id = os.getenv("ONESIGNAL_APP_ID", "").strip()
        monetag_zone = os.getenv("MONETAG_ZONE_ID", "").strip()
        if not ga_id and not clarity_id and not gtm_id and not gads_id and not adsense_id and not onesignal_id and not monetag_zone:
            return response

        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype:
            return response

        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            snippet = ""
            if gtm_id:
                snippet += (
                    f'<!-- Google Tag Manager -->\n'
                    f'<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{"gtm.start":\n'
                    f'new Date().getTime(),event:"gtm.js"}});var f=d.getElementsByTagName(s)[0],\n'
                    f'j=d.createElement(s),dl=l!="dataLayer"?"&l="+l:"";j.async=true;j.src=\n'
                    f'"https://www.googletagmanager.com/gtm.js?id="+i+dl;f.parentNode.insertBefore(j,f);\n'
                    f'}})(window,document,"script","dataLayer","{gtm_id}");</script>\n'
                    f'<!-- End Google Tag Manager -->\n'
                )
            if ga_id:
                snippet += (
                    f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>\n'
                    f'<script>\n'
                    f'  window.dataLayer = window.dataLayer || [];\n'
                    f'  function gtag(){{dataLayer.push(arguments);}}\n'
                    f'  gtag("js", new Date());\n'
                    f'  gtag("config", "{ga_id}", {{ anonymize_ip: true }});\n'
                    + (f'  gtag("config", "{gads_id}");\n' if gads_id else '')
                    + f'</script>\n'
                )
            if clarity_id:
                snippet += (
                    f'<script>\n'
                    f'  (function(c,l,a,r,i,t,y){{\n'
                    f'    c[a]=c[a]||function(){{(c[a].q=c[a].q||[]).push(arguments)}};\n'
                    f'    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;\n'
                    f'    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);\n'
                    f'  }})(window, document, "clarity", "script", "{clarity_id}");\n'
                    f'</script>\n'
                )
            if adsense_id:
                snippet += (
                    f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={adsense_id}"\n'
                    f'     crossorigin="anonymous"></script>\n'
                )
            if onesignal_id:
                snippet += (
                    f'<script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>\n'
                    f'<script>\n'
                    f'  window.OneSignalDeferred = window.OneSignalDeferred || [];\n'
                    f'  OneSignalDeferred.push(async function(OneSignal) {{\n'
                    f'    await OneSignal.init({{ appId: "{onesignal_id}" }});\n'
                    f'  }});\n'
                    f'</script>\n'
                )
            if monetag_zone:
                snippet += (
                    f'<meta name="monetag" content="ce90bfedb944d0ac4754ee93708ed15d">\n'
                    f'<script src="https://5gvci.com/act/files/tag.min.js?z=11479109" data-cfasync="false" async></script>\n'
                    # Push Notifications — registers sw.js for opt-in browser push
                    f'<script>(function(s){{s.dataset.zone="11481358",s.src="https://nap5k.com/tag.min.js"}})([document.documentElement, document.body].filter(Boolean).pop().appendChild(document.createElement("script")))</script>\n'
                    # In-Page Push Banner — small notification-style ad in corner
                )
            snippet = snippet.encode("utf-8")

            if b"</head>" in body:
                body = body.replace(b"</head>", snippet + b"</head>", 1)

            # GTM also needs a <noscript> iframe right after <body>
            if gtm_id:
                gtm_noscript = (
                    f'\n<!-- Google Tag Manager (noscript) -->\n'
                    f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}"\n'
                    f'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
                    f'<!-- End Google Tag Manager (noscript) -->\n'
                ).encode("utf-8")
                # Match both <body> and <body ...> variants
                import re as _re
                body = _re.sub(
                    rb"(<body\b[^>]*>)",
                    lambda m: m.group(1) + gtm_noscript,
                    body, count=1, flags=_re.IGNORECASE,
                )

            from starlette.responses import Response
            # Strip content-length so Starlette recalculates
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=ctype,
            )
        except Exception as e:
            logger = logging.getLogger("dondever")
            logger.warning(f"GA inject failed: {e}")
            return response


app.add_middleware(GAInjectMiddleware)


# ── Template helpers ─────────────────────────────────────
def format_mx_time(iso_date: str) -> str:
    """Convert ISO date to Mexico City time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


_DAYS_ES_FMT = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def format_mx_day_time(iso_date: str) -> str:
    """'Domingo 26 · 7:20 PM' in Mexico City time (Spanish, DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return f"{_DAYS_ES_FMT[mx.weekday()]} {mx.day} · {mx.strftime('%I:%M %p').lstrip('0')}"
    except Exception:
        return ""


def format_us_time(iso_date: str) -> str:
    """Convert ISO date to US Eastern time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        et = dt.astimezone(TZ_ET)
        return et.strftime("%I:%M %p ET")
    except Exception:
        return ""


templates.env.globals["format_mx_time"] = format_mx_time
templates.env.globals["format_us_time"] = format_us_time
templates.env.globals["affiliates"] = AFFILIATES
templates.env.globals["streaming_aff"] = STREAMING_AFFILIATES
templates.env.globals["app_url"] = APP_URL
templates.env.globals["now"] = lambda: datetime.now(TZ_MX)
templates.env.globals["team_shop"] = TEAM_SHOP
templates.env.globals["meli_aff"] = MELI_AFF_PARAM
templates.env.globals["team_shop_meli"] = TEAM_SHOP_MELI
templates.env.globals["popular_teams"] = POPULAR_TEAMS


def _team_name_to_slug(team_name: str) -> str | None:
    """Reverse lookup: ESPN team display name → DondeVer slug."""
    if not team_name:
        return None
    name_lower = team_name.lower()
    # Exact match first
    for slug, info in POPULAR_TEAMS.items():
        if info["name"].lower() == name_lower:
            return slug
    # Slug appears in team name (e.g. "yankees" in "new york yankees")
    for slug, info in POPULAR_TEAMS.items():
        slug_clean = slug.replace("-", " ")
        if slug_clean in name_lower:
            return slug
    return None


# ── Service Workers (must be at root scope) ──────────────
from pathlib import Path as _Path

@app.get("/OneSignalSDKWorker.js")
async def onesignal_service_worker():
    sw_path = _Path(__file__).parent / "OneSignalSDKWorker.js"
    return Response(
        content=sw_path.read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )

@app.get("/sw.js")
async def monetag_service_worker():
    sw_path = _Path(__file__).parent / "sw.js"
    return Response(
        content=sw_path.read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


# ── Web Routes ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    date: Optional[str] = Query(None, description="Date YYYYMMDD"),
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
):
    """Main page — today's games."""
    games = await get_todays_games(
        date_str=date, sport_filter=sport, league_filter=league
    )

    # Group games by sport
    sports_grouped = {}
    for game in games:
        sport_key = game["league_slug"].split("-")[0] if "-" in game["league_slug"] else game["league_slug"]
        # Use league_name for grouping
        league_name = game["league_name"]
        if league_name not in sports_grouped:
            sports_grouped[league_name] = {
                "emoji": game["emoji"],
                "games": [],
            }
        sports_grouped[league_name]["games"].append(game)

    # ── Fetch odds for homepage games ────────────────────
    # Collect unique league slugs from upcoming AND live games (live betting)
    odds_leagues = set()
    for g in games:
        if g["status"]["state"] in ("pre", "in"):
            odds_leagues.add(g.get("league_slug", ""))
    # Fetch priority leagues first (MLB, NFL, NBA, etc.), then secondary
    from sports_api import ODDS_PRIORITY_LEAGUES
    sorted_leagues = sorted(
        odds_leagues,
        key=lambda ls: ODDS_PRIORITY_LEAGUES.index(ls) if ls in ODDS_PRIORITY_LEAGUES else 99
    )
    odds_by_league: dict[str, list] = {}
    for ls in sorted_leagues:
        try:
            ol = await fetch_odds(ls)
            if ol:
                odds_by_league[ls] = ol
        except Exception:
            pass
    # Attach odds to each game (pre + live) + quick prediction from odds
    for g in games:
        ls = g.get("league_slug", "")
        if g["status"]["state"] in ("pre", "in") and ls in odds_by_league:
            g["odds"] = match_odds_to_game(g, odds_by_league[ls])
        else:
            g["odds"] = None
        g["prediction"] = _quick_prediction_from_odds(g) if g.get("odds") else None

    # ── Live games section ──────────────────────────────
    live_games = [g for g in games if g["status"]["state"] == "in"]

    # ── Featured games (DESTACADOS) ───────────────────────
    # Algorithmically pick 5-8 of the most interesting upcoming games
    priority_leagues = ["liga-mx", "premier-league", "champions", "nfl", "nba",
                        "la-liga", "mlb", "nhl", "mls", "liga-argentina",
                        "copa-del-rey", "fa-cup", "leagues-cup", "euro"]
    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    featured_games = []
    # 1. Priority leagues with broadcasts first
    for pl in priority_leagues:
        for g in upcoming:
            if g["league_slug"] == pl and g not in featured_games:
                featured_games.append(g)
                if len(featured_games) >= 8:
                    break
        if len(featured_games) >= 8:
            break
    # 2. Fill up to 8 with any upcoming game that has odds
    if len(featured_games) < 8:
        for g in upcoming:
            if g not in featured_games and g.get("odds"):
                featured_games.append(g)
                if len(featured_games) >= 8:
                    break
    # 3. Still short? add any upcoming with broadcasts
    if len(featured_games) < 5:
        for g in upcoming:
            if g not in featured_games and g["broadcasts"]:
                featured_games.append(g)
                if len(featured_games) >= 5:
                    break

    # Pick del dia — best single game for the card
    pick_game = featured_games[0] if featured_games else (live_games[0] if live_games else None)

    # Available sports for filter
    sport_types = sorted(set(v[0] for v in LEAGUES.values()))

    today = datetime.now(TZ_MX)

    # Date navigation
    if date:
        try:
            viewing_date = datetime.strptime(date, "%Y%m%d").replace(tzinfo=TZ_MX)
        except ValueError:
            viewing_date = today
    else:
        viewing_date = today

    prev_date = (viewing_date - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (viewing_date + timedelta(days=1)).strftime("%Y%m%d")

    # ── Sport counts for hero cards ─────────────────────
    sport_counts: dict[str, int] = {"all": len(games)}
    for g in games:
        ls = g.get("league_slug", "")
        s = LEAGUES.get(ls, ("other",))[0] if ls in LEAGUES else "other"
        sport_counts[s] = sport_counts.get(s, 0) + 1

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "games": games,
            "sports_grouped": sports_grouped,
            "sport_types": sport_types,
            "leagues": LEAGUES,
            "current_sport": sport,
            "current_league": league,
            "current_date": date or today.strftime("%Y%m%d"),
            "today_display": viewing_date.strftime("%A %d de %B, %Y"),
            "prev_date": prev_date,
            "next_date": next_date,
            "total_games": len(games),
            "pick_game": pick_game,
            "live_games": live_games,
            "featured_games": featured_games,
            "sport_counts": sport_counts,
        },
    )


@app.get("/canales-tv", response_class=HTMLResponse)
async def canales_ads_page(request: Request):
    """Clean landing page for Google Ads — no betting/affiliate content."""
    games = await get_todays_games()

    sports_grouped = {}
    for game in games:
        league_name = game["league_name"]
        if league_name not in sports_grouped:
            sports_grouped[league_name] = {
                "emoji": game["emoji"],
                "games": [],
            }
        sports_grouped[league_name]["games"].append(game)

    return templates.TemplateResponse(
        request,
        "canales.html",
        context={
            "games": games,
            "sports_grouped": sports_grouped,
            "total_games": len(games),
        },
    )


def _slugify_channel(name: str) -> str:
    """Turn a channel name into a URL-safe slug: 'ESPN MX' -> 'espn-mx'."""
    import re, unicodedata
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


@app.get("/canales", response_class=HTMLResponse)
async def canales_index(request: Request):
    """Channel index — list all channels broadcasting today with game counts."""
    games = await get_todays_games()

    # Build channel -> games mapping
    channels: dict[str, dict] = {}
    for g in games:
        for b in g.get("broadcasts", []):
            ch = b["channel"]
            slug = _slugify_channel(ch)
            if slug not in channels:
                saff = STREAMING_AFFILIATES.get(ch)
                channels[slug] = {
                    "name": ch,
                    "slug": slug,
                    "games": [],
                    "is_streaming": saff is not None,
                    "bg": saff["bg"] if saff else "#6b7280",
                    "color": saff["color"] if saff else "white",
                }
            channels[slug]["games"].append(g)

    # Sort: most games first
    sorted_channels = sorted(channels.values(), key=lambda c: len(c["games"]), reverse=True)

    return templates.TemplateResponse(
        request,
        "canales_index.html",
        context={
            "channels": sorted_channels,
            "total_channels": len(sorted_channels),
            "total_games": len(games),
        },
    )


@app.get("/canal/{channel_slug}", response_class=HTMLResponse)
async def canal_page(request: Request, channel_slug: str, date: Optional[str] = Query(None)):
    """Per-channel page — all games airing on a specific channel today."""
    games = await get_todays_games(date_str=date)

    today = datetime.now(TZ_MX)
    if date:
        try:
            viewing_date = datetime.strptime(date, "%Y%m%d").replace(tzinfo=TZ_MX)
        except ValueError:
            viewing_date = today
    else:
        viewing_date = today
    prev_date = (viewing_date - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (viewing_date + timedelta(days=1)).strftime("%Y%m%d")

    # Find channel name from slug, and filter games
    channel_name = None
    channel_games = []
    for g in games:
        for b in g.get("broadcasts", []):
            if _slugify_channel(b["channel"]) == channel_slug:
                if not channel_name:
                    channel_name = b["channel"]
                if g not in channel_games:
                    channel_games.append(g)

    if not channel_name:
        # Try to reconstruct name from slug for SEO even with no games today
        channel_name = channel_slug.replace("-", " ").title()

    # Check if this is a streaming platform with affiliate
    saff = STREAMING_AFFILIATES.get(channel_name)

    # Group by league
    sports_grouped = {}
    for g in channel_games:
        ln = g["league_name"]
        if ln not in sports_grouped:
            sports_grouped[ln] = {"emoji": g["emoji"], "games": []}
        sports_grouped[ln]["games"].append(g)

    # Live / upcoming counts
    live_count = sum(1 for g in channel_games if g["status"]["state"] == "in")
    upcoming_count = sum(1 for g in channel_games if g["status"]["state"] == "pre")

    return templates.TemplateResponse(
        request,
        "canal.html",
        context={
            "channel_name": channel_name,
            "channel_slug": channel_slug,
            "channel_games": channel_games,
            "sports_grouped": sports_grouped,
            "total_games": len(channel_games),
            "live_count": live_count,
            "upcoming_count": upcoming_count,
            "saff": saff,
            "current_date": date or today.strftime("%Y%m%d"),
            "today_display": viewing_date.strftime("%A %d de %B, %Y"),
            "prev_date": prev_date,
            "next_date": next_date,
        },
    )


@app.get("/juego/{event_id}", response_class=HTMLResponse)
async def game_detail(request: Request, event_id: str, date: Optional[str] = Query(None)):
    """Individual game page — good for SEO."""
    # Try the requested date first, then today, then nearby dates
    all_games = await get_todays_games(date_str=date)
    game = next((g for g in all_games if g["id"] == event_id), None)

    # If not found with the given date, try today (in case date param is stale)
    if not game and date:
        all_games = await get_todays_games()
        game = next((g for g in all_games if g["id"] == event_id), None)

    # Still not found — try yesterday and tomorrow as fallback
    if not game:
        now = datetime.now(TZ_MX)
        for delta in [1, -1, 2, -2]:
            try_date = (now + timedelta(days=delta)).strftime("%Y%m%d")
            fallback_games = await get_todays_games(date_str=try_date)
            game = next((g for g in fallback_games if g["id"] == event_id), None)
            if game:
                break

    if not game:
        # 410 Gone: le dice a Google que la URL existio pero ya no.
        # Google desindexa mas rapido con 410 que con 404.
        return templates.TemplateResponse(
            request, "404.html", status_code=410,
            context={"message": "Este juego ya termino. Ve los juegos de hoy en la home."}
        )

    # Fetch odds (show for pre-game and in-progress)
    odds = None
    if game["status"]["state"] in ("pre", "in"):
        try:
            league_slug = game.get("league_slug", "")
            odds_list = await fetch_odds(league_slug)
            odds = match_full_odds_to_game(game, odds_list)
        except Exception as e:
            logger.warning(f"Odds fetch failed for game {event_id}: {e}")

    # Find team slugs for clickable logos
    home_slug = _team_name_to_slug(game["home"]["name"])
    away_slug = _team_name_to_slug(game["away"]["name"])

    # Fetch stats for both teams (standings, record, etc.)
    home_stats = {}
    away_stats = {}
    try:
        if home_slug:
            home_stats = await get_team_stats(home_slug)
        if away_slug:
            away_stats = await get_team_stats(away_slug)
    except Exception as e:
        logger.warning(f"Stats fetch failed for game {event_id}: {e}")

    # Fetch ESPN event summary (lineups, H2H, standings, boxscore)
    summary = {}
    try:
        sport = game.get("sport", "")
        league_slug = game.get("league_slug", "")
        # Resolve ESPN league code from slug (e.g. "liga-mx" → "mex.1")
        from config import ALL_LEAGUES
        league_info = ALL_LEAGUES.get(league_slug)
        espn_league = league_info[1] if isinstance(league_info, tuple) else league_slug
        if sport and espn_league:
            summary = await fetch_espn_event_summary(sport, espn_league, event_id)
    except Exception as e:
        logger.warning(f"Summary fetch failed for game {event_id}: {e}")

    return templates.TemplateResponse(
        request, "game.html", context={
            "game": game, "odds": odds,
            "home_slug": home_slug, "away_slug": away_slug,
            "home_stats": home_stats, "away_stats": away_stats,
            "summary": summary,
        }
    )


# ── Affiliate click tracking ──────────────────────────────
import json as _json
from pathlib import Path as _Path
from datetime import date as _date

_CLICKS_FILE = os.getenv("CLICKS_FILE", os.path.join(
    os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "affiliate_clicks.json"
))


def _track_click(affiliate: str, source: str):
    """Persist affiliate click count by day/affiliate/source."""
    try:
        _Path(_CLICKS_FILE).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CLICKS_FILE, "r") as f:
                data = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            data = {}
        today = _date.today().isoformat()
        data.setdefault(today, {})
        key = f"{affiliate}:{source}"
        data[today][key] = data[today].get(key, 0) + 1
        with open(_CLICKS_FILE, "w") as f:
            _json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Click tracking failed: {e}")


def get_click_stats(days: int = 7) -> dict:
    """Get click stats for the last N days."""
    try:
        with open(_CLICKS_FILE, "r") as f:
            data = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        data = {}
    from datetime import timedelta
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    result = {}
    for day, clicks in data.items():
        if day >= cutoff:
            for k, v in clicks.items():
                result[k] = result.get(k, 0) + v
    return result


# Branded affiliate redirect — "dondever.app/go/betsson" en vez de links largos
@app.get("/go/{key}")
async def affiliate_redirect(key: str, s: str = "web", request: Request = None):
    """
    Redirige a la URL del afiliado con tracking de source.
    Uso: /go/betsson?s=twitter  →  link afiliado real + sub1=twitter

    /go/bet = link geo-inteligente: decide el casino según el país
    del visitante (Cloudflare / Vercel header / Accept-Language).
    MX → Jubilee/Vivento, US → Betsson, LATAM → 1xBet.
    """
    from fastapi.responses import RedirectResponse
    from config import get_affiliate_url
    import random as _random
    # Legacy: strendus fue removido — redirigir links viejos a betsson
    if key == "strendus":
        key = "betsson"
    # Smart geo link: /go/bet
    if key == "bet":
        country = ""
        al = ""
        if request is not None:
            country = (request.headers.get("cf-ipcountry")
                       or request.headers.get("x-vercel-ip-country") or "").upper()
            al = (request.headers.get("accept-language") or "").lower()
        LATAM_COUNTRIES = {"VE","PA","DO","CO","NI","CL","AR","PE","CR","EC",
                           "GT","HN","SV","CU","BO","PY","UY","BR","ES","PR"}
        if country:
            if country == "MX":
                key = _random.choice(["jubilee", "vivento"])
            elif country == "US":
                key = "betsson"
            elif country in LATAM_COUNTRIES:
                key = "1xbet"
            else:
                key = "1xbet"  # default resto del mundo → 1xBet
        else:
            # Heurística por idioma
            if "es-mx" in al:
                key = _random.choice(["jubilee", "vivento"])
            elif "en-us" in al or "en-gb" in al or al.startswith("en"):
                key = "betsson"
            else:
                key = "1xbet"  # español genérico u otro → 1xBet
    _track_click(key, s)  # track antes de redirigir
    target = get_affiliate_url(key, source=s)
    if target == "#":
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=target, status_code=302)


# Legacy URLs que Google sigue rastreando de versiones viejas del sitio
# Redirect 301 permanente a la home para recuperar SEO
@app.get("/game/{old_id}")
async def legacy_game_redirect(old_id: str):
    """Redirect de /game/* (URL vieja) a /juego/* (URL actual) o a home."""
    from fastapi.responses import RedirectResponse
    # Si el ID existe hoy, redirige al /juego/{id}, si no, a la home
    all_games = await get_todays_games()
    if any(g["id"] == old_id for g in all_games):
        return RedirectResponse(url=f"/juego/{old_id}", status_code=301)
    return RedirectResponse(url="/", status_code=301)


@app.get("/liga/{league_slug}", response_class=HTMLResponse)
async def league_page(request: Request, league_slug: str):
    """
    Permanent league landing page — always has content for Google to index.
    e.g. /liga/liga-mx, /liga/nfl, /liga/nba
    """
    if league_slug not in ALL_LEAGUES:
        return templates.TemplateResponse(
            request, "404.html", status_code=404
        )

    sport, league_id, display_name, emoji = ALL_LEAGUES[league_slug]
    games = await get_todays_games(league_filter=league_slug)

    # Fetch standings (top 10) — fail gracefully
    standings = []
    try:
        standings = await get_league_standings(sport, league_id, limit=10)
    except Exception:
        pass

    # Fetch recent results and upcoming games (enriched content for SEO)
    recent_results = []
    upcoming_games = []
    try:
        recent_results = await get_recent_league_results(sport, league_id, days=5, limit=5)
    except Exception:
        pass
    try:
        upcoming_games = await get_upcoming_league_games(sport, league_id, days=7, limit=5)
    except Exception:
        pass

    # Related teams from POPULAR_TEAMS that play in this league
    league_teams = {
        slug: info for slug, info in POPULAR_TEAMS.items()
        if info.get("league") == league_slug
    }

    # Collect actual channels from today's games for this league
    league_channels_today = []
    for g in games:
        for b in g.get("broadcasts", []):
            ch = b.get("channel", "")
            if ch and ch not in league_channels_today:
                league_channels_today.append(ch)
    # Fallback to default channels if no games today
    from sports_api import DEFAULT_LEAGUE_CHANNELS
    default_channels = DEFAULT_LEAGUE_CHANNELS.get(league_slug, [])

    return templates.TemplateResponse(
        request, "league.html", context={
            "league_slug": league_slug,
            "league_name": display_name,
            "emoji": emoji,
            "sport": sport,
            "games": games,
            "total_games": len(games),
            "standings": standings,
            "league_teams": league_teams,
            "recent_results": recent_results,
            "upcoming_games": upcoming_games,
            "league_channels_today": league_channels_today[:8],
            "default_channels": default_channels,
        }
    )


# ── Sport-Today Pages (SEO) ────────────────────────────────

# Config: slug → (sport_key, display_name, emoji, seo_channels_mx, seo_channels_us, seo_streaming)
SPORT_TODAY_PAGES = {
    "futbol-hoy": (
        "soccer", "Futbol", "⚽",
        "Los partidos de futbol se transmiten en Mexico por TUDN, Canal 5, Azteca 7, Fox Sports Mexico, ESPN Mexico, y ViX Premium. Para ligas europeas como Premier League y La Liga, ESPN y Fox Sports tienen los derechos principales.",
        "En Estados Unidos, el futbol se ve por ESPN, ESPN+, Fox Sports, Univision, TUDN USA, Peacock, Paramount+ y Apple TV (MLS Season Pass). Los partidos de Champions League se transmiten por CBS y Paramount+.",
        "Las mejores opciones de streaming para futbol en vivo son ViX Premium (Liga MX y ligas europeas en Mexico), ESPN+ y Peacock (en USA), y Paramount+ para Champions League. Apple TV tiene los derechos exclusivos de la MLS.",
    ),
    "futbol-americano-hoy": (
        "football", "Futbol Americano", "🏈",
        "La NFL en Mexico se ve principalmente por ESPN Mexico, Fox Sports Mexico, y TV Azteca para los juegos en Mexico City. Los playoffs y Super Bowl tienen transmision en abierto por Canal 5 o Azteca 7.",
        "En Estados Unidos, la NFL se transmite por CBS, Fox, NBC (Sunday Night Football), ESPN (Monday Night Football), Amazon Prime Video (Thursday Night Football), y NFL Network. College Football se ve en ESPN, ABC, Fox y CBS.",
        "Para streaming de futbol americano, NFL+ es la opcion oficial. En USA tambien Peacock, Paramount+, y Amazon Prime Video tienen juegos. ESPN+ transmite College Football selecto.",
    ),
    "basquetbol-hoy": (
        "basketball", "Basquetbol", "🏀",
        "La NBA en Mexico se transmite por ESPN Mexico y NBA League Pass. Algunos juegos de temporada regular y playoffs se transmiten por TV Azteca o Canal 5 en acuerdos especiales.",
        "En Estados Unidos, la NBA se ve por ESPN, ABC, TNT, NBA TV, y los canales regionales (RSN). Los playoffs y Finals se transmiten en ESPN, ABC y TNT.",
        "NBA League Pass es la mejor opcion para ver todos los juegos de la NBA en streaming. En Mexico tambien esta disponible ESPN Play. La WNBA se transmite por ESPN, ABC, CBS, y ION Television.",
    ),
    "beisbol-hoy": (
        "baseball", "Beisbol", "⚾",
        "La MLB en Mexico se ve por ESPN Mexico y Fox Sports Mexico. Algunos juegos de postemporada se transmiten en abierto. La Liga Mexicana del Pacifico se transmite en canales regionales y Claro Sports.",
        "En Estados Unidos, la MLB se transmite por ESPN, Fox, TBS, FS1, y los canales regionales de cada equipo. Los playoffs se ven en Fox, TBS y ESPN. Apple TV+ tiene Friday Night Baseball.",
        "Para streaming de beisbol, MLB.TV es la opcion completa para todos los juegos fuera de mercado. ESPN+ tiene algunos juegos exclusivos. Apple TV+ transmite viernes de beisbol.",
    ),
    "hockey-hoy": (
        "hockey", "Hockey", "🏒",
        "La NHL tiene cobertura limitada en Mexico. ESPN Mexico transmite algunos juegos de playoffs y las Stanley Cup Finals.",
        "En Estados Unidos, la NHL se ve por ESPN, ABC, TNT, y los canales regionales. Los playoffs se transmiten en ESPN, ABC y TNT.",
        "ESPN+ y Hulu son las principales opciones de streaming para la NHL en Estados Unidos. NHL Center Ice ofrece cobertura de todos los juegos fuera de mercado.",
    ),
}


async def _render_sport_today(request: Request, sport_slug: str):
    """
    Sport-specific landing page — /futbol-hoy, /beisbol-hoy, etc.
    Always has content for Google to index (SEO text even with no games).
    """
    sport_key, sport_display, sport_emoji, seo_mx, seo_us, seo_stream = SPORT_TODAY_PAGES[sport_slug]

    # Get today's games filtered by sport
    games = await get_todays_games(sport_filter=sport_key)

    # Group games by league for organized display
    games_by_league = {}
    for g in games:
        ls = g["league_slug"]
        if ls not in games_by_league:
            league_info = ALL_LEAGUES.get(ls, (sport_key, ls, ls, ""))
            games_by_league[ls] = {
                "name": league_info[2],
                "emoji": league_info[3],
                "games": [],
            }
        games_by_league[ls]["games"].append(g)

    # Related teams for this sport (from POPULAR_TEAMS)
    related_teams = {
        slug: info for slug, info in POPULAR_TEAMS.items()
        if info.get("sport") == sport_key
    }

    # Leagues for this sport
    sport_leagues = {
        slug: {"name": info[2], "emoji": info[3]}
        for slug, info in ALL_LEAGUES.items()
        if info[0] == sport_key
    }

    total_games = len(games)

    return templates.TemplateResponse(
        request, "sport_today.html", context={
            "page_slug": sport_slug,
            "page_title": f"Donde ver {sport_display} en vivo hoy - Canales Mexico y USA",
            "meta_description": f"{total_games} juegos de {sport_display} en vivo hoy. Horarios y canales de TV para Mexico y Estados Unidos. TUDN, ESPN, Fox Sports y mas.",
            "hero_text": f"Todos los juegos de {sport_display} de hoy con horarios, canales de TV y opciones de streaming para Mexico y Estados Unidos.",
            "sport_key": sport_key,
            "sport_display": sport_display,
            "sport_emoji": sport_emoji,
            "games": games,
            "games_by_league": games_by_league,
            "total_games": total_games,
            "related_teams": related_teams,
            "sport_leagues": sport_leagues,
            "seo_channels_mx": seo_mx,
            "seo_channels_us": seo_us,
            "seo_streaming": seo_stream,
        }
    )


# Explicit routes for each sport page (avoids catch-all /{slug} conflicts)
@app.get("/futbol-hoy", response_class=HTMLResponse)
async def futbol_hoy(request: Request):
    return await _render_sport_today(request, "futbol-hoy")

@app.get("/futbol-americano-hoy", response_class=HTMLResponse)
async def futbol_americano_hoy(request: Request):
    return await _render_sport_today(request, "futbol-americano-hoy")

@app.get("/basquetbol-hoy", response_class=HTMLResponse)
async def basquetbol_hoy(request: Request):
    return await _render_sport_today(request, "basquetbol-hoy")

@app.get("/beisbol-hoy", response_class=HTMLResponse)
async def beisbol_hoy(request: Request):
    return await _render_sport_today(request, "beisbol-hoy")

@app.get("/hockey-hoy", response_class=HTMLResponse)
async def hockey_hoy(request: Request):
    return await _render_sport_today(request, "hockey-hoy")


# ── API Routes ───────────────────────────────────────────

@app.get("/api/games")
async def api_games(
    date: Optional[str] = None,
    sport: Optional[str] = None,
    league: Optional[str] = None,
    q: Optional[str] = None,
):
    """JSON API for games."""
    if q:
        games = await search_games(q, date_str=date)
    else:
        games = await get_todays_games(
            date_str=date, sport_filter=sport, league_filter=league
        )
    return JSONResponse({"games": games, "count": len(games)})


@app.get("/api/live-scores")
async def api_live_scores():
    """Lightweight endpoint for live score polling — only returns games in progress or recently finished."""
    games = await get_todays_games()
    live = []
    for g in games:
        state = g["status"]["state"]
        if state in ("in", "post"):
            live.append({
                "id": g["id"],
                "home_score": g["home"]["score"],
                "away_score": g["away"]["score"],
                "state": state,
                "clock": g["status"].get("clock", ""),
                "period": g["status"].get("period", 0),
                "detail": g["status"].get("detail", ""),
            })
    return JSONResponse({"games": live, "ts": datetime.now(TZ_MX).isoformat()})


@app.get("/api/instagram-image")
async def api_instagram_image(date: Optional[str] = None):
    """Generate Instagram daily image and return public URL.
    The image is saved to /static/instagram/ so it's publicly accessible
    (required by Meta Content Publishing API).
    """
    from generate_instagram import (
        get_todays_games as ig_get_games,
        pick_best_games, generate_image,
    )

    now = datetime.now(TZ_MX)
    if date:
        date_str = date.replace("-", "")
    else:
        date_str = now.strftime("%Y%m%d")

    date_nice = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # Fetch real games
    games = await ig_get_games(date_str)
    if not games:
        return JSONResponse({"error": "No games found", "date": date_nice}, status_code=404)

    selected = pick_best_games(games, 7)

    # Generate image with Playwright (HTML → PNG)
    filename = f"juegos_{date_nice}.png"
    static_path = os.path.join("static", "instagram", filename)
    os.makedirs(os.path.dirname(static_path), exist_ok=True)
    await generate_image(selected, date_str, False, static_path)

    public_url = f"{APP_URL}/static/instagram/{filename}"

    # Build caption
    lines = [f"⚽🏀⚾ Juegos de hoy — {date_nice}\n"]
    for g in selected:
        emoji = g.get("emoji", "⚽")
        ch = g.get("channel", "")
        lines.append(f"{g['time']} {g['away']['name']} vs {g['home']['name']} — {ch}")
    lines.append("\n📺 Todos los horarios y canales en DondeVer.app")
    lines.append("\n#DondeVer #DeportesEnVivo #LigaMX #MLB #NBA #NFL #FutbolMexicano #DeportesHoy")
    caption = "\n".join(lines)

    return JSONResponse({
        "image_url": public_url,
        "filename": filename,
        "date": date_nice,
        "games_count": len(selected),
        "caption": caption,
        "games": [
            {"time": g["time"], "league": g["league"],
             "away": g["away"]["name"], "home": g["home"]["name"],
             "channel": g.get("channel", "")}
            for g in selected
        ],
    })


@app.get("/api/leagues")
async def api_leagues():
    """List available leagues."""
    return JSONResponse({
        "leagues": [
            {"slug": slug, "sport": sport, "league": league, "name": name, "emoji": emoji}
            for slug, (sport, league, name, emoji) in LEAGUES.items()
        ]
    })


@app.get("/api/team/{team_slug}")
async def api_team_quick(team_slug: str):
    """
    Rich team info for the smart search panel — mini team page.
    Returns: stats, next/live games, upcoming list, last result, shop products.
    """
    from config import HOME_LEFT_SPORTS, TEAM_SHOP_MELI, MELI_AFF_PARAM
    from sports_api import TEAM_LEAGUE_MAP

    team_info = POPULAR_TEAMS.get(team_slug)
    if not team_info:
        return JSONResponse({"found": False})

    team_name = team_info["name"]
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))

    # Fetch games and stats in parallel
    games_task = search_games(search_term)
    stats_task = get_team_stats(team_slug)

    games, stats = await asyncio.gather(games_task, stats_task, return_exceptions=True)
    if isinstance(games, Exception): games = []
    if isinstance(stats, Exception): stats = {}

    # Find team logo
    team_logo = ""
    team_league = team_info.get("league", "")
    for g in games:
        if search_term.lower() in g["home"]["name"].lower():
            team_logo = g["home"].get("logo", "")
            if not team_league: team_league = g.get("league_name", "")
            break
        elif search_term.lower() in g["away"]["name"].lower():
            team_logo = g["away"].get("logo", "")
            if not team_league: team_league = g.get("league_name", "")
            break

    # ── Stats block (mirrors team page) ──
    stats_block = {}
    if stats:
        sport_type = stats.get("sport_type", "")
        w = stats.get("wins", "")
        l = stats.get("losses", "")
        t = stats.get("ties", "")
        stats_block = {
            "rank": stats.get("rank", ""),
            "wins": w, "losses": l, "ties": t,
            "points": stats.get("points", ""),
            "goals_for": stats.get("goals_for", ""),
            "goals_against": stats.get("goals_against", ""),
            "goal_diff": stats.get("goal_diff", ""),
            "games_played": stats.get("games_played", ""),
            "win_pct": stats.get("win_pct", ""),
            "streak": stats.get("streak", ""),
            "sport_type": sport_type,
        }
        # Build G-E-P or W-L record string
        if sport_type == "soccer" and w and l:
            stats_block["record_display"] = f"{w}-{t}-{l}" if t else f"{w}-{l}"
        elif w and l:
            stats_block["record_display"] = f"{w}-{l}"
        else:
            stats_block["record_display"] = ""

    # ── Games: upcoming, live, completed ──
    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    live_list = [g for g in games if g["status"]["state"] == "in"]
    completed = [g for g in games if g["status"]["state"] == "post"]

    def _format_game(g, include_odds=False):
        sport = g.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        first = g["home"]["name"] if home_left else g["away"]["name"]
        second = g["away"]["name"] if home_left else g["home"]["name"]
        channels = [b["channel"] for b in g.get("broadcasts", [])[:4]]
        result = {
            "id": g["id"],
            "first": first, "second": second,
            "first_logo": g["home"].get("logo", "") if home_left else g["away"].get("logo", ""),
            "second_logo": g["away"].get("logo", "") if home_left else g["home"].get("logo", ""),
            "date": g["date"],
            "time_mx": format_mx_time(g["date"]),
            "league": g.get("league_name", ""),
            "channels": channels,
            "state": g["status"]["state"],
            "status_display": g["status"].get("display", ""),
        }
        # Scores for live/completed
        if g["status"]["state"] in ("in", "post"):
            result["score_first"] = g["home"].get("score", "") if home_left else g["away"].get("score", "")
            result["score_second"] = g["away"].get("score", "") if home_left else g["home"].get("score", "")
        return result

    # Next game with odds
    next_game = None
    if upcoming:
        next_game = _format_game(upcoming[0])
        try:
            ls = upcoming[0].get("league_slug", "")
            odds_list = await fetch_odds(ls)
            next_game["odds"] = match_odds_to_game(upcoming[0], odds_list)
        except Exception:
            next_game["odds"] = None

    # More upcoming (up to 3 total)
    upcoming_list = [_format_game(g) for g in upcoming[1:3]]

    # Live game
    live_game = _format_game(live_list[0]) if live_list else None

    # Last result
    last_result = _format_game(completed[0]) if completed else None

    # ── Shop products ──
    shop = TEAM_SHOP.get(team_slug, {})
    amz_tag = AFFILIATES.get("amazon", "dondever0f-20")
    products = []
    if shop.get("jersey"):
        j = shop["jersey"]
        products.append({
            "type": "jersey", "name": j.get("name", "Jersey"),
            "brand": j.get("brand", ""),
            "img": j.get("img", ""),
            "url": f"https://www.amazon.com/dp/{j['asin']}?tag={amz_tag}",
            "store": "Amazon",
        })
    if shop.get("gorra"):
        g = shop["gorra"]
        products.append({
            "type": "gorra", "name": g.get("name", "Gorra"),
            "brand": g.get("brand", ""),
            "img": g.get("img", ""),
            "url": f"https://www.amazon.com/dp/{g['asin']}?tag={amz_tag}",
            "store": "Amazon",
        })
    # MercadoLibre curated link
    meli_curated = TEAM_SHOP_MELI.get(team_slug)
    if meli_curated:
        products.append({
            "type": "meli", "name": f"Jerseys en MercadoLibre",
            "brand": "", "img": "",
            "url": f"{meli_curated}{MELI_AFF_PARAM}",
            "store": "MercadoLibre",
        })

    return JSONResponse({
        "found": True,
        "slug": team_slug,
        "name": team_name,
        "logo": team_logo,
        "league": team_league,
        "stats": stats_block,
        "next_game": next_game,
        "upcoming": upcoming_list,
        "live_game": live_game,
        "last_result": last_result,
        "products": products,
        "has_shop": bool(shop),
    })


# ── WhatsApp Webhook ─────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    Body: str = Form(""),
    From: str = Form(""),
):
    """Twilio WhatsApp webhook — receives messages, responds with game info."""
    logger.info(f"WhatsApp from {From}: {Body!r}")

    try:
        response_text = await handle_whatsapp_message(Body, From)
        if not response_text:
            logger.warning(f"WhatsApp handler returned empty response for body={Body!r}")
            response_text = (
                "Hmm, no entendi. Escribe *ayuda* para ver comandos, "
                "*hoy* para juegos, o *picks* para el pick del dia."
            )
    except Exception as e:
        logger.exception(f"WhatsApp handler crashed on body={Body!r}: {e}")
        response_text = (
            "Tuvimos un problema procesando tu mensaje. Intenta de nuevo o escribe *ayuda*."
        )

    logger.info(f"WhatsApp reply to {From}: {response_text[:100]}...")

    if response_text == "__SENT_DIRECTLY__":
        # Already sent via Meta API — return empty TwiML
        twiml = MessagingResponse()
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    twiml = MessagingResponse()
    twiml.message(response_text)
    return HTMLResponse(content=str(twiml), media_type="application/xml")


@app.get("/webhook/whatsapp")
async def whatsapp_verify():
    """Health check for Twilio webhook verification."""
    return {"status": "ok", "service": "dondever-whatsapp"}


# ── Meta WhatsApp Cloud API Webhook ────────────────────────

WELCOME_BUTTONS = [
    {"id": "btn_picks", "title": "Picks del Dia"},
    {"id": "btn_hoy", "title": "Juegos de Hoy"},
    {"id": "btn_suscribir", "title": "Suscribirse"},
]

WELCOME_BODY = (
    "Hola! Soy *DondeVer* - tu guia de deportes en vivo.\n\n"
    "Elige una opcion o escribe el nombre de un equipo:"
)


@app.get("/webhook/meta-whatsapp")
async def meta_whatsapp_verify(request: Request):
    """Meta webhook verification — responds to hub.challenge."""
    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == verify_token and verify_token:
        logger.info("Meta WhatsApp webhook verified successfully")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning(f"Meta webhook verification failed: mode={mode}, token_match={token == verify_token}")
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/webhook/meta-whatsapp")
async def meta_whatsapp_webhook(request: Request):
    """Meta WhatsApp Cloud API webhook — receives messages and replies."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok"}

    messages = meta_whatsapp.parse_inbound_webhook(payload)
    if not messages:
        return {"status": "ok"}

    for msg in messages:
        from_number = msg["from"]
        body = msg["body"]
        message_id = msg.get("message_id")

        logger.info(f"Meta WA from {from_number}: {body!r}")

        # Mark as read (blue check)
        if message_id:
            meta_whatsapp.mark_as_read(message_id)

        try:
            response_text = await handle_whatsapp_message(body, from_number)
        except Exception as e:
            logger.exception(f"WhatsApp handler error: {e}")
            response_text = "Tuvimos un problema. Intenta de nuevo o escribe *ayuda*."

        if not response_text:
            response_text = (
                "No entendi. Escribe *ayuda* para ver comandos, "
                "*hoy* para juegos, o *picks* para el pick del dia."
            )

        # Special markers — already handled or need special treatment
        if response_text == "__SENT_DIRECTLY__":
            logger.info(f"Meta WA: already sent directly to {from_number}, skipping duplicate")
            continue

        # Special marker: send interactive buttons instead of plain text
        if response_text == "__BUTTONS_WELCOME__":
            result = meta_whatsapp.send_interactive_buttons(
                to=from_number,
                body=WELCOME_BODY,
                buttons=WELCOME_BUTTONS,
                header="DondeVer.app",
                footer="dondever.app — Todo GRATIS",
            )
            logger.info(f"Meta WA buttons to {from_number}: {result.get('ok')}")
        else:
            result = meta_whatsapp.send_text(from_number, response_text)
            logger.info(f"Meta WA reply to {from_number}: ok={result.get('ok')}")

    return {"status": "ok"}


# ── Email Newsletter Endpoints ──────────────────────────────


@app.post("/api/email-subscribe")
async def api_email_subscribe(request: Request):
    """Subscribe an email to the daily picks newsletter."""
    try:
        data = await request.json()
        email_addr = data.get("email", "").strip().lower()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    if not email_addr:
        return JSONResponse({"ok": False, "error": "email_required"}, status_code=400)

    result = email_subscribers.subscribe(email_addr)
    if not result["success"]:
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    return {"ok": True, "is_new": result["is_new"]}


@app.get("/email-unsubscribe")
async def email_unsubscribe(request: Request, token: str = ""):
    """Unsubscribe from email newsletter via token link."""
    if not token:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
            "<h2>Link invalido</h2><p>No se pudo cancelar la suscripcion.</p>"
            "</body></html>",
            status_code=400,
        )

    success = email_subscribers.unsubscribe(token=token)
    if success:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
            "<h2>Listo!</h2><p>Te desuscribiste del newsletter de DondeVer.</p>"
            "<p>Si cambias de opinion, suscribete de nuevo en "
            "<a href='https://dondever.app'>dondever.app</a></p>"
            "</body></html>"
        )
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
        "<h2>No encontramos tu suscripcion</h2>"
        "<p>Es posible que ya estuvieras desuscrito.</p>"
        "</body></html>"
    )


@app.get("/api/email-subscribers/count")
async def api_email_subscriber_count():
    """Get email subscriber count (for admin)."""
    return {"count": email_subscribers.get_subscriber_count()}


@app.get("/api/internal/whatsapp-subscribers")
async def internal_whatsapp_subscribers(key: str = ""):
    """Internal endpoint for cron jobs to fetch WhatsApp subscriber list."""
    internal_key = os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    from subscribers import get_active_subscribers
    return {"subscribers": get_active_subscribers()}


@app.get("/api/internal/email-subscribers")
async def internal_email_subscribers(key: str = ""):
    """Internal endpoint for cron jobs to fetch email subscriber list."""
    internal_key = os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    return {"subscribers": email_subscribers.get_active_subscribers()}


@app.get("/whatsapp/debug")
async def whatsapp_debug():
    """Diagnostico del webhook de WhatsApp."""
    import os as _os
    from subscribers import get_active_subscribers, get_subscriber_count
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")
    info = {
        "twilio_sid_set": bool(sid),
        "twilio_sid_prefix": sid[:6] + "..." if sid else None,
        "twilio_token_set": bool(token),
        "whatsapp_number": wa_num,
        "webhook_url_expected": "https://dondever.app/webhook/whatsapp (POST)",
        "total_subscribers": 0,
    }
    try:
        info["total_subscribers"] = get_subscriber_count()
    except Exception as e:
        info["subscribers_error"] = str(e)
    return info


@app.get("/api/internal/whatsapp-diag")
async def whatsapp_diagnostics(key: str = ""):
    """Diagnostic endpoint: subscribe app to WABA + check templates."""
    import os as _os
    internal_key = _os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    token = _os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    waba_id = "1224835083125902"
    results = {}

    async with httpx.AsyncClient(timeout=15) as client:
        # 1. Subscribe app to WABA
        try:
            r = await client.post(
                f"https://graph.facebook.com/v25.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {token}"},
            )
            results["subscribe_app"] = r.json()
        except Exception as e:
            results["subscribe_app_error"] = str(e)

        # 2. Get message templates
        try:
            r = await client.get(
                f"https://graph.facebook.com/v25.0/{waba_id}/message_templates",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name,language,status,category", "limit": 50},
            )
            results["templates"] = r.json()
        except Exception as e:
            results["templates_error"] = str(e)

        # 3. Check WABA details
        try:
            r = await client.get(
                f"https://graph.facebook.com/v25.0/{waba_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name,account_review_status,messaging_limit_tier,on_behalf_of_business_info"},
            )
            results["waba_info"] = r.json()
        except Exception as e:
            results["waba_info_error"] = str(e)

        # 4. Check phone number quality
        phone_id = _os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        if phone_id:
            try:
                r = await client.get(
                    f"https://graph.facebook.com/v25.0/{phone_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "verified_name,quality_rating,display_phone_number,status,messaging_limit_tier"},
                )
                results["phone_info"] = r.json()
            except Exception as e:
                results["phone_info_error"] = str(e)

    return results


@app.post("/api/whatsapp/subscribe")
async def api_whatsapp_subscribe(request: Request):
    """Public endpoint for WhatsApp opt-in from the website."""
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        if not phone:
            return {"ok": False, "error": "Ingresa tu numero de telefono."}
        # Normalize: ensure + prefix, remove spaces
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        # Basic validation: must be 10-15 digits after +
        digits = phone[1:]
        if not digits.isdigit() or len(digits) < 10 or len(digits) > 15:
            return {"ok": False, "error": "Numero invalido. Usa formato +521XXXXXXXXXX"}
        from subscribers import subscribe
        is_new = subscribe(phone)
        logger.info(f"WhatsApp subscribe from web: {phone} (new={is_new})")
        return {"ok": True, "new": is_new}
    except Exception as e:
        logger.error(f"WhatsApp subscribe error: {e}")
        return {"ok": False, "error": "Error interno. Intenta de nuevo."}


# ── User Favorites (by phone) ──────────────────────────────


@app.post("/api/favs/save")
async def api_favs_save(request: Request):
    """Save user favorites linked to WhatsApp phone number."""
    try:
        data = await request.json()
        phone = data.get("phone", "").strip()
        favs = data.get("favs", [])
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    if not phone:
        return JSONResponse({"ok": False, "error": "phone_required"}, status_code=400)

    result = user_favs.save_favs(phone, favs)
    if not result["success"]:
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    return {"ok": True, "count": result["count"]}


@app.get("/api/favs/load")
async def api_favs_load(phone: str = ""):
    """Load user favorites by phone number."""
    if not phone:
        return JSONResponse({"ok": False, "error": "phone_required"}, status_code=400)

    result = user_favs.load_favs(phone)
    if not result["success"]:
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    return {"ok": True, "favs": result["favs"], "found": result.get("found", False)}


@app.get("/admin/subscribers")
async def admin_subscribers(token: str = ""):
    """
    Lista detallada de suscriptores. Protegido por ADMIN_TOKEN.
    Uso: https://dondever.app/admin/subscribers?token=TU_TOKEN
    """
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    from subscribers import _load
    data = _load()
    subs = data.get("subscribers", {})
    active = [(p, info) for p, info in subs.items() if info.get("active", True)]
    inactive = [(p, info) for p, info in subs.items() if not info.get("active", True)]

    def mask(phone: str) -> str:
        # Muestra +52155***1234 para privacidad en logs
        if len(phone) > 6:
            return phone[:5] + "***" + phone[-4:]
        return phone

    return {
        "ok": True,
        "total": len(subs),
        "active_count": len(active),
        "inactive_count": len(inactive),
        "active": [
            {
                "phone": mask(p),
                "subscribed_at": info.get("subscribed_at"),
                "last_active": info.get("last_active"),
            }
            for p, info in active
        ],
        "inactive": [
            {"phone": mask(p), "subscribed_at": info.get("subscribed_at")}
            for p, info in inactive
        ],
    }


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, token: str = ""):
    """Dashboard admin con métricas clave de DondeVer."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return HTMLResponse("<h1>Token inválido</h1><p>Usa ?token=TU_ADMIN_TOKEN</p>", status_code=403)

    from subscribers import _load, get_subscriber_count, get_active_subscribers
    from twitter_bot import _tweet_timestamps, _posted_games, MAX_TWEETS_PER_DAY

    # Subscribers
    subs_data = _load()
    all_subs = subs_data.get("subscribers", {})
    active_subs = [(p, i) for p, i in all_subs.items() if i.get("active", True)]
    inactive_subs = [(p, i) for p, i in all_subs.items() if not i.get("active", True)]

    # Today's tweets
    now_ts = __import__("time").time()
    tweets_today = len(_tweet_timestamps)
    tweets_last_hour = sum(1 for t in _tweet_timestamps if t > now_ts - 3600)

    # Games posted today
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games_posted = len(_posted_games.get(today_key, set()))

    # Affiliate clicks
    clicks_7d = get_click_stats(7)
    clicks_today = get_click_stats(1)

    # Group clicks by affiliate and source
    def group_clicks(raw: dict) -> dict:
        by_aff = {}
        by_src = {}
        total = 0
        for k, v in raw.items():
            aff, src = k.split(":", 1) if ":" in k else (k, "unknown")
            by_aff[aff] = by_aff.get(aff, 0) + v
            by_src[src] = by_src.get(src, 0) + v
            total += v
        return {"by_affiliate": by_aff, "by_source": by_src, "total": total}

    clicks_7d_grouped = group_clicks(clicks_7d)
    clicks_today_grouped = group_clicks(clicks_today)

    # Today's games count
    try:
        games = await get_todays_games()
        total_games = len(games)
        live_games = sum(1 for g in games if g["status"]["state"] == "in")
    except Exception:
        total_games = 0
        live_games = 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "active_count": len(active_subs),
        "inactive_count": len(inactive_subs),
        "active_subs": active_subs,
        "tweets_today": tweets_today,
        "tweets_max": MAX_TWEETS_PER_DAY,
        "tweets_last_hour": tweets_last_hour,
        "games_posted": games_posted,
        "total_games": total_games,
        "live_games": live_games,
        "clicks_today": clicks_today_grouped,
        "clicks_7d": clicks_7d_grouped,
        "token": token,
    })


@app.post("/whatsapp/test-send")
async def whatsapp_test_send(to: str):
    """Enviar un mensaje de prueba a un numero via Twilio. ej: /whatsapp/test-send?to=+521XXXXXXXXXX"""
    import os as _os
    from twilio.rest import Client as TwilioClient
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")
    if not sid or not token:
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN no configurados"}
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    try:
        client = TwilioClient(sid, token)
        msg = client.messages.create(
            body="Test de DondeVer.app — si recibes este mensaje, el webhook de salida funciona. Responde *suscribir* para probar el flujo de entrada.",
            from_=wa_num,
            to=to,
        )
        return {"ok": True, "message_sid": msg.sid, "status": msg.status, "to": to, "from": wa_num}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.get("/whatsapp/debug")
async def whatsapp_debug():
    """Diagnostico completo del sistema WhatsApp: subscribers, twilio, scheduler."""
    from subscribers import get_active_subscribers, _load, SUBSCRIBERS_FILE
    from whatsapp_broadcast import get_twilio_client, CONTENT_SID
    from config import TWILIO_WA_NUMBER

    # Subscriber info
    data = _load()
    active = get_active_subscribers()

    # Twilio check
    client = get_twilio_client()
    twilio_ok = client is not None

    return {
        "subscribers_file": SUBSCRIBERS_FILE,
        "total_subscribers": len(data.get("subscribers", {})),
        "active_subscribers": len(active),
        "subscriber_numbers": active,  # remove in production if privacy concern
        "all_data": data,
        "twilio_configured": twilio_ok,
        "twilio_from": TWILIO_WA_NUMBER,
        "content_template_sid": CONTENT_SID or "NOT SET — broadcasts only work within 24h window",
        "hint": "Si el broadcast falla, el usuario debe mandar un mensaje al bot dentro de las 24h previas, O configura TWILIO_CONTENT_SID con un template aprobado.",
    }


# Store last broadcast result for diagnostics
_last_broadcast = {"ran_at": None, "result": None, "error": None}


@app.api_route("/whatsapp/broadcast-now", methods=["GET", "POST"])
async def whatsapp_broadcast_now(token: str = ""):
    """Disparar el broadcast diario ahora mismo a todos los suscriptores.
    Acepta GET y POST para compatibilidad con cron externos (cron-job.org, etc.).
    Usa Meta Cloud API (send_whatsapp_daily.py)."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    try:
        from send_whatsapp_daily import send_daily_broadcast
        result = await send_daily_broadcast()
        _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
        _last_broadcast["result"] = result
        _last_broadcast["error"] = None
        return {"ok": True, "result": result}
    except Exception as e:
        _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
        _last_broadcast["result"] = None
        _last_broadcast["error"] = str(e)
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.api_route("/whatsapp/broadcast-preview", methods=["GET"])
async def whatsapp_broadcast_preview():
    """Preview del mensaje diario sin enviarlo."""
    try:
        from send_whatsapp_daily import compose_daily_message
        msg = await compose_daily_message()
        if msg:
            return {"ok": True, "message": msg, "chars": len(msg)}
        return {"ok": True, "message": None, "note": "No games today"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/whatsapp/broadcast-status")
async def whatsapp_broadcast_status():
    """Ver el resultado del ultimo broadcast (sin auth)."""
    from subscribers import get_active_subscribers
    from meta_whatsapp import is_configured as wa_configured
    active = get_active_subscribers()
    return {
        "last_broadcast": _last_broadcast,
        "active_subscribers": len(active),
        "meta_whatsapp_configured": wa_configured(),
        "hint": "Usa Meta Cloud API. Set WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID en env vars."
    }


@app.get("/whatsapp/check-delivery")
async def whatsapp_check_delivery():
    """Verifica el estado de entrega de los ultimos mensajes enviados por Twilio."""
    from whatsapp_broadcast import get_twilio_client
    client = get_twilio_client()
    if not client:
        return {"ok": False, "error": "Twilio no configurado"}
    try:
        # Get last 10 outbound messages
        messages = client.messages.list(
            from_=TWILIO_WA_NUMBER if 'TWILIO_WA_NUMBER' in dir() else None,
            limit=10
        )
        results = []
        for m in messages:
            results.append({
                "sid": m.sid[:12] + "...",
                "to": m.to,
                "status": m.status,  # queued, sent, delivered, read, failed, undelivered
                "error_code": m.error_code,
                "error_message": m.error_message,
                "date_sent": str(m.date_sent) if m.date_sent else None,
                "date_created": str(m.date_created),
                "direction": m.direction,
            })
        return {"ok": True, "messages": results}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.post("/whatsapp/broadcast-to")
async def whatsapp_broadcast_to(to: str):
    """Mandar el broadcast diario a un solo numero. ej: /whatsapp/broadcast-to?to=+521XXXXXXXXXX"""
    try:
        from whatsapp_broadcast import compose_daily_broadcast, get_twilio_client, CONTENT_SID
        from config import TWILIO_WA_NUMBER
        msg = await compose_daily_broadcast()
        if not msg:
            return {"ok": False, "error": "No hay juegos hoy"}
        client = get_twilio_client()
        if not client:
            return {"ok": False, "error": "Twilio no configurado"}
        from whatsapp_broadcast import _ensure_wa_number
        to_num = _ensure_wa_number(to)

        if CONTENT_SID:
            import json as _json
            m = client.messages.create(content_sid=CONTENT_SID, content_variables=_json.dumps({"1": msg}), from_=TWILIO_WA_NUMBER, to=to_num)
        else:
            m = client.messages.create(body=msg, from_=TWILIO_WA_NUMBER, to=to_num)

        return {
            "ok": True, "sid": m.sid, "status": m.status, "to": to_num,
            "used_template": bool(CONTENT_SID),
            "preview": msg[:200],
        }
    except Exception as e:
        error_msg = str(e)
        hint = ""
        if "63016" in error_msg or "63032" in error_msg or "outside" in error_msg.lower():
            hint = "El usuario esta fuera de la ventana de 24h. Necesitas un Content Template aprobado en Twilio."
        elif "21408" in error_msg:
            hint = "El numero no tiene sesion activa de WhatsApp. El usuario debe mandar un mensaje primero."
        elif "credentials" in error_msg.lower() or "auth" in error_msg.lower():
            hint = "Credenciales de Twilio invalidas. Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN."
        return {"ok": False, "error": error_msg, "type": type(e).__name__, "hint": hint}


# ── Twitter Bot Scheduler ────────────────────────────────

import os
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from twitter_bot import setup_twitter_scheduler
    from facebook_bot import setup_facebook_scheduler
    from send_whatsapp_daily import send_daily_broadcast as _meta_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from push_notifications import check_and_send_pregame_pushes, send_daily_push_summary
    from apscheduler.triggers.interval import IntervalTrigger

    async def _tracked_broadcast():
        """Wrapper that logs broadcast results — now uses Meta Cloud API."""
        try:
            result = await _meta_broadcast()
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = result
            _last_broadcast["error"] = None
            _last_broadcast["source"] = "scheduler"
            logger.info(f"Scheduled broadcast completed: {result}")
        except Exception as e:
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = None
            _last_broadcast["error"] = str(e)
            _last_broadcast["source"] = "scheduler"
            logger.error(f"Scheduled broadcast FAILED: {e}")

    async def _keep_alive_ping():
        """Ping self every 13 min to prevent Render free-tier sleep."""
        import urllib.request
        try:
            ping_url = os.getenv("RENDER_EXTERNAL_URL", "https://dondever.app")
            urllib.request.urlopen(f"{ping_url}/health", timeout=10)
        except Exception:
            pass

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():
        # Twitter bot (only if credentials set)
        if os.getenv("TWITTER_API_KEY"):
            setup_twitter_scheduler(scheduler)

        # Facebook bot (only if credentials set)
        if os.getenv("FB_PAGE_ACCESS_TOKEN"):
            setup_facebook_scheduler(scheduler)

        # WhatsApp daily broadcast — DISABLED in scheduler.
        # Handled by Render cron job "Dondever-WhatsApp" to avoid duplicate sends.
        # scheduler.add_job(
        #     _tracked_broadcast,
        #     CronTrigger(hour=15, minute=0),
        #     id="whatsapp_daily_broadcast",
        #     name="Daily WhatsApp picks broadcast",
        #     replace_existing=True,
        # )
        # logger.info("WhatsApp broadcast scheduled at 9:00 AM MX")

        # WhatsApp pre-game alerts every 5 minutes
        scheduler.add_job(
            send_pregame_alerts,
            IntervalTrigger(minutes=5),
            id="whatsapp_pregame_alerts",
            name="Pre-game WhatsApp alerts",
            replace_existing=True,
        )
        logger.info("Pre-game alerts scheduled every 5 min")

        # Web Push notifications (only if OneSignal configured)
        if os.getenv("ONESIGNAL_APP_ID") and os.getenv("ONESIGNAL_API_KEY"):
            async def _push_pregame_check():
                """Check for games starting soon and send push notifications."""
                try:
                    games = await get_todays_games()
                    await check_and_send_pregame_pushes(games)
                except Exception as e:
                    logger.error(f"Push pre-game check failed: {e}")

            async def _push_daily_summary():
                """Send daily summary push at 8:00 AM MX."""
                try:
                    games = await get_todays_games()
                    await send_daily_push_summary(games)
                except Exception as e:
                    logger.error(f"Push daily summary failed: {e}")

            # Pre-game push alerts every 5 minutes
            scheduler.add_job(
                _push_pregame_check,
                IntervalTrigger(minutes=5),
                id="push_pregame_alerts",
                name="Pre-game push notifications",
                replace_existing=True,
            )
            # Daily push summary at 8:00 AM MX (14:00 UTC)
            scheduler.add_job(
                _push_daily_summary,
                CronTrigger(hour=14, minute=0),
                id="push_daily_summary",
                name="Daily push summary",
                replace_existing=True,
            )
            logger.info("Push notifications scheduled (pre-game every 5 min + daily at 8AM MX)")

        # TikTok/Reels daily video + images at 7:30 AM MX (13:30 UTC)
        scheduler.add_job(
            generate_daily_video,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_video",
            name="Daily TikTok video generation",
            replace_existing=True,
        )
        scheduler.add_job(
            generate_daily_images,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_images",
            name="Daily TikTok images generation",
            replace_existing=True,
        )
        logger.info("TikTok video generation scheduled at 7:30 AM MX")

        # Keep-alive ping every 13 min (prevents Render free-tier sleep at 15 min)
        scheduler.add_job(
            _keep_alive_ping,
            IntervalTrigger(minutes=13),
            id="keep_alive_ping",
            name="Keep-alive self-ping",
            replace_existing=True,
        )
        logger.info("Keep-alive ping scheduled every 13 min")

        scheduler.start()
        logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def stop_scheduler():
        scheduler.shutdown()

except ImportError:
    logger.warning("APScheduler not installed, scheduled jobs disabled")


# ── SEO: Sitemap & Robots ───────────────────────────────

@app.get("/tiktokVCdYT0dv6jrqTL4pncMRP6dXaRB54Aka.txt", response_class=PlainTextResponse)
async def tiktok_verification_old():
    """TikTok domain verification file (legacy — sandbox)."""
    return "tiktok-developers-site-verification=VCdYT0dv6jrqTL4pncMRP6dXaRB54Aka"


@app.get("/tiktokaCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg.txt", response_class=PlainTextResponse)
async def tiktok_verification_prod():
    """TikTok domain verification file (production)."""
    return "tiktok-developers-site-verification=aCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg"


@app.get("/ads.txt", response_class=PlainTextResponse)
async def ads_txt():
    """Serve ads.txt for Google AdSense verification."""
    pub_id = os.getenv("ADSENSE_PUB_ID", "ca-pub-2576227882415709")
    return f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n"


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt for search engine crawlers."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /juego/\n"
        "Allow: /liga/\n"
        "Disallow: /api/\n"
        "Disallow: /webhook/\n"
        f"\nSitemap: {APP_URL}/sitemap.xml\n"
    )


@app.get("/tiktok/hoy")
async def tiktok_today():
    """Show today's TikTok video and images for easy download."""
    from pathlib import Path
    from datetime import datetime
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"/static/tiktok/dondever_picks_{date_tag}.mp4"
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = []
    if images_dir.exists():
        images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")])
    return {
        "date": date_tag,
        "video": video_path,
        "images": images,
        "instructions": "Descarga el video y subelo a TikTok/Reels/Shorts. Las imagenes sirven para carrusel de Instagram.",
    }


@app.get("/twitter/debug")
async def twitter_debug():
    """Diagnostico del bot de Twitter — para saber por que no tweetea."""
    from twitter_bot import (
        twitter_credentials_valid, get_twitter_client,
        _tweet_timestamps, MAX_TWEETS_PER_HOUR, MAX_TWEETS_PER_DAY,
        MIN_SECONDS_BETWEEN_TWEETS, _can_post_now,
    )
    from sports_api import get_todays_games
    from datetime import datetime, timezone
    import time as _time

    info = {
        "credentials_set": twitter_credentials_valid(),
        "tweets_posted_last_24h": len(_tweet_timestamps),
        "limits": {
            "per_hour": MAX_TWEETS_PER_HOUR,
            "per_day": MAX_TWEETS_PER_DAY,
            "min_seconds_between": MIN_SECONDS_BETWEEN_TWEETS,
        },
        "can_post_now": None,
        "rate_limit_reason": None,
        "auth_check": None,
        "upcoming_games_20min": [],
        "games_today": 0,
    }

    allowed, reason = _can_post_now()
    info["can_post_now"] = allowed
    info["rate_limit_reason"] = reason or None

    # Verifica que los tokens funcionen (sin postear)
    try:
        client = get_twitter_client()
        if client:
            me = client.get_me()
            info["auth_check"] = {"ok": True, "username": me.data.username if me.data else None}
        else:
            info["auth_check"] = {"ok": False, "error": "no client (credentials missing)"}
    except Exception as e:
        info["auth_check"] = {"ok": False, "error": str(e)}

    # Juegos próximos en los siguientes 20 min
    try:
        games = await get_todays_games()
        info["games_today"] = len(games)
        now = datetime.now(timezone.utc)
        for g in games:
            if g["status"]["state"] != "pre":
                continue
            try:
                gt = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                diff_min = (gt - now).total_seconds() / 60
                if 0 < diff_min <= 20:
                    info["upcoming_games_20min"].append({
                        "name": g["name"], "in_minutes": round(diff_min, 1),
                    })
            except Exception:
                pass
    except Exception as e:
        info["games_error"] = str(e)

    return info


@app.post("/twitter/test-tweet")
async def twitter_test_tweet():
    """Postea un tweet de prueba MANUALMENTE. Solo usar para verificar que funciona."""
    from twitter_bot import post_tweet
    from datetime import datetime
    text = f"Test de DondeVer.app — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC. Juegos de hoy en https://dondever.app"
    result = post_tweet(text)
    return result


@app.post("/twitter/trigger/{job_type}")
async def twitter_trigger_job(job_type: str):
    """Disparar manualmente un job de Twitter: summary, poll, pick, games."""
    from twitter_bot import (
        compose_daily_summary_tweet, post_tweet, post_daily_poll,
        post_pick_del_dia, post_game_tweets,
    )
    from sports_api import get_todays_games

    if job_type == "summary":
        games = await get_todays_games()
        if not games:
            return {"status": "no_games", "message": "No hay juegos hoy"}
        tweet = compose_daily_summary_tweet(games)
        result = post_tweet(tweet)
        return {"status": "ok", "type": "summary", "tweet": tweet, "result": result}
    elif job_type == "poll":
        result = await post_daily_poll()
        return {"status": "ok", "type": "poll", "result": result}
    elif job_type == "pick":
        result = await post_pick_del_dia()
        return {"status": "ok", "type": "pick", "result": result}
    elif job_type == "games":
        result = await post_game_tweets(minutes_before=240, max_tweets=2)
        return {"status": "ok", "type": "games", "result": result}
    else:
        return {"status": "error", "message": f"Tipo '{job_type}' no valido. Usa: summary, poll, pick, games"}


@app.get("/tiktok/generar")
async def tiktok_generate_now(images: bool = False):
    """Manually trigger TikTok video generation. Pass ?images=true para generar carrusel tambien."""
    try:
        from tiktok_generator import generate_daily_video, generate_daily_images
        from sports_api import get_todays_games

        # Verificar primero que si hay juegos (para distinguir no_games vs error ffmpeg)
        games_check = await get_todays_games()
        if not games_check:
            return JSONResponse({"status": "no_games", "message": "No hay juegos hoy"})

        logger.info(f"[generar] {len(games_check)} juegos encontrados, generando video...")
        video = await generate_daily_video()

        if not video:
            # Hay juegos pero el video no se genero → error en ffmpeg/PIL
            return JSONResponse(
                {"status": "error", "error": "Video generation returned empty (ffmpeg/PIL error)", "games_found": len(games_check)},
                status_code=500,
            )

        img_count = 0
        if images:
            img_list = await generate_daily_images()
            img_count = len(img_list) if img_list else 0

        return JSONResponse({
            "video": video,
            "images_count": img_count,
            "games_used": len(games_check),
            "status": "ok",
        })
    except Exception as e:
        logger.exception("tiktok_generate_now failed")
        return JSONResponse(
            {"status": "error", "error": str(e), "type": type(e).__name__},
            status_code=500,
        )


@app.get("/sitemap.xml")
async def sitemap_xml():
    """Dynamic sitemap with today's game pages for Google indexing."""
    today = datetime.now(TZ_MX)
    today_str = today.strftime("%Y-%m-%d")

    games = await get_todays_games()

    urls = [
        f'  <url>\n    <loc>{APP_URL}</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>hourly</changefreq>\n'
        f'    <priority>1.0</priority>\n  </url>'
    ]

    for game in games:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/juego/{game["id"]}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>hourly</changefreq>\n'
            f'    <priority>0.8</priority>\n  </url>'
        )

    # Static pages (legal + guides)
    static_pages = [
        ("sobre-nosotros", "monthly", "0.5"),
        ("privacidad", "monthly", "0.3"),
        ("terminos", "monthly", "0.3"),
        ("guia/donde-ver-liga-mx", "weekly", "0.8"),
        ("guia/donde-ver-nfl-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-nba-en-mexico", "weekly", "0.8"),
        ("guia/mejores-streaming-deportes-mexico", "weekly", "0.8"),
        # donde-ver-champions-league redirects 301 → donde-ver-champions-en-mexico
        ("guia/como-ver-tudn-en-usa", "weekly", "0.8"),
        ("guia/mejores-casas-apuestas-liga-mx", "weekly", "0.9"),
        ("guia/donde-ver-champions-en-mexico", "weekly", "0.8"),
        # New "Como ver" guides
        ("guia/como-ver-premier-league-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-la-liga-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-serie-a-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-bundesliga-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-ligue-1-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-mlb-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-nhl-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-mls-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-ufc-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-liga-mx-femenil", "weekly", "0.8"),
        ("guia/como-ver-europa-league-en-mexico", "weekly", "0.8"),
        # Caribbean / LatAm MLB guides
        ("guia/donde-ver-mlb-en-venezuela", "weekly", "0.8"),
        ("guia/donde-ver-mlb-en-republica-dominicana", "weekly", "0.8"),
        ("guia/donde-ver-mlb-en-panama", "weekly", "0.8"),
        # Evergreen high-volume guides (Jul 2026)
        ("guia/donde-ver-mundial-2026-gratis", "daily", "0.9"),
        ("guia/mejores-apps-ver-futbol-en-vivo", "weekly", "0.8"),
        ("guia/como-ver-fox-sports-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-futbol-gratis-por-internet", "weekly", "0.8"),
        ("guia/guia-canales-deportivos-mexico", "weekly", "0.8"),
    ]
    for page, freq, priority in static_pages:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/{page}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>{freq}</changefreq>\n'
            f'    <priority>{priority}</priority>\n  </url>'
        )

    # Permanent league landing pages (high priority — always have content)
    for slug in LEAGUES:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/liga/{slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.9</priority>\n  </url>'
        )

    # Sport-today pages (high priority — always have SEO content)
    for sport_slug in SPORT_TODAY_PAGES:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/{sport_slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.9</priority>\n  </url>'
        )

    # Channel pages (SEO: "que pasan hoy en ESPN")
    for ch_slug in CHANNEL_PAGES:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/canal/{ch_slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.8</priority>\n  </url>'
        )

    # Matchup pages (SEO: "donde ver america vs chivas")
    for game in games:
        home_slug = _slugify_team(game["home"]["name"])
        away_slug = _slugify_team(game["away"]["name"])
        if home_slug and away_slug:
            matchup = f"{away_slug}-vs-{home_slug}"
            urls.append(
                f'  <url>\n    <loc>{APP_URL}/donde-ver/{matchup}</loc>\n'
                f'    <lastmod>{today_str}</lastmod>\n'
                f'    <changefreq>daily</changefreq>\n'
                f'    <priority>0.7</priority>\n  </url>'
            )

    # Country pages (SEO: "donde ver deportes en venezuela")
    for c_slug in COUNTRY_PAGES:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/donde-ver-en-{c_slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>monthly</changefreq>\n'
            f'    <priority>0.8</priority>\n  </url>'
        )

    # Streaming comparator
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/streaming</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>monthly</changefreq>\n'
        f'    <priority>0.8</priority>\n  </url>'
    )

    # Casinos comparator (high-value page for affiliate conversion)
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/casinos</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>weekly</changefreq>\n'
        f'    <priority>0.9</priority>\n  </url>'
    )

    # Team pages (SEO goldmine)
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/equipos</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>daily</changefreq>\n'
        f'    <priority>0.8</priority>\n  </url>'
    )
    for team_slug in POPULAR_TEAMS:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/equipo/{team_slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.7</priority>\n  </url>'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) +
        '\n</urlset>'
    )

    return Response(content=xml, media_type="application/xml")


# ── Static Pages (Legal + Guides for AdSense) ───────────

@app.get("/sobre-nosotros", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html")

@app.get("/privacidad", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")

@app.get("/terminos", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html")

@app.get("/casinos", response_class=HTMLResponse)
async def casinos_page(request: Request):
    """Casino comparison landing — for SEO + affiliate conversion."""
    from config import get_affiliate_url
    return templates.TemplateResponse(request, "casinos.html", {
        "betsson_url": get_affiliate_url("betsson", source="casinos"),
        "jubilee_url": get_affiliate_url("jubilee", source="casinos"),
        "vivento_url": get_affiliate_url("vivento", source="casinos"),
    })

GUIDE_REDIRECTS = {
    # Consolidate duplicate Champions League guides — avoid keyword cannibalization
    "donde-ver-champions-league": "donde-ver-champions-en-mexico",
}

@app.get("/guia/{guide_slug}", response_class=HTMLResponse)
async def guide_page(request: Request, guide_slug: str):
    """Original content guides for SEO + AdSense."""
    if guide_slug in GUIDE_REDIRECTS:
        return RedirectResponse(
            url=f"/guia/{GUIDE_REDIRECTS[guide_slug]}",
            status_code=301,
        )
    template_name = f"guides/{guide_slug}.html"
    try:
        return templates.TemplateResponse(request, template_name)
    except Exception:
        return templates.TemplateResponse(request, "404.html", status_code=404)


# ── Channel Pages (SEO: "que pasan hoy en ESPN") ────────

CHANNEL_PAGES = {
    # Mexico
    "tudn":          {"name": "TUDN",          "country": "MX", "type": "cable",     "desc": "TUDN es el canal deportivo mas importante de Mexico. Transmite Liga MX, Champions League, Liga de Naciones y mas."},
    "canal-5":       {"name": "Canal 5",       "country": "MX", "type": "broadcast", "desc": "Canal 5 de Televisa transmite partidos selectos de Liga MX en television abierta, gratis para toda Mexico."},
    "azteca-7":      {"name": "Azteca 7",      "country": "MX", "type": "broadcast", "desc": "Azteca 7 transmite partidos de futbol mexicano y eventos de la Seleccion Mexicana en TV abierta."},
    "fox-sports-mx": {"name": "Fox Sports MX", "country": "MX", "type": "cable",     "desc": "Fox Sports Mexico cubre Liga MX, MLB, NFL y UFC. Disponible en los principales sistemas de cable."},
    "vix":           {"name": "ViX",           "country": "MX", "type": "streaming", "desc": "ViX es la plataforma de streaming de TelevisaUnivision. Ofrece partidos de Liga MX, MLS, Champions y mas."},
    "espn-mx":       {"name": "ESPN MX",       "country": "MX", "type": "cable",     "desc": "ESPN Mexico transmite Premier League, La Liga, Serie A, NBA, NFL y mas deportes internacionales."},
    "claro-sports":  {"name": "Claro Sports",  "country": "MX", "type": "cable",     "desc": "Claro Sports cubre eventos deportivos selectos incluyendo Juegos Olimpicos y liga mexicana."},
    # USA
    "espn":          {"name": "ESPN",          "country": "US", "type": "cable",     "desc": "ESPN es el canal deportivo numero uno en Estados Unidos. Transmite NFL, NBA, MLB, MLS y mas."},
    "espn-plus":     {"name": "ESPN+",         "country": "US", "type": "streaming", "desc": "ESPN+ es el servicio de streaming deportivo de Disney. Incluye La Liga, Bundesliga, UFC y mas."},
    "fox":           {"name": "FOX",           "country": "US", "type": "broadcast", "desc": "FOX transmite NFL, MLB World Series, NASCAR y eventos deportivos premium en TV abierta en EE.UU."},
    "fs1":           {"name": "FS1",           "country": "US", "type": "cable",     "desc": "Fox Sports 1 cubre MLB, NASCAR, USFL y eventos de UFC en cable."},
    "nbc":           {"name": "NBC",           "country": "US", "type": "broadcast", "desc": "NBC transmite Sunday Night Football de la NFL, Premier League y Juegos Olimpicos."},
    "peacock":       {"name": "Peacock",       "country": "US", "type": "streaming", "desc": "Peacock de NBCUniversal ofrece Premier League, Sunday Night Football y eventos en vivo."},
    "paramount-plus":{"name": "Paramount+",    "country": "US", "type": "streaming", "desc": "Paramount+ transmite Champions League, Europa League, Serie A, NWSL y CBS Sports."},
    "tnt":           {"name": "TNT",           "country": "US", "type": "cable",     "desc": "TNT transmite NBA, NHL y AEW Wrestling en Estados Unidos."},
    "abc":           {"name": "ABC",           "country": "US", "type": "broadcast", "desc": "ABC transmite NBA Finals, College Football Playoff y Saturday Night Football."},
    "prime-video":   {"name": "Prime Video",   "country": "US", "type": "streaming", "desc": "Amazon Prime Video tiene Thursday Night Football de la NFL y partidos selectos de MLS."},
    "apple-tv":      {"name": "Apple TV+",     "country": "US", "type": "streaming", "desc": "Apple TV+ tiene MLS Season Pass con todos los partidos de la MLS y Friday Night Baseball de MLB."},
    "univision":     {"name": "Univision",     "country": "US", "type": "broadcast", "desc": "Univision transmite Liga MX, Concacaf y la Seleccion Mexicana para la audiencia hispana en EE.UU."},
    "telemundo":     {"name": "Telemundo",     "country": "US", "type": "broadcast", "desc": "Telemundo cubre Premier League, Copa del Mundo y eventos deportivos en espanol en EE.UU."},
}


@app.get("/canal/{channel_slug}", response_class=HTMLResponse)
async def channel_page(request: Request, channel_slug: str):
    """Channel page — what's on today for a specific channel. SEO goldmine."""
    channel = CHANNEL_PAGES.get(channel_slug)
    if not channel:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Canal no encontrado."}
        )

    all_games = await get_todays_games()

    # Filter games that broadcast on this channel
    channel_name = channel["name"]
    channel_games = []
    for g in all_games:
        if g.get("broadcasts"):
            for b in g["broadcasts"]:
                # Match by normalized name or raw name
                raw = b.get("channel", "")
                info = b.get("info", {})
                norm = info.get("name", raw) if info else raw
                if norm == channel_name or raw == channel_name:
                    channel_games.append(g)
                    break

    today = datetime.now(TZ_MX)
    return templates.TemplateResponse(
        request, "canal.html",
        context={
            "channel": channel,
            "channel_slug": channel_slug,
            "games": channel_games,
            "total_games": len(channel_games),
            "today_display": today.strftime("%A %d de %B, %Y"),
        },
    )


# ── Country Pages (SEO: "donde ver deportes en venezuela") ──

COUNTRY_PAGES = {
    "mexico": {
        "name": "México",
        "flag": "🇲🇽",
        "desc": "Guía completa de dónde ver deportes en vivo en México. Canales de TV abierta, cable y streaming disponibles.",
        "channels": [
            {"name": "Canal 5 / Las Estrellas", "type": "TV Abierta (gratis)", "sports": "Liga MX selectos, Selección Mexicana"},
            {"name": "Azteca 7", "type": "TV Abierta (gratis)", "sports": "Liga MX, Selección Mexicana"},
            {"name": "TUDN", "type": "Cable", "sports": "Liga MX, Champions, Selección, Liga de Naciones"},
            {"name": "ESPN MX", "type": "Cable", "sports": "Premier League, La Liga, Serie A, NBA, NFL"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "Liga MX, MLB, NFL, UFC"},
            {"name": "Claro Sports", "type": "Cable", "sports": "Eventos selectos, Olímpicos"},
            {"name": "ViX Premium", "type": "Streaming", "sports": "Liga MX, MLS, Champions, Selección"},
            {"name": "ESPN+ (con VPN)", "type": "Streaming", "sports": "La Liga, Bundesliga, UFC, MLB"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "NFL Game Pass", "type": "Streaming", "sports": "Todos los juegos de NFL"},
        ],
        "tip": "La mayoría de partidos de Liga MX se transmiten por TUDN (cable) y ViX (streaming). Los partidos de la Selección Mexicana se pasan por TV abierta en Canal 5 o Azteca 7.",
    },
    "venezuela": {
        "name": "Venezuela",
        "flag": "🇻🇪",
        "desc": "Dónde ver deportes en vivo desde Venezuela. Canales disponibles, streaming y opciones gratuitas para béisbol, fútbol y más.",
        "channels": [
            {"name": "ESPN Caribe/Latam", "type": "Cable", "sports": "MLB, NBA, NFL, Premier League, La Liga"},
            {"name": "DirecTV Sports", "type": "Cable/Satélite", "sports": "Liga MX, Premier League, Champions"},
            {"name": "IVC (Inter)", "type": "Cable", "sports": "Béisbol LVBP, eventos locales"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "Para MLB, la opción más completa es MLB.TV. Los juegos de la LVBP (liga venezolana) se transmiten por IVC y canales locales. Para fútbol europeo, Disney+ (ex Star+) tiene la mayoría de ligas.",
    },
    "republica-dominicana": {
        "name": "República Dominicana",
        "flag": "🇩🇴",
        "desc": "Guía de dónde ver deportes en vivo en República Dominicana. Canales de TV y streaming para béisbol, fútbol y más.",
        "channels": [
            {"name": "ESPN Caribe", "type": "Cable", "sports": "MLB, NBA, NFL, fútbol europeo"},
            {"name": "CDN Deportes", "type": "Cable/TV", "sports": "LIDOM (béisbol dominicano)"},
            {"name": "Sky/Claro TV", "type": "Cable/Satélite", "sports": "Liga MX, Champions, NBA"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "La LIDOM se transmite por CDN y canales locales. Para MLB, la mejor opción es MLB.TV que incluye todos los juegos. Para fútbol europeo y NBA, Disney+ (ESPN) tiene la cobertura más amplia.",
    },
    "panama": {
        "name": "Panamá",
        "flag": "🇵🇦",
        "desc": "Dónde ver deportes en vivo en Panamá. TV, cable y streaming disponibles para béisbol, fútbol y más.",
        "channels": [
            {"name": "ESPN Centroamérica", "type": "Cable", "sports": "MLB, NBA, NFL, fútbol europeo"},
            {"name": "RPC / TVN", "type": "TV Abierta", "sports": "Selección de Panamá, eventos locales"},
            {"name": "Cable & Wireless", "type": "Cable", "sports": "Liga MX, Champions, NBA"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, LaLiga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "El béisbol profesional panameño (Probeis) se transmite por canales locales. Para MLB y deportes internacionales, ESPN Centroamérica y MLB.TV son las mejores opciones.",
    },
    "estados-unidos": {
        "name": "Estados Unidos",
        "flag": "🇺🇸",
        "desc": "Guía completa de dónde ver deportes en vivo en Estados Unidos en español. Canales hispanos, streaming y opciones para ver Liga MX, MLB y más.",
        "channels": [
            {"name": "Univision / UniMás", "type": "TV Abierta (gratis)", "sports": "Liga MX, Selección Mexicana, MLS"},
            {"name": "Telemundo", "type": "TV Abierta (gratis)", "sports": "Premier League, Copa del Mundo"},
            {"name": "TUDN", "type": "Cable", "sports": "Liga MX, Champions, Liga de Naciones"},
            {"name": "ESPN / ESPN2", "type": "Cable", "sports": "NFL, NBA, MLB, MLS, fútbol europeo"},
            {"name": "FOX / FS1", "type": "Cable/TV", "sports": "MLB, NFL, NASCAR, UFC"},
            {"name": "NBC / Peacock", "type": "Cable/Streaming", "sports": "Premier League, SNF, NHL"},
            {"name": "ESPN+", "type": "Streaming", "sports": "La Liga, Bundesliga, UFC, MLB extra"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions, Europa League, NWSL"},
            {"name": "Prime Video", "type": "Streaming", "sports": "Thursday Night Football (NFL)"},
            {"name": "Apple TV+ / MLS Season Pass", "type": "Streaming", "sports": "MLS (todos los partidos)"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "NFL+", "type": "Streaming", "sports": "Juegos de NFL en vivo (móvil)"},
        ],
        "tip": "Para ver Liga MX en EE.UU., las opciones principales son TUDN (cable), Univision (TV abierta gratis) y ViX (streaming). Para NFL, la mayoría de juegos están en FOX, CBS, NBC y ESPN, con Thursday Night Football exclusivo en Amazon Prime Video.",
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "desc": "Dónde ver deportes en vivo en Colombia. Canales de TV, cable y streaming para fútbol, béisbol y más deportes.",
        "channels": [
            {"name": "Win Sports", "type": "Streaming/Cable", "sports": "Liga BetPlay (fútbol colombiano)"},
            {"name": "ESPN Latam", "type": "Cable", "sports": "Premier League, La Liga, NBA, NFL"},
            {"name": "DirecTV Sports", "type": "Cable/Satélite", "sports": "Liga MX, Champions, fútbol sudamericano"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
        ],
        "tip": "El fútbol colombiano (Liga BetPlay) se ve exclusivamente en Win Sports. Para fútbol europeo y deportes americanos, Disney+ (ESPN) tiene la cobertura más amplia.",
    },
    "argentina": {
        "name": "Argentina",
        "flag": "🇦🇷",
        "desc": "Guía de dónde ver deportes en vivo en Argentina. Canales, streaming y opciones para fútbol, NBA, NFL y más.",
        "channels": [
            {"name": "ESPN Latam", "type": "Cable", "sports": "Premier League, La Liga, NBA, NFL, fútbol argentino"},
            {"name": "TNT Sports Argentina", "type": "Cable", "sports": "Liga Profesional, Copa Argentina"},
            {"name": "TV Pública", "type": "TV Abierta (gratis)", "sports": "Selección Argentina, eventos selectos"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A, fútbol argentino"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
        ],
        "tip": "El fútbol argentino (Liga Profesional) se transmite por TNT Sports y Disney+ (Star+). Para fútbol europeo, Disney+ tiene la mayoría de ligas. La Selección Argentina se pasa por TV Pública cuando juega de local.",
    },
}


# Per-country sport priorities + internal SEO links.
# Games from priority leagues are shown FIRST on the country page.
COUNTRY_SPORT_PRIORITY = {
    "mexico": {
        "leagues": ["liga-mx", "liga-expansion", "mls", "mlb", "nfl", "nba", "champions"],
        "links": [
            {"label": "⚽ Dónde ver Liga MX", "url": "/guia/donde-ver-liga-mx"},
            {"label": "⚾ Dónde ver MLB", "url": "/guia/como-ver-mlb-en-mexico"},
            {"label": "🏈 Dónde ver NFL", "url": "/guia/donde-ver-nfl-en-mexico"},
            {"label": "🏀 Dónde ver NBA", "url": "/guia/donde-ver-nba-en-mexico"},
            {"label": "🏆 Dónde ver Champions League", "url": "/guia/donde-ver-champions-en-mexico"},
            {"label": "🏎️ Dónde ver F1", "url": "/liga/f1"},
            {"label": "🏆 Mundial 2026 gratis", "url": "/guia/donde-ver-mundial-2026-gratis"},
            {"label": "📱 Apps para ver futbol", "url": "/guia/mejores-apps-ver-futbol-en-vivo"},
            {"label": "📺 Canales deportivos", "url": "/guia/guia-canales-deportivos-mexico"},
        ],
    },
    "venezuela": {
        "leagues": ["mlb", "champions", "la-liga", "premier-league", "serie-a", "europa-league"],
        "links": [
            {"label": "⚾ Dónde ver MLB en Venezuela", "url": "/guia/donde-ver-mlb-en-venezuela"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Premier League", "url": "/liga/premier-league"},
        ],
    },
    "republica-dominicana": {
        "leagues": ["mlb", "nba", "champions"],
        "links": [
            {"label": "⚾ Dónde ver MLB en República Dominicana", "url": "/guia/donde-ver-mlb-en-republica-dominicana"},
            {"label": "🏀 Dónde ver NBA", "url": "/liga/nba"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
        ],
    },
    "panama": {
        "leagues": ["mlb", "nba", "champions"],
        "links": [
            {"label": "⚾ Dónde ver MLB en Panamá", "url": "/guia/donde-ver-mlb-en-panama"},
            {"label": "🏀 Dónde ver NBA", "url": "/liga/nba"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
        ],
    },
    "estados-unidos": {
        "leagues": ["liga-mx", "nfl", "mlb", "nba", "mls", "champions"],
        "links": [
            {"label": "⚽ Cómo ver TUDN en USA", "url": "/guia/como-ver-tudn-en-usa"},
            {"label": "⚽ Dónde ver Liga MX", "url": "/guia/donde-ver-liga-mx"},
            {"label": "🏈 NFL hoy", "url": "/liga/nfl"},
            {"label": "⚾ MLB hoy", "url": "/liga/mlb"},
        ],
    },
    "colombia": {
        "leagues": ["champions", "la-liga", "premier-league", "mlb", "nba"],
        "links": [
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Premier League", "url": "/liga/premier-league"},
        ],
    },
    "argentina": {
        "leagues": ["champions", "la-liga", "premier-league", "serie-a", "nba"],
        "links": [
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Serie A", "url": "/liga/serie-a"},
        ],
    },
}

_DAYS_ES_FULL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS_ES_FULL = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                   "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


@app.get("/donde-ver-en-{country_slug}", response_class=HTMLResponse)
async def country_page(request: Request, country_slug: str):
    """Country-specific guide — where to watch sports from each country."""
    country = COUNTRY_PAGES.get(country_slug)
    if not country:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "País no encontrado."}
        )

    all_games = await get_todays_games()
    today = datetime.now(TZ_MX)

    # Prioritize games by the country's sport interests
    prio = COUNTRY_SPORT_PRIORITY.get(country_slug, {})
    prio_leagues = prio.get("leagues", [])

    def _game_sort_key(g):
        slug = g.get("league_slug", "")
        try:
            rank = prio_leagues.index(slug)
        except ValueError:
            rank = len(prio_leagues) + 1
        return (rank, g.get("date", ""))

    sorted_games = sorted(all_games, key=_game_sort_key) if prio_leagues else all_games

    # Spanish date (server locale is English — never use %A/%B directly)
    today_display = f"{_DAYS_ES_FULL[today.weekday()]} {today.day} de {_MONTHS_ES_FULL[today.month]}, {today.year}"

    return templates.TemplateResponse(
        request, "country.html",
        context={
            "country": country,
            "country_slug": country_slug,
            "games": sorted_games,
            "total_games": len(all_games),
            "today_display": today_display,
            "sport_links": prio.get("links", []),
        },
    )


# ── Matchup Pages (SEO: "donde ver america vs chivas") ──

def _slugify_team(name: str) -> str:
    """Convert team name to URL slug."""
    import unicodedata, re
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s.lower().strip())
    return re.sub(r"[-\s]+", "-", s)


def _build_team_faq(*, team_name, team_sport, team_league, stats, games,
                    upcoming_games, recent_results, today_date_str) -> list[dict]:
    """Build unique FAQ items from real team data — no boilerplate."""
    faq = []

    # ── 1. Canales — use ACTUAL broadcast data from today's games ──
    channels_today = []
    if games:
        for g in games[:2]:
            for b in g.get("broadcasts", []):
                ch = b.get("channel", "")
                if ch and ch not in channels_today:
                    channels_today.append(ch)
    if channels_today:
        ch_str = ", ".join(channels_today[:5])
        faq.append({
            "q": f"¿En qué canal pasan a {team_name} hoy?",
            "a": f"Hoy {team_name} se transmite por {ch_str}. "
                 f"Los canales pueden variar según el partido — en DondeVer.app actualizamos esta información en tiempo real."
        })
    else:
        faq.append({
            "q": f"¿En qué canal transmiten los partidos de {team_name}?",
            "a": f"Hoy {today_date_str} no hay partidos de {team_name} programados. "
                 f"Los partidos de {team_league} se transmiten en distintos canales según el país — "
                 f"consulta esta página el día del juego para ver el canal exacto."
        })

    # ── 2. Record/posición — use standings data ──
    if stats:
        record = stats.get("record", "")
        pos = stats.get("position", "")
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        sport_type = stats.get("sport_type", "")
        if sport_type == "soccer":
            pts = stats.get("points", "")
            gd = stats.get("goal_diff", "")
            answer = f"{team_name} está en la posición #{pos} de {team_league}"
            if record:
                answer += f" con récord {record}"
            if pts:
                answer += f" ({pts} puntos"
                if gd:
                    answer += f", diferencia de goles {gd}"
                answer += ")"
            answer += ". Estos datos se actualizan automáticamente después de cada jornada."
            faq.append({"q": f"¿Cómo va {team_name} en la tabla de {team_league}?", "a": answer})
        elif record:
            answer = f"{team_name} tiene récord de {record} (posición #{pos} en {team_league})"
            if wins or losses:
                answer += f" — {wins} victorias y {losses} derrotas"
            answer += ". Las estadísticas se actualizan después de cada juego."
            faq.append({"q": f"¿Cuál es el récord de {team_name} esta temporada?", "a": answer})

    # ── 3. Próximo partido — use upcoming_games ──
    if upcoming_games:
        nxt = upcoming_games[0]
        opp = nxt["away"] if team_name.lower() in nxt["home"].lower() else nxt["home"]
        venue = "local" if team_name.lower() in nxt["home"].lower() else "visitante"
        faq.append({
            "q": f"¿Cuándo es el próximo partido de {team_name}?",
            "a": f"El próximo juego de {team_name} es contra {opp} como {venue}. "
                 f"Consulta la sección de próximos partidos arriba para ver la fecha, hora y canal exactos."
        })
    elif games:
        faq.append({
            "q": f"¿A qué hora juega {team_name} hoy?",
            "a": f"{team_name} tiene {len(games)} partido{'s' if len(games) > 1 else ''} hoy. "
                 f"Revisa los horarios arriba — se muestran en tu hora local automáticamente."
        })

    # ── 4. Últimos resultados — use recent_results ──
    if recent_results:
        last = recent_results[0]
        score = f"{last['home']} {last.get('home_score', '?')} - {last.get('away_score', '?')} {last['away']}"
        streak_w = sum(1 for r in recent_results if _team_won(r, team_name))
        streak_l = sum(1 for r in recent_results if _team_lost(r, team_name))
        form_str = f"{streak_w}G {len(recent_results) - streak_w - streak_l}E {streak_l}P"
        faq.append({
            "q": f"¿Cómo le fue a {team_name} en su último partido?",
            "a": f"El resultado más reciente fue {score}. "
                 f"En sus últimos {len(recent_results)} partidos: {form_str}."
        })

    # ── 5. Streaming gratis — sport-specific with real platforms ──
    if team_sport == "futbol":
        if "Liga MX" in team_league:
            answer = (f"Algunos partidos de {team_name} se transmiten en TV abierta por Canal 5 o Azteca 7, "
                      f"dependiendo de los derechos del equipo local. ViX ofrece partidos selectos en su versión gratuita. "
                      f"En USA, Univision transmite algunos juegos sin costo.")
        elif "Premier" in team_league or "Champions" in team_league:
            answer = (f"La {team_league} no suele tener transmisiones gratuitas en México. "
                      f"Puedes verla por ESPN (con suscripción de TV de paga) o Paramount+. "
                      f"Algunos partidos se transmiten en abierto en temporadas especiales.")
        elif any(x in team_league for x in ["Argentina", "Colombia", "Ecuador", "Chile", "Perú"]):
            answer = (f"Los partidos de {team_league} se transmiten localmente por TV de paga. "
                      f"En streaming puedes verlos por ESPN, TNT Sports o las plataformas locales de cada país.")
        else:
            answer = (f"La disponibilidad gratuita de {team_league} varía por país. "
                      f"Consulta esta página el día del partido para ver las opciones de transmisión disponibles.")
    elif team_sport == "beisbol":
        answer = (f"MLB.TV y Apple TV+ ocasionalmente ofrecen juegos gratuitos de {team_name}. "
                  f"En TV de paga, ESPN transmite juegos selectos. El 'Free Game of the Day' de MLB.TV "
                  f"rota entre diferentes equipos.")
    elif team_sport == "basketball":
        answer = (f"Juegos selectos de {team_name} se transmiten por ABC en USA (gratis con antena). "
                  f"NBA League Pass ofrece prueba gratuita al inicio de temporada. "
                  f"En México, ESPN transmite partidos selectos con suscripción de TV de paga.")
    elif team_sport == "futbol americano":
        answer = (f"En México, Canal 5 y Azteca 7 transmiten juegos de NFL cada semana en abierto. "
                  f"En USA, CBS, Fox y NBC transmiten juegos gratis con antena. "
                  f"Amazon Prime tiene Thursday Night Football (requiere suscripción).")
    else:
        answer = f"Consulta DondeVer.app el día del partido para ver las opciones de transmisión de {team_name}."
    faq.append({"q": f"¿Cómo ver a {team_name} en vivo gratis?", "a": answer})

    # ── 6. Liga-specific bonus question ──
    if team_sport == "futbol" and "Liga MX" in team_league:
        faq.append({
            "q": f"¿Qué necesita {team_name} para calificar a liguilla?",
            "a": f"La calificación a liguilla depende de la posición en la tabla general. "
                 f"Los primeros 4 clasifican directo a cuartos de final, del 5° al 12° van a Play-In. "
                 f"Consulta la posición actual de {team_name} en la tabla arriba."
        })
    elif team_sport == "beisbol":
        faq.append({
            "q": f"¿Qué posición tiene {team_name} en su división?",
            "a": f"En MLB, cada división tiene 5 equipos y los ganadores de cada una clasifican a postemporada. "
                 f"También hay 3 Wild Cards por liga. Consulta las estadísticas de {team_name} arriba para ver su posición actual."
        })
    elif team_sport == "basketball":
        faq.append({
            "q": f"¿{team_name} va a Playoffs?",
            "a": f"En la NBA, los primeros 6 de cada conferencia clasifican directo a Playoffs, "
                 f"y del 7° al 10° van al Play-In Tournament. Consulta el récord actual de {team_name} arriba."
        })

    return faq


def _team_won(result: dict, team_name: str) -> bool:
    """Check if team won a recent result."""
    tn = team_name.lower()
    hs = result.get("home_score", 0)
    as_ = result.get("away_score", 0)
    try:
        hs, as_ = int(hs), int(as_)
    except (ValueError, TypeError):
        return False
    if tn in result.get("home", "").lower():
        return hs > as_
    elif tn in result.get("away", "").lower():
        return as_ > hs
    return False


def _team_lost(result: dict, team_name: str) -> bool:
    """Check if team lost a recent result."""
    tn = team_name.lower()
    hs = result.get("home_score", 0)
    as_ = result.get("away_score", 0)
    try:
        hs, as_ = int(hs), int(as_)
    except (ValueError, TypeError):
        return False
    if tn in result.get("home", "").lower():
        return hs < as_
    elif tn in result.get("away", "").lower():
        return as_ < hs
    return False


def _quick_prediction_from_odds(game: dict) -> dict | None:
    """
    Fast prediction from odds only — no API calls, no standings.
    Used on homepage cards to show '🤖 Houston 63% favorito'.
    """
    odds = game.get("odds")
    if not odds:
        return None
    h_str = odds.get("home_odds", "")
    a_str = odds.get("away_odds", "")
    if not h_str or not a_str:
        return None
    try:
        h_num = int(str(h_str).replace("+", ""))
        a_num = int(str(a_str).replace("+", ""))
    except (ValueError, TypeError):
        return None
    # Implied probability from American odds
    h_prob = abs(h_num) / (abs(h_num) + 100) if h_num < 0 else 100 / (h_num + 100)
    a_prob = abs(a_num) / (abs(a_num) + 100) if a_num < 0 else 100 / (a_num + 100)
    total = h_prob + a_prob
    if total == 0:
        return None
    h_prob /= total
    a_prob /= total
    if h_prob >= a_prob:
        pick, prob, pick_odds = game["home"]["name"], int(h_prob * 100), h_str
        underdog, dog_odds = game["away"]["name"], a_str
    else:
        pick, prob, pick_odds = game["away"]["name"], int(a_prob * 100), a_str
        underdog, dog_odds = game["home"]["name"], h_str
    # Value detection: underdog with positive odds
    value = None
    try:
        dog_num = int(str(dog_odds).replace("+", ""))
        if dog_num > 0 and dog_num >= 140:
            value = {"team": underdog, "odds": dog_odds}
    except (ValueError, TypeError):
        pass
    return {
        "pick": pick,
        "prob": prob,
        "pick_odds": pick_odds,
        "underdog": underdog,
        "dog_odds": dog_odds,
        "value": value,
    }


def _build_matchup_context(game, home_stats, away_stats, prediction, summary):
    """Build a 1-2 line narrative about why this game matters."""
    home_left = game.get("sport", "") in ("soccer", "boxing", "mma")
    first = game["home"]["name"] if home_left else game["away"]["name"]
    second = game["away"]["name"] if home_left else game["home"]["name"]
    league = game.get("league_name", "")
    parts = []

    # Standings-based context
    for stats, team in [(home_stats, game["home"]["name"]), (away_stats, game["away"]["name"])]:
        record = stats.get("record", "")
        standing = stats.get("standing", "")
        if standing and "1" in str(standing)[:2]:
            parts.append(f"{team} llega como lider")
        elif record:
            parts.append(f"{team} ({record})")

    # Streak context from prediction
    if prediction:
        reason = prediction.get("reason", "")
        if "racha" in reason.lower():
            parts.append(reason.split("|")[0].strip())

    # H2H context
    h2h = summary.get("seasonseries", [])
    if h2h:
        for series in h2h:
            events = series.get("events", [])
            if events:
                parts.append(f"{len(events)} enfrentamientos esta temporada")
                break

    if not parts:
        parts.append(f"Partido de {league}")

    return " — ".join(parts[:2]) + "."


@app.get("/donde-ver/{matchup_slug}", response_class=HTMLResponse)
async def matchup_page(request: Request, matchup_slug: str):
    """
    SEO matchup page: /donde-ver/america-vs-chivas
    Finds today's game matching the team names in the slug and renders it.
    """
    parts = matchup_slug.split("-vs-")
    if len(parts) != 2:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Formato invalido. Usa /donde-ver/equipo1-vs-equipo2"}
        )

    slug_a, slug_b = parts[0].strip(), parts[1].strip()
    all_games = await get_todays_games()

    # Find the matching game
    matched_game = None
    for g in all_games:
        home_slug = _slugify_team(g["home"]["name"])
        away_slug = _slugify_team(g["away"]["name"])
        # Match in either order
        if (slug_a in home_slug and slug_b in away_slug) or \
           (slug_b in home_slug and slug_a in away_slug) or \
           (slug_a in away_slug and slug_b in home_slug) or \
           (slug_b in away_slug and slug_a in home_slug):
            matched_game = g
            break

    if not matched_game:
        # Try fuzzy: check if any word matches
        for g in all_games:
            h = _slugify_team(g["home"]["name"])
            a = _slugify_team(g["away"]["name"])
            a_in = any(w in h or w in a for w in slug_a.split("-") if len(w) > 3)
            b_in = any(w in h or w in a for w in slug_b.split("-") if len(w) > 3)
            if a_in and b_in:
                matched_game = g
                break

    if not matched_game:
        # Redirect to team page instead of 404 — preserves SEO value
        # Try to find a matching team page for slug_a or slug_b
        for s in (slug_a, slug_b):
            if s in POPULAR_TEAMS:
                return RedirectResponse(url=f"/equipo/{s}", status_code=302)
            # Check TEAM_ALIASES for partial match
            for alias, _search in TEAM_ALIASES.items():
                if s in alias or alias in s:
                    team_slug_match = alias.replace(" ", "-")
                    if team_slug_match in POPULAR_TEAMS:
                        return RedirectResponse(url=f"/equipo/{team_slug_match}", status_code=302)
                    break
        # Fallback: redirect to homepage
        return RedirectResponse(url="/", status_code=302)

    # Fetch odds for the game
    odds = None
    league_slug = matched_game.get("league_slug", "")
    if matched_game["status"]["state"] == "pre":
        try:
            odds_list = await fetch_odds(league_slug)
            odds = match_odds_to_game(matched_game, odds_list)
        except Exception:
            pass

    # ── Enriched data: team stats, ESPN summary, AI prediction ──
    home_slug = _team_name_to_slug(matched_game["home"]["name"])
    away_slug = _team_name_to_slug(matched_game["away"]["name"])

    home_stats = {}
    away_stats = {}
    try:
        if home_slug:
            home_stats = await get_team_stats(home_slug)
        if away_slug:
            away_stats = await get_team_stats(away_slug)
    except Exception:
        pass

    # ESPN event summary (H2H, rosters, standings)
    summary = {}
    try:
        sport = matched_game.get("sport", "")
        league_info = ALL_LEAGUES.get(league_slug)
        espn_league = league_info[1] if isinstance(league_info, tuple) else league_slug
        event_id = matched_game.get("id", "")
        if sport and espn_league and event_id:
            summary = await fetch_espn_event_summary(sport, espn_league, event_id)
    except Exception:
        pass

    # AI prediction using stat tip engine
    prediction = None
    try:
        from send_whatsapp_daily import _generate_stat_tip, _find_team_in_standings
        from send_whatsapp_daily import fetch_standings, LEAGUE_ESPN_MAP
        standings = []
        espn_info = LEAGUE_ESPN_MAP.get(league_slug)
        if espn_info:
            s_sport, s_league = espn_info
            standings = await fetch_standings(s_sport, s_league)
        prediction = await _generate_stat_tip(matched_game, odds, standings)
    except Exception as e:
        logger.warning(f"Prediction failed for matchup {matchup_slug}: {e}")

    # Context narrative — 1-2 lines about why this game matters
    context_narrative = _build_matchup_context(
        matched_game, home_stats, away_stats, prediction, summary
    )

    return templates.TemplateResponse(
        request, "matchup.html",
        context={
            "game": matched_game,
            "odds": odds,
            "matchup_slug": matchup_slug,
            "home_slug": home_slug,
            "away_slug": away_slug,
            "home_stats": home_stats,
            "away_stats": away_stats,
            "summary": summary,
            "prediction": prediction,
            "context_narrative": context_narrative,
        },
    )


# ── Streaming Comparator ────────────────────────────────

@app.get("/streaming", response_class=HTMLResponse)
async def streaming_page(request: Request):
    return templates.TemplateResponse(request, "streaming.html")


# ── Team Pages ──────────────────────────────────────────

# Popular teams for SEO (slug -> display name)
# slug -> {name, sport_label, league, keywords}
# sport_label se usa en SEO: "futbol", "basketball", "futbol americano", "beisbol"
POPULAR_TEAMS = {
    # Liga MX
    "chivas": {"name": "Guadalajara (Chivas)", "sport": "futbol", "league": "Liga MX", "aka": "Chivas, Guadalajara, Rebaño Sagrado"},
    "america": {"name": "Club América", "sport": "futbol", "league": "Liga MX", "aka": "América, Águilas, Club America"},
    "cruz-azul": {"name": "Cruz Azul", "sport": "futbol", "league": "Liga MX", "aka": "Cruz Azul, La Máquina, Cementeros"},
    "pumas": {"name": "Pumas UNAM", "sport": "futbol", "league": "Liga MX", "aka": "Pumas, UNAM, Auriazules"},
    "tigres": {"name": "Tigres UANL", "sport": "futbol", "league": "Liga MX", "aka": "Tigres, UANL, Tigres de Monterrey"},
    "monterrey": {"name": "Monterrey", "sport": "futbol", "league": "Liga MX", "aka": "Rayados, Monterrey, Rayados de Monterrey"},
    "toluca": {"name": "Toluca", "sport": "futbol", "league": "Liga MX", "aka": "Toluca, Diablos Rojos, Choriceros"},
    "santos": {"name": "Santos Laguna", "sport": "futbol", "league": "Liga MX", "aka": "Santos Laguna, Guerreros"},
    "leon": {"name": "León", "sport": "futbol", "league": "Liga MX", "aka": "León, Club León, La Fiera"},
    "pachuca": {"name": "Pachuca", "sport": "futbol", "league": "Liga MX", "aka": "Pachuca, Tuzos"},
    "atlas": {"name": "Atlas", "sport": "futbol", "league": "Liga MX", "aka": "Atlas, Zorros, Rojinegros"},
    "necaxa": {"name": "Necaxa", "sport": "futbol", "league": "Liga MX", "aka": "Necaxa, Rayos"},
    "puebla": {"name": "Puebla", "sport": "futbol", "league": "Liga MX", "aka": "Puebla, La Franja, Camoteros"},
    "queretaro": {"name": "Querétaro", "sport": "futbol", "league": "Liga MX", "aka": "Querétaro, Gallos Blancos"},
    "mazatlan": {"name": "Mazatlán FC", "sport": "futbol", "league": "Liga MX", "aka": "Mazatlán, Cañoneros"},
    "tijuana": {"name": "Club Tijuana", "sport": "futbol", "league": "Liga MX", "aka": "Tijuana, Xolos, Xoloitzcuintles"},
    "juarez": {"name": "FC Juárez", "sport": "futbol", "league": "Liga MX", "aka": "Juárez, Bravos"},
    # Europa
    "real-madrid": {"name": "Real Madrid", "sport": "futbol", "league": "La Liga", "aka": "Real Madrid, Merengues"},
    "barcelona": {"name": "FC Barcelona", "sport": "futbol", "league": "La Liga", "aka": "Barcelona, Barça, Blaugrana"},
    "liverpool": {"name": "Liverpool FC", "sport": "futbol", "league": "Premier League", "aka": "Liverpool, Reds"},
    "manchester-city": {"name": "Manchester City", "sport": "futbol", "league": "Premier League", "aka": "Man City, Citizens"},
    "manchester-united": {"name": "Manchester United", "sport": "futbol", "league": "Premier League", "aka": "Man United, Red Devils"},
    "arsenal": {"name": "Arsenal", "sport": "futbol", "league": "Premier League", "aka": "Arsenal, Gunners"},
    "chelsea": {"name": "Chelsea", "sport": "futbol", "league": "Premier League", "aka": "Chelsea, Blues"},
    "psg": {"name": "Paris Saint-Germain", "sport": "futbol", "league": "Ligue 1", "aka": "PSG, Paris"},
    "bayern": {"name": "Bayern Munich", "sport": "futbol", "league": "Bundesliga", "aka": "Bayern, Bayern München"},
    "juventus": {"name": "Juventus", "sport": "futbol", "league": "Serie A", "aka": "Juventus, Juve, Vecchia Signora"},
    "inter-milan": {"name": "Inter de Milán", "sport": "futbol", "league": "Serie A", "aka": "Inter, Nerazzurri"},
    # MLS
    "lafc": {"name": "Los Angeles FC", "sport": "futbol", "league": "MLS", "aka": "LAFC, Los Angeles FC"},
    "la-galaxy": {"name": "LA Galaxy", "sport": "futbol", "league": "MLS", "aka": "Galaxy, LA Galaxy"},
    "inter-miami": {"name": "Inter Miami CF", "sport": "futbol", "league": "MLS", "aka": "Inter Miami, Miami CF, Messi Miami"},
    "austin-fc": {"name": "Austin FC", "sport": "futbol", "league": "MLS", "aka": "Austin FC, Austin"},
    "houston-dynamo": {"name": "Houston Dynamo FC", "sport": "futbol", "league": "MLS", "aka": "Dynamo, Houston Dynamo"},
    "fc-dallas": {"name": "FC Dallas", "sport": "futbol", "league": "MLS", "aka": "FC Dallas, Dallas FC"},
    # Europa extra
    "atletico-madrid": {"name": "Atlético de Madrid", "sport": "futbol", "league": "La Liga", "aka": "Atlético, Atleti, Colchoneros"},
    "ac-milan": {"name": "AC Milan", "sport": "futbol", "league": "Serie A", "aka": "Milan, AC Milan, Rossoneri"},
    "napoli": {"name": "SSC Napoli", "sport": "futbol", "league": "Serie A", "aka": "Napoli, Nápoles"},
    "borussia-dortmund": {"name": "Borussia Dortmund", "sport": "futbol", "league": "Bundesliga", "aka": "Dortmund, BVB, Borussia"},
    "tottenham": {"name": "Tottenham Hotspur", "sport": "futbol", "league": "Premier League", "aka": "Tottenham, Spurs, Hotspur"},
    "aston-villa": {"name": "Aston Villa", "sport": "futbol", "league": "Premier League", "aka": "Aston Villa, Villa, Villans"},
    # NBA
    "lakers": {"name": "Los Angeles Lakers", "sport": "basketball", "league": "NBA", "aka": "Lakers, LA Lakers"},
    "celtics": {"name": "Boston Celtics", "sport": "basketball", "league": "NBA", "aka": "Celtics, Boston"},
    "warriors": {"name": "Golden State Warriors", "sport": "basketball", "league": "NBA", "aka": "Warriors, Dubs, Golden State"},
    "bulls": {"name": "Chicago Bulls", "sport": "basketball", "league": "NBA", "aka": "Bulls, Chicago"},
    "heat": {"name": "Miami Heat", "sport": "basketball", "league": "NBA", "aka": "Heat, Miami"},
    "knicks": {"name": "New York Knicks", "sport": "basketball", "league": "NBA", "aka": "Knicks, NY Knicks"},
    "nuggets": {"name": "Denver Nuggets", "sport": "basketball", "league": "NBA", "aka": "Nuggets, Denver"},
    "bucks": {"name": "Milwaukee Bucks", "sport": "basketball", "league": "NBA", "aka": "Bucks, Milwaukee"},
    "mavericks": {"name": "Dallas Mavericks", "sport": "basketball", "league": "NBA", "aka": "Mavericks, Mavs, Dallas Mavs"},
    "clippers": {"name": "LA Clippers", "sport": "basketball", "league": "NBA", "aka": "Clippers, LA Clippers"},
    "suns": {"name": "Phoenix Suns", "sport": "basketball", "league": "NBA", "aka": "Suns, Phoenix"},
    "spurs-nba": {"name": "San Antonio Spurs", "sport": "basketball", "league": "NBA", "aka": "Spurs SA, San Antonio, Wemby"},
    "76ers": {"name": "Philadelphia 76ers", "sport": "basketball", "league": "NBA", "aka": "76ers, Sixers, Philadelphia"},
    "thunder": {"name": "Oklahoma City Thunder", "sport": "basketball", "league": "NBA", "aka": "Thunder, OKC"},
    "timberwolves": {"name": "Minnesota Timberwolves", "sport": "basketball", "league": "NBA", "aka": "Timberwolves, Wolves, Minnesota"},
    "cavaliers": {"name": "Cleveland Cavaliers", "sport": "basketball", "league": "NBA", "aka": "Cavaliers, Cavs, Cleveland"},
    # NFL
    "cowboys": {"name": "Dallas Cowboys", "sport": "futbol americano", "league": "NFL", "aka": "Cowboys, Vaqueros, Dallas"},
    "chiefs": {"name": "Kansas City Chiefs", "sport": "futbol americano", "league": "NFL", "aka": "Chiefs, Kansas City"},
    "49ers": {"name": "San Francisco 49ers", "sport": "futbol americano", "league": "NFL", "aka": "49ers, Niners, San Francisco"},
    "eagles": {"name": "Philadelphia Eagles", "sport": "futbol americano", "league": "NFL", "aka": "Eagles, Philadelphia, Águilas"},
    "packers": {"name": "Green Bay Packers", "sport": "futbol americano", "league": "NFL", "aka": "Packers, Green Bay"},
    "steelers": {"name": "Pittsburgh Steelers", "sport": "futbol americano", "league": "NFL", "aka": "Steelers, Pittsburgh, Acereros"},
    "raiders": {"name": "Las Vegas Raiders", "sport": "futbol americano", "league": "NFL", "aka": "Raiders, Las Vegas, Oakland Raiders"},
    "dolphins": {"name": "Miami Dolphins", "sport": "futbol americano", "league": "NFL", "aka": "Dolphins, Delfines, Miami"},
    "patriots": {"name": "New England Patriots", "sport": "futbol americano", "league": "NFL", "aka": "Patriots, Patriotas, New England"},
    "texans": {"name": "Houston Texans", "sport": "futbol americano", "league": "NFL", "aka": "Texans, Houston, Tejanos"},
    "ravens": {"name": "Baltimore Ravens", "sport": "futbol americano", "league": "NFL", "aka": "Ravens, Cuervos, Baltimore"},
    "bears": {"name": "Chicago Bears", "sport": "futbol americano", "league": "NFL", "aka": "Bears, Osos, Chicago Bears"},
    "rams": {"name": "Los Angeles Rams", "sport": "futbol americano", "league": "NFL", "aka": "Rams, LA Rams, Carneros"},
    "chargers": {"name": "Los Angeles Chargers", "sport": "futbol americano", "league": "NFL", "aka": "Chargers, LA Chargers, Cargadores"},
    "broncos": {"name": "Denver Broncos", "sport": "futbol americano", "league": "NFL", "aka": "Broncos, Denver"},
    "bills": {"name": "Buffalo Bills", "sport": "futbol americano", "league": "NFL", "aka": "Bills, Buffalo"},
    "lions": {"name": "Detroit Lions", "sport": "futbol americano", "league": "NFL", "aka": "Lions, Leones, Detroit"},
    "vikings": {"name": "Minnesota Vikings", "sport": "futbol americano", "league": "NFL", "aka": "Vikings, Vikingos, Minnesota"},
    "bengals": {"name": "Cincinnati Bengals", "sport": "futbol americano", "league": "NFL", "aka": "Bengals, Bengalíes, Cincinnati"},
    "giants-nfl": {"name": "New York Giants", "sport": "futbol americano", "league": "NFL", "aka": "Giants NY, Gigantes, NY Giants"},
    "jets": {"name": "New York Jets", "sport": "futbol americano", "league": "NFL", "aka": "Jets, NY Jets"},
    "saints": {"name": "New Orleans Saints", "sport": "futbol americano", "league": "NFL", "aka": "Saints, Santos, New Orleans"},
    "seahawks": {"name": "Seattle Seahawks", "sport": "futbol americano", "league": "NFL", "aka": "Seahawks, Seattle"},
    "commanders": {"name": "Washington Commanders", "sport": "futbol americano", "league": "NFL", "aka": "Commanders, Washington"},
    "cardinals-nfl": {"name": "Arizona Cardinals", "sport": "futbol americano", "league": "NFL", "aka": "Cardinals AZ, Cardenales, Arizona Cardinals"},
    "buccaneers": {"name": "Tampa Bay Buccaneers", "sport": "futbol americano", "league": "NFL", "aka": "Buccaneers, Bucs, Tampa Bay"},
    "falcons": {"name": "Atlanta Falcons", "sport": "futbol americano", "league": "NFL", "aka": "Falcons, Halcones, Atlanta"},
    "panthers-nfl": {"name": "Carolina Panthers", "sport": "futbol americano", "league": "NFL", "aka": "Panthers, Panteras, Carolina"},
    "colts": {"name": "Indianapolis Colts", "sport": "futbol americano", "league": "NFL", "aka": "Colts, Potros, Indianapolis"},
    "jaguars": {"name": "Jacksonville Jaguars", "sport": "futbol americano", "league": "NFL", "aka": "Jaguars, Jaguares, Jacksonville"},
    "titans": {"name": "Tennessee Titans", "sport": "futbol americano", "league": "NFL", "aka": "Titans, Titanes, Tennessee"},
    # MLB
    "dodgers": {"name": "Los Angeles Dodgers", "sport": "beisbol", "league": "MLB", "aka": "Dodgers, LA Dodgers"},
    "yankees": {"name": "New York Yankees", "sport": "beisbol", "league": "MLB", "aka": "Yankees, Yanquis, NY Yankees"},
    "red-sox": {"name": "Boston Red Sox", "sport": "beisbol", "league": "MLB", "aka": "Red Sox, Medias Rojas, Boston"},
    "astros": {"name": "Houston Astros", "sport": "beisbol", "league": "MLB", "aka": "Astros, Houston"},
    "mets": {"name": "New York Mets", "sport": "beisbol", "league": "MLB", "aka": "Mets, NY Mets"},
    "padres": {"name": "San Diego Padres", "sport": "beisbol", "league": "MLB", "aka": "Padres, San Diego"},
    "angels": {"name": "Los Angeles Angels", "sport": "beisbol", "league": "MLB", "aka": "Angels, Angelinos, LA Angels"},
    "athletics": {"name": "Athletics", "sport": "beisbol", "league": "MLB", "aka": "Athletics, A's, Atléticos"},
    "blue-jays": {"name": "Toronto Blue Jays", "sport": "beisbol", "league": "MLB", "aka": "Blue Jays, Azulejos, Toronto"},
    "braves": {"name": "Atlanta Braves", "sport": "beisbol", "league": "MLB", "aka": "Braves, Atlanta"},
    "brewers": {"name": "Milwaukee Brewers", "sport": "beisbol", "league": "MLB", "aka": "Brewers, Cerveceros, Milwaukee"},
    "cardinals": {"name": "St. Louis Cardinals", "sport": "beisbol", "league": "MLB", "aka": "Cardinals, Cardenales, St. Louis"},
    "cubs": {"name": "Chicago Cubs", "sport": "beisbol", "league": "MLB", "aka": "Cubs, Cachorros, Chicago Cubs"},
    "diamondbacks": {"name": "Arizona Diamondbacks", "sport": "beisbol", "league": "MLB", "aka": "Diamondbacks, D-backs, Arizona"},
    "giants": {"name": "San Francisco Giants", "sport": "beisbol", "league": "MLB", "aka": "Giants, Gigantes, San Francisco"},
    "guardians": {"name": "Cleveland Guardians", "sport": "beisbol", "league": "MLB", "aka": "Guardians, Guardianes, Cleveland"},
    "mariners": {"name": "Seattle Mariners", "sport": "beisbol", "league": "MLB", "aka": "Mariners, Marineros, Seattle"},
    "marlins": {"name": "Miami Marlins", "sport": "beisbol", "league": "MLB", "aka": "Marlins, Miami"},
    "nationals": {"name": "Washington Nationals", "sport": "beisbol", "league": "MLB", "aka": "Nationals, Nacionales, Washington"},
    "orioles": {"name": "Baltimore Orioles", "sport": "beisbol", "league": "MLB", "aka": "Orioles, Baltimore"},
    "phillies": {"name": "Philadelphia Phillies", "sport": "beisbol", "league": "MLB", "aka": "Phillies, Filis, Philadelphia"},
    "pirates": {"name": "Pittsburgh Pirates", "sport": "beisbol", "league": "MLB", "aka": "Pirates, Piratas, Pittsburgh"},
    "rangers": {"name": "Texas Rangers", "sport": "beisbol", "league": "MLB", "aka": "Rangers, Texas"},
    "rays": {"name": "Tampa Bay Rays", "sport": "beisbol", "league": "MLB", "aka": "Rays, Rayas, Tampa Bay"},
    "reds": {"name": "Cincinnati Reds", "sport": "beisbol", "league": "MLB", "aka": "Reds, Rojos, Cincinnati"},
    "rockies": {"name": "Colorado Rockies", "sport": "beisbol", "league": "MLB", "aka": "Rockies, Colorado"},
    "royals": {"name": "Kansas City Royals", "sport": "beisbol", "league": "MLB", "aka": "Royals, Reales, Kansas City"},
    "tigers": {"name": "Detroit Tigers", "sport": "beisbol", "league": "MLB", "aka": "Tigers, Tigres de Detroit, Detroit"},
    "twins": {"name": "Minnesota Twins", "sport": "beisbol", "league": "MLB", "aka": "Twins, Gemelos, Minnesota"},
    "white-sox": {"name": "Chicago White Sox", "sport": "beisbol", "league": "MLB", "aka": "White Sox, Medias Blancas, Chicago White Sox"},
    # NHL
    "bruins": {"name": "Boston Bruins", "sport": "hockey", "league": "NHL", "aka": "Bruins, Boston"},
    "golden-knights": {"name": "Vegas Golden Knights", "sport": "hockey", "league": "NHL", "aka": "Golden Knights, Vegas, VGK"},
    "avalanche": {"name": "Colorado Avalanche", "sport": "hockey", "league": "NHL", "aka": "Avalanche, Avs, Colorado"},
    "panthers-nhl": {"name": "Florida Panthers", "sport": "hockey", "league": "NHL", "aka": "Panthers Florida, Florida"},
    "rangers-nhl": {"name": "New York Rangers", "sport": "hockey", "league": "NHL", "aka": "Rangers NY, NY Rangers"},
    "maple-leafs": {"name": "Toronto Maple Leafs", "sport": "hockey", "league": "NHL", "aka": "Maple Leafs, Toronto, Leafs"},
    "oilers": {"name": "Edmonton Oilers", "sport": "hockey", "league": "NHL", "aka": "Oilers, Edmonton"},
    "stars": {"name": "Dallas Stars", "sport": "hockey", "league": "NHL", "aka": "Stars, Dallas Stars, Estrellas"},
    "blackhawks": {"name": "Chicago Blackhawks", "sport": "hockey", "league": "NHL", "aka": "Blackhawks, Hawks, Chicago"},
    "penguins": {"name": "Pittsburgh Penguins", "sport": "hockey", "league": "NHL", "aka": "Penguins, Pinguinos, Pittsburgh"},
    "capitals": {"name": "Washington Capitals", "sport": "hockey", "league": "NHL", "aka": "Capitals, Caps, Washington"},
    # UFC
    "ufc": {"name": "UFC", "sport": "MMA", "league": "UFC", "aka": "UFC, Ultimate Fighting"},
}

@app.get("/equipo/{team_slug}", response_class=HTMLResponse)
async def team_page(request: Request, team_slug: str):
    """Dynamic team page with today's games for that team."""
    # Resolve team info from slug
    team_info = POPULAR_TEAMS.get(team_slug)
    if team_info:
        team_name = team_info["name"]
        team_sport = team_info.get("sport", "")
        team_league_seo = team_info.get("league", "")
        team_aka = team_info.get("aka", team_name)
    else:
        # Fallback for unknown slugs
        clean_slug = team_slug.replace("-", " ")
        resolved = TEAM_ALIASES.get(clean_slug, clean_slug)
        team_name = resolved.title()
        team_sport = ""
        team_league_seo = ""
        team_aka = team_name

    # Search for games
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    games = await search_games(search_term)

    # Get team info from first game found
    team_logo = ""
    team_league = ""
    if games:
        for game in games:
            if search_term.lower() in game["home"]["name"].lower():
                team_logo = game["home"].get("logo", "")
                team_league = game.get("league_name", "")
                break
            elif search_term.lower() in game["away"]["name"].lower():
                team_logo = game["away"].get("logo", "")
                team_league = game.get("league_name", "")
                break

    # Fetch team stats (standings, record, etc.)
    stats = await get_team_stats(team_slug)

    # If we got stats but no logo from games, use logo from stats
    if not team_logo and stats.get("team_logo"):
        team_logo = stats["team_logo"]

    # Today's date in Spanish for dynamic SEO content
    DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now_mx = datetime.now(TZ_MX)
    today_date_str = f"{DAYS_ES[now_mx.weekday()]} {now_mx.day} de {MONTHS_ES[now_mx.month - 1]} de {now_mx.year}"

    # Related teams: other teams from the same league for internal linking
    final_league = team_league or team_league_seo
    related_teams = []
    for slug, info in POPULAR_TEAMS.items():
        if slug != team_slug and info.get("league") == final_league:
            related_teams.append({"slug": slug, "name": info["name"]})

    # Fetch recent results and upcoming games for the team's league
    recent_results = []
    upcoming_games = []
    from sports_api import TEAM_LEAGUE_MAP
    league_info_map = TEAM_LEAGUE_MAP.get(team_slug)
    if league_info_map:
        t_sport, t_league = league_info_map
        try:
            all_recent = await get_recent_league_results(t_sport, t_league, days=7, limit=20)
            # Filter for this team
            for r in all_recent:
                if (search_term.lower() in r["home"].lower() or
                    search_term.lower() in r["away"].lower()):
                    recent_results.append(r)
                if len(recent_results) >= 5:
                    break
        except Exception:
            pass
        try:
            all_upcoming = await get_upcoming_league_games(t_sport, t_league, days=10, limit=30)
            for u in all_upcoming:
                if (search_term.lower() in u["home"].lower() or
                    search_term.lower() in u["away"].lower()):
                    upcoming_games.append(u)
                if len(upcoming_games) >= 5:
                    break
        except Exception:
            pass

    # Fetch team news from ESPN
    team_news = []
    if league_info_map:
        t_sport, t_league = league_info_map
        try:
            team_news = await fetch_team_news(t_sport, t_league, team_name, limit=5)
        except Exception:
            pass

    # Fetch MercadoLibre product images for the team (cached 24h each)
    # Fetch all 3 product types in parallel for speed
    meli_jersey, meli_gorra, meli_acc = None, None, None
    try:
        meli_jersey, meli_gorra, meli_acc = await asyncio.gather(
            fetch_meli_product_image(f"jersey {team_name} oficial"),
            fetch_meli_product_image(f"gorra {team_name}"),
            fetch_meli_product_image(f"{team_name} accesorios futbol"),
            return_exceptions=True,
        )
        # If any returned an exception, set to None
        if isinstance(meli_jersey, Exception): meli_jersey = None
        if isinstance(meli_gorra, Exception): meli_gorra = None
        if isinstance(meli_acc, Exception): meli_acc = None
    except Exception:
        pass

    # Build dynamic FAQ based on real data (not boilerplate)
    faq_items = _build_team_faq(
        team_name=team_name,
        team_sport=team_sport,
        team_league=team_league or team_league_seo,
        stats=stats,
        games=games,
        upcoming_games=upcoming_games,
        recent_results=recent_results,
        today_date_str=today_date_str,
    )

    return templates.TemplateResponse(request, "team.html", {
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league or team_league_seo,
        "team_sport": team_sport,
        "team_aka": team_aka,
        "games": games,
        "stats": stats,
        "format_mx_time": format_mx_time,
        "format_mx_day_time": format_mx_day_time,
        "today_date_str": today_date_str,
        "related_teams": related_teams,
        "recent_results": recent_results,
        "upcoming_games": upcoming_games,
        "team_news": team_news,
        "meli_jersey": meli_jersey,
        "meli_gorra": meli_gorra,
        "meli_acc": meli_acc,
        "faq_items": faq_items,
    })


@app.get("/equipos", response_class=HTMLResponse)
async def teams_list(request: Request):
    """List all popular teams for SEO indexing."""
    teams_by_league = {
        "Liga MX": ["chivas", "america", "cruz-azul", "pumas", "tigres", "monterrey", "toluca", "santos", "leon", "pachuca", "atlas", "necaxa", "puebla", "queretaro"],
        "Premier League": ["liverpool", "manchester-city", "manchester-united", "arsenal", "chelsea"],
        "La Liga": ["real-madrid", "barcelona"],
        "Serie A": ["juventus", "inter-milan"],
        "Bundesliga": ["bayern"],
        "Ligue 1": ["psg"],
        "NBA": ["lakers", "celtics", "warriors", "bulls", "heat", "knicks"],
        "NFL": ["cowboys", "chiefs", "49ers", "eagles", "packers"],
        "MLB": ["dodgers", "yankees", "red-sox", "astros"],
    }
    return templates.TemplateResponse(request, "teams_list.html", {
        "teams_by_league": teams_by_league,
        "POPULAR_TEAMS": POPULAR_TEAMS,
    })


# ── TikTok OAuth + Content Posting ─────────────────────

@app.get("/tiktok/login")
async def tiktok_login():
    """Redirect to TikTok OAuth authorization."""
    from fastapi.responses import RedirectResponse
    auth_url = get_tiktok_auth_url()
    return RedirectResponse(url=auth_url)


@app.get("/auth/tiktok/callback", response_class=HTMLResponse)
async def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle TikTok OAuth callback after user authorizes."""
    if error:
        return HTMLResponse(f"<h1>Error de autorización TikTok</h1><p>{error}</p>")

    if not code:
        return HTMLResponse("<h1>No se recibió código de autorización</h1>")

    # Exchange code for token
    result = await exchange_code_for_token(code)

    if "access_token" in result:
        user_info = await get_user_info()
        display_name = user_info.get("data", {}).get("user", {}).get("display_name", "Usuario")
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html lang="es">
        <head><meta charset="UTF-8"><title>TikTok Conectado | DondeVer</title>
        <style>
            body {{ font-family: system-ui; background: #0a0a0a; color: #fff; display: flex;
                   justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
            .card {{ background: #1a1a1a; border-radius: 16px; padding: 40px; text-align: center; max-width: 500px; }}
            .success {{ color: #25D366; font-size: 48px; }}
            h1 {{ margin: 16px 0 8px; }}
            .btn {{ display: inline-block; background: #fe2c55; color: #fff; padding: 14px 32px;
                    border-radius: 8px; text-decoration: none; margin-top: 20px; font-weight: 600; }}
            .btn:hover {{ opacity: 0.9; }}
        </style></head>
        <body><div class="card">
            <div class="success">✓</div>
            <h1>TikTok Conectado</h1>
            <p>Cuenta: <strong>{display_name}</strong></p>
            <p>Ahora puedes publicar videos automaticamente.</p>
            <a href="/tiktok/panel" class="btn">Ir al Panel TikTok</a>
        </div></body></html>
        """)
    else:
        error_msg = result.get("error_description", result.get("error", "Error desconocido"))
        return HTMLResponse(f"""
        <h1>Error al conectar TikTok</h1>
        <p>{error_msg}</p>
        <a href="/tiktok/login">Intentar de nuevo</a>
        """)


@app.get("/tiktok/panel", response_class=HTMLResponse)
async def tiktok_panel(request: Request):
    """TikTok management panel — shows status, generate & publish videos."""
    token_info = get_token_info()
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")

    # Check if today's video exists
    from pathlib import Path
    video_path = Path(f"static/tiktok/dondever_picks_{date_tag}.mp4")
    video_exists = video_path.exists()
    video_url = f"/static/tiktok/dondever_picks_{date_tag}.mp4" if video_exists else None

    # Check images
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")]) if images_dir.exists() else []

    return templates.TemplateResponse(request, "tiktok_panel.html", {
        "authenticated": token_info["authenticated"],
        "open_id": token_info.get("open_id"),
        "video_exists": video_exists,
        "video_url": video_url,
        "images": images,
        "date_tag": date_tag,
    })


@app.post("/tiktok/publicar")
async def tiktok_publish():
    """Publish today's video to TikTok."""
    if not is_authenticated():
        return JSONResponse({"error": "No conectado a TikTok. Ve a /tiktok/login"}, status_code=401)

    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"static/tiktok/dondever_picks_{date_tag}.mp4"

    from pathlib import Path
    if not Path(video_path).exists():
        # Try generating first
        from tiktok_generator import generate_daily_video
        video_path_gen = await generate_daily_video()
        if not video_path_gen:
            return JSONResponse({"error": "No hay juegos hoy para generar video"}, status_code=404)
        video_path = video_path_gen

    today = datetime.now(TZ_MX)
    title = f"Partidos de hoy {today.strftime('%d/%m')} | Donde verlos en vivo #deportes #futbol #nba #nfl #dondever"

    result = await upload_video_to_tiktok(video_path, title)
    return JSONResponse(result)


@app.get("/tiktok/status/{publish_id}")
async def tiktok_status(publish_id: str):
    """Check publishing status of a video."""
    result = await check_publish_status(publish_id)
    return JSONResponse(result)


# ── Push Notifications Admin ─────────────────────────────

@app.get("/admin/push-test")
async def push_test(token: str = ""):
    """Send a test push notification."""
    if token != os.getenv("ADMIN_TOKEN", "dondever2026"):
        return JSONResponse({"error": "unauthorized"}, 401)
    from push_notifications import send_push
    result = await send_push(
        heading="🏟️ DondeVer — Test",
        message="Las notificaciones push funcionan correctamente",
        url=APP_URL,
    )
    return JSONResponse(result)


@app.get("/admin/push-summary")
async def push_summary_now(token: str = ""):
    """Trigger daily push summary now."""
    if token != os.getenv("ADMIN_TOKEN", "dondever2026"):
        return JSONResponse({"error": "unauthorized"}, 401)
    from push_notifications import send_daily_push_summary
    games = await get_todays_games()
    result = await send_daily_push_summary(games)
    return JSONResponse(result)


# ── Health ───────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "dondever.app", "version": "1.0.0"}


# ── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
