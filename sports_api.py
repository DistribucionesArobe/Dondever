"""
Sports data fetcher — pulls schedules from ESPN's public API
and enriches with TV broadcast data from TheSportsDB Premium.
"""
from __future__ import annotations

import httpx
import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from cachetools import TTLCache

from config import (
    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,
    LEAGUES, ALL_LEAGUES, CHANNEL_ALIASES, ESPN_CHANNEL_NORMALIZE, TZ_MX, TEAM_ALIASES,
    PAYING_BOOKMAKER_KEYS,
)

logger = logging.getLogger("dondever.sports")

# ── ESPN status description → Spanish translation ──
_STATUS_ES = {
    "Scheduled": "Programado",
    "In Progress": "En vivo",
    "Final": "Final",
    "Final/OT": "Final/Prórroga",
    "Final/SO": "Final/Shootout",
    "Postponed": "Pospuesto",
    "Cancelled": "Cancelado",
    "Suspended": "Suspendido",
    "Delayed": "Demorado",
    "Rain Delay": "Demora por lluvia",
    "Halftime": "Medio tiempo",
    "End of Period": "Fin del periodo",
    "Full Time": "Tiempo completo",
    "After Extra Time": "Después de prórroga",
    "After Penalties": "Después de penales",
    "Abandoned": "Abandonado",
    "1st Half": "1er Tiempo",
    "2nd Half": "2do Tiempo",
    "Pre-Game": "Por iniciar",
    "Warmup": "Calentamiento",
    "End of Regulation": "Fin del tiempo regular",
    # ESPN alterna entre "1st Half" y "First Half" según el deporte/competición
    "First Half": "1er Tiempo",
    "Second Half": "2do Tiempo",
    "End of 1st Half": "Fin del 1er tiempo",
    "End of 2nd Half": "Fin del 2do tiempo",
    "Extra Time": "Tiempo extra",
    "1st Extra Time": "1er tiempo extra",
    "2nd Extra Time": "2do tiempo extra",
    "Penalties": "Penales",
    "Shootout": "Tanda de penales",
    "1st Quarter": "1er cuarto",
    "2nd Quarter": "2do cuarto",
    "3rd Quarter": "3er cuarto",
    "4th Quarter": "4to cuarto",
    "Overtime": "Tiempo extra",
    "End of Game": "Fin del partido",
    "In Progress": "En juego",
    "Scheduled": "Programado",
}


def _translate_status(desc: str) -> str:
    """Translate ESPN status description to Spanish."""
    if desc in _STATUS_ES:
        return _STATUS_ES[desc]
    # Try case-insensitive match
    for en, es in _STATUS_ES.items():
        if en.lower() == desc.lower():
            return es
    return desc

def nfl_mx_channels(us_channels: list[str]) -> list[str]:
    """Infer Mexico channels for an NFL game from its US broadcasters.

    Rights in Mexico (temporada 2026): ESPN MX / Disney+ tienen MNF y SNF;
    Fox Sports MX tiene TNF y ventanas dominicales; NFL Game Pass (DAZN)
    transmite todos los juegos. Netflix: juegos navideños.
    """
    out: list[str] = []

    def add(*chs):
        for c in chs:
            if c not in out:
                out.append(c)

    joined = " ".join(c.lower() for c in us_channels)
    if "netflix" in joined:
        add("Netflix")
    if "prime" in joined or "amazon" in joined:           # Thursday Night Football
        add("Fox Sports MX")
    if "espn" in joined or "abc" in joined:               # Monday Night Football
        add("ESPN MX", "Disney+")
    if "nbc" in joined or "peacock" in joined:            # Sunday Night Football
        add("ESPN MX", "Disney+")
    if "cbs" in joined or "fox" in joined:                # Sunday afternoon windows
        add("Fox Sports MX")
    add("NFL Game Pass")                                  # todos los juegos
    return out


def _normalize_channel(raw: str) -> str:
    """Normalize ESPN's truncated channel names to canonical form."""
    raw = raw.strip()
    # Direct lookup in normalize map
    if raw in ESPN_CHANNEL_NORMALIZE:
        return ESPN_CHANNEL_NORMALIZE[raw]
    # Check if raw is a prefix of a known channel
    for truncated, canonical in ESPN_CHANNEL_NORMALIZE.items():
        if raw.lower() == truncated.lower():
            return canonical
    return raw



# Cache: 5 min TTL — reduced from 500 to 80 to fit in 512MB Render free tier
_cache = TTLCache(maxsize=64, ttl=300)  # 47 leagues need 64 slots to avoid eviction
# TV cache: 4 hour TTL — reduced from 1000 to 100
_tv_cache = TTLCache(maxsize=96, ttl=14400)  # ~47 ligas x fecha: con 30 slots se desalojaba y repetía llamadas (429 en TheSportsDB)
# Track when TheSportsDB is rate-limiting us to avoid flooding with 429s
_sportsdb_blocked_until = 0  # timestamp when we can retry
# Odds cache: 6h TTL — reduced from 200 to 50
# Créditos mensuales del plan de the-odds-api (cada request cuesta 1 crédito por mercado).
# Plan gratis = 500 → modo ahorro: solo h2h, caché 12 h y solo ligas prioritarias (~15 créditos/día).
ODDS_MONTHLY_CREDITS = int(os.getenv("ODDS_MONTHLY_CREDITS", "20000") or 20000)
ODDS_LOW_MODE = ODDS_MONTHLY_CREDITS <= 1000
ODDS_MARKETS = os.getenv("ODDS_MARKETS", "h2h" if ODDS_LOW_MODE else "h2h,spreads,totals")
_ODDS_TTL = 43200 if ODDS_LOW_MODE else 21600
_odds_cache = TTLCache(maxsize=50, ttl=_ODDS_TTL)
_odds_request_count = 0  # track requests this process lifetime
_odds_fail_cache = TTLCache(maxsize=50, ttl=600)
ODDS_DIAG: dict = {"key_configured": bool(os.getenv("ODDS_API_KEY", "")), "last_status": None, "remaining": None,
                   "used": None, "last_error": "", "errors": 0, "events_by_sport": {},
                   "monthly_credits": ODDS_MONTHLY_CREDITS, "low_mode": ODDS_LOW_MODE, "markets": ODDS_MARKETS}
_ODDS_MONTHLY_BUDGET = 6000  # ~6,666 effective requests with 3-market calls
# En modo ahorro solo pedimos cuotas de las ligas que generan clics de apuestas
ODDS_PRIORITY_LEAGUES = {"liga-mx", "nfl", "mlb", "nba", "champions", "premier-league", "la-liga", "libertadores", "ufc"}
_ODDS_RESERVE = 25  # créditos que dejamos sin usar para no quedar en 0 (la API regresa 401 al agotarse)

# ── Odds API (the-odds-api.com) ────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"

# Map our league slugs to the-odds-api sport keys
# Paid plan: all 19 leagues with 6h cache, ~7K credits/month of 20K budget.
ODDS_SPORT_MAP = {
    # Futbol
    "liga-mx": "soccer_mexico_ligamx",
    "premier-league": "soccer_epl",
    "la-liga": "soccer_spain_la_liga",
    "serie-a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue-1": "soccer_france_ligue_one",
    "champions": "soccer_uefa_champs_league",
    "europa-league": "soccer_uefa_europa_league",
    "mls": "soccer_usa_mls",
    "liga-argentina": "soccer_argentina_primera_division",
    "liga-colombia": "soccer_colombia_primera_a",
    "libertadores": "soccer_conmebol_copa_libertadores",
    "sudamericana": "soccer_conmebol_copa_sudamericana",
    "copa-america": "soccer_conmebol_copa_america",
    # Copas domésticas
    "copa-del-rey": "soccer_spain_copa_del_rey",
    "fa-cup": "soccer_fa_cup",
    "carabao-cup": "soccer_england_efl_cup",
    "dfb-pokal": "soccer_germany_dfb_pokal",
    "coppa-italia": "soccer_italy_coppa_italia",
    "coupe-de-france": "soccer_france_coupe_de_france",
    # Copas internacionales
    "leagues-cup": "soccer_concacaf_leagues_cup",
    "club-world-cup": "soccer_fifa_club_world_cup",
    # Selecciones
    "euro": "soccer_uefa_european_championship",
    "nations-league": "soccer_uefa_nations_league",
    "gold-cup": "soccer_concacaf_gold_cup",
    # Futbol americano
    "nfl": "americanfootball_nfl",
    "college-football": "americanfootball_ncaaf",
    # Basquetbol
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    # Beisbol
    "mlb": "baseball_mlb",
    # Hockey
    "nhl": "icehockey_nhl",
    # Combate
    "ufc": "mma_mixed_martial_arts",
}
# Priority order: fetch these first when budget is tight
ODDS_PRIORITY_LEAGUES = [
    "mlb", "nfl", "nba", "liga-mx", "premier-league",
    "champions", "la-liga", "mls", "nhl", "wnba",
]


# ── Default TV channels per league (fallback when ESPN has no broadcast info) ──
# Default TV channels per league (fallback when ESPN has no broadcast info)
# Updated Aug 2026 — sources: RÉCORD, Infobae, Mediotiempo
DEFAULT_LEAGUE_CHANNELS = {
    # ── Futbol México ──
    "liga-mx-femenil": ["TUDN", "ViX"],
    "liga-expansion": ["ESPN MX", "ViX"],
    "mls": ["Apple TV+", "ViX"],
    # ── Futbol Europa (2026-27 season) ──
    "premier-league": ["Fox Sports MX", "Max", "TNT Sports"],
    "la-liga": ["SKY"],
    "serie-a": ["ESPN MX", "Disney+"],
    "bundesliga": ["Fox Sports MX"],
    "ligue-1": ["Fox Sports MX"],
    "champions": ["Fox Sports MX", "Max", "TNT Sports"],
    "europa-league": ["Fox Sports MX", "Max"],
    # ── Copas domésticas ──
    "copa-del-rey": ["SKY"],
    "fa-cup": ["Fox Sports MX", "ESPN MX"],
    "carabao-cup": ["Fox Sports MX"],
    "dfb-pokal": ["Fox Sports MX"],
    "coppa-italia": ["ESPN MX", "Disney+"],
    "coupe-de-france": ["Fox Sports MX"],
    "us-open-cup": ["ESPN+", "Apple TV+"],
    "copa-argentina": ["ESPN", "TNT Sports"],
    # ── Copas internacionales ──
    "concacaf-cl": ["TUDN", "ViX", "Canal 5"],
    "copa-america": ["TUDN", "Canal 5", "ViX"],
    "world-cup": ["TUDN", "Canal 5", "Azteca 7", "ViX"],
    "club-friendly": ["TUDN", "ViX"],
    "leagues-cup": ["Apple TV+", "MLS Season Pass"],
    "club-world-cup": ["TUDN", "ViX", "Fox Sports MX"],
    # ── Selecciones ──
    "euro": ["Fox Sports MX", "Max"],
    "gold-cup": ["TUDN", "Canal 5", "ViX"],
    "wcq-conmebol": ["ESPN MX", "Disney+"],
    "wcq-concacaf": ["TUDN", "Canal 5", "ViX"],
    "nations-league": ["Fox Sports MX"],
    "concacaf-nations": ["TUDN", "ViX"],
    # ── Futbol LATAM ──
    "liga-colombia": ["Win Sports+", "ESPN"],
    "liga-argentina": ["ESPN", "TNT Sports", "Disney+"],
    "liga-ecuador": ["GOLTV", "ESPN"],
    "liga-panama": ["TVMax", "RPC"],
    "liga-chile": ["TNT Sports", "ESPN"],
    "liga-peru": ["GOLPERU", "Liga1 Max"],
    "libertadores": ["ESPN", "Disney+", "Fox Sports MX"],
    "sudamericana": ["ESPN", "Disney+"],
    "liga-portugal": ["ESPN MX"],
    "eredivisie": ["ESPN MX"],
    # ── NFL 2026 ──
    "nfl": ["ESPN MX", "Fox Sports MX", "TUDN"],
    "college-football": ["ESPN MX"],
    # ── NBA 2025-26 ──
    "nba": ["ESPN MX", "Disney+"],
    "wnba": ["ESPN MX", "Disney+"],
    # ── MLB 2026 ──
    "mlb": ["ESPN MX", "Disney+", "Fox Sports MX"],
    # ── Béisbol México ──
    "lmp": ["TUDN", "ESPN MX", "Canal 5"],
    "lmb": ["ESPN MX", "TUDN"],
    # ── Béisbol invernal del Caribe (Oct–Ene) ──
    # LVBP: los ocho canales con derechos, en orden de utilidad para el lector
    # (primero la señal abierta, que no cuesta nada, después el cable).
    # Fuente: El Estímulo, 23/12/2025 — temporada 2025-26. SimpleTV estaba aquí
    # por error: es una operadora de cable, no un canal con derechos; quien la
    # tiene ve la LVBP por Meridiano, IVC o ByM, no "por SimpleTV".
    "lvbp": ["Meridiano TV", "Televen", "Venevisión", "TVES", "Canal i",
             "IVC", "ByM Sport", "1 Baseball"],
    "lidom": ["CDN Deportes", "Teleantillas", "Coral 39", "Digital 15"],
    # ── Basquetbol México ──
    "lnbp": ["ESPN MX", "TUDN", "Claro Sports"],
    # ── NHL 2025-26 ──
    "nhl": ["ESPN MX", "Disney+", "SKY"],
    # ── Combate ──
    "ufc": ["Paramount+", "Fox Sports MX"],
    # ── Motorsport ──
    "f1": ["Fox Sports MX", "Canal 5", "TUDN", "F1 TV"],
}

# Liga MX Apertura 2026: broadcast rights per team (home matches)
# Source: infobae.com Jul 17, 2026
LIGA_MX_TEAM_CHANNELS = {
    # TelevisaUnivision: Canal 5 + TUDN + ViX Premium
    "america": ["Canal 5", "TUDN", "ViX"],
    "pumas": ["Canal 5", "TUDN", "ViX"],
    "unam": ["Canal 5", "TUDN", "ViX"],
    "monterrey": ["Canal 5", "TUDN", "ViX"],
    "rayados": ["Canal 5", "TUDN", "ViX"],
    # TelevisaUnivision: TUDN + ViX Premium
    "atlas": ["TUDN", "ViX"],
    "cruz azul": ["TUDN", "ViX"],
    "santos laguna": ["TUDN", "ViX"],
    "santos": ["TUDN", "ViX"],
    # FOX One / FOX Sports exclusivo
    "leon": ["Fox Sports MX"],
    "pachuca": ["Fox Sports MX"],
    "queretaro": ["Fox Sports MX"],
    "tijuana": ["Fox Sports MX"],
    "xolos": ["Fox Sports MX"],
    # FOX One + TV Azteca (compartido)
    "necaxa": ["Fox Sports MX", "Azteca 7"],
    # TV Azteca + FOX One
    "tigres uanl": ["Azteca 7", "Fox Sports MX"],
    "tigres": ["Azteca 7", "Fox Sports MX"],
    "puebla": ["Azteca 7", "Fox Sports MX"],
    "atlante": ["Azteca 7", "Fox Sports MX"],
    "juarez": ["Azteca 7", "Fox Sports MX"],
    # Toluca: alterna entre TUDN, TV Azteca y FOX One
    "toluca": ["TUDN", "Azteca 7", "Fox Sports MX"],
    # ESPN + Disney+
    "atletico san luis": ["ESPN MX", "Disney+"],
    "san luis": ["ESPN MX", "Disney+"],
    # Amazon Prime Video exclusivo
    "guadalajara": ["Amazon Prime"],
    "chivas": ["Amazon Prime"],
}

# ── ESPN API ─────────────────────────────────────────────

_cache_ts: dict = {}      # cache_key → epoch del último fetch (para el TTL corto en vivo)
_LIVE_TTL = 25            # segundos entre refrescos cuando hay partidos en curso


def _has_live_event(data: dict) -> bool:
    try:
        return any((((e.get("status") or {}).get("type") or {}).get("state") == "in") for e in (data.get("events") or []))
    except Exception:
        return False


async def fetch_espn_scoreboard(
    sport: str, league: str, date_str: Optional[str] = None
) -> dict:
    """Fetch scoreboard for a sport/league from ESPN API.
    Cache 5 min, pero si la liga tiene partidos EN VIVO se refresca cada 25 s
    (marcador, down/yarda, outs… en tiempo real para /partido/, portada y push)."""
    import time as _time
    cache_key = f"espn:{sport}:{league}:{date_str}"
    if cache_key in _cache:
        cached = _cache[cache_key]
        age = _time.time() - _cache_ts.get(cache_key, 0)
        if age < _LIVE_TTL or not _has_live_event(cached):
            return cached

    url = f"{ESPN_BASE}/{sport}/{league}/scoreboard"
    params = {}
    if date_str:
        params["dates"] = date_str

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            _cache[cache_key] = data
            _cache_ts[cache_key] = _time.time()
            return data
        except httpx.HTTPError as e:
            logger.warning(f"ESPN API error for {sport}/{league}: {e}")
            return _cache.get(cache_key) or {"events": [], "leagues": []}


# ── ESPN Event Summary (lineups, H2H, standings, leaders) ──

_summary_cache = TTLCache(maxsize=10, ttl=300)  # 5 min

