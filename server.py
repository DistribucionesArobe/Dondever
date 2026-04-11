"""
DondeVer.app — Main FastAPI server
Where to watch sports in Mexico & USA
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.twiml.messaging_response import MessagingResponse

from config import AFFILIATES, LEAGUES, ALL_LEAGUES, APP_URL, TZ_MX, TZ_ET, TEAM_ALIASES
from sports_api import get_todays_games, search_games
from whatsapp_bot import handle_whatsapp_message

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


# ── Template helpers ─────────────────────────────────────
def format_mx_time(iso_date: str) -> str:
    """Convert ISO date to Mexico City time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
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
templates.env.globals["app_url"] = APP_URL
templates.env.globals["now"] = lambda: datetime.now(TZ_MX)


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

    # Pick del dia — choose most interesting upcoming game
    pick_game = None
    priority_leagues = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga", "mlb"]
    upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]
    if upcoming:
        # Try priority leagues first
        for pl in priority_leagues:
            pick = next((g for g in upcoming if g["league_slug"] == pl), None)
            if pick:
                pick_game = pick
                break
        if not pick_game:
            pick_game = upcoming[0]
    elif games:
        # If no upcoming, pick a live game
        live = [g for g in games if g["status"]["state"] == "in"]
        if live:
            pick_game = live[0]

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
        },
    )


@app.get("/juego/{event_id}", response_class=HTMLResponse)
async def game_detail(request: Request, event_id: str):
    """Individual game page — good for SEO."""
    all_games = await get_todays_games()
    game = next((g for g in all_games if g["id"] == event_id), None)

    if not game:
        return templates.TemplateResponse(
            request, "404.html", status_code=404
        )

    return templates.TemplateResponse(
        request, "game.html", context={"game": game}
    )


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

    return templates.TemplateResponse(
        request, "league.html", context={
            "league_slug": league_slug,
            "league_name": display_name,
            "emoji": emoji,
            "sport": sport,
            "games": games,
            "total_games": len(games),
        }
    )


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


@app.get("/api/leagues")
async def api_leagues():
    """List available leagues."""
    return JSONResponse({
        "leagues": [
            {"slug": slug, "sport": sport, "league": league, "name": name, "emoji": emoji}
            for slug, (sport, league, name, emoji) in LEAGUES.items()
        ]
    })


# ── WhatsApp Webhook ─────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    Body: str = Form(""),
    From: str = Form(""),
):
    """Twilio WhatsApp webhook — receives messages, responds with game info."""
    logger.info(f"WhatsApp from {From}: {Body}")

    response_text = await handle_whatsapp_message(Body, From)

    twiml = MessagingResponse()
    twiml.message(response_text)
    return HTMLResponse(content=str(twiml), media_type="application/xml")


@app.get("/webhook/whatsapp")
async def whatsapp_verify():
    """Health check for Twilio webhook verification."""
    return {"status": "ok", "service": "dondever-whatsapp"}


# ── Twitter Bot Scheduler ────────────────────────────────

import os
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from twitter_bot import setup_twitter_scheduler
    from whatsapp_broadcast import send_daily_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():
        # Twitter bot (only if credentials set)
        if os.getenv("TWITTER_API_KEY"):
            setup_twitter_scheduler(scheduler)

        # WhatsApp daily broadcast at 9:00 AM MX time (15:00 UTC)
        scheduler.add_job(
            send_daily_broadcast,
            CronTrigger(hour=15, minute=0),
            id="whatsapp_daily_broadcast",
            name="Daily WhatsApp picks broadcast",
            replace_existing=True,
        )
        logger.info("WhatsApp broadcast scheduled at 9:00 AM MX")

        # WhatsApp pre-game alerts every 5 minutes
        scheduler.add_job(
            send_pregame_alerts,
            IntervalTrigger(minutes=5),
            id="whatsapp_pregame_alerts",
            name="Pre-game WhatsApp alerts",
            replace_existing=True,
        )
        logger.info("Pre-game alerts scheduled every 5 min")

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

        scheduler.start()
        logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def stop_scheduler():
        scheduler.shutdown()

except ImportError:
    logger.warning("APScheduler not installed, scheduled jobs disabled")


# ── SEO: Sitemap & Robots ───────────────────────────────

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


@app.get("/tiktok/generar")
async def tiktok_generate_now():
    """Manually trigger TikTok video generation."""
    from tiktok_generator import generate_daily_video, generate_daily_images
    video = await generate_daily_video()
    images = await generate_daily_images()
    return {
        "video": video,
        "images_count": len(images),
        "status": "ok" if video else "no_games",
    }


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
        ("guia/donde-ver-champions-league", "weekly", "0.8"),
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

    # Streaming comparator
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/streaming</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>monthly</changefreq>\n'
        f'    <priority>0.8</priority>\n  </url>'
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

@app.get("/guia/{guide_slug}", response_class=HTMLResponse)
async def guide_page(request: Request, guide_slug: str):
    """Original content guides for SEO + AdSense."""
    template_name = f"guides/{guide_slug}.html"
    try:
        return templates.TemplateResponse(request, template_name)
    except Exception:
        return templates.TemplateResponse(request, "404.html", status_code=404)


# ── Streaming Comparator ────────────────────────────────

@app.get("/streaming", response_class=HTMLResponse)
async def streaming_page(request: Request):
    return templates.TemplateResponse(request, "streaming.html")


# ── Team Pages ──────────────────────────────────────────

# Popular teams for SEO (slug -> display name)
POPULAR_TEAMS = {
    "chivas": "Guadalajara (Chivas)", "america": "America", "cruz-azul": "Cruz Azul",
    "pumas": "Pumas UNAM", "tigres": "Tigres UANL", "monterrey": "Monterrey",
    "toluca": "Toluca", "santos": "Santos Laguna", "leon": "Leon", "pachuca": "Pachuca",
    "atlas": "Atlas", "necaxa": "Necaxa", "puebla": "Puebla", "queretaro": "Queretaro",
    "real-madrid": "Real Madrid", "barcelona": "Barcelona", "liverpool": "Liverpool",
    "manchester-city": "Manchester City", "manchester-united": "Manchester United",
    "arsenal": "Arsenal", "chelsea": "Chelsea", "psg": "Paris Saint-Germain",
    "bayern": "Bayern Munich", "juventus": "Juventus", "inter-milan": "Inter Milan",
    "lakers": "Los Angeles Lakers", "celtics": "Boston Celtics", "warriors": "Golden State Warriors",
    "bulls": "Chicago Bulls", "heat": "Miami Heat", "knicks": "New York Knicks",
    "cowboys": "Dallas Cowboys", "chiefs": "Kansas City Chiefs", "49ers": "San Francisco 49ers",
    "eagles": "Philadelphia Eagles", "packers": "Green Bay Packers",
    "dodgers": "Los Angeles Dodgers", "yankees": "New York Yankees",
    "red-sox": "Boston Red Sox", "astros": "Houston Astros",
}

@app.get("/equipo/{team_slug}", response_class=HTMLResponse)
async def team_page(request: Request, team_slug: str):
    """Dynamic team page with today's games for that team."""
    # Resolve team name from slug
    team_name = POPULAR_TEAMS.get(team_slug)
    if not team_name:
        # Try from TEAM_ALIASES
        clean_slug = team_slug.replace("-", " ")
        resolved = TEAM_ALIASES.get(clean_slug, clean_slug)
        team_name = resolved.title()

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

    return templates.TemplateResponse(request, "team.html", {
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league,
        "games": games,
        "format_mx_time": format_mx_time,
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


# ── Health ───────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "dondever.app", "version": "1.0.0"}


# ── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
