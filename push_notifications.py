"""
Push Notifications via OneSignal — Web push alerts for game reminders.

Sends notifications to all subscribers:
- Pre-game: "América vs Chivas empieza en 15 min — Canal 5, TUDN"
- Daily summary: "Hoy 12 partidos: Liga MX, NFL, NBA"

Requires env vars:
  ONESIGNAL_APP_ID    — from OneSignal dashboard
  ONESIGNAL_API_KEY   — REST API key from OneSignal dashboard
"""

import os
import logging
import httpx
from datetime import datetime, timedelta
from config import TZ_MX, APP_URL

logger = logging.getLogger("dondever.push")

ONESIGNAL_APP_ID = os.getenv("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY = os.getenv("ONESIGNAL_API_KEY", "")
ONESIGNAL_API_URL = "https://api.onesignal.com/notifications"


async def send_push(
    heading: str,
    message: str,
    url: str = "",
    icon: str = "",
    segments: list[str] | None = None,
) -> dict:
    """
    Send a web push notification to all subscribed users (or specific segments).

    Args:
        heading: Notification title (e.g., "América vs Chivas en 15 min")
        message: Body text (e.g., "Canal 5, TUDN — 8:00 PM MX")
        url: URL to open when clicked (e.g., game page)
        icon: URL of the notification icon
        segments: OneSignal segments (default: ["Subscribed Users"])
    """
    if not ONESIGNAL_APP_ID or not ONESIGNAL_API_KEY:
        logger.warning("OneSignal not configured — skipping push notification")
        return {"error": "OneSignal not configured"}

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "included_segments": segments or ["Subscribed Users"],
        "headings": {"es": heading, "en": heading},
        "contents": {"es": message, "en": message},
        "url": url or APP_URL,
        "chrome_web_icon": icon or f"{APP_URL}/static/icon-192.png",
        "ttl": 3600,  # expire after 1 hour
    }

    headers = {
        "Authorization": f"Basic {ONESIGNAL_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(ONESIGNAL_API_URL, json=payload, headers=headers)
            result = resp.json()
            if resp.status_code == 200:
                logger.info(f"Push sent: '{heading}' — recipients: {result.get('recipients', 0)}")
            else:
                logger.warning(f"Push failed ({resp.status_code}): {result}")
            return result
    except Exception as e:
        logger.error(f"Push notification error: {e}")
        return {"error": str(e)}


async def send_pregame_push(game: dict) -> dict:
    """
    Send a pre-game push notification for a specific game.

    Args:
        game: Game dict with keys: home, away, sport, league_name, date, broadcasts
    """
    home_left = game.get("sport") in ("soccer", "boxing", "mma")
    team1 = game["home"]["name"] if home_left else game["away"]["name"]
    team2 = game["away"]["name"] if home_left else game["home"]["name"]
    emoji = game.get("emoji", "⚽")

    # Build channel list
    channels = []
    if game.get("broadcasts"):
        for b in game["broadcasts"][:3]:
            channels.append(b["channel"])
    channel_str = ", ".join(channels) if channels else "Ver canales"

    # Format time
    from config import TZ_MX
    game_dt = game.get("date_obj") or datetime.fromisoformat(game["date"])
    if game_dt.tzinfo is None:
        from datetime import timezone
        game_dt = game_dt.replace(tzinfo=timezone.utc)
    mx_time = game_dt.astimezone(TZ_MX).strftime("%-I:%M %p")

    heading = f"{emoji} {team1} vs {team2} — ¡Ya casi!"
    message = f"Empieza en 15 min · {channel_str} · {mx_time} MX"

    event_id = game.get("id", "")
    url = f"{APP_URL}/juego/{event_id}" if event_id else APP_URL

    return await send_push(heading, message, url=url)


async def send_daily_push_summary(games: list[dict]) -> dict:
    """
    Send daily summary push: "Hoy 12 partidos: Liga MX, NFL, NBA"
    """
    if not games:
        return {"skipped": "no games today"}

    total = len(games)
    leagues = list(dict.fromkeys(g.get("league_name", "") for g in games))[:4]
    league_str = ", ".join(leagues)

    heading = f"🏟️ Hoy {total} partidos en vivo"
    message = f"{league_str} — Ver horarios y canales"

    return await send_push(heading, message, url=APP_URL)


async def check_and_send_pregame_pushes(all_games: list[dict]) -> list[dict]:
    """
    Check all games and send push for those starting in ~15 minutes.
    Called by scheduler every 5 minutes.

    Returns list of notifications sent.
    """
    now = datetime.now(TZ_MX)
    sent = []

    for game in all_games:
        if game.get("status", {}).get("state") != "pre":
            continue

        try:
            game_dt = datetime.fromisoformat(game["date"])
            if game_dt.tzinfo is None:
                from datetime import timezone
                game_dt = game_dt.replace(tzinfo=timezone.utc)
            game_mx = game_dt.astimezone(TZ_MX)

            # Send if game starts in 12-17 minutes (window to catch with 5-min cron)
            diff = (game_mx - now).total_seconds() / 60
            if 12 <= diff <= 17:
                result = await send_pregame_push(game)
                sent.append({
                    "game": f"{game.get('home', {}).get('name')} vs {game.get('away', {}).get('name')}",
                    "starts_in_min": round(diff),
                    "result": result,
                })
        except Exception as e:
            logger.warning(f"Pre-game push check error: {e}")
            continue

    if sent:
        logger.info(f"Sent {len(sent)} pre-game push notifications")
    return sent