_SERIES_TITLE_ES = {
    "season series": "Serie de temporada",
    "regular season series": "Serie de temporada regular",
    "playoff series": "Serie de playoffs",
    "postseason series": "Serie de postemporada",
    "head to head": "Enfrentamientos directos",
    "head-to-head": "Enfrentamientos directos",
}


def _series_title_es(title: str) -> str:
    t = (title or "").strip()
    return _SERIES_TITLE_ES.get(t.lower(), t)


def _series_summary_es(summary: str) -> str:
    """ESPN: 'FIO leads series 2-1' / 'Series tied 1-1' / 'LAD wins series 3-1' → español."""
    t = (summary or "").strip()
    if not t:
        return t
    m = re.match(r"^(.+?)\s+leads?\s+(?:the\s+)?series\s+([\d-]+)$", t, re.I)
    if m:
        return f"{m.group(1)} lidera la serie {m.group(2)}"
    m = re.match(r"^(.+?)\s+(?:wins?|won)\s+(?:the\s+)?series\s+([\d-]+)$", t, re.I)
    if m:
        return f"{m.group(1)} gana la serie {m.group(2)}"
    m = re.match(r"^series\s+tied\s+([\d-]+)$", t, re.I)
    if m:
        return f"Serie empatada {m.group(1)}"
    m = re.match(r"^(.+?)\s+leads?\s+([\d-]+)$", t, re.I)
    if m:
        return f"{m.group(1)} lidera {m.group(2)}"
    return t.replace("leads series", "lidera la serie").replace("Series tied", "Serie empatada").replace("wins series", "gana la serie")


async def fetch_espn_event_summary(
    sport: str, league: str, event_id: str
) -> dict:
    """
    Fetch ESPN summary for a single event.
    Returns parsed dict with rosters, standings, seasonseries, boxscore.
    """
    cache_key = f"summary:{sport}:{league}:{event_id}"
    if cache_key in _summary_cache:
        return _summary_cache[cache_key]

    url = f"{ESPN_BASE}/{sport}/{league}/summary"
    params = {"event": event_id}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.warning(f"ESPN summary error for {event_id}: {e}")
            return {}
        except Exception as e:
            logger.warning(f"ESPN summary parse error for {event_id}: {e}")
            return {}

    result = {}

    # ── Rosters / Lineups ──
    try:
        rosters_raw = data.get("rosters", [])
        parsed_rosters = []
        for team_roster in rosters_raw:
            team_info = team_roster.get("team", {})
            players = []
            for p in team_roster.get("roster", []):
                athlete = p.get("athlete", {})
                pos = p.get("position", {})
                # Parse stats array into dict
                stats = {}
                for s in p.get("stats", []):
                    if isinstance(s, dict):
                        stats[s.get("abbreviation", s.get("name", ""))] = s.get("displayValue", s.get("value", ""))
                players.append({
                    "name": athlete.get("displayName", ""),
                    "short_name": athlete.get("shortName", ""),
                    "headshot": athlete.get("headshot", {}).get("href", "") if isinstance(athlete.get("headshot"), dict) else athlete.get("headshot", ""),
                    "jersey": p.get("jersey", ""),
                    "position": pos.get("abbreviation", pos.get("name", "")),
                    "starter": p.get("starter", False),
                    "bat_order": p.get("batOrder", 0),
                    "stats": stats,
                })
            parsed_rosters.append({
                "team_id": team_info.get("id", ""),
                "team_name": team_info.get("displayName", ""),
                "team_abbr": team_info.get("abbreviation", ""),
                "team_logo": team_info.get("logo", ""),
                "players": players,
            })
        if parsed_rosters:
            result["rosters"] = parsed_rosters
    except Exception as e:
        logger.debug(f"Rosters parse error: {e}")

    # ── Season Series (H2H) ──
    try:
        series_raw = data.get("seasonseries", [])
        parsed_series = []
        for s in series_raw:
            events = []
            for ev in s.get("events", []):
                competitors = ev.get("competitors", [])
                teams = []
                for c in competitors:
                    teams.append({
                        "name": c.get("team", {}).get("displayName", c.get("team", {}).get("name", "")),
                        "abbr": c.get("team", {}).get("abbreviation", ""),
                        "score": c.get("score", ""),
                        "winner": c.get("winner", False),
                    })
                events.append({
                    "id": ev.get("id", ""),
                    "date": ev.get("date", ""),
                    "status": ev.get("statusType", {}).get("name", ev.get("status", "")),
                    "teams": teams,
                })
            parsed_series.append({
                "type": s.get("type", ""),
                "title": _series_title_es(s.get("title", "")),
                "summary": _series_summary_es(s.get("summary", "")),
                "events": events,
            })
        if parsed_series:
            result["seasonseries"] = parsed_series
    except Exception as e:
        logger.debug(f"Season series parse error: {e}")

    # ── Standings ──
    try:
        standings_raw = data.get("standings", {})
        groups = standings_raw.get("groups", [])
        parsed_standings = []
        for g in groups:
            header = g.get("header", "")
            entries = []
            for entry in g.get("standings", {}).get("entries", []):
                team = entry.get("team", {})
                stats = {}
                for st in entry.get("stats", []):
                    stats[st.get("abbreviation", st.get("name", ""))] = st.get("displayValue", st.get("value", ""))
                entries.append({
                    "team_name": team.get("displayName", team.get("name", "")),
                    "team_abbr": team.get("abbreviation", ""),
                    "team_logo": team.get("logos", [{}])[0].get("href", "") if team.get("logos") else "",
                    "wins": stats.get("W", ""),
                    "losses": stats.get("L", ""),
                    "pct": stats.get("PCT", ""),
                    "gb": stats.get("GB", ""),
                    "streak": stats.get("STRK", ""),
                })
            parsed_standings.append({
                "header": header,
                "entries": entries,
            })
        if parsed_standings:
            result["standings"] = parsed_standings
    except Exception as e:
        logger.debug(f"Standings parse error: {e}")

    # ── Boxscore Player Stats (for key players) ──
    try:
        boxscore = data.get("boxscore", {})
        players_raw = boxscore.get("players", [])
        parsed_players = []
        for team_players in players_raw:
            team_info = team_players.get("team", {})
            stat_groups = []
            for group in team_players.get("statistics", []):
                group_name = group.get("type", group.get("name", ""))
                labels = group.get("labels", [])
                athletes = []
                for ath in group.get("athletes", []):
                    athlete_info = ath.get("athlete", {})
                    stat_values = ath.get("stats", [])
                    stat_dict = {}
                    for i, label in enumerate(labels):
                        if i < len(stat_values):
                            stat_dict[label] = stat_values[i]
                    athletes.append({
                        "name": athlete_info.get("displayName", ""),
                        "short_name": athlete_info.get("shortName", ""),
                        "headshot": athlete_info.get("headshot", ""),
                        "jersey": athlete_info.get("jersey", ""),
                        "position": ath.get("position", {}).get("abbreviation", ""),
                        "starter": ath.get("starter", False),
                        "stats": stat_dict,
                    })
                stat_groups.append({
                    "type": group_name,
                    "labels": labels,
                    "athletes": athletes,
                })
            parsed_players.append({
                "team_name": team_info.get("displayName", ""),
                "team_abbr": team_info.get("abbreviation", ""),
                "stat_groups": stat_groups,
            })
        if parsed_players:
            result["boxscore_players"] = parsed_players
    except Exception as e:
        logger.debug(f"Boxscore players parse error: {e}")

    # ── Probables (starting pitchers, etc.) from header ──
    try:
        header = data.get("header", {})
        competitions = header.get("competitions", [{}])
        if competitions:
            comp = competitions[0]
            for competitor in comp.get("competitors", []):
                probables = competitor.get("probables", [])
                if probables:
                    if "probables" not in result:
                        result["probables"] = []
                    for prob in probables:
                        athlete = prob.get("athlete", {})
                        stats_list = []
                        splits = prob.get("statistics", {}).get("splits", {})
                        if splits:
                            for cat in splits.get("categories", []):
                                for st in cat.get("stats", []):
                                    stats_list.append({
                                        "name": st.get("abbreviation", st.get("name", "")),
                                        "value": st.get("displayValue", st.get("value", "")),
                                    })
                        result["probables"].append({
                            "team_id": competitor.get("id", ""),
                            "name": athlete.get("displayName", ""),
                            "short_name": athlete.get("shortName", ""),
                            "headshot": athlete.get("headshot", {}).get("href", "") if isinstance(athlete.get("headshot"), dict) else "",
                            "stats": {s["name"]: s["value"] for s in stats_list},
                        })
    except Exception as e:
        logger.debug(f"Probables parse error: {e}")

    _summary_cache[cache_key] = result
    return result


# ── TheSportsDB Premium API ─────────────────────────────

# Map ESPN league IDs to TheSportsDB league IDs
SPORTSDB_LEAGUE_MAP = {
    "liga-mx": "4350",
    "mls": "4346",
    "premier-league": "4328",
    "la-liga": "4335",
    "serie-a": "4332",
    "bundesliga": "4331",
    "ligue-1": "4334",
    "champions": "4480",
    "europa-league": "4481",
    "nfl": "4391",
    "nba": "4387",
    "mlb": "4424",
    "nhl": "4380",
    # Ligas mexicanas (TheSportsDB-only, sin ESPN)
    "lmp": "5109",
    "lmb": "5064",
    "lnbp": "5119",
    # Ligas caribeñas (TheSportsDB-only)
    "lvbp": "5112",
}

# Leagues that use TheSportsDB as PRIMARY source (no ESPN data)
SPORTSDB_ONLY_LEAGUES = {"lmp", "lmb", "lnbp", "lvbp", "lidom"}


async def fetch_sportsdb_schedule(
    sportsdb_league_id: str, date_str: str
) -> list[dict]:
    """
    Fetch schedule from TheSportsDB Premium API.
    Returns events with TV station info.
    date_str format: YYYY-MM-DD
    """
    cache_key = f"sportsdb:schedule:{sportsdb_league_id}:{date_str}"
    if cache_key in _tv_cache:
        return _tv_cache[cache_key]

    url = f"{SPORTSDB_BASE}/eventsday.php"
    params = {"d": date_str, "l": sportsdb_league_id}

    async with httpx.AsyncClient(timeout=12) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            events = data.get("events") or []
            _tv_cache[cache_key] = events
            return events
        except Exception as e:
            logger.warning(f"TheSportsDB schedule error for league {sportsdb_league_id}: {e}")
            return []


# Nuestro tipo de deporte → el nombre que usa TheSportsDB en eventstv.php
_SPORTSDB_TV_SPORT = {
    "baseball": "Baseball",
    "soccer": "Soccer",
    "football": "American Football",
    "basketball": "Basketball",
    "hockey": "Ice Hockey",
}

_tv_day_cache = TTLCache(maxsize=40, ttl=3600)


async def fetch_sportsdb_tv_by_day(sport_type: str, date_iso: str) -> dict[str, list[dict]]:
    """Transmisiones de TV de todo un día, indexadas por idEvent de TheSportsDB.

    Reemplaza a lookupeventtv.php, que devuelve 404 con nuestra clave (probado
    2026-09-16: responde una página HTML de error, no JSON). Ese endpoint muerto
    se llamaba una vez POR PARTIDO y siempre fallaba en silencio dentro del
    try/except, así que gastábamos una petición por juego para nada.

    eventstv.php sí responde y trae strChannel + strCountry, y con una sola
    llamada por deporte y día cubre todos los partidos.
    """
    sport = _SPORTSDB_TV_SPORT.get(sport_type)
    if not sport:
        return {}
    cache_key = f"sportsdb:tvday:{sport}:{date_iso}"
    if cache_key in _tv_day_cache:
        return _tv_day_cache[cache_key]

    import time as _time
    global _sportsdb_blocked_until
    if _time.time() < _sportsdb_blocked_until:
        return {}

    by_event: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{SPORTSDB_BASE}/eventstv.php",
                                    params={"d": date_iso, "s": sport})
            if resp.status_code == 429:
                _sportsdb_blocked_until = _time.time() + 1800
                logger.warning("TheSportsDB rate limited (429) — backing off 30 min")
                return {}
            resp.raise_for_status()
            for tv in (resp.json().get("tvevents") or []):
                cc = SPORTSDB_COUNTRY_CODE.get(tv.get("strCountry", ""))
                channel = tv.get("strChannel", "")
                ev_id = str(tv.get("idEvent", ""))
                if not (cc and channel and ev_id):
                    continue
                bucket = by_event.setdefault(ev_id, [])
                if not any(x["channel"] == channel and x["cc"] == cc for x in bucket):
                    bucket.append({"channel": channel, "cc": cc})
    except Exception as e:
        logger.warning(f"TheSportsDB TV-by-day error: {e}")
        return {}

    _tv_day_cache[cache_key] = by_event
    return by_event


async def fetch_sportsdb_tv_by_event(event_id: str) -> list[dict]:
    """
    Lookup TV broadcast channels for a specific event ID.
    TheSportsDB Premium endpoint.
    Includes rate-limit protection: if we get 429, back off for 30 min.

    OJO: con nuestra clave este endpoint responde 404 (probado 2026-09-16).
    Queda por si se reactiva, pero el camino vivo es fetch_sportsdb_tv_by_day().
    """
    import time as _time
    global _sportsdb_blocked_until

    # If we're rate-limited, don't even try
    if _time.time() < _sportsdb_blocked_until:
        return []

    cache_key = f"sportsdb:tv:{event_id}"
    if cache_key in _tv_cache:
        return _tv_cache[cache_key]

    url = f"{SPORTSDB_BASE}/lookupeventtv.php"
    params = {"id": event_id}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                # Rate limited — back off for 30 minutes
                _sportsdb_blocked_until = _time.time() + 1800
                logger.warning("TheSportsDB rate limited (429) — backing off 30 min")
                return []
            resp.raise_for_status()
            data = resp.json()
            tv_list = data.get("tvevent") or []
            result = []
            for tv in tv_list:
                country = tv.get("strCountry", "")
                # Antes sólo se guardaban México, EE.UU. y Worldwide, y todo lo
                # demás se tiraba. Según Search Console, Venezuela (17%), Panamá
                # (8%) y Dominicana (4%) son casi un tercio de los clics del
                # sitio: estábamos descartando el único dato de canal REAL que
                # tenemos para ellos. Ahora se guardan todos los países que
                # servimos, con su código ISO.
                cc = SPORTSDB_COUNTRY_CODE.get(country)
                if not cc:
                    continue
                channel = tv.get("strChannel", "")
                if channel:
                    result.append({
                        "channel": channel,
                        "country": country,
                        "cc": cc,          # "MX", "VE", "PA"… o "*" para Worldwide
                        "info": CHANNEL_ALIASES.get(channel, {
                            "name": channel,
                            "country": cc if cc != "*" else "",
                            "type": "cable",
                        }),
                    })
            _tv_cache[cache_key] = result
            return result
        except Exception as e:
            logger.warning(f"TheSportsDB TV lookup error: {e}")
            return []


# TheSportsDB uses different team names than ESPN sometimes
# Map ESPN names -> additional search terms for matching
SPORTSDB_TEAM_ALIASES = {
    "guadalajara": ["chivas", "cd guadalajara"],
    "america": ["club america", "cf america"],
    "unam": ["pumas", "pumas unam"],
    "cruz azul": ["cruz azul"],
    "tigres uanl": ["tigres", "uanl tigres"],
    "monterrey": ["cf monterrey", "rayados"],
    "santos laguna": ["santos", "santos laguna"],
    "pachuca": ["cf pachuca", "tuzos"],
    "toluca": ["deportivo toluca"],
    "tijuana": ["club tijuana", "xolos"],
    "leon": ["club leon"],
    "atletico madrid": ["atletico de madrid", "atletico"],
    "atletico de madrid": ["atletico madrid", "atletico"],
}


def _team_matches(espn_name: str, db_name: str) -> bool:
    """Check if an ESPN team name matches a TheSportsDB team name."""
    espn = espn_name.lower()
    db = db_name.lower()

    # Direct contains
    if espn in db or db in espn:
        return True

    # Last word match (e.g. "Guadalajara" matches "CD Guadalajara")
    espn_last = espn.split()[-1] if espn else ""
    db_last = db.split()[-1] if db else ""
    if espn_last and len(espn_last) > 3 and (espn_last in db or db_last in espn):
        return True

    # Check aliases
    aliases = SPORTSDB_TEAM_ALIASES.get(espn, [])
    for alias in aliases:
        if alias in db or db in alias:
            return True

    return False


async def get_sportsdb_tv_for_teams(
    home_team: str, away_team: str, league_slug: str, date_str: str
) -> list[dict]:
    """
    Try to find TV info from TheSportsDB by matching teams
    from the daily schedule.
    """
    sportsdb_league = SPORTSDB_LEAGUE_MAP.get(league_slug)
    if not sportsdb_league:
        return []

    # Convert YYYYMMDD to YYYY-MM-DD
    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    events = await fetch_sportsdb_schedule(sportsdb_league, formatted_date)

    for ev in events:
        db_home = (ev.get("strHomeTeam") or "")
        db_away = (ev.get("strAwayTeam") or "")

        home_match = _team_matches(home_team, db_home)
        away_match = _team_matches(away_team, db_away)

        if home_match and away_match:
            # Found the match! Get TV info
            event_id = ev.get("idEvent", "")
            tv_station = ev.get("strTVStation", "")

            # First try the TV station field directly
            tv_channels = []
            if tv_station:
                for ch in tv_station.split(","):
                    ch = ch.strip()
                    if ch:
                        tv_channels.append({
                            "channel": ch,
                            "country": "",
                            "info": CHANNEL_ALIASES.get(ch, {"name": ch, "type": "cable"}),
                        })

            # Then try the detailed TV lookup if we have an event ID
            if event_id and not tv_channels:
                tv_channels = await fetch_sportsdb_tv_by_event(event_id)

            return tv_channels

    return []


