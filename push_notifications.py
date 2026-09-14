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
    subscription_ids: list[str] | None = None,
    ttl: int = 3600,
    web_badge: str = "",
    collapse_id: str = "",
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
        "headings": {"es": heading, "en": heading},
        "contents": {"es": message, "en": message},
        "url": url or APP_URL,
        "chrome_web_icon": icon or f"{APP_URL}/static/logo.png",
        "ttl": ttl,
    }
    if subscription_ids:
        payload["include_subscription_ids"] = list(subscription_ids)[:2000]
    else:
        payload["included_segments"] = segments or ["Subscribed Users"]
    if web_badge:
        payload["chrome_web_badge"] = web_badge
    if collapse_id:
        payload["collapse_id"] = collapse_id

    headers = {
        "Authorization": f"Key {ONESIGNAL_API_KEY}",
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
    url = game.get("_url") or (f"{APP_URL}/juego/{event_id}" if event_id else APP_URL)

    targets = game.get("_targets")
    if targets is not None and not targets:
        return {"skipped": "sin seguidores"}
    return await send_push(heading, message, url=url, subscription_ids=targets, ttl=1200,
                           collapse_id=f"pre-{event_id}")


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


_pre_sent: set = set()


async def check_and_send_pregame_pushes(all_games: list[dict], targets_fn=None, url_fn=None) -> list[dict]:
    """
    Check all games and send push for those starting in ~15 minutes.
    Called by scheduler every 5 minutes.
    `targets_fn(game) -> list[sub_id]` limita el aviso a quienes siguen a uno de los dos
    equipos o pusieron 🔔 al partido (antes se mandaba a TODOS por cada partido).

    Returns list of notifications sent.
    """
    now = datetime.now(TZ_MX)
    sent = []

    for game in all_games:
        if game.get("status", {}).get("state") != "pre":
            continue
        if game.get("id") in _pre_sent:
            continue
        if targets_fn:
            game["_targets"] = targets_fn(game)
            if not game["_targets"]:
                continue
        if url_fn:
            game["_url"] = url_fn(game)

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
                _pre_sent.add(game.get("id"))
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


# ── Alertas en vivo: inicio, gol/anotación, final (solo a seguidores) ──────────
# Estado por partido entre corridas del watcher (cada 60 s). Se resetea al reiniciar;
# en la primera corrida solo se "aprende" el estado, no se notifica.
_live_state: dict[str, dict] = {}
_live_sent: set = set()


def _score_int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0


def _short(name: str) -> str:
    return (name or "").split(" ")[-1] if len(name or "") > 18 else (name or "")


def _score_line(game: dict) -> str:
    h, a = game["home"], game["away"]
    if game.get("sport") in ("soccer", "boxing", "mma"):
        return f"{_short(h['name'])} {h['score']}-{a['score']} {_short(a['name'])}"
    return f"{_short(a['name'])} {a['score']}-{h['score']} {_short(h['name'])}"


def _scoring_headline(game: dict, side: str, diff: int) -> str | None:
    """Encabezado por deporte para un cambio de marcador. None → no avisar (NBA, etc.)."""
    team = game[side]["name"]
    sport = game.get("sport", "")
    lv = game.get("live") or {}
    if sport == "soccer":
        who = ""
        for g in reversed(lv.get("goals") or []):
            if g.get("side") == side:
                who = f" {g.get('minute', '')} {g.get('who', '')}{g.get('tag', '')}".rstrip()
                break
        return f"⚽ ¡GOL de {team}!{who}"
    if sport in ("football", "college-football"):
        if diff >= 6:
            return f"🏈 ¡Touchdown {team}!"
        if diff == 3:
            return f"🏈 Gol de campo de {team}"
        if diff == 2:
            return f"🏈 Safety a favor de {team}"
        return f"🏈 Anotación de {team}"
    if sport == "baseball":
        return f"⚾ {team} anota{' ' + str(diff) if diff > 1 else ''}"
    if sport == "hockey":
        return f"🏒 ¡Gol de {team}!"
    return None  # basketball: demasiadas anotaciones


async def check_live_pushes(all_games: list[dict], targets_fn, url_fn=None) -> list[dict]:
    """Compara con la corrida anterior y manda push a seguidores en: inicio, anotación, final."""
    sent = []
    seen_ids = set()
    for game in all_games:
        gid = str(game.get("id") or "")
        if not gid or game.get("home", {}).get("name") == "TBD":
            continue
        seen_ids.add(gid)
        state = game.get("status", {}).get("state", "pre")
        hs, as_ = _score_int(game["home"].get("score")), _score_int(game["away"].get("score"))
        prev = _live_state.get(gid)
        _live_state[gid] = {"state": state, "hs": hs, "as": as_}
        if prev is None:
            continue  # primera vez que lo vemos: aprender, no avisar
        events = []  # (key, heading, message, ttl)
        line = _score_line(game)
        if game.get("sport") == "soccer":
            ctx = game.get("status", {}).get("clock", "") or ""
        else:
            ctx = (game.get("live") or {}).get("situation_text") or game.get("status", {}).get("detail", "")
        if prev["state"] == "pre" and state == "in":
            chans = ", ".join(b.get("channel", "") for b in (game.get("broadcasts") or [])[:2]) or "ver canales"
            events.append(("start", f"{game.get('emoji', '')} ¡Empezó {_short(game['home']['name'])} vs {_short(game['away']['name'])}!".strip(),
                           f"{game.get('league_name', '')} · {chans}", 1800))
        if state == "in" and (hs != prev["hs"] or as_ != prev["as"]):
            for side, new, old in (("home", hs, prev["hs"]), ("away", as_, prev["as"])):
                if new > old:
                    head = _scoring_headline(game, side, new - old)
                    if head:
                        events.append((f"score-{hs}-{as_}", head, f"{line}{(' · ' + ctx) if ctx else ''}", 900))
        if prev["state"] == "in" and state == "post":
            events.append(("final", f"{game.get('emoji', '')} Final: {line}".strip(),
                           f"{game.get('league_name', '')} · resumen y próximos partidos en DondeVer", 3600))
        if not events:
            continue
        targets = targets_fn(game)
        if not targets:
            continue
        url = url_fn(game) if url_fn else APP_URL
        for key, heading, message, ttl in events:
            dedupe = f"{gid}:{key}"
            if dedupe in _live_sent:
                continue
            _live_sent.add(dedupe)
            result = await send_push(heading, message, url=url, subscription_ids=targets, ttl=ttl,
                                     collapse_id=f"live-{gid}")
            sent.append({"game": line, "event": key, "targets": len(targets), "result": result})
    # olvidar partidos que ya no están en el día
    for gid in [g for g in _live_state if g not in seen_ids]:
        _live_state.pop(gid, None)
    if len(_live_sent) > 5000:
        _live_sent.clear()
    return sent
