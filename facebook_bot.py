"""
Facebook bot for DondeVer.app
Auto-posts daily game schedules to the DondeVer Facebook page.
Uses Facebook Graph API v19.0 via Page Access Token.
"""

import logging
import os
import random
import httpx
from datetime import datetime
from config import AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS, get_short_affiliate_url
from sports_api import get_todays_games

logger = logging.getLogger("dondever.facebook")

# ── Facebook Config ─────────────────────────────────────
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "1151151121408702")
FB_PAGE_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
FB_API_VERSION = "v19.0"
FB_API_BASE = f"https://graph.facebook.com/{FB_API_VERSION}"

# WhatsApp link for CTAs
WA_PICKS_LINK = "https://wa.me/15715463202?text=picks"


def fb_configured() -> bool:
    """Check if Facebook credentials are set."""
    return bool(FB_PAGE_TOKEN and FB_PAGE_ID)


async def fb_post(message: str, link: str = "") -> dict:
    """
    Post to Facebook page via Graph API.
    Returns {"success": True, "post_id": "..."} or {"success": False, "error": "..."}
    """
    if not fb_configured():
        logger.error("Facebook not configured — missing FB_PAGE_ACCESS_TOKEN or FB_PAGE_ID")
        return {"success": False, "error": "Facebook not configured"}

    url = f"{FB_API_BASE}/{FB_PAGE_ID}/feed"
    data = {
        "message": message,
        "access_token": FB_PAGE_TOKEN,
    }
    if link:
        data["link"] = link

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data=data)
            result = resp.json()

        if "id" in result:
            logger.info(f"Facebook post published: {result['id']}")
            return {"success": True, "post_id": result["id"]}
        else:
            error = result.get("error", {}).get("message", str(result))
            logger.error(f"Facebook post failed: {error}")
            return {"success": False, "error": error}
    except Exception as e:
        logger.error(f"Facebook post exception: {e}")
        return {"success": False, "error": str(e)}


# ── Formatters ──────────────────────────────────────────

def get_team_order(game: dict) -> tuple[str, str]:
    """Return (first_team, second_team) respecting sport conventions."""
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