# ── ESPN Event Parser (enriched with TheSportsDB) ────────

# ── Situación en vivo por deporte (para /partido/ y cards de portada) ──
_ORD_ES = {1: "1er", 2: "2do", 3: "3er", 4: "4to", 5: "5to", 6: "6to", 7: "7mo", 8: "8vo", 9: "9no"}
_DOWN_ES = {1: "1ª", 2: "2ª", 3: "3ª", 4: "4ª"}


def _period_label(sport_type: str, status_raw: dict, league_slug: str = "") -> str:
    """'3er cuarto', 'Alta 5ª', '2º tiempo', 'OT'…"""
    st = status_raw.get("type") or {}
    period = status_raw.get("period", 0) or 0
    detail = (st.get("shortDetail") or st.get("detail") or "")
    if st.get("state") != "in":
        return ""
    if st.get("name") == "STATUS_HALFTIME":
        return "Medio tiempo"
    if st.get("name") == "STATUS_END_PERIOD" and sport_type != "baseball":
        return f"Fin del {_ORD_ES.get(period, str(period) + '°')}"
    if sport_type == "baseball":
        d = detail.lower()
        half = "Alta" if "top" in d else ("Baja" if "bot" in d else ("Media" if "mid" in d else ("Final" if "end" in d else "")))
        return f"{half} {period}ª".strip() if period else detail
    if sport_type == "soccer":
        if period == 1:
            return "1er tiempo"
        if period == 2:
            return "2do tiempo"
        if period in (3, 4):
            return "Tiempo extra"
        if period >= 5:
            return "Penales"
        return "Descanso" if "half" in detail.lower() else detail
    if sport_type in ("football", "college-football"):
        return f"{_ORD_ES.get(period, str(period) + '°')} cuarto" if period <= 4 else "Tiempo extra"
    if sport_type == "basketball":
        if period <= 4:
            return f"{_ORD_ES.get(period, str(period) + '°')} cuarto"
        return f"OT{period - 4 if period > 5 else ''}"
    if sport_type == "hockey":
        return f"{_ORD_ES.get(period, str(period) + '°')} periodo" if period <= 3 else ("OT" if period == 4 else "Shootout")
    if sport_type == "mma":
        return f"Round {period}" if period else detail
    return detail


def build_live(comp: dict, ev: dict, sport_type: str, home: dict, away: dict,
               home_id: str = "", away_id: str = "") -> dict:
    """Extrae del scoreboard de ESPN lo que Apple Sports muestra en vivo:
    NFL: down/distancia, posesión, yarda (0-100 → gráfico de campo), zona roja, timeouts, última jugada.
    MLB: entrada, bolas-strikes-outs, corredores en base, pitcher/bateador.
    Fútbol: minuto, goles (autor + minuto), tarjetas rojas.
    NBA/NHL: reloj, última jugada. Todos: marcador por periodo (linescores).
    Devuelve {} si el partido no está en curso y no hay linescores."""
    status_raw = ev.get("status") or {}
    st = status_raw.get("type") or {}
    state = st.get("state", "pre")
    if state == "pre":
        return {}
    live: dict = {
        "state": state,
        "clock": status_raw.get("displayClock", "") or "",
        "period": status_raw.get("period", 0) or 0,
        "period_label": _period_label(sport_type, status_raw),
        "detail": st.get("shortDetail") or st.get("detail") or "",
        "linescores": {},
        "situation_text": "",
    }
    # Marcador por periodo
    for c in comp.get("competitors") or []:
        side = "home" if c.get("homeAway") == "home" else "away"
        ls = c.get("linescores") or []
        if ls:
            live["linescores"][side] = [(x.get("displayValue") if x.get("displayValue") not in (None, "") else x.get("value", "")) for x in ls]
        if c.get("timeouts") is not None:
            live[f"{side}_timeouts"] = c.get("timeouts")
    sit = comp.get("situation") or {}
    last_play = (sit.get("lastPlay") or {}).get("text", "") if isinstance(sit.get("lastPlay"), dict) else ""
    if last_play:
        live["last_play"] = last_play[:160]

    if sport_type in ("football", "college-football") and state == "in":
        poss = str(sit.get("possession") or "")
        poss_side = "home" if poss and poss == str(home_id) else ("away" if poss and poss == str(away_id) else "")
        down = sit.get("down") or 0
        dist = sit.get("distance")
        poss_text = sit.get("possessionText") or ""
        yard = sit.get("yardLine")
        # yardLine de ESPN: 0 = zona de anotación del local… lo normalizamos a 0-100 de izquierda (visitante) a derecha (local)
        # usando possessionText ("LAC 12" = 12 yardas de la zona de LAC).
        x = None
        try:
            abbr, y = poss_text.split()
            y = int(y)
            x = (100 - y) if abbr == home.get("short") else y
        except Exception:
            if isinstance(yard, (int, float)):
                x = int(yard)
        live.update({
            "possession": poss_side,
            "down": down,
            "distance": dist,
            "down_text": (f"{_DOWN_ES.get(down, str(down))} y {'meta' if dist == 0 else dist}" if down else ""),
            "yard_text": poss_text,
            "ball_x": x,
            "red_zone": bool(sit.get("isRedZone")),
            "home_timeouts": sit.get("homeTimeouts", live.get("home_timeouts")),
            "away_timeouts": sit.get("awayTimeouts", live.get("away_timeouts")),
        })
        parts = [p for p in (live["period_label"], live["clock"], live["down_text"], poss_text) if p]
        live["situation_text"] = " · ".join(parts) + (" · 🔴 zona roja" if live["red_zone"] else "")

    elif sport_type == "baseball" and state == "in":
        pitcher = ((sit.get("pitcher") or {}).get("athlete") or {})
        batter = ((sit.get("batter") or {}).get("athlete") or {})
        live.update({
            "balls": sit.get("balls", 0), "strikes": sit.get("strikes", 0), "outs": sit.get("outs", 0),
            "on_first": bool(sit.get("onFirst")), "on_second": bool(sit.get("onSecond")), "on_third": bool(sit.get("onThird")),
            "pitcher": pitcher.get("shortName") or pitcher.get("displayName", ""),
            "batter": batter.get("shortName") or batter.get("displayName", ""),
        })
        bases = [b for b, on in (("1ª", live["on_first"]), ("2ª", live["on_second"]), ("3ª", live["on_third"])) if on]
        outs = live["outs"]
        parts = [live["period_label"], f"{outs} out{'s' if outs != 1 else ''}",
                 f"{live['balls']}-{live['strikes']}"]
        if bases:
            parts.append("corredor en " + " y ".join(bases) if len(bases) == 1 else "corredores en " + " y ".join(bases))
        live["situation_text"] = " · ".join(p for p in parts if p)

    elif sport_type == "soccer":
        goals, reds = [], []
        for d in comp.get("details") or []:
            t = d.get("team") or {}
            side = "home" if str(t.get("id")) == str(home_id) else "away"
            who = ", ".join(a.get("shortName") or a.get("displayName", "") for a in d.get("athletesInvolved") or [])
            minute = (d.get("clock") or {}).get("displayValue", "")
            if d.get("scoringPlay"):
                tag = " (pen.)" if d.get("penaltyKick") else (" (a.g.)" if d.get("ownGoal") else "")
                goals.append({"side": side, "who": who, "minute": minute, "tag": tag})
            elif d.get("redCard"):
                reds.append({"side": side, "who": who, "minute": minute})
        live["goals"] = goals
        live["red_cards"] = reds
        if state == "in":
            last = goals[-1] if goals else None
            _pl = live["period_label"]
            parts = [_pl if _pl in ("Medio tiempo", "Penales", "Descanso") else (live["clock"] or _pl)]
            if _pl == "Tiempo extra" and live["clock"]:
                parts = [f"{live['clock']} (T.E.)"]
            if last:
                parts.append(f"⚽ {last['minute']} {last['who']}{last['tag']}")
            live["situation_text"] = " · ".join(p for p in parts if p)

    elif state == "in":  # basketball / hockey / otros
        parts = [live["period_label"], live["clock"]]
        live["situation_text"] = " · ".join(p for p in parts if p)

    return live


async def parse_espn_events_enriched(
    raw: dict, league_slug: str, date_str: str
) -> list[dict]:
    """Parse ESPN events and enrich with TheSportsDB TV data."""
    league_info = ALL_LEAGUES.get(league_slug, {})
    events = []

    # Pre-fetch TheSportsDB schedule ONCE per league (cached 4h)
    sportsdb_events = []
    # Transmisiones de TV del día, una sola llamada para todos los partidos del
    # deporte (antes era una petición por partido a un endpoint que da 404).
    sportsdb_tv_day: dict[str, list[dict]] = {}
    sportsdb_league = SPORTSDB_LEAGUE_MAP.get(league_slug)
    if sportsdb_league:
        formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        try:
            sportsdb_events = await fetch_sportsdb_schedule(sportsdb_league, formatted_date)
        except Exception:
            pass
        try:
            # sport_type se calcula más abajo, dentro del bucle de partidos; aquí
            # todavía no existe, así que lo derivamos de league_info igual que allá.
            _sport_for_tv = league_info[0] if isinstance(league_info, tuple) else ""
            sportsdb_tv_day = await fetch_sportsdb_tv_by_day(_sport_for_tv, formatted_date)
        except Exception:
            pass

    for ev in raw.get("events", []):
        competitions = ev.get("competitions", [{}])
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors", [])

        home = away = None
        _sport_kind = league_info[0] if isinstance(league_info, tuple) else ""
        if _sport_kind == "mma" and competitions:
            # UFC: cada competition es una pelea; la estelar es la ÚLTIMA. Los
            # competidores son athletes (no teams) → sin esto salía "TBD vs TBD".
            main = competitions[-1]
            fighters = sorted(main.get("competitors") or [], key=lambda x: x.get("order", 0))
            side = []
            for f in fighters[:2]:
                ath = f.get("athlete") or {}
                side.append({
                    "name": ath.get("displayName") or ath.get("fullName") or "TBD",
                    "short": (ath.get("displayName") or "").split()[-1] if ath.get("displayName") else "",
                    "logo": (ath.get("headshot") or {}).get("href", ""),
                    "score": "",
                    "record": f.get("record", "") or "",
                    "winner": bool(f.get("winner")),
                })
            if len(side) == 2:
                home, away = side[0], side[1]
            comp = main  # broadcasts/venue de la estelar
            competitors = []
            _date_override = main.get("date", "")
        else:
            _date_override = ""
        for team_data in competitors:
            # Extract team record from ESPN (W-L or W-D-L)
            records = team_data.get("records", [])
            record_str = ""
            for rec in records:
                if rec.get("type") == "total" or rec.get("name") == "overall":
                    record_str = rec.get("summary", "")
                    break
            if not record_str and records:
                record_str = records[0].get("summary", "")
            team_info = {
                "name": team_data.get("team", {}).get("displayName", "TBD"),
                "short": team_data.get("team", {}).get("abbreviation", ""),
                "team_id": str(team_data.get("team", {}).get("id", "")),
                "logo": team_data.get("team", {}).get("logo", ""),
                "score": team_data.get("score", ""),
                "record": record_str,
            }
            if team_data.get("homeAway") == "home":
                home = team_info
            else:
                away = team_info

        if not home:
            home = {"name": "TBD", "short": "", "logo": "", "score": "", "record": ""}
        if not away:
            away = {"name": "TBD", "short": "", "logo": "", "score": "", "record": ""}

        # 1) Get ESPN broadcast info (normalize truncated names)
        espn_broadcasts = []
        seen_channels = set()
        # Channels that are the same service (dedup group)
        _CHANNEL_GROUPS = {
            "mls season pass": "apple tv+",
            "apple tv+ mls season pass": "apple tv+",
        }
        for geo_broadcast in comp.get("geoBroadcasts", []):
            market = geo_broadcast.get("market", {}).get("type", "")
            media = geo_broadcast.get("media", {})
            raw_channel = media.get("shortName", "")
            if not raw_channel:
                continue
            channel = _normalize_channel(raw_channel)
            display_name = CHANNEL_ALIASES.get(channel, {}).get("name", channel)
            # Deduplicate by display name AND by group
            key = display_name.lower()
            group_key = _CHANNEL_GROUPS.get(key, key)
            if group_key in seen_channels:
                continue
            seen_channels.add(group_key)
            seen_channels.add(key)
            info = CHANNEL_ALIASES.get(channel, {})
            # Mark US regional sports networks (not in our known channels + local market)
            is_us_regional = (not info) and market in ("Home", "Away")
            espn_broadcasts.append({
                "channel": display_name,
                "market": market,
                "info": info,
                "is_us_regional": is_us_regional,
            })

        # 2) Smart MX channel merging — per-game, not per-league
        # Priority: TheSportsDB per-game data > US→MX mapping > league defaults
        broadcasts = list(espn_broadcasts)

        # Check if ESPN already included any MX/Spanish channel
        has_mx_channel = any(
            CHANNEL_ALIASES.get(b["channel"], {}).get("country") == "MX"
            or CHANNEL_ALIASES.get(
                _normalize_channel(b["channel"]), {}
            ).get("country") == "MX"
            for b in espn_broadcasts
        )

        # Determine MX channels to add
        mx_defaults = []
        # ¿Los canales salen de un dato real (ESPN, TheSportsDB, la tabla curada
        # de Liga MX) o de un default de liga? Solo lo primero se puede afirmar.
        channels_confirmed = True
        # Canal por país tal como lo reporta TheSportsDB: {"MX": [...], "VE": [...]}.
        # Vacío cuando esa fuente no tiene nada para este partido.
        channels_by_country: dict[str, list[str]] = {}
        if league_slug == "liga-mx":
            # Liga MX: always add team-specific channels (ESPN rarely has MX data)
            home_lower = home["name"].lower()
            away_lower = away["name"].lower()
            team_channels = None
            for team_key, channels in LIGA_MX_TEAM_CHANNELS.items():
                if team_key in home_lower or home_lower in team_key:
                    team_channels = channels
                    break
            if not team_channels:
                for team_key, channels in LIGA_MX_TEAM_CHANNELS.items():
                    if team_key in away_lower or away_lower in team_key:
                        team_channels = channels
                        break
            mx_defaults = team_channels or ["TUDN", "ViX"]
        elif league_slug == "nfl":
            # NFL: rights-based mapping (TNF→Fox Sports MX, MNF/SNF→ESPN MX/Disney+, todos→Game Pass)
            mx_defaults = nfl_mx_channels([b["channel"] for b in espn_broadcasts])
            # Sin datos de ESPN, nfl_mx_channels devuelve el reparto genérico de
            # derechos, no el canal de ESTE partido: es suposición.
            if not espn_broadcasts:
                channels_confirmed = False
        elif not has_mx_channel:
            # Try TheSportsDB first — match this game in pre-fetched schedule
            for sdb_ev in sportsdb_events:
                db_home = (sdb_ev.get("strHomeTeam") or "")
                db_away = (sdb_ev.get("strAwayTeam") or "")
                if _team_matches(home["name"], db_home) and _team_matches(away["name"], db_away):
                    # Found! Get TV station from schedule data
                    tv_station = sdb_ev.get("strTVStation", "")
                    if tv_station:
                        for ch in tv_station.split(","):
                            ch = ch.strip()
                            if ch:
                                normalized = _normalize_channel(ch)
                                alias = CHANNEL_ALIASES.get(normalized, CHANNEL_ALIASES.get(ch))
                                final_name = alias.get("name", ch) if alias else ch
                                if final_name not in mx_defaults:
                                    mx_defaults.append(final_name)
                    # Also try detailed TV lookup if schedule had no TV data
                    if not mx_defaults:
                        sdb_event_id = str(sdb_ev.get("idEvent", ""))
                        if sdb_event_id:
                            try:
                                tv_channels = sportsdb_tv_day.get(sdb_event_id, [])
                                for tv in tv_channels:
                                    ch_name = tv.get("channel", "")
                                    cc = tv.get("cc", "")
                                    if not ch_name or not cc:
                                        continue
                                    normalized = _normalize_channel(ch_name)
                                    alias = CHANNEL_ALIASES.get(normalized, CHANNEL_ALIASES.get(ch_name))
                                    final_name = alias.get("name", ch_name) if alias else ch_name
                                    # Guardamos el canal de CADA país, no sólo el
                                    # de México: Venezuela, Panamá y Dominicana
                                    # juntas son el 29% de los clics del sitio.
                                    bucket = channels_by_country.setdefault(cc, [])
                                    if final_name not in bucket:
                                        bucket.append(final_name)
                                    if cc in ("MX", "*") and final_name not in mx_defaults:
                                        mx_defaults.append(final_name)
                            except Exception:
                                pass
                    break

            if not mx_defaults and espn_broadcasts:
                # TheSportsDB no tenía nada — traducimos el canal de EE.UU.
                # Antes había AQUÍ una segunda copia de la tabla US→MX, aparte de
                # US_TO_MX_CHANNEL. Dos copias de la misma tabla es justo el bug
                # que ya nos dio tres respuestas distintas para Juárez–Tigres:
                # se actualiza una y la otra se queda vieja. Ahora hay una sola.
                mapped = set()
                for b in espn_broadcasts:
                    mx_ch = US_TO_MX_CHANNEL.get(b["channel"])
                    if mx_ch and mx_ch not in mapped:
                        mapped.add(mx_ch)
                        mx_defaults.append(mx_ch)
            elif not mx_defaults and not espn_broadcasts:
                # Nada de ESPN ni de TheSportsDB. Los defaults de liga son una
                # SUPOSICIÓN, no un dato: se marcan como no confirmados para que
                # el título, la meta description y el JSON-LD no los afirmen.
                mx_defaults = DEFAULT_LEAGUE_CHANNELS.get(league_slug, [])
                channels_confirmed = False

        # Add MX channels that aren't already in ESPN data
        for ch in mx_defaults:
            info = CHANNEL_ALIASES.get(ch, {"name": ch, "type": "cable"})
            display_name = info.get("name", ch)
            key = display_name.lower()
            group_key = _CHANNEL_GROUPS.get(key, key)
            if group_key in seen_channels or key in seen_channels:
                continue
            seen_channels.add(group_key)
            seen_channels.add(key)
            broadcasts.append({
                "channel": display_name,
                "market": "National",
                "info": info,
            })

        # Orden: primero por PAÍS, después por tipo (abierta → cable → streaming).
        #
        # El país faltaba y por eso el título de /equipo/dodgers decía "por
        # MLB.TV" a lectores de México. MLB.TV no está en CHANNEL_ALIASES, así
        # que caía en el tipo "cable" por defecto y empataba con ESPN MX; como
        # los canales de ESPN se agregan ANTES que los mexicanos y el sort de
        # Python es estable, el empate lo ganaba siempre el de Estados Unidos.
        # El público de este sitio está en México: un canal mexicano va primero
        # aunque sea streaming, y uno de EE.UU. va al final aunque sea abierto.
        def _channel_sort_key(b):
            if b.get("is_us_regional"):
                return (3, 0)
            country = (b.get("info") or {}).get("country", "")
            if country == "MX":
                geo = 0
            elif not country:          # global (Netflix, Amazon…) o desconocido
                geo = 1
            else:                      # US y cualquier otro país
                geo = 2
            ch_type = (b.get("info") or {}).get("type", "cable")
            tier = {"free": 0, "cable": 1}.get(ch_type, 2)
            return (geo, tier)
        broadcasts.sort(key=_channel_sort_key)

        # Status
        status_type = ev.get("status", {}).get("type", {})
        status_raw = ev.get("status", {})
        raw_clock = status_raw.get("displayClock", "")
        detail = status_type.get("detail", "")
        period = status_raw.get("period", 0)
        sport_type_tmp = league_info[0] if isinstance(league_info, tuple) else ""

        # Baseball: replace useless "0:00" clock with inning info
        if sport_type_tmp == "baseball" and status_type.get("state") == "in":
            detail_lower = detail.lower()
            if "top" in detail_lower:
                raw_clock = f"▲{period}"
            elif "bot" in detail_lower:
                raw_clock = f"▼{period}"
            elif "mid" in detail_lower:
                raw_clock = f"▲▼{period}"
            elif "end" in detail_lower:
                raw_clock = f"▼{period}'"
            else:
                raw_clock = f"{period}°"

        status = {
            "state": status_type.get("state", "pre"),
            "detail": detail,
            "display": _translate_status(status_type.get("description", "Scheduled")),
            "clock": raw_clock,
            "period": period,
        }

        venue_raw = comp.get("venue", {})
        venue = venue_raw.get("fullName", "")

        sport_type = league_info[0] if isinstance(league_info, tuple) else ""

        # Situación en vivo (down/yarda, bases/outs, goles, línea por periodo)
        try:
            live = build_live(comp, ev, sport_type, home, away, home.get("team_id", ""), away.get("team_id", ""))
        except Exception as _le:
            logger.debug(f"build_live failed: {_le}")
            live = {}

        # ── Post-game recap data ──────────────────
        recap = {}
        if status.get("state") == "post":
            # Headline recap (e.g. "Keaschall hits 2-run HR as Twins top Royals 5-3")
            headlines = comp.get("headlines", [])
            if headlines:
                recap["headline"] = headlines[0].get("shortLinkText", "")
                recap["description"] = headlines[0].get("description", "")
            # MVP / top performer (competition-level leader)
            comp_leaders = comp.get("leaders", [])
            if comp_leaders:
                top_cat = comp_leaders[0]
                top_leaders = top_cat.get("leaders", [])
                if top_leaders:
                    mvp_ath = top_leaders[0].get("athlete", {})
                    recap["mvp"] = {
                        "name": mvp_ath.get("displayName", ""),
                        "short": mvp_ath.get("shortName", ""),
                        "headshot": mvp_ath.get("headshot", ""),
                        "stat": top_leaders[0].get("displayValue", ""),
                        "team_id": top_leaders[0].get("team", {}).get("id", ""),
                    }
            # Winner flag
            for team_data in competitors:
                if team_data.get("winner"):
                    winner_name = team_data.get("team", {}).get("displayName", "")
                    recap["winner"] = winner_name
                    break

        # Slug de /evento/ para UFC y F1 (misma regla que events_api → sin duplicados)
        _event_slug = ""
        try:
            if sport_type == "mma":
                from events_api import slugify as _ev_slugify, mx_date as _ev_mx_date
                _event_slug = f"{_ev_slugify(ev.get('name', ''))}-{_ev_mx_date(_date_override or ev.get('date', ''))}"
            elif sport_type == "racing" and league_slug == "f1":
                from events_api import _gp_spanish as _ev_gp
                _event_slug = _ev_gp(ev.get("name", ""), int((ev.get("date") or "2026")[:4]))[1]
            elif sport_type == "racing" and league_slug in ("nascar", "indycar"):
                from events_api import _parse_race as _ev_race
                _event_slug = _ev_race(league_slug, ev)["slug"]
        except Exception:
            _event_slug = ""

        events.append({
            "id": ev.get("id", ""),
            "league_slug": league_slug,
            "league_name": league_info[2] if isinstance(league_info, tuple) else league_slug,
            "emoji": league_info[3] if isinstance(league_info, tuple) and len(league_info) > 3 else "",
            "sport": sport_type,
            "event_slug": _event_slug,
            "date": _date_override or ev.get("date", ""),
            "name": ev.get("name", f"{away['name']} vs {home['name']}"),
            "short_name": ev.get("shortName", ""),
            "home": home,
            "away": away,
            "status": status,
            "live": live,
            "broadcasts": broadcasts,
            # False = los canales salen del default de la liga, no de un dato de
            # este partido. Quien AFIRME el canal (título, meta description,
            # JSON-LD) tiene que callarse cuando esto es False.
            "channels_confirmed": channels_confirmed,
            "channels_by_country": channels_by_country,
            "venue": venue,
            "recap": recap,
            "link": ev.get("links", [{}])[0].get("href", "") if ev.get("links") else "",
            # Postseason metadata (ESPN: season.type 3 = playoffs; notes carry "ALDS - Game 2")
            "season_type": (ev.get("season") or {}).get("type", 0),
            "series_note": _series_summary_es(next((n.get("headline", "") for n in (comp.get("notes") or []) if n.get("headline")), "")),
        })

    return events


