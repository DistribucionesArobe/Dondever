"""
WhatsApp real-time alerts for DondeVer.
- Pre-game alerts: 1 hour before kickoff for subscriber's favorite teams
- Goal/event alerts: instant notification when score changes

Subscribers set favorite teams via WhatsApp: "alerta chivas" / "alerta lakers"
"""

import logging
import random
from datetime import datetime, timedelta
from twilio.rest import Client as TwilioClient
from config import (
    TWILIO_SID, TWILIO_TOKEN, TWILIO_WA_NUMBER,
    AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS, TEAM_ALIASES,
    get_affiliate_url, get_short_affiliate_url,
)
from sports_api import get_todays_games
from subscribers import _load, _save, get_active_subscribers

logger = logging.getLogger("dondever.alerts")

# Track which alerts have already been sent to avoid duplicates
_sent_pregame_alerts = set()  # (phone, game_id)
_sent_goal_alerts = set()     # (phone, game_id, score_key)


def get_twilio_client() -> TwilioClient | None:
    if not TWILIO_SID or not TWILIO_TOKEN:
        return None
    return TwilioClient(TWILIO_SID, TWILIO_TOKEN)


def send_whatsapp(client, to: str, body: str):
    """Send a single WhatsApp message."""
    to_number = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    try:
        client.messages.create(
            body=body,
            from_=TWILIO_WA_NUMBER,
            to=to_number,
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to send alert to {to}: {e}")
        return False


# ── Favorite Teams Management ───────────────────────────

def add_favorite_team(phone: str, team: str) -> str:
    """Add a team to subscriber's favorites. Returns confirmation message."""
    phone = phone.strip()
    team_clean = team.strip().lower()

    # Resolve alias
    resolved = TEAM_ALIASES.get(team_clean, team_clean)

    data = _load()
    if phone not in data["subscribers"]:
        data["subscribers"][phone] = {
            "subscribed_at": datetime.now(TZ_MX).isoformat(),
            "last_active": datetime.now(TZ_MX).isoformat(),
            "active": True,
            "favorites": [],
        }

    favs = data["subscribers"][phone].get("favorites", [])
    if resolved not in favs:
        favs.append(resolved)
        data["subscribers"][phone]["favorites"] = favs
        _save(data)
        return (
            f"Listo! Te avisare 1 hora antes de cada juego de *{resolved.title()}* "
            f"y cuando anoten gol.\n\n"
            f"Tus equipos: {', '.join(f.title() for f in favs)}\n\n"
            f"Escribe *alerta [equipo]* para agregar mas.\n"
            f"Escribe *mis equipos* para ver tu lista."
        )
    return f"Ya tienes a *{resolved.title()}* en tus favoritos.\n\nTus equipos: {', '.join(f.title() for f in favs)}"


def remove_favorite_team(phone: str, team: str) -> str:
    """Remove a team from subscriber's favorites."""
    phone = phone.strip()
    team_clean = team.strip().lower()
    resolved = TEAM_ALIASES.get(team_clean, team_clean)

    data = _load()
    if phone not in data["subscribers"]:
        return "No tienes equipos favoritos. Escribe *alerta [equipo]* para agregar."

    favs = data["subscribers"][phone].get("favorites", [])
    if resolved in favs:
        favs.remove(resolved)
        data["subscribers"][phone]["favorites"] = favs
        _save(data)
        if favs:
            return f"Listo, ya no recibiras alertas de *{resolved.title()}*.\n\nTus equipos: {', '.join(f.title() for f in favs)}"
        return f"Listo, ya no recibiras alertas de *{resolved.title()}*. No tienes equipos favoritos."
    return f"*{resolved.title()}* no esta en tus favoritos."


def get_favorites_list(phone: str) -> str:
    """Get subscriber's favorite teams list."""
    phone = phone.strip()
    data = _load()
    if phone not in data["subscribers"]:
        return "No tienes equipos favoritos.\n\nEscribe *alerta chivas* o *alerta lakers* para agregar."

    favs = data["subscribers"][phone].get("favorites", [])
    if not favs:
        return "No tienes equipos favoritos.\n\nEscribe *alerta chivas* o *alerta lakers* para agregar."

    lines = ["*Tus equipos favoritos:*\n"]
    for f in favs:
        lines.append(f"- {f.title()}")
    lines.append(f"\nRecibiras alertas 1h antes del partido y cuando anoten.")
    lines.append(f"\nEscribe *quitar [equipo]* para eliminar.")
    return "\n".join(lines)


def get_subscriber_favorites(phone: str) -> list[str]:
    """Get list of favorite team names for a subscriber."""
    data = _load()
    if phone in data["subscribers"]:
        return data["subscribers"][phone].get("favorites", [])
    return []


# ── Pre-Game Alerts ─────────────────────────────────────

def _team_matches_favorites(game: dict, favorites: list[str]) -> bool:
    """Check if any team in a game matches subscriber's favorites."""
    home = game["home"]["name"].lower()
    away = game["away"]["name"].lower()
    for fav in favorites:
        fav_lower = fav.lower()
        if fav_lower in home or home in fav_lower:
            return True
        if fav_lower in away or away in fav_lower:
            return True
    return False


def _get_betting_text() -> str:
    betting_keys = [k for k in AFFILIATES if k in ("caliente", "betsson")]
    if not betting_keys:
        return ""
    key = random.choice(betting_keys)
    aff = AFFILIATES[key]
    url = get_short_affiliate_url(key, source="whatsapp")
    return f"\n{aff['cta']}: {url}"


def _format_time_mx(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


def _team_order(game: dict) -> tuple:
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


async def send_pregame_alerts():
    """
    Check all upcoming games. If a game starts within 55-65 minutes
    and a subscriber has one of the teams as favorite, send alert.
    Called every 5 minutes by scheduler.
    """
    global _sent_pregame_alerts

    client = get_twilio_client()
    if not client:
        return {"sent": 0}

    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    sent_count = 0

    # Find games starting in ~1 hour (55-65 min from now)
    upcoming = []
    for game in games:
        if game["status"]["state"] != "pre":
            continue
        try:
            kickoff = datetime.fromisoformat(game["date"].replace("Z", "+00:00")).astimezone(TZ_MX)
            diff = (kickoff - now).total_seconds() / 60  # minutes
            if 30 <= diff <= 65:
                upcoming.append(game)
        except Exception:
            continue

    if not upcoming:
        return {"sent": 0}

    # Check each subscriber's favorites against upcoming games
    data = _load()
    for phone, info in data["subscribers"].items():
        if not info.get("active", True):
            continue
        favorites = info.get("favorites", [])
        if not favorites:
            continue

        for game in upcoming:
            alert_key = (phone, game["id"])
            if alert_key in _sent_pregame_alerts:
                continue

            if _team_matches_favorites(game, favorites):
                first, second = _team_order(game)
                time_str = _format_time_mx(game["date"])
                channels = ", ".join(b["channel"] for b in game.get("broadcasts", [])[:3]) or "Por confirmar"
                emoji = game.get("emoji", "")
                betting = _get_betting_text()

                msg = (
                    f"*EMPIEZA EN 1 HORA* {emoji}\n\n"
                    f"{first} vs {second}\n"
                    f"{game['league_name']} - {time_str}\n"
                    f"Donde verlo: {channels}"
                    f"{betting}\n\n"
                    f"{APP_URL}"
                )

                if send_whatsapp(client, phone, msg):
                    _sent_pregame_alerts.add(alert_key)
                    sent_count += 1
                    logger.info(f"Pre-game alert sent to {phone} for {game['name']}")

    # Cleanup old alert keys
    current_ids = {g["id"] for g in games}
    _sent_pregame_alerts = {(p, gid) for p, gid in _sent_pregame_alerts if gid in current_ids}

    if sent_count:
        logger.info(f"Pre-game alerts: {sent_count} sent")
    return {"sent": sent_count}


# ── Goal/Event Alerts ───────────────────────────────────

async def send_goal_alerts(game: dict, event_type: str):
    """
    Send WhatsApp alert to subscribers whose favorite team just scored.
    Called from the live monitor in twitter_bot.py when a score change is detected.

    event_type: 'goal', 'score_change', 'started', 'final'
    """
    global _sent_goal_alerts

    client = get_twilio_client()
    if not client:
        return 0

    first, second = _team_order(game)
    sport = game.get("sport", "")
    home_left = sport in HOME_LEFT_SPORTS
    first_score = game["home"]["score"] if home_left else game["away"]["score"]
    second_score = game["away"]["score"] if home_left else game["home"]["score"]
    emoji = game.get("emoji", "")
    channels = ", ".join(b["channel"] for b in game.get("broadcasts", [])[:3]) or ""

    # Build alert message based on event type
    if event_type == "goal":
        header = f"*GOOOL!* {emoji}"
        score_line = f"{first} *{first_score}* - *{second_score}* {second}"
    elif event_type == "score_change":
        header = f"*ANOTACION!* {emoji}"
        score_line = f"{first} *{first_score}* - *{second_score}* {second}"
    elif event_type == "started":
        header = f"*YA EMPEZO!* {emoji}"
        score_line = f"{first} vs {second}"
    elif event_type == "final":
        header = f"*FINAL!* {emoji}"
        score_line = f"{first} *{first_score}* - *{second_score}* {second}"
    else:
        return 0

    betting = _get_betting_text()
    msg = f"{header}\n\n{score_line}\n{game['league_name']}"
    if channels and event_type in ("started", "goal", "score_change"):
        msg += f"\nDonde verlo: {channels}"
    msg += f"{betting}"

    # Score key to prevent duplicate alerts
    score_key = f"{first_score}-{second_score}-{event_type}"

    sent_count = 0
    data = _load()

    for phone, info in data["subscribers"].items():
        if not info.get("active", True):
            continue
        favorites = info.get("favorites", [])
        if not favorites:
            continue

        alert_key = (phone, game["id"], score_key)
        if alert_key in _sent_goal_alerts:
            continue

        if _team_matches_favorites(game, favorites):
            if send_whatsapp(client, phone, msg):
                _sent_goal_alerts.add(alert_key)
                sent_count += 1

    # Cleanup old keys
    if len(_sent_goal_alerts) > 500:
        _sent_goal_alerts.clear()

    if sent_count:
        logger.info(f"Goal alerts: {sent_count} sent for {game['name']} ({event_type})")
    return sent_count
