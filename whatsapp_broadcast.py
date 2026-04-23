"""
WhatsApp broadcast for DondeVer.
Sends daily picks + betting links to all subscribers via Twilio.

NOTE on WhatsApp 24-hour window:
  WhatsApp only allows freeform messages within 24h of the user's last message.
  Outside that window, you MUST use a pre-approved Content Template.
  Set TWILIO_CONTENT_SID env var with an approved template SID to enable
  broadcasts outside the 24h window. Without it, only users who messaged
  recently will receive the broadcast.
"""

import logging
import os
import random
from twilio.rest import Client as TwilioClient
from config import (
    TWILIO_SID, TWILIO_TOKEN, TWILIO_WA_NUMBER,
    AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS,
    get_affiliate_url, get_short_affiliate_url,
)
from sports_api import get_todays_games
from subscribers import get_active_subscribers, get_subscriber_count
from datetime import datetime

logger = logging.getLogger("dondever.broadcast")

# Optional: Twilio Content Template SID for messages outside 24h window
# Create one at https://console.twilio.com/content-editor
CONTENT_SID = os.getenv("TWILIO_CONTENT_SID", "")


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


def _ensure_wa_number(phone: str) -> str:
    """Ensure phone has whatsapp:+XXX format, stripping spaces and adding prefix."""
    phone = phone.strip()
    if phone.startswith("whatsapp:"):
        phone = phone[9:].strip()  # remove prefix to re-normalize
    if not phone.startswith("+"):
        phone = f"+{phone}"
    return f"whatsapp:{phone}"


def format_broadcast_channels(broadcasts: list[dict]) -> str:
    """Format channels for broadcast message."""
    if not broadcasts:
        return "Por confirmar"
    channels = [b["channel"] for b in broadcasts[:4]]
    return ", ".join(channels)


def get_betting_link() -> dict:
    """Get random betting affiliate with WhatsApp tracking."""
    betting_keys = [k for k in AFFILIATES if k in ("caliente", "betsson")]
    if not betting_keys:
        return {"name": "", "cta": "", "url": ""}
    key = random.choice(betting_keys)
    aff = AFFILIATES[key].copy()
    aff["url"] = get_short_affiliate_url(key, source="whatsapp")
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

    # Build message — WhatsApp template rules:
    # - Max 10 emojis total
    # - No more than 2 consecutive newlines
    # - Must have some fixed text (not only variables)
    lines = [f"*DONDE VER HOY {date_display}*"]
    emojis_used = 0
    max_emojis = 8  # leave room for template wrapper

    # Pick del dia section
    if pick:
        from whatsapp_bot import _compute_pick, _compute_extra_market
        first, second = team_order(pick)
        channels = format_broadcast_channels(pick["broadcasts"])
        time_str = fmt_time(pick["date"])
        pick_team, pick_reason = _compute_pick(pick)
        extra = _compute_extra_market(pick)
        lines.append("")
        lines.append("*PICK DEL DIA*")
        lines.append(f"{first} vs {second}")
        lines.append(f"{pick['league_name']} - {time_str} MX")
        lines.append(f"TV: {channels}")
        lines.append(f"Ganador: *{pick_team}* - _{pick_reason}_")
        lines.append(f"Mercado extra: {extra}")

    # Top games (up to 5, excluding pick)
    pick_id = pick["id"] if pick else None
    top_games = [g for g in upcoming if g["id"] != pick_id][:5]

    if top_games:
        lines.append("")
        lines.append("*MAS JUEGOS HOY*")
        for g in top_games:
            first, second = team_order(g)
            time_str = fmt_time(g["date"])
            channels = format_broadcast_channels(g["broadcasts"])
            lines.append(f"{first} vs {second} - {time_str}")
            if g["broadcasts"]:
                lines.append(f"  {channels}")

    # Betting CTA
    aff = get_betting_link()
    if aff["url"]:
        lines.append("")
        lines.append(f"{aff['cta']}: {aff['url']}")

    # Site link
    lines.append("")
    lines.append(f"Todos los juegos: {APP_URL}")
    lines.append("_Escribe salir para dejar de recibir._")
    lines.append("_Solo entretenimiento. +18_")

    return "\n".join(lines)


async def send_daily_broadcast():
    """
    Send the daily picks broadcast to all active subscribers.
    Called by scheduler every morning.

    Strategy:
    1. If TWILIO_CONTENT_SID is set → use template (works outside 24h window)
    2. Otherwise → try freeform message (only works within 24h of user's last msg)
    3. Log detailed errors so we can diagnose delivery failures
    """
    subscribers = get_active_subscribers()
    count = len(subscribers)

    logger.info(f"Broadcast starting: {count} subscriber(s), content_sid={'set' if CONTENT_SID else 'NOT set'}")

    if count == 0:
        logger.info("No subscribers for broadcast")
        return {"sent": 0, "failed": 0}

    message_text = await compose_daily_broadcast()
    if not message_text:
        logger.info("No games today — skipping broadcast")
        return {"sent": 0, "failed": 0}

    client = get_twilio_client()
    if not client:
        logger.error("Twilio client creation failed — broadcast aborted")
        return {"sent": 0, "failed": count, "error": "Twilio not configured"}

    sent = 0
    failed = 0
    errors = []
    from_number = TWILIO_WA_NUMBER

    for phone in subscribers:
        to_number = _ensure_wa_number(phone)
        try:
            if CONTENT_SID:
                # Use pre-approved template (works outside 24h window)
                import json as _json
                msg = client.messages.create(
                    content_sid=CONTENT_SID,
                    content_variables=_json.dumps({"1": message_text}),
                    from_=from_number,
                    to=to_number,
                )
            else:
                # Freeform message (only works within 24h session window)
                msg = client.messages.create(
                    body=message_text,
                    from_=from_number,
                    to=to_number,
                )
            sent += 1
            logger.info(f"Broadcast sent to {to_number} — SID: {msg.sid}, status: {msg.status}")
        except Exception as e:
            failed += 1
            error_detail = str(e)
            errors.append({"phone": phone, "error": error_detail})
            # Log the full Twilio error for diagnosis
            logger.error(
                f"Broadcast FAILED for {to_number}: {error_detail} "
                f"(hint: if error 63016/63032, user is outside 24h window — need Content Template)"
            )

    result = {"sent": sent, "failed": failed, "total": count, "errors": errors}
    logger.info(f"Broadcast complete: {sent} sent, {failed} failed out of {count}")
    if errors:
        logger.warning(f"Broadcast errors detail: {errors}")
    return result