# ── TheSportsDB-only event parser ───────────────────────

async def parse_sportsdb_standalone_events(
    league_slug: str, date_str: str
) -> list[dict]:
    """
    Fetch and parse events from TheSportsDB for leagues without ESPN coverage.
    Produces the same output format as parse_espn_events_enriched().
    date_str format: YYYYMMDD
    """
    league_info = ALL_LEAGUES.get(league_slug, ())
    sportsdb_id = SPORTSDB_LEAGUE_MAP.get(league_slug)
    if not sportsdb_id:
        return []

    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    # TheSportsDB indexes by UTC date, but we query by MX date.
    # A 9 PM MX game = 3 AM UTC next day, so we must also check the next
    # UTC day and filter by the event's local dateEvent.
    from datetime import date as _date_cls
    target = _date_cls.fromisoformat(formatted_date)
    next_day = (target + timedelta(days=1)).isoformat()

    raw_today, raw_next = await asyncio.gather(
        fetch_sportsdb_schedule(sportsdb_id, formatted_date),
        fetch_sportsdb_schedule(sportsdb_id, next_day),
    )
    # Merge and filter: keep only events whose local dateEvent matches target
    seen_ids = set()
    events_raw = []
    for ev in raw_today + raw_next:
        eid = ev.get("idEvent", "")
        ev_date = ev.get("dateEvent") or ""
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        # Keep events whose dateEvent matches the requested MX date
        if ev_date and ev_date != formatted_date:
            continue
        events_raw.append(ev)

    events = []
    for ev in events_raw:
        home_name = ev.get("strHomeTeam") or "TBD"
        away_name = ev.get("strAwayTeam") or "TBD"
        home_score = ev.get("intHomeScore")
        away_score = ev.get("intAwayScore")

        home = {
            "name": home_name,
            "short": home_name[:3].upper() if home_name != "TBD" else "",
            "logo": ev.get("strHomeTeamBadge") or "",
            "score": str(home_score) if home_score is not None else "",
            "record": "",
        }
        away = {
            "name": away_name,
            "short": away_name[:3].upper() if away_name != "TBD" else "",
            "logo": ev.get("strAwayTeamBadge") or "",
            "score": str(away_score) if away_score is not None else "",
            "record": "",
        }

        # Determine game status from TheSportsDB fields
        sdb_status = (ev.get("strStatus") or "").lower()
        event_timestamp = ev.get("strTimestamp") or ""
        sport_type = league_info[0] if isinstance(league_info, tuple) else "baseball"

        if sdb_status in ("match finished", "ft", "aet", "finished"):
            state = "post"
            display = "Final"
            detail = "Final"
            clock = ""
        elif sdb_status in ("not started", "ns", ""):
            state = "pre"
            display = "Programado"
            # Parse time for display
            raw_time = ev.get("strTime") or ""
            if raw_time:
                try:
                    from datetime import datetime as _dt
                    utc_time = _dt.strptime(raw_time, "%H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                    mx_time = utc_time.astimezone(TZ_MX)
                    detail = mx_time.strftime("%H:%M") + " hrs"
                except Exception:
                    detail = raw_time
            else:
                detail = "Hora por confirmar"
            clock = ""
        else:
            # In progress
            state = "in"
            display = "En vivo"
            detail = sdb_status.title()
            # Baseball inning display
            if sport_type == "baseball":
                progress = ev.get("intProgress") or ev.get("strProgress") or ""
                clock = str(progress) if progress else ""
            else:
                clock = ""

        status = {
            "state": state,
            "detail": detail,
            "display": display,
            "clock": clock,
            "period": 0,
        }

        # Build broadcasts from TheSportsDB TV station or defaults
        broadcasts = []
        seen_channels = set()
        tv_station = ev.get("strTVStation") or ""
        if tv_station:
            for ch in tv_station.split(","):
                ch = ch.strip()
                if ch:
                    normalized = _normalize_channel(ch)
                    info = CHANNEL_ALIASES.get(normalized, CHANNEL_ALIASES.get(ch, {"name": ch, "type": "cable"}))
                    display_name = info.get("name", ch)
                    key = display_name.lower()
                    if key not in seen_channels:
                        seen_channels.add(key)
                        broadcasts.append({
                            "channel": display_name,
                            "market": "National",
                            "info": info,
                        })

        # Add league defaults if no specific TV info
        if not broadcasts:
            for ch in DEFAULT_LEAGUE_CHANNELS.get(league_slug, []):
                info = CHANNEL_ALIASES.get(ch, {"name": ch, "type": "cable"})
                display_name = info.get("name", ch)
                key = display_name.lower()
                if key not in seen_channels:
                    seen_channels.add(key)
                    broadcasts.append({
                        "channel": display_name,
                        "market": "National",
                        "info": info,
                    })

        # Sort: free TV first, cable, streaming last
        def _ch_sort(b):
            ct = (b.get("info") or {}).get("type", "cable")
            return 0 if ct == "free" else (1 if ct == "cable" else 2)
        broadcasts.sort(key=_ch_sort)

        # Recap for finished games
        recap = {}
        if state == "post" and home_score is not None and away_score is not None:
            h = int(home_score)
            a = int(away_score)
            if h > a:
                recap["winner"] = home_name
            elif a > h:
                recap["winner"] = away_name

        # Build ISO date from TheSportsDB fields
        date_event = ev.get("dateEvent") or formatted_date
        time_event = ev.get("strTime") or "00:00:00"
        iso_date = f"{date_event}T{time_event}+00:00"

        venue = ev.get("strVenue") or ""
        event_id = ev.get("idEvent") or ""

        events.append({
            "id": f"sdb-{event_id}",
            "league_slug": league_slug,
            "league_name": league_info[2] if isinstance(league_info, tuple) else league_slug,
            "emoji": league_info[3] if isinstance(league_info, tuple) and len(league_info) > 3 else "⚾",
            "sport": sport_type,
            "date": iso_date,
            "name": f"{away_name} vs {home_name}",
            "short_name": f"{away['short']} @ {home['short']}",
            "home": home,
            "away": away,
            "status": status,
            "broadcasts": broadcasts,
            "venue": venue,
            "recap": recap,
            "link": "",
        })

    return events


# ── Main aggregator ──────────────────────────────────────

async def get_todays_games(
    date_str: Optional[str] = None,
    league_filter: Optional[str] = None,
    sport_filter: Optional[str] = None,
) -> list[dict]:
    """
    Fetch today's games across all configured leagues.
    ESPN for schedule + TheSportsDB Premium for TV channels.
    """
    if not date_str:
        now = datetime.now(TZ_MX)
        date_str = now.strftime("%Y%m%d")

    espn_tasks = []
    espn_slugs = []
    sportsdb_tasks = []
    sportsdb_slugs = []

    # Use ALL_LEAGUES when filtering specific league/sport, LEAGUES for homepage
    source = ALL_LEAGUES if (league_filter or sport_filter) else LEAGUES

    for slug, (sport, league, name, emoji) in source.items():
        if league_filter and slug != league_filter:
            continue
        if sport_filter and sport != sport_filter:
            continue
        if slug in ("boxeo", "motogp"):
            continue  # sin scoreboard ESPN: viven en events_api (/evento/)
        if slug in SPORTSDB_ONLY_LEAGUES:
            # TheSportsDB-only league — skip ESPN entirely
            sportsdb_tasks.append(parse_sportsdb_standalone_events(slug, date_str))
            sportsdb_slugs.append(slug)
        else:
            espn_tasks.append(fetch_espn_scoreboard(sport, league, date_str))
            espn_slugs.append(slug)

    # Fetch ESPN and TheSportsDB in parallel
    all_tasks = espn_tasks + sportsdb_tasks
    results = await asyncio.gather(*all_tasks, return_exceptions=True)

    espn_results = results[:len(espn_tasks)]
    sportsdb_results = results[len(espn_tasks):]

    all_events = []

    # Process ESPN results
    for slug, result in zip(espn_slugs, espn_results):
        if isinstance(result, Exception):
            logger.error(f"Error fetching {slug}: {result}")
            continue
        # Use enriched parser with TheSportsDB TV data
        events = await parse_espn_events_enriched(result, slug, date_str)
        all_events.extend(events)

    # Process TheSportsDB-only results (already parsed)
    for slug, result in zip(sportsdb_slugs, sportsdb_results):
        if isinstance(result, Exception):
            logger.error(f"Error fetching TheSportsDB {slug}: {result}")
            continue
        all_events.extend(result)

    all_events.sort(key=lambda e: e.get("date", ""))

    # ── Persist to DB (fire-and-forget) ──
    try:
        from db import persist_games
        asyncio.create_task(persist_games(all_events, date_str))
    except Exception:
        pass  # DB unavailable — degrade gracefully

    return all_events


async def search_games(query: str, date_str: Optional[str] = None) -> list[dict]:
    """Search for games matching a query (team name, league, etc.)"""
    query_lower = query.lower().strip()

    # Expand aliases: "chivas" -> also search "guadalajara"
    search_terms = [query_lower]
    alias_target = TEAM_ALIASES.get(query_lower)
    if alias_target:
        search_terms.append(alias_target.lower())

    # Also check if query is part of a multi-word alias key
    for alias_key, alias_val in TEAM_ALIASES.items():
        if query_lower in alias_key and alias_key != query_lower:
            search_terms.append(alias_val.lower())

    all_games = await get_todays_games(date_str=date_str)

    matches = []
    for game in all_games:
        searchable = " ".join([
            game["home"]["name"],
            game["away"]["name"],
            game["home"]["short"],
            game["away"]["short"],
            game["league_name"],
            game["league_slug"],
            game["name"],
        ]).lower()

        if any(term in searchable for term in search_terms):
            matches.append(game)

    return matches


# ── Team Stats & Standings ──────────────────────────────

# Map team slugs to ESPN sport/league for standings lookup
TEAM_LEAGUE_MAP = {
    # Liga MX
    "chivas": ("soccer", "mex.1"), "america": ("soccer", "mex.1"),
    "cruz-azul": ("soccer", "mex.1"), "pumas": ("soccer", "mex.1"),
    "tigres": ("soccer", "mex.1"), "monterrey": ("soccer", "mex.1"),
    "toluca": ("soccer", "mex.1"), "santos": ("soccer", "mex.1"),
    "leon": ("soccer", "mex.1"), "pachuca": ("soccer", "mex.1"),
    "atlas": ("soccer", "mex.1"), "necaxa": ("soccer", "mex.1"),
    "puebla": ("soccer", "mex.1"), "queretaro": ("soccer", "mex.1"),
    "mazatlan": ("soccer", "mex.1"), "tijuana": ("soccer", "mex.1"),
    "juarez": ("soccer", "mex.1"),
    # Premier League
    "liverpool": ("soccer", "eng.1"), "manchester-city": ("soccer", "eng.1"),
    "manchester-united": ("soccer", "eng.1"), "arsenal": ("soccer", "eng.1"),
    "chelsea": ("soccer", "eng.1"),
    # La Liga
    "real-madrid": ("soccer", "esp.1"), "barcelona": ("soccer", "esp.1"),
    # Serie A
    "juventus": ("soccer", "ita.1"), "inter-milan": ("soccer", "ita.1"),
    # Bundesliga
    "bayern": ("soccer", "ger.1"),
    # Ligue 1
    "psg": ("soccer", "fra.1"),
    # MLS
    "lafc": ("soccer", "usa.1"), "la-galaxy": ("soccer", "usa.1"),
    "inter-miami": ("soccer", "usa.1"), "austin-fc": ("soccer", "usa.1"),
    "houston-dynamo": ("soccer", "usa.1"), "fc-dallas": ("soccer", "usa.1"),
    # Europa extra
    "atletico-madrid": ("soccer", "esp.1"), "ac-milan": ("soccer", "ita.1"),
    "napoli": ("soccer", "ita.1"), "borussia-dortmund": ("soccer", "ger.1"),
    "tottenham": ("soccer", "eng.1"), "aston-villa": ("soccer", "eng.1"),
    # NBA
    "lakers": ("basketball", "nba"), "celtics": ("basketball", "nba"),
    "warriors": ("basketball", "nba"), "bulls": ("basketball", "nba"),
    "heat": ("basketball", "nba"), "knicks": ("basketball", "nba"),
    "nuggets": ("basketball", "nba"), "bucks": ("basketball", "nba"),
    "mavericks": ("basketball", "nba"), "clippers": ("basketball", "nba"),
    "suns": ("basketball", "nba"), "spurs-nba": ("basketball", "nba"),
    "76ers": ("basketball", "nba"), "thunder": ("basketball", "nba"),
    "timberwolves": ("basketball", "nba"), "cavaliers": ("basketball", "nba"),
    # NFL (all 32 teams)
    "cowboys": ("football", "nfl"), "chiefs": ("football", "nfl"),
    "49ers": ("football", "nfl"), "eagles": ("football", "nfl"),
    "packers": ("football", "nfl"), "steelers": ("football", "nfl"),
    "raiders": ("football", "nfl"), "dolphins": ("football", "nfl"),
    "patriots": ("football", "nfl"), "texans": ("football", "nfl"),
    "ravens": ("football", "nfl"), "bears": ("football", "nfl"),
    "rams": ("football", "nfl"), "chargers": ("football", "nfl"),
    "broncos": ("football", "nfl"), "bills": ("football", "nfl"),
    "lions": ("football", "nfl"), "vikings": ("football", "nfl"),
    "bengals": ("football", "nfl"), "giants-nfl": ("football", "nfl"),
    "jets": ("football", "nfl"), "saints": ("football", "nfl"),
    "seahawks": ("football", "nfl"), "commanders": ("football", "nfl"),
    "cardinals-nfl": ("football", "nfl"), "buccaneers": ("football", "nfl"),
    "falcons": ("football", "nfl"), "panthers-nfl": ("football", "nfl"),
    "colts": ("football", "nfl"), "jaguars": ("football", "nfl"),
    "titans": ("football", "nfl"),
    # NHL
    "bruins": ("hockey", "nhl"), "golden-knights": ("hockey", "nhl"),
    "avalanche": ("hockey", "nhl"), "panthers-nhl": ("hockey", "nhl"),
    "rangers-nhl": ("hockey", "nhl"), "maple-leafs": ("hockey", "nhl"),
    "oilers": ("hockey", "nhl"), "stars": ("hockey", "nhl"),
    "blackhawks": ("hockey", "nhl"), "penguins": ("hockey", "nhl"),
    "capitals": ("hockey", "nhl"),
    # MLB
    "dodgers": ("baseball", "mlb"), "yankees": ("baseball", "mlb"),
    "red-sox": ("baseball", "mlb"), "astros": ("baseball", "mlb"),
    "mets": ("baseball", "mlb"), "padres": ("baseball", "mlb"),
    "angels": ("baseball", "mlb"), "athletics": ("baseball", "mlb"),
    "blue-jays": ("baseball", "mlb"), "braves": ("baseball", "mlb"),
    "brewers": ("baseball", "mlb"), "cardinals": ("baseball", "mlb"),
    "cubs": ("baseball", "mlb"), "diamondbacks": ("baseball", "mlb"),
    "giants": ("baseball", "mlb"), "guardians": ("baseball", "mlb"),
    "mariners": ("baseball", "mlb"), "marlins": ("baseball", "mlb"),
    "nationals": ("baseball", "mlb"), "orioles": ("baseball", "mlb"),
    "phillies": ("baseball", "mlb"), "pirates": ("baseball", "mlb"),
    "rangers": ("baseball", "mlb"), "rays": ("baseball", "mlb"),
    "reds": ("baseball", "mlb"), "rockies": ("baseball", "mlb"),
    "royals": ("baseball", "mlb"), "tigers": ("baseball", "mlb"),
    "twins": ("baseball", "mlb"), "white-sox": ("baseball", "mlb"),
    # ── Premier League (remaining) ──
    "bournemouth": ("soccer", "eng.1"), "brentford": ("soccer", "eng.1"),
    "brighton": ("soccer", "eng.1"), "crystal-palace": ("soccer", "eng.1"),
    "everton": ("soccer", "eng.1"), "fulham": ("soccer", "eng.1"),
    "ipswich": ("soccer", "eng.1"), "leicester": ("soccer", "eng.1"),
    "newcastle": ("soccer", "eng.1"), "nottingham-forest": ("soccer", "eng.1"),
    "southampton": ("soccer", "eng.1"), "west-ham": ("soccer", "eng.1"),
    "wolverhampton": ("soccer", "eng.1"),
    # ── La Liga (remaining) ──
    "alaves": ("soccer", "esp.1"), "athletic-club": ("soccer", "esp.1"),
    "celta-vigo": ("soccer", "esp.1"), "espanyol": ("soccer", "esp.1"),
    "getafe": ("soccer", "esp.1"), "girona": ("soccer", "esp.1"),
    "las-palmas": ("soccer", "esp.1"), "leganes": ("soccer", "esp.1"),
    "mallorca": ("soccer", "esp.1"), "osasuna": ("soccer", "esp.1"),
    "rayo-vallecano": ("soccer", "esp.1"), "real-betis": ("soccer", "esp.1"),
    "real-sociedad": ("soccer", "esp.1"), "real-valladolid": ("soccer", "esp.1"),
    "sevilla": ("soccer", "esp.1"), "valencia": ("soccer", "esp.1"),
    "villarreal": ("soccer", "esp.1"),
    # ── Serie A (remaining) ──
    "as-roma": ("soccer", "ita.1"), "atalanta": ("soccer", "ita.1"),
    "bologna": ("soccer", "ita.1"), "cagliari": ("soccer", "ita.1"),
    "como": ("soccer", "ita.1"), "empoli": ("soccer", "ita.1"),
    "fiorentina": ("soccer", "ita.1"), "genoa": ("soccer", "ita.1"),
    "lazio": ("soccer", "ita.1"), "lecce": ("soccer", "ita.1"),
    "monza": ("soccer", "ita.1"), "parma": ("soccer", "ita.1"),
    "torino": ("soccer", "ita.1"), "udinese": ("soccer", "ita.1"),
    "venezia": ("soccer", "ita.1"), "verona": ("soccer", "ita.1"),
    # ── Bundesliga (remaining) ──
    "augsburg": ("soccer", "ger.1"), "frankfurt": ("soccer", "ger.1"),
    "freiburg": ("soccer", "ger.1"), "heidenheim": ("soccer", "ger.1"),
    "hoffenheim": ("soccer", "ger.1"), "koln": ("soccer", "ger.1"),
    "leverkusen": ("soccer", "ger.1"), "mainz": ("soccer", "ger.1"),
    "gladbach": ("soccer", "ger.1"), "rb-leipzig": ("soccer", "ger.1"),
    "st-pauli": ("soccer", "ger.1"), "stuttgart": ("soccer", "ger.1"),
    "union-berlin": ("soccer", "ger.1"), "werder-bremen": ("soccer", "ger.1"),
    "wolfsburg": ("soccer", "ger.1"),
    # ── Ligue 1 (remaining) ──
    "angers": ("soccer", "fra.1"), "auxerre": ("soccer", "fra.1"),
    "brest": ("soccer", "fra.1"), "le-havre": ("soccer", "fra.1"),
    "lens": ("soccer", "fra.1"), "lille": ("soccer", "fra.1"),
    "lyon": ("soccer", "fra.1"), "marseille": ("soccer", "fra.1"),
    "monaco": ("soccer", "fra.1"), "montpellier": ("soccer", "fra.1"),
    "nantes": ("soccer", "fra.1"), "nice": ("soccer", "fra.1"),
    "reims": ("soccer", "fra.1"), "rennes": ("soccer", "fra.1"),
    "strasbourg": ("soccer", "fra.1"), "toulouse": ("soccer", "fra.1"),
    # ── Liga Portugal ──
    "arouca": ("soccer", "por.1"), "benfica": ("soccer", "por.1"),
    "braga": ("soccer", "por.1"), "casa-pia": ("soccer", "por.1"),
    "estoril": ("soccer", "por.1"), "estrela": ("soccer", "por.1"),
    "famalicao": ("soccer", "por.1"), "gil-vicente": ("soccer", "por.1"),
    "guimaraes": ("soccer", "por.1"), "moreirense": ("soccer", "por.1"),
    "nacional": ("soccer", "por.1"), "porto": ("soccer", "por.1"),
    "rio-ave": ("soccer", "por.1"), "santa-clara": ("soccer", "por.1"),
    "sporting-cp": ("soccer", "por.1"),
    # ── Eredivisie ──
    "ajax": ("soccer", "ned.1"), "az-alkmaar": ("soccer", "ned.1"),
    "fc-twente": ("soccer", "ned.1"), "fc-utrecht": ("soccer", "ned.1"),
    "feyenoord": ("soccer", "ned.1"), "fortuna-sittard": ("soccer", "ned.1"),
    "go-ahead-eagles": ("soccer", "ned.1"), "groningen": ("soccer", "ned.1"),
    "heerenveen": ("soccer", "ned.1"), "heracles": ("soccer", "ned.1"),
    "nac-breda": ("soccer", "ned.1"), "nec": ("soccer", "ned.1"),
    "psv": ("soccer", "ned.1"), "sparta-rotterdam": ("soccer", "ned.1"),
    # ── Liga Colombia ──
    "america-cali": ("soccer", "col.1"), "atletico-nacional": ("soccer", "col.1"),
    "bucaramanga": ("soccer", "col.1"), "deportes-tolima": ("soccer", "col.1"),
    "deportivo-cali": ("soccer", "col.1"), "deportivo-pasto": ("soccer", "col.1"),
    "deportivo-pereira": ("soccer", "col.1"), "independiente-medellin": ("soccer", "col.1"),
    "independiente-santa-fe": ("soccer", "col.1"), "junior-barranquilla": ("soccer", "col.1"),
    "millonarios": ("soccer", "col.1"), "once-caldas": ("soccer", "col.1"),
    # ── Liga Argentina ──
    "argentinos-juniors": ("soccer", "arg.1"), "banfield": ("soccer", "arg.1"),
    "belgrano": ("soccer", "arg.1"), "boca-juniors": ("soccer", "arg.1"),
    "defensa-y-justicia": ("soccer", "arg.1"), "estudiantes": ("soccer", "arg.1"),
    "gimnasia-lp": ("soccer", "arg.1"), "huracan": ("soccer", "arg.1"),
    "independiente-arg": ("soccer", "arg.1"), "lanus": ("soccer", "arg.1"),
    "newells": ("soccer", "arg.1"), "platense": ("soccer", "arg.1"),
    "racing-club": ("soccer", "arg.1"), "river-plate": ("soccer", "arg.1"),
    "rosario-central": ("soccer", "arg.1"), "san-lorenzo": ("soccer", "arg.1"),
    "talleres": ("soccer", "arg.1"), "tigre": ("soccer", "arg.1"),
    "velez-sarsfield": ("soccer", "arg.1"),
    # ── LigaPro Ecuador ──
    "aucas": ("soccer", "ecu.1"), "barcelona-sc": ("soccer", "ecu.1"),
    "delfin": ("soccer", "ecu.1"), "deportivo-cuenca": ("soccer", "ecu.1"),
    "emelec": ("soccer", "ecu.1"), "independiente-del-valle": ("soccer", "ecu.1"),
    "ldu-quito": ("soccer", "ecu.1"), "mushuc-runa": ("soccer", "ecu.1"),
    "orense": ("soccer", "ecu.1"), "tecnico-universitario": ("soccer", "ecu.1"),
    # ── Primera Chile ──
    "audax-italiano": ("soccer", "chi.1"), "cobreloa": ("soccer", "chi.1"),
    "cobresal": ("soccer", "chi.1"), "colo-colo": ("soccer", "chi.1"),
    "everton-chile": ("soccer", "chi.1"), "huachipato": ("soccer", "chi.1"),
    "ohiggins": ("soccer", "chi.1"), "palestino": ("soccer", "chi.1"),
    "universidad-catolica": ("soccer", "chi.1"), "universidad-chile": ("soccer", "chi.1"),
    # ── Liga 1 Perú ──
    "alianza-lima": ("soccer", "per.1"), "cienciano": ("soccer", "per.1"),
    "cusco-fc": ("soccer", "per.1"), "melgar": ("soccer", "per.1"),
    "sport-boys": ("soccer", "per.1"), "sport-huancayo": ("soccer", "per.1"),
    "sporting-cristal": ("soccer", "per.1"), "universitario": ("soccer", "per.1"),
    # ── Liga MX Femenil (ESPN) ──
    "america-femenil": ("soccer", "mex.w1"), "chivas-femenil": ("soccer", "mex.w1"),
    "tigres-femenil": ("soccer", "mex.w1"), "monterrey-femenil": ("soccer", "mex.w1"),
    "cruz-azul-femenil": ("soccer", "mex.w1"), "pumas-femenil": ("soccer", "mex.w1"),
    "pachuca-femenil": ("soccer", "mex.w1"), "toluca-femenil": ("soccer", "mex.w1"),
    "santos-femenil": ("soccer", "mex.w1"), "atlas-femenil": ("soccer", "mex.w1"),
    "leon-femenil": ("soccer", "mex.w1"), "tijuana-femenil": ("soccer", "mex.w1"),
    "necaxa-femenil": ("soccer", "mex.w1"), "puebla-femenil": ("soccer", "mex.w1"),
    "queretaro-femenil": ("soccer", "mex.w1"), "mazatlan-femenil": ("soccer", "mex.w1"),
    "juarez-femenil": ("soccer", "mex.w1"), "san-luis-femenil": ("soccer", "mex.w1"),
    # ── LMP (TheSportsDB) ──
    "aguilas-mexicali": ("baseball", "sportsdb:5109"),
    "algodoneros-guasave": ("baseball", "sportsdb:5109"),
    "caneros-los-mochis": ("baseball", "sportsdb:5109"),
    "charros-jalisco": ("baseball", "sportsdb:5109"),
    "mayos-navojoa": ("baseball", "sportsdb:5109"),
    "naranjeros-hermosillo": ("baseball", "sportsdb:5109"),
    "sultanes-monterrey-lmp": ("baseball", "sportsdb:5109"),
    "tomateros-culiacan": ("baseball", "sportsdb:5109"),
    "venados-mazatlan": ("baseball", "sportsdb:5109"),
    "yaquis-obregon": ("baseball", "sportsdb:5109"),
    # ── LMB (TheSportsDB) ──
    "acereros-monclova": ("baseball", "sportsdb:5064"),
    "bravos-leon": ("baseball", "sportsdb:5064"),
    "diablos-rojos": ("baseball", "sportsdb:5064"),
    "generales-durango": ("baseball", "sportsdb:5064"),
    "guerreros-oaxaca": ("baseball", "sportsdb:5064"),
    "leones-yucatan": ("baseball", "sportsdb:5064"),
    "mariachis-guadalajara": ("baseball", "sportsdb:5064"),
    "olmecas-tabasco": ("baseball", "sportsdb:5064"),
    "pericos-puebla": ("baseball", "sportsdb:5064"),
    "rieleros-aguascalientes": ("baseball", "sportsdb:5064"),
    "saraperos-saltillo": ("baseball", "sportsdb:5064"),
    "sultanes-monterrey-lmb": ("baseball", "sportsdb:5064"),
    "tecolotes-dos-laredos": ("baseball", "sportsdb:5064"),
    "tigres-quintana-roo": ("baseball", "sportsdb:5064"),
    "toros-tijuana": ("baseball", "sportsdb:5064"),
    # ── LNBP (TheSportsDB) ──
    "astros-jalisco": ("basketball", "sportsdb:5119"),
    "abejas-leon": ("basketball", "sportsdb:5119"),
    "fuerza-regia": ("basketball", "sportsdb:5119"),
    "capitanes-cdmx": ("basketball", "sportsdb:5119"),
    "soles-mexicali": ("basketball", "sportsdb:5119"),
    "libertadores-queretaro": ("basketball", "sportsdb:5119"),
    "plateros-fresnillo": ("basketball", "sportsdb:5119"),
    "dorados-chihuahua": ("basketball", "sportsdb:5119"),
    "correcaminos-uam": ("basketball", "sportsdb:5119"),
    "lenadores-durango": ("basketball", "sportsdb:5119"),
    # LVBP (Liga Venezolana de Béisbol Profesional)
    "navegantes-del-magallanes": ("baseball", "sportsdb:5112"),
    "leones-del-caracas": ("baseball", "sportsdb:5112"),
    "tigres-de-aragua": ("baseball", "sportsdb:5112"),
    "cardenales-de-lara": ("baseball", "sportsdb:5112"),
    "aguilas-del-zulia": ("baseball", "sportsdb:5112"),
    "tiburones-de-la-guaira": ("baseball", "sportsdb:5112"),
    "caribes-de-anzoategui": ("baseball", "sportsdb:5112"),
    "bravos-de-margarita": ("baseball", "sportsdb:5112"),
    # LIDOM (Liga Dominicana de Béisbol Invernal) — no TheSportsDB data
    "tigres-del-licey": ("baseball", "sportsdb:lidom"),
    "aguilas-cibaenas": ("baseball", "sportsdb:lidom"),
    "leones-del-escogido": ("baseball", "sportsdb:lidom"),
    "estrellas-orientales": ("baseball", "sportsdb:lidom"),
    "gigantes-del-cibao": ("baseball", "sportsdb:lidom"),
    "toros-del-este": ("baseball", "sportsdb:lidom"),
    # ── F1 (pilotos) ──
    "checo-perez": ("racing", "f1"),
    "verstappen": ("racing", "f1"),
    "hamilton": ("racing", "f1"),
    "sainz": ("racing", "f1"),
    "norris": ("racing", "f1"),
    "leclerc": ("racing", "f1"),
    "alonso": ("racing", "f1"),
    # ── UFC (peleadores) ──
    "brandon-moreno": ("mma", "ufc"),
    "alexa-grasso": ("mma", "ufc"),
    "islam-makhachev": ("mma", "ufc"),
    "alex-pereira": ("mma", "ufc"),
    "jon-jones": ("mma", "ufc"),
    "ilia-topuria": ("mma", "ufc"),
}

# ── TheSportsDB helpers for team pages ──

_sportsdb_events_cache = TTLCache(maxsize=20, ttl=1800)  # 30 min
_sportsdb_standings_cache = TTLCache(maxsize=10, ttl=3600)  # 1 hour
_sportsdb_team_cache = TTLCache(maxsize=60, ttl=86400)  # 24 hours

# Season format per league: "summer" = YYYY, "winter" = YYYY-YYYY+1
SPORTSDB_SEASON_FORMAT = {
    "5109": "winter",  # LMP: Oct-Jan
    "5064": "summer",  # LMB: Apr-Aug
    "5119": "winter",  # LNBP: Feb-Jun (spans year boundary in naming)
    "5112": "winter",  # LVBP: Oct-Feb
}


def _get_sportsdb_season(league_id: str) -> str:
    """Compute current season string for a TheSportsDB league."""
    now = datetime.now(TZ_MX)
    fmt = SPORTSDB_SEASON_FORMAT.get(league_id, "summer")
    if fmt == "winter":
        # Winter leagues: if month >= Oct, season is YYYY-YYYY+1; else YYYY-1-YYYY
        if now.month >= 10:
            return f"{now.year}-{now.year + 1}"
        else:
            return f"{now.year - 1}-{now.year}"
    else:
        return str(now.year)


async def compute_sportsdb_standings(league_id: str) -> list[dict]:
    """
    Compute W/L standings from TheSportsDB season events.
    Returns list of dicts compatible with fetch_standings() output.
    """
    cache_key = f"sdb_standings:{league_id}"
    if cache_key in _sportsdb_standings_cache:
        return _sportsdb_standings_cache[cache_key]

    season = _get_sportsdb_season(league_id)
    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/eventsseason.php?id={league_id}&s={season}"

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"SportsDB season events error for {league_id}/{season}: {e}")
            return []

    events = data.get("events") or []
    teams = {}  # team_name → {w, l, team_id, badge}

    for ev in events:
        if ev.get("strStatus") != "FT":
            continue
        hs = ev.get("intHomeScore")
        aws = ev.get("intAwayScore")
        if hs is None or aws is None:
            continue
        try:
            hs, aws = int(hs), int(aws)
        except (ValueError, TypeError):
            continue

        home = ev.get("strHomeTeam", "")
        away = ev.get("strAwayTeam", "")

        if home not in teams:
            teams[home] = {"w": 0, "l": 0, "team_id": ev.get("idHomeTeam", ""), "badge": ev.get("strHomeTeamBadge", "")}
        if away not in teams:
            teams[away] = {"w": 0, "l": 0, "team_id": ev.get("idAwayTeam", ""), "badge": ev.get("strAwayTeamBadge", "")}

        if hs > aws:
            teams[home]["w"] += 1
            teams[away]["l"] += 1
        elif aws > hs:
            teams[away]["w"] += 1
            teams[home]["l"] += 1
        # ties: neither gets W or L (rare in baseball/basketball)

    # Sort by win pct descending
    entries = []
    sorted_teams = sorted(teams.items(), key=lambda x: x[1]["w"] / max(x[1]["w"] + x[1]["l"], 1), reverse=True)

    for rank, (name, stats) in enumerate(sorted_teams, 1):
        total = stats["w"] + stats["l"]
        pct = f"{stats['w'] / total:.3f}" if total > 0 else ".000"
        entries.append({
            "team_id": stats["team_id"],
            "team_name": name,
            "team_short": name.split(" de ")[0] if " de " in name else name.split()[-1],
            "team_logo": stats["badge"],
            "group": "",
            "rank": str(rank),
            "wins": str(stats["w"]),
            "losses": str(stats["l"]),
            "ties": "",
            "points": "",
            "games_played": str(total),
            "goals_for": "",
            "goals_against": "",
            "goal_diff": "",
            "win_pct": pct,
            "streak": "",
            "record": f"{stats['w']}-{stats['l']}",
            "all_stats": {"wins": str(stats["w"]), "losses": str(stats["l"]), "winPercent": pct},
        })

    _sportsdb_standings_cache[cache_key] = entries
    logger.info(f"Computed {len(entries)} standings for SportsDB league {league_id} ({season})")
    return entries