def format_time_mx(date_str: str) -> str:
    """Convert ISO date to MX time string."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


def format_channels(broadcasts: list[dict]) -> str:
    """Format channels for Facebook post."""
    if not broadcasts:
        return "Por confirmar"
    channels = [b["channel"] for b in broadcasts[:4]]
    return ", ".join(channels)


# ── Post Composers ──────────────────────────────────────

async def compose_daily_post() -> str | None:
    """
    Compose the daily Facebook post with all games of the day.
    Facebook allows longer posts than Twitter, so we include more detail.
    """
    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    date_display = now.strftime("%d de %B %Y")

    if not games:
        return None

    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    live = [g for g in games if g["status"]["state"] == "in"]

    lines = [f"PARTIDOS DE HOY - {date_display}"]
    lines.append(f"Horarios en hora centro de Mexico")
    lines.append("")

    # Group by league
    leagues: dict[str, list] = {}
    for g in upcoming:
        league = g.get("league_name", "Otros")
        leagues.setdefault(league, []).append(g)

    for league_name, league_games in leagues.items():
        lines.append(f"--- {league_name} ---")
        for g in league_games:
            first, second = get_team_order(g)
            time_str = format_time_mx(g["date"])
            channels = format_channels(g["broadcasts"])
            lines.append(f"{first} vs {second}")
            lines.append(f"   {time_str} | {channels}")
        lines.append("")

    if live:
        lines.append("--- EN VIVO AHORA ---")
        for g in live:
            first, second = get_team_order(g)
            hs = g["home"]["score"] or 0
            as_ = g["away"]["score"] or 0
            if g.get("sport") in HOME_LEFT_SPORTS:
                lines.append(f"{first} {hs} - {as_} {second}")
            else:
                lines.append(f"{first} {as_} - {hs} {second}")
        lines.append("")

    # Pick del dia
    priority = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga", "mlb"]
    pick = None
    for pl in priority:
        pick = next((g for g in upcoming if g.get("league_slug") == pl), None)
        if pick:
            break
    if not pick and upcoming:
        pick = upcoming[0]

    if pick:
        first, second = get_team_order(pick)
        pick_team = first if random.random() < 0.6 else second
        reasons = [
            "viene en racha positiva",
            "juega de local",
            "mejor forma reciente",
            "favorito en momios",
        ]
        lines.append(f"PICK DEL DIA: {pick_team} en {first} vs {second}")
        lines.append(f"({random.choice(reasons)})")
        lines.append("")

    # CTA
    lines.append(f"Todos los horarios y canales en: {APP_URL}")
    lines.append(f"Picks gratis por WhatsApp: {WA_PICKS_LINK}")
    lines.append("")

    # Betting CTA (50% of the time)
    if random.random() < 0.5:
        betting_keys = [k for k in AFFILIATES if k in ("caliente", "betsson")]
        if betting_keys:
            key = random.choice(betting_keys)
            aff = AFFILIATES[key]
            url = get_short_affiliate_url(key, source="facebook")
            lines.append(f"{aff.get('cta_twitter', aff['cta'])}: {url}")
            lines.append("")

    lines.append("Solo entretenimiento. +18")

    return "\n".join(lines)


async def compose_pick_post(game: dict) -> str:
    """Compose a Pick del Dia Facebook post for a specific game."""
    first, second = get_team_order(game)
    league = game.get("league_name", "")
    time_str = format_time_mx(game["date"])
    channels = format_channels(game["broadcasts"])

    pick_team = first if random.random() < 0.6 else second
    reasons = [
        "viene en racha positiva",
        "juega de local",
        "mejor forma reciente",
        "favorito en momios",
        "historico a favor",
        "defensa solida ultimos juegos",
    ]
    reason = random.choice(reasons)

    lines = [
        f"PICK DEL DIA",
        "",
        f"{first} vs {second}",
        f"{league} - {time_str} (hora Mexico)",
        f"Donde verlo: {channels}",
        "",
        f"Pick: {pick_team}",
        f"({reason})",
        "",
        f"Horarios y canales: {APP_URL}",
        f"Picks gratis por WhatsApp: {WA_PICKS_LINK}",
        "",
        "Solo entretenimiento. +18",
    ]
    return "\n".join(lines)


# ── Scheduler Functions ─────────────────────────────────

async def post_daily_facebook():
    """Post the daily game schedule to Facebook. Called by scheduler."""
    if not fb_configured():
        logger.warning("Facebook not configured — skipping daily post")
        return None

    message = await compose_daily_post()
    if not message:
        logger.info("No games today — skipping Facebook post")
        return None

    result = await fb_post(message, link=APP_URL)
    if result["success"]:
        logger.info(f"Daily Facebook post published: {result['post_id']}")
    return result


async def post_pick_facebook():
    """Post Pick del Dia to Facebook. Called by scheduler."""
    if not fb_configured():
        return None

    games = await get_todays_games()
    priority = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga"]
    upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]

    pick = None
    for pl in priority:
        pick = next((g for g in upcoming if g.get("league_slug") == pl), None)
        if pick:
            break
    if not pick and upcoming:
        pick = upcoming[0]

    if not pick:
        logger.info("No pick available for Facebook")
        return None

    message = await compose_pick_post(pick)
    result = await fb_post(message)
    if result["success"]:
        logger.info(f"Pick del dia Facebook post: {result['post_id']}")
    return result


# ── Scheduler Setup ─────────────────────────────────────

def setup_facebook_scheduler(scheduler):
    """
    Add Facebook bot jobs to APScheduler.
    Call this from server.py on startup.
    """
    from apscheduler.triggers.cron import CronTrigger

    if not fb_configured():
        logger.warning("Facebook credentials not configured — scheduler NOT started")
        return

    # Daily post at 9:30 AM MX (15:30 UTC) — after WhatsApp broadcast
    scheduler.add_job(
        post_daily_facebook,
        CronTrigger(hour=15, minute=30),
        id="facebook_daily_post",
        name="Daily Facebook game schedule",
        replace_existing=True,
    )

    # Pick del dia at 11:00 AM MX (17:00 UTC)
    scheduler.add_job(
        post_pick_facebook,
        CronTrigger(hour=17, minute=0),
        id="facebook_pick_post",
        name="Facebook pick del dia",
        replace_existing=True,
    )

    logger.info("Facebook bot scheduler configured (daily post + pick del dia)")
