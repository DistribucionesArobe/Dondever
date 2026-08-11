"""
Daily WhatsApp broadcast for DondeVer — via Meta Cloud API.
Sends games + statistical betting tips + Betsson link.

Replaces the old Twilio-based whatsapp_broadcast.py.

Env vars:
  WHATSAPP_ACCESS_TOKEN     — System User permanent token from Meta Business
  WHATSAPP_PHONE_NUMBER_ID  — Phone Number ID (from Meta Developer console)

Usage:
  python send_whatsapp_daily.py              # send to all subscribers
  python send_whatsapp_daily.py --preview    # print message, don't send
  python send_whatsapp_daily.py --test 5218341751234   # send to one number
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

from config import (
    AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS,
    get_short_affiliate_url,
)
from sports_api import (
    get_todays_games, fetch_odds, match_odds_to_game,
    fetch_standings, ODDS_SPORT_MAP,
)
from subscribers import get_active_subscribers
from meta_whatsapp import send_text, send_template, is_configured

APP_URL = os.getenv("APP_URL", "https://dondever.app")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("dondever.wa_daily")

# ── League → ESPN sport/league mapping for standings lookup ──
LEAGUE_ESPN_MAP = {
    "liga-mx": ("soccer", "mex.1"),
    "premier-league": ("soccer", "eng.1"),
    "la-liga": ("soccer", "esp.1"),
    "serie-a": ("soccer", "ita.1"),
    "bundesliga": ("soccer", "ger.1"),
    "ligue-1": ("soccer", "fra.1"),
    "champions": ("soccer", "uefa.champions"),
    "mls": ("soccer", "usa.1"),
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "liga-mx-femenil": ("soccer", "mex.w.1"),
}

# Priority order for picking featured game
LEAGUE_PRIORITY = [
    "liga-mx", "premier-league", "champions", "nfl", "nba",
    "la-liga", "mlb", "serie-a", "bundesliga", "mls",
]


# ── Statistical Tips Engine ──────────────────────────────────

def _team_order(game: dict) -> tuple[str, str]:
    """Return (first_team, second_team) based on sport display convention."""
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


def _fmt_time(date_str: str) -> str:
    """Convert ISO date to MX time string."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def _format_channels(broadcasts: list[dict]) -> str:
    """Pick top 3 channels for display."""
    if not broadcasts:
        return "Por confirmar"
    channels = [b["channel"] for b in broadcasts[:3]]
    return ", ".join(channels)


def _find_team_in_standings(team_name: str, standings: list[dict]) -> dict | None:
    """Fuzzy-find a team in standings data."""
    name_lower = team_name.lower()
    words = [w for w in name_lower.split() if len(w) > 3]
    for entry in standings:
        entry_name = entry["team_name"].lower()
        entry_short = entry["team_short"].lower()
        if (name_lower in entry_name or entry_name in name_lower or
                any(w in entry_name for w in words) or
                name_lower in entry_short or entry_short in name_lower):
            return entry
    return None