async def fetch_sportsdb_team_info(team_id: str) -> dict:
    """Fetch rich team info from TheSportsDB lookupteam endpoint."""
    cache_key = f"sdb_team:{team_id}"
    if cache_key in _sportsdb_team_cache:
        return _sportsdb_team_cache[cache_key]

    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/lookupteam.php?id={team_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"SportsDB team lookup error for {team_id}: {e}")
            return {}

    teams = data.get("teams") or []
    if not teams:
        return {}

    t = teams[0]
    info = {
        "team_id": t.get("idTeam", ""),
        "name": t.get("strTeam", ""),
        "stadium": t.get("strStadium", ""),
        "location": t.get("strLocation", ""),
        "stadium_capacity": t.get("intStadiumCapacity", ""),
        "formed_year": t.get("intFormedYear", ""),
        "description": t.get("strDescriptionES") or t.get("strDescriptionEN") or "",
        "badge": t.get("strBadge", ""),
        "jersey": t.get("strJersey", ""),
        "country": t.get("strCountry", ""),
        "website": t.get("strWebsite", ""),
        "facebook": t.get("strFacebook", ""),
        "twitter": t.get("strTwitter", ""),
        "instagram": t.get("strInstagram", ""),
    }
    _sportsdb_team_cache[cache_key] = info
    return info


