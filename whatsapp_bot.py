"""
WhatsApp bot handler for DondeVer.
Receives messages via Twilio webhook, searches for games,
and responds with where to watch + affiliate links.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from config import AFFILIATES, APP_URL, LEAGUES, TZ_MX, get_affiliate_url, get_short_affiliate_url, TEAM_ALIASES, HOME_LEFT_SPORTS
from sports_api import search_games, get_todays_games
from subscribers import subscribe, unsubscribe, update_last_active
from whatsapp_alerts import add_favorite_team, remove_favorite_team, get_favorites_list

logger = logging.getLogger("dondever.whatsapp")


def format_broadcast_text(broadcasts: list[dict]) -> str:
    """Format broadcast channels for WhatsApp message.
    Prioritize Mexican/Latin American channels over US-only ones."""
    if not broadcasts:
        return "Canal por confirmar"

    # Channels relevant to LATAM audience (prioritized)
    mx_priority = {"ESPN MX", "TUDN", "Fox Sports MX", "Canal 5", "Azteca 7",
                   "Azteca Deportes", "ViX", "ViX+", "Claro Sports", "Fox Deportes",
                   "Caliente TV", "TV Azteca", "Televisa", "Star+", "SKY Sports",
                   "ESPN Deportes", "Paramount+", "Apple TV+", "Amazon Prime Video",
                   "TNT Sports", "HBO Max", "Max", "DAZN", "YouTube"}

    prioritized = []
    others = []
    for b in broadcasts:
        ch = b["channel"]
        if ch in mx_priority:
            prioritized.append(ch)
        else:
            others.append(ch)

    # Show MX channels first, then up to 2 others
    result = prioritized + others[:2]
    if not result:
        result = [b["channel"] for b in broadcasts[:3]]

    return ", ".join(result[:4])


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
    sport = game.get("sport", "")
    time_str = format_game_time(game["date"])
    channels = format_broadcast_text(game["broadcasts"])
    status = game["status"]

    # Soccer: Local vs Visitante. US sports: Away @ Home
    home_left = sport in HOME_LEFT_SPORTS
    first = home if home_left else away
    second = away if home_left else home
    first_score = game["home"]["score"] if home_left else game["away"]["score"]
    second_score = game["away"]["score"] if home_left else game["home"]["score"]

    if status["state"] == "in":
        score_line = f"EN VIVO {first} {first_score} - {second_score} {second}"
    elif status["state"] == "post":
        score_line = f"FINAL: {first} {first_score} - {second_score} {second}"
    else:
        score_line = f"{first} vs {second}"

    lines = [
        f"{emoji} *{league}*",
        f"{score_line}",
    ]
    if time_str and status["state"] == "pre":
        lines.append(f"Hora: {time_str} (hora centro)")
    lines.append(f"Donde verlo: {channels}")

    return "\n".join(lines)


def get_random_affiliate(betting_only: bool = False) -> dict:
    """Pick an affiliate to show (rotate between them) with WhatsApp tracking.
    betting_only=True excludes VPN/non-betting affiliates (for picks, game results).
    """
    if betting_only:
        betting_keys = [k for k in AFFILIATES if k in ("strendus", "betsson")]
        key = random.choice(betting_keys) if betting_keys else random.choice(list(AFFILIATES.keys()))
    else:
        key = random.choice(list(AFFILIATES.keys()))
    aff = AFFILIATES[key].copy()
    aff["url"] = get_short_affiliate_url(key, source="whatsapp")
    return aff


# ── Pick logic ───────────────────────────────────────────

_PICK_REASONS_HOME = [
    "juega en casa y llega mejor parado",
    "de local viene sólido",
    "la localía pesa en este duelo",
    "favorito en casa según momios",
    "el factor casa marca la diferencia",
]
_PICK_REASONS_AWAY = [
    "viene caliente de visita",
    "mejor forma reciente",
    "en racha ganadora",
    "favorito según momios de apertura",
    "llega con plantilla completa",
]


def _compute_pick(game: dict) -> tuple[str, str]:
    """
    Elige ganador sugerido + razón corta.
    Heurística: 65% local (home advantage). Razón aleatoria del pool.
    """
    home = game["home"]["name"]
    away = game["away"]["name"]
    # Seed estable por game_id para que el mismo juego dé siempre el mismo pick en el día
    gid = str(game.get("id", home + away))
    rng = random.Random(gid + datetime.now(TZ_MX).strftime("%Y%m%d"))

    if rng.random() < 0.65:
        return home, rng.choice(_PICK_REASONS_HOME)
    return away, rng.choice(_PICK_REASONS_AWAY)


def _compute_extra_market(game: dict) -> str:
    """Sugerencia de mercado extra (Over/Under, BTTS, Hándicap) según deporte."""
    sport = game.get("sport", "")
    gid = str(game.get("id", ""))
    rng = random.Random(gid + "extra" + datetime.now(TZ_MX).strftime("%Y%m%d"))

    if sport == "soccer":
        opciones = [
            "Más de 2.5 goles",
            "Menos de 3.5 goles",
            "Ambos equipos anotan (BTTS) — Sí",
            "Primer tiempo con gol",
            "Tarjetas: más de 4.5",
        ]
    elif sport == "basketball":
        opciones = [
            "Más de 220.5 puntos totales",
            "Menos de 225.5 puntos totales",
            "Primer cuarto: favorito gana",
            "Doble-doble del jugador estrella",
        ]
    elif sport == "football":
        opciones = [
            "Más de 44.5 puntos totales",
            "Menos de 47.5 puntos totales",
            "Favorito cubre el spread",
            "Primer TD antes del minuto 10",
        ]
    elif sport == "baseball":
        opciones = [
            "Más de 8.5 carreras totales",
            "Menos de 7.5 carreras totales",
            "Se anota en la 1a entrada",
            "Favorito gana primeras 5 entradas",
        ]
    elif sport == "hockey":
        opciones = [
            "Más de 5.5 goles totales",
            "Menos de 6.5 goles totales",
            "Ambos equipos anotan",
        ]
    else:
        opciones = ["Favorito gana", "Partido cerrado"]

    return rng.choice(opciones)


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
        # Strip common prefixes like "donde ver chivas" -> "chivas"
        for prefix in ("donde ver ", "donde puedo ver ", "como ver ", "en donde ver "):
            if body_clean.startswith(prefix):
                body_clean = body_clean[len(prefix):]
                break
    except Exception:
        body_clean = ""

    # Track activity for all users
    update_last_active(from_number)

    # Unsubscribe
    if body_clean in ("salir", "stop", "parar", "cancelar", "baja", "unsub"):
        unsubscribe(from_number)
        return (
            "Listo, ya no recibiras picks diarios.\n\n"
            "Si cambias de opinion, escribe *suscribir* para volver a recibir.\n\n"
            f"Siempre puedes consultar juegos en {APP_URL}"
        )

    # Subscribe (keywords explícitas + button reply "btn_suscribir")
    if body_clean in (
        "suscribir", "suscribirme", "suscribirse",
        "suscripcion", "suscripción", "inscribir", "inscribirme",
        "subscribe", "subscribirse", "alta", "diario",
        "quiero picks", "quiero suscribirme", "quiero recibir picks",
        "picks diarios", "recibir picks", "btn_suscribir",
    ):
        is_new = subscribe(from_number)
        if is_new:
            # Send today's picks via Meta Cloud API
            try:
                from send_whatsapp_daily import compose_daily_message
                import meta_whatsapp
                picks_msg = await compose_daily_message()
                if picks_msg and meta_whatsapp.is_configured():
                    result = meta_whatsapp.send_text(from_number, picks_msg)
                    logger.info(f"Welcome picks sent via Meta to {from_number}: {result.get('ok')}")
            except Exception as e:
                logger.exception(f"Failed to send welcome picks: {e}")
            return (
                "Te suscribiste a *picks diarios* de DondeVer!\n\n"
                "Cada manana recibiras:\n"
                "- Pick del dia\n"
                "- Los mejores juegos\n"
                "- Donde verlos\n\n"
                "Te acabo de mandar los picks de *hoy* de regalo.\n\n"
                "Escribe *salir* para cancelar cuando quieras."
            )
        return (
            "Ya estas suscrito a picks diarios!\n\n"
            "Cada manana recibes el pick del dia.\n"
            "Escribe *salir* si quieres cancelar."
        )

    # Favorite team alerts: "alerta chivas", "alerta lakers"
    if body_clean.startswith(("alerta ", "alertar ", "seguir ", "favorito ")):
        team = body_clean.split(" ", 1)[1] if " " in body_clean else ""
        if team:
            subscribe(from_number)  # auto-subscribe
            return add_favorite_team(from_number, team)
        return "Escribe *alerta* seguido del equipo. Ejemplo: *alerta chivas*"

    # Remove favorite: "quitar chivas"
    if body_clean.startswith(("quitar ", "borrar ", "eliminar ")):
        team = body_clean.split(" ", 1)[1] if " " in body_clean else ""
        if team:
            return remove_favorite_team(from_number, team)
        return "Escribe *quitar* seguido del equipo. Ejemplo: *quitar chivas*"

    # List favorites: "mis equipos"
    if body_clean in ("mis equipos", "favoritos", "equipos", "mis alertas", "alertas"):
        return get_favorites_list(from_number)

    # Help / Hola — returns special marker so webhook sends buttons
    if body_clean in ("ayuda", "help", "hola", "hi", "menu", "inicio"):
        return "__BUTTONS_WELCOME__"

    # Picks del dia (auto-subscribe anyone who asks for picks) + button reply
    if body_clean in ("picks", "pick", "pick del dia", "sugerencia", "tip", "btn_picks"):
        subscribe(from_number)  # silent auto-subscribe
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
        aff = get_random_affiliate(betting_only=True)
        pick_sport = pick.get("sport", "")
        pick_home_left = pick_sport in HOME_LEFT_SPORTS
        pick_first = pick["home"]["name"] if pick_home_left else pick["away"]["name"]
        pick_second = pick["away"]["name"] if pick_home_left else pick["home"]["name"]

        # Ganador sugerido (pick real) + razón
        pick_team, pick_reason = _compute_pick(pick)
        # Mercado extra (Over/Under, BTTS, etc.)
        extra_market = _compute_extra_market(pick)

        return (
            f"🎯 *PICK DEL DIA*\n\n"
            f"{pick.get('emoji', '')} *{pick['league_name']}*\n"
            f"{pick_first} vs {pick_second}\n"
            f"🕐 {time_str} (MX)\n"
            f"📺 {channels}\n\n"
            f"✅ *Ganador sugerido:* {pick_team}\n"
            f"_{pick_reason}_\n\n"
            f"💡 *Mercado extra:* {extra_market}\n\n"
            f"Escribe *combinada* para un parlay de 3 picks 🔥\n\n"
            f"{aff['cta']}: {aff['url']}\n\n"
            f"_Sugerencias de entretenimiento. Apuesta responsable. +18_"
        )

    # Combinada / parlay: 2-3 picks de distintos juegos
    if body_clean in ("combinada", "parlay", "combo", "acumulada", "multiple"):
        subscribe(from_number)
        games = await get_todays_games()
        upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]
        if len(upcoming) < 2:
            return f"No hay suficientes juegos hoy para una combinada. Checa {APP_URL}"

        # Priorizar ligas top
        priority = ["liga-mx", "champions", "premier-league", "la-liga", "nfl", "nba", "mlb"]
        def score(g):
            slug = g.get("league_slug", "")
            return priority.index(slug) if slug in priority else 99
        upcoming.sort(key=score)
        combo_games = upcoming[:3] if len(upcoming) >= 3 else upcoming[:2]

        aff = get_random_affiliate(betting_only=True)
        lines = ["🔥 *COMBINADA DEL DIA*\n"]
        for i, g in enumerate(combo_games, 1):
            team, reason = _compute_pick(g)
            sport = g.get("sport", "")
            home_left = sport in HOME_LEFT_SPORTS
            first = g["home"]["name"] if home_left else g["away"]["name"]
            second = g["away"]["name"] if home_left else g["home"]["name"]
            time_s = format_game_time(g["date"])
            lines.append(
                f"{i}) {g.get('emoji', '')} {first} vs {second}\n"
                f"   ✅ {team} — _{reason}_\n"
                f"   🕐 {time_s} MX"
            )
        lines.append(f"\n⚠️ A mayor número de selecciones, mayor riesgo.")
        lines.append(f"\n{aff['cta']}: {aff['url']}\n")
        lines.append("_Solo entretenimiento. Apuesta responsable. +18_")
        return "\n".join(lines)

    try:
        # Today's overview — short version to avoid WhatsApp rejecting long messages
        if body_clean in ("hoy", "juegos", "games", "today", "que hay hoy", "partidos", "btn_hoy"):
            games = await get_todays_games()
            if not games:
                return "No encontre juegos programados para hoy. Intenta manana!"

            # Prioritize upcoming games with broadcasts first, then live, then finals
            def _priority(g):
                state = g.get("status", {}).get("state", "")
                has_broadcast = bool(g.get("broadcasts"))
                # Lower is better (sorts first)
                if state == "pre" and has_broadcast:
                    return 0
                if state == "in":
                    return 1
                if state == "pre":
                    return 2
                return 3  # post/final

            sorted_games = sorted(games, key=_priority)

            # Max 6 games to stay under ~900 bytes (WhatsApp business API is finicky with long messages)
            MAX_GAMES = 6
            top = sorted_games[:MAX_GAMES]

            lines = ["*Juegos de hoy:*\n"]
            for game in top:
                lines.append(format_game_for_whatsapp(game))
                lines.append("")

            remaining = len(games) - len(top)
            if remaining > 0:
                lines.append(f"_...y {remaining} juegos mas_")
            lines.append("")
            lines.append("Ver todos los juegos, canales y horarios:")
            lines.append("dondever.app")

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
        aff = get_random_affiliate(betting_only=True)
        lines.append(f"\n{aff['cta']}: {aff['url']}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error handling WhatsApp message '{body}': {e}")
        return (
            "Ups, hubo un error buscando los juegos.\n"
            f"Intenta de nuevo o visita {APP_URL}"
        )