async def _generate_stat_tip(game: dict, odds_data: dict | None, standings: list[dict]) -> dict:
    """
    Generate a statistical betting tip for a game.

    Returns dict with:
      pick: str — team name recommended
      confidence: str — "Alta", "Media", "Baja"
      reason: str — data-backed reason
      extra_market: str — over/under or other market tip
      odds_display: str — moneyline odds display
    """
    home = game["home"]["name"]
    away = game["away"]["name"]
    sport = game.get("sport", "")

    # 1. Find both teams in standings
    home_stats = _find_team_in_standings(home, standings) if standings else None
    away_stats = _find_team_in_standings(away, standings) if standings else None

    pick = home  # default to home
    confidence = "Media"
    reasons = []

    # 2. Analyze win records
    if home_stats and away_stats:
        h_wins = _to_int(home_stats.get("wins", "0"))
        h_losses = _to_int(home_stats.get("losses", "0"))
        a_wins = _to_int(away_stats.get("wins", "0"))
        a_losses = _to_int(away_stats.get("losses", "0"))

        h_pct = h_wins / max(h_wins + h_losses, 1)
        a_pct = a_wins / max(a_wins + a_losses, 1)

        h_record = f"{h_wins}G-{h_losses}P"
        a_record = f"{a_wins}G-{a_losses}P"

        # Streak info
        h_streak = home_stats.get("streak", "")
        a_streak = away_stats.get("streak", "")

        if h_pct > a_pct + 0.15:
            pick = home
            confidence = "Alta"
            reasons.append(f"Récord {h_record} vs {a_record}")
        elif a_pct > h_pct + 0.15:
            pick = away
            confidence = "Alta"
            reasons.append(f"Récord {a_record} vs {h_record}")
        elif h_pct > a_pct:
            pick = home
            reasons.append(f"Récord ligeramente mejor ({h_record})")
        else:
            pick = away
            reasons.append(f"Récord ligeramente mejor ({a_record})")

        # Streak bonus
        if h_streak and ("W" in str(h_streak).upper() or "G" in str(h_streak).upper()):
            streak_num = "".join(c for c in str(h_streak) if c.isdigit())
            if streak_num and int(streak_num) >= 3:
                if pick == home:
                    confidence = "Alta"
                reasons.append(f"{home} racha de {streak_num} victorias")

        if a_streak and ("W" in str(a_streak).upper() or "G" in str(a_streak).upper()):
            streak_num = "".join(c for c in str(a_streak) if c.isdigit())
            if streak_num and int(streak_num) >= 3:
                if pick == away:
                    confidence = "Alta"
                reasons.append(f"{away} racha de {streak_num} victorias")

        # Goal difference / point differential
        h_diff = _to_int(home_stats.get("goal_diff", "0"))
        a_diff = _to_int(away_stats.get("goal_diff", "0"))
        if abs(h_diff - a_diff) > 10:
            better = home if h_diff > a_diff else away
            reasons.append(f"{better} mejor diferencial ({'+' if max(h_diff, a_diff) > 0 else ''}{max(h_diff, a_diff)})")

    # 3. Check odds for favorite
    odds_display = ""
    if odds_data:
        h_odds = odds_data.get("home_odds", "")
        a_odds = odds_data.get("away_odds", "")
        d_odds = odds_data.get("draw_odds", "")

        if h_odds and a_odds:
            odds_display = f"{home} {h_odds} | {away} {a_odds}"
            if d_odds:
                odds_display += f" | Empate {d_odds}"

            # Use odds to refine pick (negative = favorite)
            h_num = _odds_to_number(h_odds)
            a_num = _odds_to_number(a_odds)
            if h_num and a_num:
                if h_num < a_num - 50:
                    if pick != home:
                        reasons.append(f"Favorito por momios ({h_odds})")
                    pick = home
                elif a_num < h_num - 50:
                    if pick != away:
                        reasons.append(f"Favorito por momios ({a_odds})")
                    pick = away

    # 4. Home advantage (if no other strong signal)
    if not reasons:
        pick = home
        reasons.append("Factor local a su favor")

    # 5. Generate extra market tip
    extra_market = _generate_extra_market(game, home_stats, away_stats, sport)

    # Cap reasons to 2 most important
    reason_str = " | ".join(reasons[:2])

    return {
        "pick": pick,
        "confidence": confidence,
        "reason": reason_str,
        "extra_market": extra_market,
        "odds_display": odds_display,
    }