async def fetch_sportsdb_past_events(league_id: str) -> list[dict]:
    """Fetch last 15 completed events from TheSportsDB for a league."""
    cache_key = f"sdb_past:{league_id}"
    if cache_key in _sportsdb_events_cache:
        return _sportsdb_events_cache[cache_key]

    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/eventspastleague.php?id={league_id}"
    async with httpx.AsyncClient(timeout=12) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"SportsDB past events error for league {league_id}: {e}")
            return []

    events = data.get("events") or []
    results = []
    for ev in events:
        results.append({
            "id": ev.get("idEvent", ""),
            "home": ev.get("strHomeTeam", ""),
            "away": ev.get("strAwayTeam", ""),
            "home_score": ev.get("intHomeScore", "0"),
            "away_score": ev.get("intAwayScore", "0"),
            "home_logo": ev.get("strHomeTeamBadge", ""),
            "away_logo": ev.get("strAwayTeamBadge", ""),
            "date": ev.get("strTimestamp") or ev.get("dateEvent", ""),
        })
    _sportsdb_events_cache[cache_key] = results
    return results


async def fetch_sportsdb_next_events(league_id: str) -> list[dict]:
    """Fetch next 15 upcoming events from TheSportsDB for a league."""
    cache_key = f"sdb_next:{league_id}"
    if cache_key in _sportsdb_events_cache:
        return _sportsdb_events_cache[cache_key]

    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}/eventsnextleague.php?id={league_id}"
    async with httpx.AsyncClient(timeout=12) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"SportsDB next events error for league {league_id}: {e}")
            return []

    # Default channels for this league (TheSportsDB rarely has TV data for LatAm leagues)
    _slug = next((s for s, i in SPORTSDB_LEAGUE_MAP.items() if i == str(league_id)), "")
    _default_chs = []
    for ch in DEFAULT_LEAGUE_CHANNELS.get(_slug, []):
        info = CHANNEL_ALIASES.get(ch, {})
        _default_chs.append({"name": info.get("name", ch), "country": info.get("country", "MX")})

    events = data.get("events") or []
    results = []
    for ev in events:
        chs = []
        tv = (ev.get("strTVStation") or "").strip()
        if tv:
            for c in tv.split(","):
                c = c.strip()
                if c:
                    info = CHANNEL_ALIASES.get(_normalize_channel(c), CHANNEL_ALIASES.get(c, {}))
                    chs.append({"name": info.get("name", c), "country": info.get("country", "")})
        results.append({
            "id": ev.get("idEvent", ""),
            "home": ev.get("strHomeTeam", ""),
            "away": ev.get("strAwayTeam", ""),
            "home_logo": ev.get("strHomeTeamBadge", ""),
            "away_logo": ev.get("strAwayTeamBadge", ""),
            "date_utc": ev.get("strTimestamp") or ev.get("dateEvent", ""),
            "date": ev.get("strTimestamp") or ev.get("dateEvent", ""),
            "channels": chs or list(_default_chs),
        })
    _sportsdb_events_cache[cache_key] = results
    return results


# Standings cache: 1 hour TTL
_standings_cache = TTLCache(maxsize=15, ttl=3600)  # ~15 leagues


async def fetch_standings(sport: str, league: str) -> list[dict]:
    """
    Fetch standings from ESPN API.
    Returns list of team entries with position, record, stats.
    """
    cache_key = f"standings:{sport}:{league}"
    if cache_key in _standings_cache:
        return _standings_cache[cache_key]

    url = f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Standings error for {sport}/{league}: {e}")
            return []

    entries = []

    # ESPN returns standings in different structures depending on sport
    standings_data = []
    if "children" in data:
        # Soccer leagues, NFL, MLB (divisions/groups)
        for group in data["children"]:
            group_name = group.get("name", "")
            for entry in group.get("standings", {}).get("entries", []):
                entry["_group"] = group_name
                standings_data.append(entry)
    elif "standings" in data:
        standings_data = data.get("standings", {}).get("entries", [])

    for entry in standings_data:
        team = entry.get("team", {})
        raw_stats = entry.get("stats", [])

        # Convert stats list to dict for easy access
        stats = {}
        for s in raw_stats:
            name = s.get("name", "")
            val = s.get("displayValue", s.get("value", ""))
            stats[name] = val

        parsed = {
            "team_id": team.get("id", ""),
            "team_name": team.get("displayName", ""),
            "team_short": team.get("abbreviation", ""),
            "team_logo": team.get("logos", [{}])[0].get("href", "") if team.get("logos") else "",
            "group": entry.get("_group", ""),
            # Soccer stats
            "rank": stats.get("rank", ""),
            "wins": stats.get("wins", ""),
            "losses": stats.get("losses", ""),
            "ties": stats.get("ties", stats.get("draws", "")),
            "points": stats.get("points", ""),
            "games_played": stats.get("gamesPlayed", ""),
            "goals_for": stats.get("pointsFor", stats.get("goalsFor", "")),
            "goals_against": stats.get("pointsAgainst", stats.get("goalsAgainst", "")),
            "goal_diff": stats.get("pointDifferential", stats.get("goalDifference", "")),
            # US sports stats
            "win_pct": stats.get("winPercent", stats.get("winPct", "")),
            "streak": stats.get("streak", ""),
            "record": stats.get("overall", stats.get("record", "")),
            "all_stats": stats,
        }
        entries.append(parsed)

    _standings_cache[cache_key] = entries
    logger.info(f"Fetched {len(entries)} standings entries for {sport}/{league}")
    return entries


async def get_team_stats(team_slug: str) -> dict:
    """
    Get stats for a specific team: standing position, record, form.
    Returns a dict with the team's stats or empty dict if not found.
    """
    league_info = TEAM_LEAGUE_MAP.get(team_slug)
    if not league_info:
        return {}

    sport, league = league_info
    # TheSportsDB-only leagues: compute standings from season events
    if league.startswith("sportsdb:"):
        league_id = league.split(":")[1]
        standings = await compute_sportsdb_standings(league_id)
    else:
        standings = await fetch_standings(sport, league)
    if not standings:
        return {}

    # Resolve team name from slug
    from config import TEAM_ALIASES
    team_name_search = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " ")).lower()

    # Find the team in standings
    for entry in standings:
        entry_name = entry["team_name"].lower()
        entry_short = entry["team_short"].lower()
        if (team_name_search in entry_name or
            entry_name in team_name_search or
            team_slug.replace("-", "") in entry_name.replace(" ", "") or
            team_name_search in entry_short):
            # Determine sport type for formatting
            entry["sport_type"] = sport
            entry["league_id"] = league
            return entry

    return {}


async def fetch_team_news(sport: str, league: str, team_name: str, limit: int = 6) -> list[dict]:
    """
    Fetch recent news articles for a team from ESPN.
    Returns list of dicts with headline, description, link, image, published.
    """
    # ESPN news endpoint — search by league
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/news"
    articles = []

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params={"limit": 30})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"News fetch error for {sport}/{league}: {e}")
            return []

    team_lower = team_name.lower()
    team_words = [w for w in team_lower.split() if len(w) > 3]

    for item in data.get("articles", []):
        headline = item.get("headline", "")
        description = item.get("description", "")
        text_check = (headline + " " + description).lower()

        # Check if article mentions this team
        matched = any(word in text_check for word in team_words)
        if not matched:
            # Also check categories
            for cat in item.get("categories", []):
                if cat.get("description", "").lower() in team_lower or team_lower in cat.get("description", "").lower():
                    matched = True
                    break

        if matched:
            img = ""
            for image in item.get("images", []):
                img = image.get("url", "")
                break

            articles.append({
                "headline": headline,
                "description": description[:120] + "..." if len(description) > 120 else description,
                "link": item.get("links", {}).get("web", {}).get("href", ""),
                "image": img,
                "published": item.get("published", ""),
            })
            if len(articles) >= limit:
                break

    return articles


async def get_league_standings(sport: str, league: str, limit: int = 10) -> list[dict]:
    """Get top N standings for a league."""
    if league.startswith("sportsdb:"):
        league_id = league.split(":")[1]
        standings = await compute_sportsdb_standings(league_id)
        return standings[:limit]
    standings = await fetch_standings(sport, league)
    return standings[:limit]


# ── League Leaders ──────────────────────────────────────

# Categories to fetch per sport (name in ESPN API → display label in Spanish)
_LEADER_CATEGORIES = {
    "soccer": [
        ("goals", "Goles", "⚽"),
        ("assists", "Asistencias", "🅰️"),
        ("yellowCards", "Tarjetas amarillas", "🟨"),
    ],
    "football": [
        ("passingYards", "Yardas por pase", "🏈"),
        ("rushingYards", "Yardas por tierra", "🏃"),
        ("receivingYards", "Yardas recibidas", "🙌"),
    ],
    "basketball": [
        ("avgPoints", "Puntos por juego", "🏀"),
        ("avgRebounds", "Rebotes por juego", "📊"),
        ("avgAssists", "Asistencias por juego", "🅰️"),
    ],
    "baseball": [
        ("homeRuns", "Home runs", "💣"),
        ("RBIs", "Carreras impulsadas", "🔥"),
        ("ERA", "ERA", "⚾"),
    ],
    "hockey": [
        ("points", "Puntos", "🏒"),
        ("goals", "Goles", "🥅"),
        ("assists", "Asistencias", "🅰️"),
    ],
}

