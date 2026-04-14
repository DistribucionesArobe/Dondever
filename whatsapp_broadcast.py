"""
WhatsApp broadcast for DondeVer.
Sends daily picks + betting links to all subscribers via Twilio.
"""

import logging
import random
from twilio.rest import Client as TwilioClient
from config import (
    TWILIO_SID, TWILIO_TOKEN, TWILIO_WA_NUMBER,
    AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS,
    get_affiliate_url,
)
from sports_api import get_todays_games
from subscribers import get_active_subscribers, get_subscriber_count
from datetime import datetime

logger = logging.getLogger("dondever.broadcast")


def get_twilio_client() -> TwilioClient | None:
    """Create Twilio client. Returns None if credentials missing.
    Read env at call time to avoid stale config if set after import."""
    import os as _os
    sid = TWILIO_SID or _os.getenv("TWILIO_ACCOUNT_SID", "") or _os.getenv("TWILIO_SID", "")
    token = TWILIO_TOKEN or _os.getenv("TWILIO_AUTH_TOKEN", "") or _os.getenv("TWILIO_TOKEN", "")
    if not sid or not token:
        logger.error(
            f"Twilio credentials not configured: sid_set={bool(sid)} token_set={bool(token)} "
            f"env_keys={[k for k in _os.environ if 'TWILIO' in k.upper()]}"
        )
        return None
    return TwilioClient(sid, token)


def format_broadcast_channels(broadcasts: list[dict]) -> str:
    """Format channels for broadcast message."""
    if not broadcasts:
        return "Por confirmar"
    channels = [b["channel"] for b in broadcasts[:4]]
    return ", ".join(channels)


def get_betting_link() -> dict:
    """Get random betting affiliate with WhatsApp tracking."""
    betting_keys = [k for k in AFFILIATES if k in ("caliente", "1xbet", "betsson")]
    if not betting_keys:
        return {"name": "", "cta": "", "url": ""}
    key = random.choice(betting_keys)
    aff = AFFILIATES[key].copy()
    aff["url"] = get_affiliate_url(key, source="whatsapp")
    return aff


async def compose_daily_broadcast() -> str:
    """
    Compose the daily broadcast message with:
    - Pick del dia (best game)
    - Top 3-5 games of the day
    - Betting link
    """
    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    date_display = now.strftime("%d/%m")

    if not games:
        return None

    # Pick del dia
    priority = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga", "mlb"]
    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    pick = None
    for pl in priority:
        pick = next((g for g in upcoming if g["league_slug"] == pl), None)
        if pick:
            break
    if not pick and upcoming:
        pick = upcoming[0]

    # Get team order
    def team_order(game):
        sport = game.get("sport", "")
        if sport in HOME_LEFT_SPORTS:
            return game["home"]["name"], game["away"]["name"]
        return game["away"]["name"], game["home"]["name"]

    # Format time
    def fmt_time(date_str):
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            mx = dt.astimezone(TZ_MX)
            return mx.strftime("%I:%M %p")
        except Exception:
            return ""

    # Build message
    lines = [f"*DONDE VER HOY {date_display}*\n"]

    # Pick del dia section
    if pick:
        first, second = team_order(pick)
        channels = format_broadcast_channels(pick["broadcasts"])
        time_str = fmt_time(pick["date"])
        lines.append(f"*PICK DEL DIA*")
        lines.append(f"{pick.get('emoji', '')} {first} vs {second}")
        lines.append(f"{pick['league_name']} - {time_str}")
        lines.append(f"Donde verlo: {channels}\n")

    # Top games (up to 5, excluding pick)
    pick_id = pick["id"] if pick else None
    top_games = [g for g in upcoming if g["id"] != pick_id][:5]

    if top_games:
        lines.append("*MAS JUEGOS HOY*")
        for g in top_games:
            first, second = team_order(g)
            time_str = fmt_time(g["date"])
            channels = format_broadcast_channels(g["broadcasts"])
            lines.append(f"{g.get('emoji', '')} {first} vs {second} - {time_str}")
            if g["broadcasts"]:
                lines.append(f"   {channels}")
        lines.append("")

    # Betting CTA
    aff = get_betting_link()
    if aff["url"]:
        lines.append(f"{aff['cta']}: {aff['url']}\n")

    # Site link
    lines.append(f"Todos los juegos: {APP_URL}")
    lines.append(f"\n_Escribe *salir* para dejar de recibir._")
    lines.append(f"_Solo entretenimiento. +18_")

    return "\n".join(lines)


async def send_daily_broadcast():
    """
    Send the daily picks broadcast to all active subscribers.
    Called by scheduler every morning.
    """
    subscribers = get_active_subscribers()
    count = len(subscribers)

    if count == 0:
        logger.info("No subscribers for broadcast")
        return {"sent": 0, "failed": 0}

    message_text = await compose_daily_broadcast()
    if not message_text:
        logger.info("No games today — skipping broadcast")
        return {"sent": 0, "failed": 0}

    client = get_twilio_client()
    if not client:
        return {"sent": 0, "failed": count, "error": "Twilio not configured"}

    sent = 0
    failed = 0
    from_number = TWILIO_WA_NUMBER

    for phone in subscribers:
        try:
            # Ensure phone has whatsapp: prefix
            to_number = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"

            client.messages.create(
                body=message_text,
                from_=from_number,
                to=to_number,
            )
            sent += 1
            logger.info(f"Broadcast sent to {to_number}")
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed for {phone}: {e}")

    logger.info(f"Broadcast complete: {sent} sent, {failed} failed out of {count}")
    return {"sent": sent, "failed": failed, "total": count}