def _generate_extra_market(game: dict, home_stats: dict | None, away_stats: dict | None, sport: str) -> str:
    """Generate over/under or other market tip based on stats."""
    # Calculate average goals/points for data-driven over/under
    if home_stats and away_stats:
        h_gf = _to_float(home_stats.get("goals_for", "0"))
        h_ga = _to_float(home_stats.get("goals_against", "0"))
        a_gf = _to_float(away_stats.get("goals_for", "0"))
        a_ga = _to_float(away_stats.get("goals_against", "0"))
        h_gp = max(_to_int(home_stats.get("games_played", "1")), 1)
        a_gp = max(_to_int(away_stats.get("games_played", "1")), 1)

        if sport == "soccer":
            h_avg = (h_gf + h_ga) / h_gp  # avg goals per game for home team
            a_avg = (a_gf + a_ga) / a_gp
            combined_avg = (h_avg + a_avg) / 2

            if combined_avg > 2.8:
                return f"Más de 2.5 goles (promedio combinado: {combined_avg:.1f})"
            elif combined_avg < 2.0:
                return f"Menos de 2.5 goles (promedio combinado: {combined_avg:.1f})"
            else:
                # Check if both teams score regularly
                h_avg_gf = h_gf / h_gp
                a_avg_gf = a_gf / a_gp
                if h_avg_gf > 1.0 and a_avg_gf > 1.0:
                    return "Ambos equipos anotan (BTTS) — promedian 1+ gol por juego"
                return "Más de 1.5 goles"

        elif sport == "baseball":
            h_avg = (h_gf + h_ga) / h_gp
            a_avg = (a_gf + a_ga) / a_gp
            combined = (h_avg + a_avg) / 2
            if combined > 9.0:
                return f"Más de 8.5 carreras (promedio: {combined:.1f})"
            else:
                return f"Menos de 8.5 carreras (promedio: {combined:.1f})"

        elif sport == "basketball":
            h_avg = (h_gf + h_ga) / h_gp
            a_avg = (a_gf + a_ga) / a_gp
            combined = (h_avg + a_avg) / 2
            if combined > 225:
                return f"Más de 220.5 pts (promedio: {combined:.0f})"
            else:
                return f"Menos de 220.5 pts (promedio: {combined:.0f})"

        elif sport == "football":
            h_avg = (h_gf + h_ga) / h_gp
            a_avg = (a_gf + a_ga) / a_gp
            combined = (h_avg + a_avg) / 2
            if combined > 48:
                return f"Más de 45.5 pts (promedio: {combined:.0f})"
            else:
                return f"Menos de 45.5 pts (promedio: {combined:.0f})"

    # Fallback generic tips by sport
    tips = {
        "soccer": "Más de 2.5 goles",
        "baseball": "Más de 8.5 carreras",
        "basketball": "Más de 220.5 puntos",
        "football": "Más de 44.5 puntos",
        "hockey": "Más de 5.5 goles",
    }
    return tips.get(sport, "Favorito gana")


def _to_int(val) -> int:
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _odds_to_number(odds_str: str) -> int | None:
    """Convert American odds string to number for comparison."""
    try:
        return int(str(odds_str).replace("+", ""))
    except (ValueError, TypeError):
        return None


# ── Message Composer ─────────────────────────────────────────

async def compose_daily_message() -> str | None:
    """
    Compose the daily WhatsApp VER response — clean and scannable.
    Top 10 games with one-line picks + casino link + page link.
    """
    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    date_display = now.strftime("%d/%m/%Y")
    weekday = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][now.weekday()]

    if not games:
        return None

    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    if not upcoming:
        return None

    # Fetch odds + standings for all relevant leagues
    leagues_in_play = set(g.get("league_slug", "") for g in upcoming)
    odds_by_league = {}
    for league_slug in leagues_in_play:
        if league_slug in ODDS_SPORT_MAP:
            odds_by_league[league_slug] = await fetch_odds(league_slug)

    standings_by_league = {}
    for league_slug in leagues_in_play:
        espn_info = LEAGUE_ESPN_MAP.get(league_slug)
        if espn_info:
            sport, league_id = espn_info
            try:
                standings_by_league[league_slug] = await fetch_standings(sport, league_id)
            except Exception:
                standings_by_league[league_slug] = []

    # Select top 10 games by priority
    top_games = []
    for pl in LEAGUE_PRIORITY:
        for g in upcoming:
            if g.get("league_slug") == pl and g not in top_games:
                top_games.append(g)
                if len(top_games) >= 10:
                    break
        if len(top_games) >= 10:
            break
    # Fill remaining from any league
    for g in upcoming:
        if g not in top_games:
            top_games.append(g)
            if len(top_games) >= 10:
                break

    # Build message — compact, one game per block
    lines = []
    lines.append(f"*🏆 TOP 10 PICKS — {weekday} {date_display}*")
    lines.append("")

    for i, g in enumerate(top_games, 1):
        f1, f2 = _team_order(g)
        t = _fmt_time(g["date"])
        ch = _format_channels(g["broadcasts"])
        league_slug = g.get("league_slug", "")
        league_name = g.get("league_name", "")

        g_odds_list = odds_by_league.get(league_slug, [])
        g_odds = match_odds_to_game(g, g_odds_list) if g_odds_list else None
        g_standings = standings_by_league.get(league_slug, [])
        g_tip = await _generate_stat_tip(g, g_odds, g_standings)

        # Compact format: number + teams + time + channel + tip
        lines.append(f"*{i}. {f1} vs {f2}*")
        lines.append(f"⏰ {t} · 📺 {ch} · {league_name}")
        lines.append(f"👉 *{g_tip['pick']}* ({g_tip['confidence']}) — {g_tip['extra_market']}")
        lines.append("")

    remaining = len(upcoming) - len(top_games)
    if remaining > 0:
        lines.append(f"_...y {remaining} juegos más en dondever.app_")
        lines.append("")

    # ── Casino CTA ──
    jubilee_url = get_short_affiliate_url("jubilee", source="whatsapp")
    lines.append(f"💰 Ver cuotas → {jubilee_url}")
    lines.append("")

    # ── Footer ──
    lines.append("📱 Todos los juegos y canales:")
    lines.append("dondever.app")
    lines.append("")
    lines.append("_+18 · Apuesta responsable · Escribe SALIR para cancelar_")

    return "\n".join(lines)