_leaders_cache: dict = TTLCache(maxsize=20, ttl=3600)  # 1 hour


async def fetch_league_leaders(sport: str, league: str, top_n: int = 5) -> list[dict]:
    """
    Fetch league stat leaders from ESPN.
    Returns list of category dicts: {name, label, emoji, leaders: [{name, value, flag, position, headshot}]}
    Only for ESPN leagues (not TheSportsDB).
    """
    if league.startswith("sportsdb:"):
        return []

    # Normalize sport for ESPN API and category lookup
    # college-football uses sport="football" in ESPN URL path
    espn_sport = "football" if sport == "college-football" else sport

    cache_key = f"leaders:{sport}:{league}"
    if cache_key in _leaders_cache:
        return _leaders_cache[cache_key]

    url = f"https://site.api.espn.com/apis/site/v3/sports/{espn_sport}/{league}/leaders"

    # ── Qué temporada pedir ───────────────────────────────────────────────────
    # Sin parámetros, ESPN devuelve basura para el fútbol. Comprobado el
    # 17/09/2026 contra la jornada 8 del Apertura:
    #   Liga MX  sin params -> Paulinho 3 goles   (viejo, de otro torneo)
    #            con params -> Salomón Rondón 8   (correcto)
    #   MLS      sin params -> nada               con params -> Messi 19
    #   Premier  sin params -> nada               con params -> Haaland 4
    # Y al revés en las ligas de EE.UU.: MLB, NFL, NBA y NHL responden bien sin
    # parámetros y devuelven vacío con ellos. Por eso la regla es por deporte.
    #
    # Se prueba también el año anterior porque las temporadas europeas cruzan el
    # año: en enero, Premier 2026-27 sigue siendo season=2026 para ESPN.
    if espn_sport == "soccer":
        _y = datetime.now(timezone.utc).year
        intentos = [f"?season={_y}&seasontype=1", f"?season={_y - 1}&seasontype=1", ""]
    else:
        intentos = [""]

    raw_categories = []
    async with httpx.AsyncClient(timeout=15) as client:
        for _q in intentos:
            try:
                resp = await client.get(url + _q)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"Leaders error for {sport}/{league}{_q}: {e}")
                continue
            cats = data.get("leaders", {}).get("categories", [])
            # "Tiene categorías" no basta: ESPN devuelve el armazón vacío.
            if any(c.get("leaders") for c in cats):
                raw_categories = cats
                break

    if not raw_categories:
        return []

    # Map ESPN category names to our display config
    wanted = _LEADER_CATEGORIES.get(espn_sport, [])
    if not wanted:
        return []

    result = []
    for cat_name, label, emoji in wanted:
        cat = next((c for c in raw_categories if c.get("name") == cat_name), None)
        if not cat:
            continue
        leaders = []
        for entry in cat.get("leaders", [])[:top_n]:
            ath = entry.get("athlete", {})
            flag_obj = ath.get("flag", {})
            headshot_obj = ath.get("headshot", {})
            # El equipo NO cuelga del atleta, cuelga de la entrada. Por no
            # leerlo de ahi mostrabamos la NACIONALIDAD donde el lector espera
            # el club, y se lee como si Paulinho jugara en Portugal (juega en
            # Toluca) o Helinho en Brasil (Toluca tambien). El dato correcto
            # estaba en la respuesta todo este tiempo, sin usar.
            team_obj = entry.get("team", {}) or ath.get("team", {}) or {}
            leaders.append({
                "name": ath.get("displayName", ""),
                "value": entry.get("displayValue", str(entry.get("value", ""))),
                "num_value": entry.get("value", 0),
                "position": ath.get("position", {}).get("abbreviation", ""),
                "jersey": ath.get("jersey", ""),
                "team": team_obj.get("displayName") or team_obj.get("name") or "",
                "team_abbr": team_obj.get("abbreviation", ""),
                "team_logo": (team_obj.get("logos") or [{}])[0].get("href", "")
                             if team_obj.get("logos") else "",
                "flag": flag_obj.get("alt", ""),
                "flag_img": flag_obj.get("href", ""),
                "headshot": headshot_obj.get("href", ""),
            })
        if leaders:
            result.append({
                "name": cat_name,
                "label": label,
                "emoji": emoji,
                "leaders": leaders,
            })

    _leaders_cache[cache_key] = result
    logger.info(f"Fetched {len(result)} leader categories for {sport}/{league}")
    return result


async def generate_nfl_power_rankings() -> list[dict]:
    """
    Generate NFL Power Rankings based on standings data.
    Ranks all 32 teams by win percentage, point differential, and streak.
    Returns list sorted by power_rank with tier labels.
    """
    standings = await fetch_standings("football", "nfl")
    if not standings:
        return []

    # Don't generate rankings if no team has played any games (preseason)
    any_games_played = any(
        (int(t.get("wins") or 0) + int(t.get("losses") or 0)) > 0
        for t in standings
    )
    if not any_games_played:
        return []

    # Score each team: win_pct (50%) + normalized point_diff (30%) + streak bonus (20%)
    scored = []
    for team in standings:
        try:
            w = int(team.get("wins") or 0)
            l = int(team.get("losses") or 0)
            total = w + l
            win_pct = w / total if total > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            win_pct = 0.0

        # Point differential
        try:
            pts_for = float(team.get("goals_for") or team.get("all_stats", {}).get("pointsFor", 0))
            pts_against = float(team.get("goals_against") or team.get("all_stats", {}).get("pointsAgainst", 0))
            pt_diff = pts_for - pts_against
        except (ValueError, TypeError):
            pt_diff = 0.0

        # Streak bonus
        streak_str = str(team.get("streak", ""))
        streak_bonus = 0.0
        if streak_str.startswith("W"):
            try:
                streak_bonus = int(streak_str[1:]) * 0.02
            except ValueError:
                streak_bonus = 0.02
        elif streak_str.startswith("L"):
            try:
                streak_bonus = -int(streak_str[1:]) * 0.02
            except ValueError:
                streak_bonus = -0.02

        # Composite score (0-100 scale)
        score = (win_pct * 50) + (min(max(pt_diff / 200, -1), 1) * 30) + (streak_bonus * 20)

        scored.append({
            **team,
            "power_score": round(score, 1),
            "pt_diff": int(pt_diff),
            "win_pct_display": f"{win_pct:.3f}",
        })

    # Sort by power_score descending
    scored.sort(key=lambda x: x["power_score"], reverse=True)

    # Assign ranks and tiers
    for i, team in enumerate(scored):
        team["power_rank"] = i + 1
        if i < 5:
            team["tier"] = "Elite"
            team["tier_color"] = "#059669"
        elif i < 12:
            team["tier"] = "Contendiente"
            team["tier_color"] = "#2563eb"
        elif i < 20:
            team["tier"] = "En la pelea"
            team["tier_color"] = "#f59e0b"
        elif i < 27:
            team["tier"] = "En desarrollo"
            team["tier_color"] = "#f97316"
        else:
            team["tier"] = "Reconstruccion"
            team["tier_color"] = "#ef4444"

        # Movement indicator (placeholder — could track week-over-week later)
        team["movement"] = "—"

    return scored


async def generate_nfl_picks(upcoming_games: list[dict], standings: list[dict]) -> list[dict]:
    """
    Generate simple picks/predictions for upcoming NFL games.
    Based on win percentage differential and home advantage.
    Returns list of game dicts with pick info.
    """
    if not upcoming_games or not standings:
        return []

    # Don't generate picks if no team has played any games (preseason)
    any_games_played = any(
        (int(t.get("wins") or 0) + int(t.get("losses") or 0)) > 0
        for t in standings
    )
    if not any_games_played:
        return []

    # Build lookup by team name
    team_stats = {}
    for t in standings:
        team_stats[t["team_name"].lower()] = t
        team_stats[t["team_short"].lower()] = t

    picks = []
    for game in upcoming_games[:10]:
        home_name = game.get("home", "").lower()
        away_name = game.get("away", "").lower()

        home_data = team_stats.get(home_name, {})
        away_data = team_stats.get(away_name, {})

        # Calculate win probabilities
        try:
            h_w = int(home_data.get("wins") or 0)
            h_l = int(home_data.get("losses") or 0)
            h_pct = h_w / (h_w + h_l) if (h_w + h_l) > 0 else 0.5
        except (ValueError, ZeroDivisionError):
            h_pct = 0.5

        try:
            a_w = int(away_data.get("wins") or 0)
            a_l = int(away_data.get("losses") or 0)
            a_pct = a_w / (a_w + a_l) if (a_w + a_l) > 0 else 0.5
        except (ValueError, ZeroDivisionError):
            a_pct = 0.5

        # Home advantage bonus (+3%)
        h_pct_adj = min(h_pct + 0.03, 1.0)

        # Determine pick
        if h_pct_adj >= a_pct:
            pick_team = game.get("home", "Local")
            confidence = min(int((h_pct_adj - a_pct) * 100 + 55), 90)
        else:
            pick_team = game.get("away", "Visitante")
            confidence = min(int((a_pct - h_pct_adj) * 100 + 55), 90)

        # Confidence label
        if confidence >= 75:
            conf_label = "Alta"
            conf_color = "#059669"
        elif confidence >= 60:
            conf_label = "Media"
            conf_color = "#f59e0b"
        else:
            conf_label = "Baja"
            conf_color = "#ef4444"

        home_record = f"{home_data.get('wins', '?')}-{home_data.get('losses', '?')}"
        away_record = f"{away_data.get('wins', '?')}-{away_data.get('losses', '?')}"

        picks.append({
            "home": game.get("home", ""),
            "away": game.get("away", ""),
            "home_logo": game.get("home_logo", ""),
            "away_logo": game.get("away_logo", ""),
            "home_record": home_record,
            "away_record": away_record,
            "date": game.get("date", ""),
            "pick_team": pick_team,
            "confidence": confidence,
            "conf_label": conf_label,
            "conf_color": conf_color,
            "reasoning": _pick_reasoning(game.get("home", ""), game.get("away", ""),
                                         h_pct, a_pct, home_data, away_data),
        })

    return picks


def _pick_reasoning(home: str, away: str, h_pct: float, a_pct: float,
                    home_data: dict, away_data: dict) -> str:
    """Generate a short reasoning sentence for a pick."""
    reasons = []
    if h_pct > a_pct + 0.1:
        reasons.append(f"{home} tiene mejor record")
    elif a_pct > h_pct + 0.1:
        reasons.append(f"{away} tiene mejor record")
    else:
        reasons.append("Records muy parejos")

    h_streak = str(home_data.get("streak", ""))
    a_streak = str(away_data.get("streak", ""))
    if h_streak.startswith("W") and len(h_streak) > 1:
        reasons.append(f"{home} en racha de {h_streak[1:]} victorias")
    if a_streak.startswith("W") and len(a_streak) > 1:
        reasons.append(f"{away} en racha de {a_streak[1:]} victorias")

    reasons.append("ventaja de local")
    return ". ".join(reasons[:2]) + "."


async def get_nfl_team_advanced_stats(team_slug: str) -> dict:
    """
    Get advanced NFL team stats from ESPN: points per game, yards,
    turnovers, etc. Returns dict with enriched stats.
    """
    league_info = TEAM_LEAGUE_MAP.get(team_slug)
    if not league_info or league_info[0] != "football":
        return {}

    standings = await fetch_standings("football", "nfl")
    if not standings:
        return {}

    # Find team
    from config import TEAM_ALIASES
    search = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " ")).lower()

    for entry in standings:
        name_lower = entry["team_name"].lower()
        short_lower = entry["team_short"].lower()
        if (search in name_lower or name_lower in search or
            team_slug.replace("-", "") in name_lower.replace(" ", "") or
            search in short_lower):
            stats = entry.get("all_stats", {})
            w = int(entry.get("wins") or 0)
            l = int(entry.get("losses") or 0)
            total = w + l

            return {
                "record": f"{w}-{l}",
                "win_pct": f"{(w/total*100):.1f}%" if total else "—",
                "division": entry.get("group", ""),
                "streak": entry.get("streak", "—"),
                "pts_for": stats.get("pointsFor", "—"),
                "pts_against": stats.get("pointsAgainst", "—"),
                "pt_diff": str(int(float(stats.get("pointsFor", 0)) - float(stats.get("pointsAgainst", 0)))) if stats.get("pointsFor") else "—",
                "ppg": f"{float(stats.get('pointsFor', 0))/total:.1f}" if total and stats.get("pointsFor") else "—",
                "ppg_against": f"{float(stats.get('pointsAgainst', 0))/total:.1f}" if total and stats.get("pointsAgainst") else "—",
                "div_record": stats.get("divisionRecord", stats.get("vsDivision", "—")),
                "conf_record": stats.get("conferenceRecord", stats.get("vsConference", "—")),
                "home_record": stats.get("Home", stats.get("homeRecord", "—")),
                "away_record": stats.get("Road", stats.get("awayRecord", stats.get("roadRecord", "—"))),
                "team_logo": entry.get("team_logo", ""),
                "team_name": entry.get("team_name", ""),
            }

    return {}


# ── Recent & Upcoming Games (for enriched pages) ──────

async def get_recent_league_results(sport: str, league: str, days: int = 5, limit: int = 10) -> list[dict]:
    """
    Get completed games from the past N days for a league.
    Returns a list of simplified game dicts sorted by date desc.
    """
    # TheSportsDB-only leagues
    if league.startswith("sportsdb:"):
        league_id = league.split(":")[1]
        return (await fetch_sportsdb_past_events(league_id))[:limit]

    now = datetime.now(TZ_MX)
    results = []

    tasks = []
    for d in range(1, days + 1):
        past_date = (now - timedelta(days=d)).strftime("%Y%m%d")
        tasks.append(fetch_espn_scoreboard(sport, league, past_date))

    scoreboards = await asyncio.gather(*tasks, return_exceptions=True)

    for sb in scoreboards:
        if isinstance(sb, Exception):
            continue
        for event in sb.get("events", []):
            status = event.get("status", {}).get("type", {}).get("state", "")
            if status != "post":
                continue
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            results.append({
                "id": event.get("id", ""),
                "home": home_c.get("team", {}).get("displayName", ""),
                "away": away_c.get("team", {}).get("displayName", ""),
                "home_score": home_c.get("score", "0"),
                "away_score": away_c.get("score", "0"),
                "home_logo": home_c.get("team", {}).get("logo", ""),
                "away_logo": away_c.get("team", {}).get("logo", ""),
                "date": event.get("date", ""),
            })

    # Sort by date descending (most recent first)
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return results[:limit]


# Mapa US→MX compartido: antes vivía dentro de get_todays_games, así que
# get_upcoming_league_games no lo aplicaba y el mismo partido salía con canales
# distintos según la página desde la que se mirara.
# Nombres de país que usa TheSportsDB → código ISO. La lista sale del reparto
# real de tráfico en Search Console (28 días): MX 41%, VE 17%, PA 8%, US 6%,
# DO 4%, CO 4%, ES 4%, PE/EC/PR ~2% cada uno.
SPORTSDB_COUNTRY_CODE = {
    "Mexico": "MX", "México": "MX", "MX": "MX",
    "Venezuela": "VE", "VE": "VE",
    "Panama": "PA", "Panamá": "PA", "PA": "PA",
    "Dominican Republic": "DO", "República Dominicana": "DO", "DO": "DO",
    "Colombia": "CO", "CO": "CO",
    "Peru": "PE", "Perú": "PE", "PE": "PE",
    "Ecuador": "EC", "EC": "EC",
    "Argentina": "AR", "AR": "AR",
    "Chile": "CL", "CL": "CL",
    "Spain": "ES", "España": "ES", "ES": "ES",
    "United States": "US", "USA": "US", "US": "US",
    "Puerto Rico": "PR", "PR": "PR",
    # eventstv.php devuelve "World" (no "Worldwide", que es lo que usa el
    # endpoint viejo). Sin esta entrada se descartaban las transmisiones globales.
    "World": "*", "Worldwide": "*", "International": "*",
}

US_TO_MX_CHANNEL = {
    "ESPN": "ESPN MX", "ESPN2": "ESPN MX", "ESPNU": "ESPN MX",
    "ESPNews": "ESPN MX", "ABC": "ESPN MX",
    "ESPN+": "Disney+",
    "FOX": "Fox Sports MX", "FS1": "Fox Sports MX", "FS2": "Fox Sports MX",
    "NBC": "ESPN MX", "NBCSN": "ESPN MX",
    "CBS": "Fox Sports MX", "CBSSN": "Fox Sports MX",
    "Univision": "TUDN", "UniMas": "TUDN",
    "Telemundo": "Telemundo",
    "TNT": "TNT Sports", "TBS": "TNT Sports",
    "Max": "Max", "HBO Max": "Max",
    "Peacock": "Disney+",
    "Amazon Prime": "Amazon Prime", "Prime Video": "Amazon Prime",
    "Netflix": "Netflix",
}


