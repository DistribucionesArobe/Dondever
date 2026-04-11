"""
WhatsApp bot handler for DondeVer.
Receives messages via Twilio webhook, searches for games,
and responds with where to watch + affiliate links.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from config import AFFILIATES, APP_URL, LEAGUES, TZ_MX, get_affiliate_url
from sports_api import search_games, get_todays_games

logger = logging.getLogger("dondever.whatsapp")


def format_broadcast_text(broadcasts: list[dict]) -> str:
    """Format broadcast channels for WhatsApp message."""
    if not broadcasts:
        return "Canal por confirmar"
    channels = []
    for b in broadcasts:
        ch = b["channel"]
        market = b.get("market", "")
        if market and market != "National":
            ch = f"{ch} ({market})"
        channels.append(ch)
    return ", ".join(channels)


def format_game_time(date_str: str) -> str:
    """Convert ISO date to readable MX time."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx_time = dt.astimezone(TZ_MX)
        return mx_time.strftime("%I:%M %p")
    except Exception:
        return ""


def format_game_for_whatsapp(game: dict) -> str:
    """Format a single game for WhatsApp response."""
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    home = game["home"]["name"]
    away = game["away"]["name"]
    time_str = format_game_time(game["date"])
    channels = format_broadcast_text(game["broadcasts"])
    status = game["status"]

    if status["state"] == "in":
        score_line = f"EN VIVO {away} {game['away']['score']} - {game['home']['score']} {home}"
    elif status["state"] == "post":
        score_line = f"FINAL: {away} {game['away']['score']} - {game['home']['score']} {home}"
    else:
        score_line = f"{away} vs {home}"

    lines = [
        f"{emoji} *{league}*",
        f"{score_line}",
    ]
    if time_str and status["state"] == "pre":
        lines.append(f"Hora: {time_str} (hora centro)")
    lines.append(f"Donde verlo: {channels}")

    return "\n".join(lines)


def get_random_affiliate() -> dict:
    """Pick an affiliate to show (rotate between them) with WhatsApp tracking."""
    key = random.choice(list(AFFILIATES.keys()))
    aff = AFFILIATES[key].copy()
    aff["url"] = get_affiliate_url(key, source="whatsapp")
    return aff


async def handle_whatsapp_message(body: str, from_number: str) -> str:
    """
    Process incoming WhatsApp message and return response text.

    Supported queries:
    - "hoy" / "juegos" -> show all today's games
    - team name -> search for that team's games
    - league name -> show that league's games
    - "ayuda" / "help" -> show help message
    """
    try:
        body_clean = body.strip().lower()
    except Exception:
        body_clean = ""

    # Help
    if body_clean in ("ayuda", "help", "hola", "hi", "menu", "inicio"):
        return (
            "Hola! Soy *DondeVer* - te digo donde ver los juegos de hoy.\n\n"
            "Escribe:\n"
            "- *hoy* - todos los juegos de hoy\n"
            "- *picks* - pick del dia\n"
            "- *nfl* o *liga mx* - juegos por liga\n"
            "- *America* o *Cowboys* - buscar por equipo\n"
            "- *futbol* o *basket* - buscar por deporte\n\n"
            f"O visita {APP_URL} para la guia completa"
        )

    # Picks del dia
    if body_clean in ("picks", "pick", "pick del dia", "sugerencia", "tip"):
        games = await get_todays_games()
        priority = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga"]
        upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]
        pick = None
        for pl in priority:
            pick = next((g for g in upcoming if g["league_slug"] == pl), None)
            if pick:
                break
        if not pick and upcoming:
            pick = upcoming[0]

        if not pick:
            return f"No hay picks disponibles ahorita. Checa los juegos de hoy en {APP_URL}"

        channels = format_broadcast_text(pick["broadcasts"])
        time_str = format_game_time(pick["date"])
        aff = get_random_affiliate()

        return (
            f"*PICK DEL DIA*\n\n"
            f"{pick.get('emoji', '')} *{pick['league_name']}*\n"
            f"{pick['away']['name']} vs {pick['home']['name']}\n"
            f"Hora: {time_str} (hora centro)\n"
            f"Donde verlo: {channels}\n\n"
            f"Escribe *picks* diario para recibir sugerencias.\n\n"
            f"{aff['cta']}: {aff['url']}\n\n"
            f"_Las sugerencias son solo entretenimiento. Apuesta responsablemente. +18_"
        )

    try:
        # Today's overview (limit to games with broadcasts)
        if body_clean in ("hoy", "juegos", "games", "today", "que hay hoy", "partidos"):
            games = await get_todays_games()
            if not games:
                return "No encontre juegos programados para hoy. Intenta manana!"

            # Group by sport, show first 15 max
            lines = ["*Juegos de hoy:*\n"]
            shown = 0
            for game in games[:15]:
                lines.append(format_game_for_whatsapp(game))
                lines.append("")  # blank line separator
                shown += 1

            remaining = len(games) - shown
            if remaining > 0:
                lines.append(f"...y {remaining} juegos mas.")
            lines.append(f"\nVe todos en {APP_URL}")

            # Add affiliate
            aff = get_random_affiliate()
            lines.append(f"\n{aff['cta']}: {aff['url']}")

            return "\n".join(lines)

        # Search by query
        games = await search_games(body_clean)

        # Also try matching league slugs and sport names
        if not games:
            # Try matching by sport
            sport_map = {
                "futbol": "soccer", "football": "soccer", "soccer": "soccer",
                "basket": "basketball", "basquetbol": "basketball", "nba": "basketball",
                "americano": "football", "nfl": "football",
                "beisbol": "baseball", "baseball": "baseball", "mlb": "baseball",
                "hockey": "hockey", "nhl": "hockey",
                "box": "boxing", "boxeo": "boxing",
                "ufc": "mma", "mma": "mma",
                "f1": "racing", "formula": "racing", "nascar": "racing",
                "tenis": "tennis", "tennis": "tennis",
                "golf": "golf",
            }
            sport = sport_map.get(body_clean)
            if sport:
                games = await get_todays_games(sport_filter=sport)

        # Try league slug match
        if not games:
            for slug in LEAGUES:
                if body_clean in slug or slug in body_clean:
                    games = await get_todays_games(league_filter=slug)
                    break

        if not games:
            return (
                f"No encontre juegos para *{body.strip()}* hoy.\n\n"
                "Intenta con:\n"
                "- Nombre de equipo (America, Cowboys, Lakers)\n"
                "- Nombre de liga (Liga MX, NFL, NBA)\n"
                "- *hoy* para ver todos los juegos\n\n"
                f"O visita {APP_URL}"
            )

        lines = [f"*Resultados para '{body.strip()}':*\n"]
        for game in games[:10]:
            lines.append(format_game_for_whatsapp(game))
            lines.append("")

        if len(games) > 10:
            lines.append(f"...y {len(games) - 10} juegos mas en {APP_URL}")

        # Affiliate link
        aff = get_random_affiliate()
        lines.append(f"\n{aff['cta']}: {aff['url']}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error handling WhatsApp message '{body}': {e}")
        return (
            "Ups, hubo un error buscando los juegos.\n"
            f"Intenta de nuevo o visita {APP_URL}"
        )