# ── Template Variables Composer ───────────────────────────────

async def compose_template_variables() -> dict | None:
    """
    Compose the single body variable for the approved WhatsApp template.
    Returns {"var1": str} or None if no games.

    Template (dondever_picks_diarios / Utility / English):
      Tu resumen diario de tu cuenta DondeVer esta listo.
      {{1}}
      Consulta mas detalles en tu cuenta.
    """
    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    date_display = now.strftime("%d/%m/%Y")
    weekday = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][now.weekday()]

    if not games:
        return None

    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    if not upcoming:
        return None

    # Fetch odds and standings
    leagues_in_play = set(g.get("league_slug", "") for g in upcoming)
    odds_by_league = {}
    for league_slug in leagues_in_play:
        if league_slug in ODDS_SPORT_MAP:
            odds_by_league[league_slug] = await fetch_odds(league_slug)

    standings_by_league = {}
    for league_slug in leagues_in_play:
        espn_info = LEAGUE_ESPN_MAP.get(league_slug)
        if espn_info:
            sport, league_id = espn_info
            try:
                standings_by_league[league_slug] = await fetch_standings(sport, league_id)
            except Exception:
                standings_by_league[league_slug] = []

    # Pick the featured game
    pick = None
    for pl in LEAGUE_PRIORITY:
        pick = next((g for g in upcoming if g.get("league_slug") == pl), None)
        if pick:
            break
    if not pick:
        pick = upcoming[0]

    pick_league = pick.get("league_slug", "")
    pick_odds_list = odds_by_league.get(pick_league, [])
    pick_odds = match_odds_to_game(pick, pick_odds_list) if pick_odds_list else None
    pick_standings = standings_by_league.get(pick_league, [])
    pick_tip = await _generate_stat_tip(pick, pick_odds, pick_standings)

    # ── Build single combined variable for {{1}} ──
    # NOTE: WhatsApp template params CANNOT contain newlines, tabs,
    # or 4+ consecutive spaces (error 132018). Use " | " as separator.
    parts = []

    first, second = _team_order(pick)
    time_str = _fmt_time(pick["date"])
    channels = _format_channels(pick["broadcasts"])

    # Header
    parts.append(f"{weekday} {date_display} — {len(upcoming)} juegos")

    # Featured pick
    pick_part = f"⭐ PICK: {first} vs {second} · {pick.get('league_name', '')} {time_str} MX · 📡 {channels}"
    if pick_tip["odds_display"]:
        pick_part += f" · 💰 {pick_tip['odds_display']}"
    pick_part += f" · 💡 {pick_tip['pick']} ({pick_tip['confidence']}) — {pick_tip['reason']}"
    parts.append(pick_part)

    # Other games
    other_games = [g for g in upcoming if g["id"] != pick["id"]]
    top_others = []
    for pl in LEAGUE_PRIORITY:
        for g in other_games:
            if g.get("league_slug") == pl and g not in top_others:
                top_others.append(g)
                if len(top_others) >= 3:
                    break
        if len(top_others) >= 3:
            break
    for g in other_games:
        if g not in top_others:
            top_others.append(g)
            if len(top_others) >= 3:
                break

    if top_others:
        game_strs = []
        for g in top_others:
            f1, f2 = _team_order(g)
            t = _fmt_time(g["date"])
            gl = g.get("league_slug", "")
            g_odds_list = odds_by_league.get(gl, [])
            g_odds = match_odds_to_game(g, g_odds_list) if g_odds_list else None
            g_standings = standings_by_league.get(gl, [])
            g_tip = await _generate_stat_tip(g, g_odds, g_standings)
            game_strs.append(f"{f1} vs {f2} {t} → {g_tip['pick']}")
        parts.append("⚾ Más: " + " | ".join(game_strs))

    remaining = len(upcoming) - 1 - len(top_others)
    if remaining > 0:
        parts.append(f"...y {remaining} más en dondever.app")

    # v3 template variables (3 params, newlines in template body itself)
    # Template dondever_daily_v3:
    #   Tu resumen DondeVer esta listo.
    #   📅 {{1}}
    #   🎯 PICK DEL DIA:
    #   {{2}}
    #   ⚾ Mas juegos:
    #   {{3}}
    #   Consulta dondever.app para horarios y canales.

    v3_pick = f"{first} vs {second} · {pick.get('league_name', '')} {time_str} MX · {channels}"
    if pick_tip["odds_display"]:
        v3_pick += f" · {pick_tip['odds_display']}"
    v3_pick += f" · Tip: {pick_tip['pick']} ({pick_tip['confidence']})"

    v3_games_parts = []
    if top_others:
        for g in top_others:
            f1, f2 = _team_order(g)
            t = _fmt_time(g["date"])
            gl = g.get("league_slug", "")
            g_odds_list = odds_by_league.get(gl, [])
            g_odds = match_odds_to_game(g, g_odds_list) if g_odds_list else None
            g_standings = standings_by_league.get(gl, [])
            g_tip = await _generate_stat_tip(g, g_odds, g_standings)
            v3_games_parts.append(f"{f1} vs {f2} {t} - {g_tip['pick']}")
    v3_games = " · ".join(v3_games_parts) if v3_games_parts else "Sin mas juegos hoy"
    if remaining > 0:
        v3_games += f" · +{remaining} mas en dondever.app"

    # Build single-param value for dondever_picks_diarios
    # SHORT teaser with PICK DEL DIA — full details sent as freeform when user replies VER.
    # Meta WhatsApp API rejects newlines/tabs in template param values (error 132018).
    channels_str = _format_channels(pick["broadcasts"])
    pick_emoji = "⚽" if pick.get("sport") == "soccer" else "⚾" if pick.get("sport") == "baseball" else "🏀" if pick.get("sport") == "basketball" else "🏈"
    var1 = (
        f"{pick_emoji} {first} vs {second} · {time_str} MX · "
        f"{pick_tip['pick']} ({pick_tip['confidence']}). "
        f"Responde VER para los 10 picks de hoy. "
        f"https://dondever.app/"
    )

    return {
        "var1": var1,
        "v3_header": f"{weekday} {date_display} - {len(upcoming)} juegos",
        "v3_pick": v3_pick,
        "v3_games": v3_games,
    }