def mx_channels_for_game(league_slug: str, home_name: str, away_name: str,
                         us_channels: list[str] | None = None) -> list[str]:
    """Canales de México para un partido, con las mismas reglas en todo el sitio.

    Una sola fuente de verdad: la ficha del partido, la página de equipo, la de
    país y el calendario tienen que responder lo mismo. Antes cada una calculaba
    lo suyo (o no calculaba nada) y Juárez–Tigres salía con tres respuestas.
    """
    us_channels = us_channels or []
    out: list[str] = []

    if league_slug == "liga-mx":
        hl, al = (home_name or "").lower(), (away_name or "").lower()
        for names in (hl, al):
            for team_key, channels in LIGA_MX_TEAM_CHANNELS.items():
                if team_key in names or (names and names in team_key):
                    return list(channels)
        return ["TUDN", "ViX"]

    if league_slug == "nfl":
        return list(nfl_mx_channels(us_channels))

    for ch in us_channels:
        mx = US_TO_MX_CHANNEL.get(ch)
        if mx and mx not in out:
            out.append(mx)
    if not out:
        out = list(DEFAULT_LEAGUE_CHANNELS.get(league_slug, []))
    return out


async def get_upcoming_league_games(sport: str, league: str, days: int = 5, limit: int = 10) -> list[dict]:
    """
    Get upcoming (not started) games for the next N days for a league.
    Returns simplified game dicts sorted by date asc.
    """
    # TheSportsDB-only leagues
    if league.startswith("sportsdb:"):
        league_id = league.split(":")[1]
        return (await fetch_sportsdb_next_events(league_id))[:limit]

    now = datetime.now(TZ_MX)
    upcoming = []

    # El slug interno (liga-mx, nfl…) a partir del código ESPN (mex.1, nfl…)
    league_slug_hint = next(
        (slug for slug, v in ALL_LEAGUES.items() if len(v) >= 2 and v[0] == sport and v[1] == league),
        league,
    )

    tasks = []
    for d in range(1, days + 1):
        future_date = (now + timedelta(days=d)).strftime("%Y%m%d")
        tasks.append(fetch_espn_scoreboard(sport, league, future_date))

    scoreboards = await asyncio.gather(*tasks, return_exceptions=True)

    for sb in scoreboards:
        if isinstance(sb, Exception):
            continue
        for event in sb.get("events", []):
            status = event.get("status", {}).get("type", {}).get("state", "")
            if status != "pre":
                continue
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            # Extract broadcast channels with country info
            channels = []
            seen_ch = set()
            for geo in comp.get("geoBroadcasts", []):
                raw = geo.get("media", {}).get("shortName", "")
                if not raw:
                    continue
                ch = _normalize_channel(raw)
                info = CHANNEL_ALIASES.get(ch, {})
                display = info.get("name", ch)
                if display.lower() not in seen_ch:
                    seen_ch.add(display.lower())
                    channels.append({
                        "name": display,
                        "country": info.get("country", ""),
                        # Cadena local de un mercado de EE.UU. (Atlanta News First,
                        # Victory+ ATL, NBC Sports BO…). ESPN las marca con
                        # market Home/Away y no están en CHANNEL_ALIASES. Sin este
                        # dato, la página de liga las imprimía sin etiqueta justo
                        # después de "MX:", y parecían opciones para México.
                        "is_us_regional": (not info) and geo.get("market", {}).get("type", "") in ("Home", "Away"),
                    })

            # Canales de México con las MISMAS reglas que usa la ficha del partido
            _home_nm = home_c.get("team", {}).get("displayName", "")
            _away_nm = away_c.get("team", {}).get("displayName", "")
            if not any(c.get("country") == "MX" for c in channels):
                for mx in mx_channels_for_game(league_slug_hint, _home_nm, _away_nm,
                                               [c["name"] for c in channels]):
                    info = CHANNEL_ALIASES.get(mx, {})
                    display = info.get("name", mx)
                    if display.lower() not in seen_ch:
                        seen_ch.add(display.lower())
                        channels.append({"name": display, "country": info.get("country", "MX")})

            upcoming.append({
                "id": event.get("id", ""),
                "home": home_c.get("team", {}).get("displayName", ""),
                "away": away_c.get("team", {}).get("displayName", ""),
                "home_logo": home_c.get("team", {}).get("logo", ""),
                "away_logo": away_c.get("team", {}).get("logo", ""),
                "date": event.get("date", ""),
                "channels": channels[:8],
            })

    upcoming.sort(key=lambda x: x.get("date", ""))
    return upcoming[:limit]


# ── Odds API Functions ──────────────────────────────────

async def fetch_odds(league_slug: str, markets: str = "h2h,spreads,totals") -> list[dict]:
    """
    Fetch odds from the-odds-api.com for a given league.
    ALWAYS fetches all markets (h2h,spreads,totals) so homepage and game page
    share the same cache entry — this halves our API usage.
    Free tier: 500 requests/month — caching is critical.
    """
    global _odds_request_count

    if not ODDS_API_KEY:
        return []

    odds_sport = ODDS_SPORT_MAP.get(league_slug)
    if not odds_sport:
        return []

    # Always use the same markets so homepage and game page share one cache entry
    markets = ODDS_MARKETS
    cache_key = f"odds:{odds_sport}"
    if cache_key in _odds_cache:
        return _odds_cache[cache_key]
    if cache_key in _odds_fail_cache:
        return []
    if ODDS_LOW_MODE and league_slug not in ODDS_PRIORITY_LEAGUES:
        return []
    # Cuota restante reportada por la API (persiste entre reinicios): no bajar de la reserva
    try:
        _rem = int(ODDS_DIAG.get("remaining") or -1)
    except (TypeError, ValueError):
        _rem = -1
    if 0 <= _rem < _ODDS_RESERVE + len(markets.split(",")):
        logger.warning(f"Odds API: créditos casi agotados ({_rem}); sin pedir {odds_sport}")
        return []

    # Budget guard — stop fetching if we're burning too many requests
    if _odds_request_count >= _ODDS_MONTHLY_BUDGET:
        logger.warning(f"Odds API budget exhausted ({_odds_request_count} requests this process). Skipping.")
        return []

    url = f"{ODDS_API_BASE}/{odds_sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            _odds_request_count += 1

            # The API returns remaining quota in headers — log it
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            logger.info(f"Odds API: {odds_sport} — used={used}, remaining={remaining}")
            ODDS_DIAG.update({"last_status": resp.status_code, "remaining": remaining, "used": used,
                              "last_sport": odds_sport, "last_at": datetime.now().isoformat(timespec="seconds"),
                              "requests_this_process": _odds_request_count})

            resp.raise_for_status()
            data = resp.json()
            _odds_cache[cache_key] = data
            ODDS_DIAG["last_error"] = ""
            ODDS_DIAG["events_by_sport"][odds_sport] = len(data)
            return data
    except Exception as e:
        logger.warning(f"Odds API error for {odds_sport}: {e}")
        ODDS_DIAG["last_error"] = f"{odds_sport}: {e}"[:300]
        ODDS_DIAG["errors"] = ODDS_DIAG.get("errors", 0) + 1
        # No martillar la API si falla (cuota agotada, key inválida): reintenta en 10 min
        _odds_fail_cache[cache_key] = True
        return []


def _extract_h2h_from_bookie(bookie: dict, home_name: str, away_name: str) -> dict | None:
    """Extract h2h odds from a single bookmaker."""
    markets = bookie.get("markets", [])
    h2h = next((m for m in markets if m["key"] == "h2h"), None)
    if not h2h:
        return None
    outcomes = h2h.get("outcomes", [])
    if len(outcomes) < 2:
        return None
    r = {"bookmaker": bookie.get("title", ""), "home_odds": None, "away_odds": None, "draw_odds": None,
         "home_price": 0, "away_price": 0, "draw_price": 0}
    for outcome in outcomes:
        name = outcome.get("name", "").lower()
        price = outcome.get("price", 0)
        if "draw" in name:
            r["draw_odds"] = _format_american_odds(price)
            r["draw_price"] = price
        elif any(w in name for w in home_name.split() if len(w) > 3):
            r["home_odds"] = _format_american_odds(price)
            r["home_price"] = price
        elif any(w in name for w in away_name.split() if len(w) > 3):
            r["away_odds"] = _format_american_odds(price)
            r["away_price"] = price
    # Fallback by position
    if not r["home_odds"] and len(outcomes) >= 2:
        r["home_odds"] = _format_american_odds(outcomes[0].get("price", 0))
        r["home_price"] = outcomes[0].get("price", 0)
        r["away_odds"] = _format_american_odds(outcomes[1].get("price", 0))
        r["away_price"] = outcomes[1].get("price", 0)
        if len(outcomes) >= 3:
            r["draw_odds"] = _format_american_odds(outcomes[2].get("price", 0))
            r["draw_price"] = outcomes[2].get("price", 0)
    return r


def match_odds_to_game(game: dict, odds_list: list[dict]) -> dict | None:
    """
    Match a game (from ESPN) to odds data (from the-odds-api).
    Returns dict with best odds across ALL bookmakers + list of individual bookmaker odds.
    """
    if not odds_list:
        return None

    home_name = game["home"]["name"].lower()
    away_name = game["away"]["name"].lower()

    for odds_game in odds_list:
        odds_home = odds_game.get("home_team", "").lower()
        odds_away = odds_game.get("away_team", "").lower()

        # Fuzzy match: check if any significant word matches
        home_match = (
            home_name in odds_home or odds_home in home_name or
            any(w in odds_home for w in home_name.split() if len(w) > 3)
        )
        away_match = (
            away_name in odds_away or odds_away in away_name or
            any(w in odds_away for w in away_name.split() if len(w) > 3)
        )

        if home_match and away_match:
            bookmakers = odds_game.get("bookmakers", [])
            if not bookmakers:
                return None

            # Collect odds from ALL bookmakers
            all_bookies: list[dict] = []
            best_home_price = -99999
            best_away_price = -99999
            best_draw_price = -99999
            best_home_odds = None
            best_away_odds = None
            best_draw_odds = None
            best_home_bookie = ""
            best_away_bookie = ""
            best_draw_bookie = ""

            for b in bookmakers:
                extracted = _extract_h2h_from_bookie(b, home_name, away_name)
                if not extracted or not extracted["home_odds"]:
                    continue
                all_bookies.append(extracted)
                # Track best odds (highest price = best for bettor)
                if extracted["home_price"] > best_home_price:
                    best_home_price = extracted["home_price"]
                    best_home_odds = extracted["home_odds"]
                    best_home_bookie = extracted["bookmaker"]
                if extracted["away_price"] > best_away_price:
                    best_away_price = extracted["away_price"]
                    best_away_odds = extracted["away_odds"]
                    best_away_bookie = extracted["bookmaker"]
                if extracted["draw_price"] and extracted["draw_price"] > best_draw_price:
                    best_draw_price = extracted["draw_price"]
                    best_draw_odds = extracted["draw_odds"]
                    best_draw_bookie = extracted["bookmaker"]

            if not all_bookies:
                return None

            # Filter to only paying affiliate bookmakers for display
            paying_bookies = [
                b for b in all_bookies
                if any(pk in b["bookmaker"].lower() for pk in PAYING_BOOKMAKER_KEYS)
            ]

            # Primary result: best odds across ALL bookmakers (for accuracy),
            # but only list/count paying bookmakers
            result = {
                "bookmaker": paying_bookies[0]["bookmaker"] if paying_bookies else all_bookies[0]["bookmaker"],
                "home_odds": best_home_odds,
                "away_odds": best_away_odds,
                "draw_odds": best_draw_odds,
                "best_home_bookie": best_home_bookie,
                "best_away_bookie": best_away_bookie,
                "best_draw_bookie": best_draw_bookie,
                "bookmakers": paying_bookies,
                "total_bookmakers": len(paying_bookies),
            }
            return result

    return None


def match_full_odds_to_game(game: dict, odds_list: list[dict]) -> dict | None:
    """
    Match a game to full odds data including h2h, spreads, and totals.
    Returns dict with all markets or None if no match found.
    Used for game detail pages.
    """
    if not odds_list:
        return None

    home_name = game["home"]["name"].lower()
    away_name = game["away"]["name"].lower()

    for odds_game in odds_list:
        odds_home = odds_game.get("home_team", "").lower()
        odds_away = odds_game.get("away_team", "").lower()

        home_match = (
            home_name in odds_home or odds_home in home_name or
            any(w in odds_home for w in home_name.split() if len(w) > 3)
        )
        away_match = (
            away_name in odds_away or odds_away in away_name or
            any(w in odds_away for w in away_name.split() if len(w) > 3)
        )

        if home_match and away_match:
            bookmakers = odds_game.get("bookmakers", [])
            if not bookmakers:
                return None

            # Prefer paying affiliate bookmakers; fall back to any
            paying_prefs = list(PAYING_BOOKMAKER_KEYS)
            fallback = ["draftkings", "fanduel", "betmgm", "pinnacle", "bet365"]
            bookie = None
            for pref in paying_prefs + fallback:
                bookie = next((b for b in bookmakers if pref in b["key"].lower()), None)
                if bookie:
                    break
            if not bookie:
                bookie = bookmakers[0]

            markets = bookie.get("markets", [])
            result = {
                "bookmaker": bookie.get("title", ""),
                "home_odds": None, "away_odds": None, "draw_odds": None,
                "spread_home": None, "spread_away": None,
                "spread_home_point": None, "spread_away_point": None,
                "total_over": None, "total_under": None,
                "total_point": None,
            }

            # h2h
            h2h = next((m for m in markets if m["key"] == "h2h"), None)
            if h2h:
                for outcome in h2h.get("outcomes", []):
                    name = outcome.get("name", "").lower()
                    price = outcome.get("price", 0)
                    if "draw" in name:
                        result["draw_odds"] = _format_american_odds(price)
                    elif any(w in name for w in home_name.split() if len(w) > 3):
                        result["home_odds"] = _format_american_odds(price)
                    elif any(w in name for w in away_name.split() if len(w) > 3):
                        result["away_odds"] = _format_american_odds(price)
                # Fallback by position
                if not result["home_odds"] and len(h2h.get("outcomes", [])) >= 2:
                    outcomes = h2h["outcomes"]
                    result["home_odds"] = _format_american_odds(outcomes[0].get("price", 0))
                    result["away_odds"] = _format_american_odds(outcomes[1].get("price", 0))
                    if len(outcomes) >= 3:
                        result["draw_odds"] = _format_american_odds(outcomes[2].get("price", 0))

            # spreads
            spreads = next((m for m in markets if m["key"] == "spreads"), None)
            if spreads:
                for outcome in spreads.get("outcomes", []):
                    name = outcome.get("name", "").lower()
                    price = outcome.get("price", 0)
                    point = outcome.get("point", 0)
                    if any(w in name for w in home_name.split() if len(w) > 3):
                        result["spread_home"] = _format_american_odds(price)
                        result["spread_home_point"] = f"{point:+g}" if point >= 0 else str(point)
                    elif any(w in name for w in away_name.split() if len(w) > 3):
                        result["spread_away"] = _format_american_odds(price)
                        result["spread_away_point"] = f"{point:+g}" if point >= 0 else str(point)
                # Fallback by position
                if not result["spread_home"] and len(spreads.get("outcomes", [])) >= 2:
                    outcomes = spreads["outcomes"]
                    result["spread_home"] = _format_american_odds(outcomes[0].get("price", 0))
                    result["spread_home_point"] = f"{outcomes[0].get('point', 0):+g}"
                    result["spread_away"] = _format_american_odds(outcomes[1].get("price", 0))
                    result["spread_away_point"] = f"{outcomes[1].get('point', 0):+g}"

            # totals
            totals = next((m for m in markets if m["key"] == "totals"), None)
            if totals:
                for outcome in totals.get("outcomes", []):
                    name = outcome.get("name", "").lower()
                    price = outcome.get("price", 0)
                    point = outcome.get("point", 0)
                    if "over" in name:
                        result["total_over"] = _format_american_odds(price)
                        result["total_point"] = str(point)
                    elif "under" in name:
                        result["total_under"] = _format_american_odds(price)
                        if not result["total_point"]:
                            result["total_point"] = str(point)

            return result

    return None


def _format_american_odds(price: int) -> str:
    """Format american odds with + or - prefix."""
    if price >= 0:
        return f"+{price}"
    return str(price)


# ── MercadoLibre Product Images (free public API) ──────────

_meli_cache = TTLCache(maxsize=15, ttl=14400)  # 4h, reduced from 24h/50


async def fetch_meli_product_image(query: str) -> dict | None:
    """
    Search MercadoLibre Mexico for a product and return thumbnail + link.
    Uses the free public API — no auth required.
    Returns: {"thumbnail": "https://...", "title": "...", "price": 799, "link": "https://..."}
    """
    cache_key = f"meli:{query}"
    if cache_key in _meli_cache:
        return _meli_cache[cache_key]

    url = "https://api.mercadolibre.com/sites/MLM/search"
    params = {"q": query, "limit": 1, "sort": "relevance"}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                _meli_cache[cache_key] = None
                return None

            item = results[0]
            # Convert http thumbnail to https and use bigger size
            thumb = item.get("thumbnail", "")
            if thumb:
                thumb = thumb.replace("http://", "https://")
                # Use higher quality image: D_NQ_NP -> D_Q_NP, or append size
                thumb = thumb.replace("-I.jpg", "-O.jpg")  # O = larger

            result = {
                "thumbnail": thumb,
                "title": item.get("title", ""),
                "price": item.get("price", 0),
                "link": item.get("permalink", ""),
            }
            _meli_cache[cache_key] = result
            return result
    except Exception as e:
        logger.warning(f"MercadoLibre API error for '{query}': {e}")
        _meli_cache[cache_key] = None
        return None