# ── Sender ───────────────────────────────────────────────────

async def send_daily_broadcast(test_number: str | None = None):
    """
    Send daily WhatsApp broadcast via Meta Cloud API.
    Strategy: template first (works outside 24h window), freeform as last resort.
    Templates: dondever_picks_diarios (1 param, UTILITY) → freeform.
    WABA: Distribuciones Arobe (ID: 1224835083125902).
    """
    if not is_configured():
        logger.error("Meta WhatsApp not configured. Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID.")
        return {"sent": 0, "failed": 0, "error": "not_configured"}

    # Compose both messages: template (reliable for broadcast) and freeform (last resort)
    freeform_message = await compose_daily_message()
    template_vars = await compose_template_variables()

    if not template_vars and not freeform_message:
        logger.info("No games today — skipping broadcast")
        return {"sent": 0, "failed": 0, "skipped": "no_games"}

    # Build template components for dondever_picks_diarios (1 param, UTILITY)
    v1_components = None
    if template_vars:
        v1_components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": template_vars["var1"]},
                ],
            }
        ]

    # Determine recipients
    if test_number:
        recipients = [test_number]
        logger.info(f"Test mode: sending to {test_number}")
    else:
        # Try API first (for cron jobs on separate services)
        if INTERNAL_API_KEY:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        f"{APP_URL}/api/internal/whatsapp-subscribers",
                        params={"key": INTERNAL_API_KEY}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        recipients = data.get("subscribers", [])
                        logger.info(f"Fetched {len(recipients)} subscribers from API")
                    else:
                        logger.error(f"API returned {resp.status_code}, falling back to file")
                        recipients = get_active_subscribers()
            except Exception as e:
                logger.error(f"Failed to fetch from API: {e}, falling back to file")
                recipients = get_active_subscribers()
        else:
            recipients = get_active_subscribers()
        logger.info(f"Broadcast to {len(recipients)} subscribers")

    if not recipients:
        logger.info("No subscribers")
        return {"sent": 0, "failed": 0}

    # Deduplicate recipients by normalized phone number
    from meta_whatsapp import _normalize_to
    seen = set()
    unique_recipients = []
    for phone in recipients:
        norm = _normalize_to(phone)
        if norm not in seen:
            seen.add(norm)
            unique_recipients.append(phone)
        else:
            logger.warning(f"Skipping duplicate phone: {phone} (normalized: {norm})")
    recipients = unique_recipients
    logger.info(f"After dedup: {len(recipients)} unique recipients")

    sent = 0
    failed = 0
    errors = []

    for phone in recipients:
        # Strategy: v1 Utility template (1 param with rich formatting),
        # then freeform (24h window only).
        sent_ok = False

        # Primary: Utility template (1 param — nicely formatted with newlines + bold)
        if not sent_ok and v1_components:
            result = send_template(
                phone,
                template_name="dondever_picks_diarios",
                language="en",
                components=v1_components,
            )
            if result["ok"]:
                sent += 1
                sent_ok = True
                logger.info(f"Sent v1 template to {phone} — msg_id: {result['id']}")
            else:
                logger.info(f"v1 template failed for {phone}: {result.get('error')}, trying freeform")

        # Last resort: freeform (only works if user messaged within 24h)
        if not sent_ok and freeform_message:
            result = send_text(phone, freeform_message)
            if result["ok"]:
                sent += 1
                logger.info(f"Sent freeform to {phone} — msg_id: {result['id']}")
            else:
                failed += 1
                errors.append({"phone": phone, "error": result["error"]})
                logger.error(f"Failed all methods for {phone}: {result['error']}")

    summary = {"sent": sent, "failed": failed, "total": len(recipients), "errors": errors}
    logger.info(f"Broadcast complete: {sent} sent, {failed} failed")
    return summary


# ── CLI ──────────────────────────────────────────────────────

async def main():
    if "--preview" in sys.argv:
        msg = await compose_daily_message()
        if msg:
            print("\n" + "=" * 60)
            print("PREVIEW — Daily WhatsApp Message")
            print("=" * 60)
            print(msg)
            print("=" * 60)
            print(f"\nCharacter count: {len(msg)}")
        else:
            print("No games today.")
        return

    test_num = None
    if "--test" in sys.argv:
        idx = sys.argv.index("--test")
        if idx + 1 < len(sys.argv):
            test_num = sys.argv[idx + 1]
        else:
            print("Usage: python send_whatsapp_daily.py --test <phone_number>")
            return

    result = await send_daily_broadcast(test_number=test_num)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
