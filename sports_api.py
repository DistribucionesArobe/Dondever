"""
Sports data fetcher — pulls schedules from ESPN's public API
and enriches with TV broadcast data from TheSportsDB Premium.
"""
from __future__ import annotations

import httpx
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from cachetools import TTLCache

from config import (
    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,
    LEAGUES, ALL_LEAGUES, CHANNEL_ALIASES, ESPN_CHANNEL_NORMALIZE, TZ_MX, TEAM_ALIASES
)

logger = logging.getLogger("dondever.sports")

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



# Cache: 5 min TTL, max 500 entries
_cache = TTLCache(maxsize=500, ttl=300)
# TV cache: 4 hour TTL — TheSportsDB free tier has aggressive rate limits (429)
_tv_cache = TTLCache(maxsize=1000, ttl=14400)
# Track when TheSportsDB is rate-limiting us to avoid flooding with 429s
_sportsdb_blocked_until = 0  # timestamp when we can retry
# Odds cache: 6h TTL — paid plan (20K credits/month)
# 3 markets (h2h,spreads,totals) = 3 credits per request
# 19 leagues × 4 refreshes/day × 3 credits × 31 days = ~7K credits/month (safe under 20K)
_odds_cache = TTLCache(maxsize=200, ttl=21600)  # 6 hours
_odds_request_count = 0  # track requests this process lifetime
_ODDS_MONTHLY_BUDGET = 6000  # ~6,666 effective requests with 3-market calls

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
DEFAULT_LEAGUE_CHANNELS = {
    # Futbol
    "liga-mx-femenil": ["TUDN", "ViX"],
    "liga-expansion": ["ESPN MX", "ViX"],
    "mls": ["Apple TV+", "ViX"],
    "premier-league": ["ESPN MX", "Paramount+"],
    "la-liga": ["ESPN MX"],
    "serie-a": ["ESPN MX", "Paramount+"],
    "bundesliga": ["ESPN MX"],
    "ligue-1": ["ESPN MX"],
    "champions": ["TUDN", "Canal 5", "ViX", "Paramount+"],
    "europa-league": ["ESPN MX", "ViX"],
    "concacaf-cl": ["TUDN", "ViX", "Canal 5"],
    "copa-america": ["TUDN", "Canal 5", "ViX"],
    "world-cup": ["TUDN", "Canal 5", "Azteca 7", "ViX"],
    "club-friendly": ["TUDN", "ViX"],
    # Copas domésticas
    "copa-del-rey": ["ESPN MX"],
    "fa-cup": ["ESPN MX"],
    "carabao-cup": ["ESPN MX"],
    "dfb-pokal": ["ESPN MX"],
    "coppa-italia": ["ESPN MX", "Paramount+"],
    "coupe-de-france": ["ESPN MX"],
    "us-open-cup": ["ESPN+", "Apple TV+"],
    "copa-argentina": ["ESPN", "TNT Sports"],
    # Copas internacionales
    "leagues-cup": ["Apple TV+", "MLS Season Pass"],
    "club-world-cup": ["TUDN", "ViX", "Fox Sports MX"],
    # Selecciones
    "euro": ["ESPN MX", "SKY", "ViX"],
    "gold-cup": ["TUDN", "Canal 5", "ViX"],
    "wcq-conmebol": ["ESPN MX", "TUDN", "ViX"],
    "wcq-concacaf": ["TUDN", "Canal 5", "ViX"],
    "nations-league": ["ESPN MX"],
    "concacaf-nations": ["TUDN", "ViX", "Paramount+"],
    # Futbol LATAM
    "liga-colombia": ["Win Sports+", "ESPN"],
    "liga-argentina": ["ESPN", "TNT Sports", "Disney+"],
    "liga-ecuador": ["GOLTV", "ESPN"],
    "liga-panama": ["TVMax", "RPC"],
    "liga-chile": ["TNT Sports", "ESPN"],
    "liga-peru": ["GOLPERU", "Liga1 Max"],
    "libertadores": ["ESPN", "Paramount+", "Fox Sports MX"],
    "sudamericana": ["ESPN", "Paramount+"],
    "liga-portugal": ["ESPN MX"],
    "eredivisie": ["ESPN MX"],
    # Futbol americano
    "nfl": ["ESPN MX", "Fox Sports MX", "TUDN", "Canal 5"],
    "college-football": ["ESPN MX"],
    # Basquetbol
    "nba": ["ESPN MX"],
    "wnba": ["ESPN MX"],
    # Beisbol
    "mlb": ["ESPN MX"],
    # Hockey
    "nhl": ["ESPN MX"],
    # Combate
    "ufc": ["Fox Sports MX", "ESPN MX"],
}

# Liga MX Clausura 2026: broadcast rights per team (home matches)
# Source: informador.mx Jan 2026
LIGA_MX_TEAM_CHANNELS = {
    # TUDN + ViX Premium
    "america": ["TUDN", "ViX"],
    "atlas": ["TUDN", "ViX"],
    "cruz azul": ["TUDN", "ViX"],
    "santos laguna": ["TUDN", "ViX"],
    "santos": ["TUDN", "ViX"],
    "monterrey": ["TUDN", "ViX"],
    "rayados": ["TUDN", "ViX"],
    "pumas": ["TUDN", "ViX"],
    "unam": ["TUDN", "ViX"],
    # TV Azteca + FOX One
    "juarez": ["Azteca 7", "Fox Sports MX"],
    "puebla": ["Azteca 7", "Fox Sports MX"],
    "mazatlan": ["Azteca 7", "Fox Sports MX"],
    "tigres uanl": ["Azteca 7", "Fox Sports MX"],
    "tigres": ["Azteca 7", "Fox Sports MX"],
    # FOX One exclusivo
    "leon": ["Fox Sports MX"],
    "pachuca": ["Fox Sports MX"],
    "queretaro": ["Fox Sports MX"],
    "tijuana": ["Fox Sports MX"],
    "xolos": ["Fox Sports MX"],
    # Necaxa: TV Azteca + ViX + Claro Sports
    "necaxa": ["Azteca 7", "ViX", "Claro Sports"],
    # ESPN + Disney+
    "atletico san luis": ["ESPN MX", "Disney+"],
    "san luis": ["ESPN MX", "Disney+"],
    # Toluca: TUDN + FOX + Azteca
    "toluca": ["TUDN", "Fox Sports MX", "Azteca 7"],
    # Amazon Prime Video exclusivo
    "guadalajara": ["Amazon Prime"],
    "chivas": ["Amazon Prime"],
}

# ── ESPN API ─────────────────────────────────────────────

async def fetch_espn_scoreboard(
    sport: str, league: str, date_str: Optional[str] = None
) -> dict:
    """Fetch scoreboard for a sport/league from ESPN API."""
    cache_key = f"espn:{sport}:{league}:{date_str}"
    if cache_key in _cache:
        return _cache[cache_key]

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
            return data
        except httpx.HTTPError as e:
            logger.warning(f"ESPN API error for {sport}/{league}: {e}")
            return {"events": [], "leagues": []}


# ── ESPN Event Summary (lineups, H2H, standings, leaders) ──

_summary_cache = TTLCache(maxsize=200, ttl=300)  # 5 min

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
                "title": s.get("title", ""),
                "summary": s.get("summary", ""),
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
}


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


async def fetch_sportsdb_tv_by_event(event_id: str) -> list[dict]:
    """
    Lookup TV broadcast channels for a specific event ID.
    TheSportsDB Premium endpoint.
    Includes rate-limit protection: if we get 429, back off for 30 min.
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
                # Only keep MX and US channels
                if country in ("Mexico", "United States", "US", "MX", "Worldwide"):
                    channel = tv.get("strChannel", "")
                    if channel:
                        result.append({
                            "channel": channel,
                            "country": country,
                            "info": CHANNEL_ALIASES.get(channel, {
                                "name": channel,
                                "country": "MX" if "Mexico" in country else "US",
                                "type": "cable"
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

async def parse_espn_events_enriched(
    raw: dict, league_slug: str, date_str: str
) -> list[dict]:
    """Parse ESPN events and enrich with TheSportsDB TV data."""
    league_info = ALL_LEAGUES.get(league_slug, {})
    events = []

    for ev in raw.get("events", []):
        competitions = ev.get("competitions", [{}])
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors", [])

        home = away = None
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

        # 2) Always merge MX channels from our manual mapping
        # ESPN is US-centric and rarely includes Mexican channels.
        # Our LIGA_MX_TEAM_CHANNELS and DEFAULT_LEAGUE_CHANNELS have
        # the actual MX broadcast data, so we always add them.
        broadcasts = list(espn_broadcasts)

        # Determine MX channels to add
        mx_defaults = []
        if league_slug == "liga-mx":
            home_lower = home["name"].lower()
            away_lower = away["name"].lower()
            team_channels = None
            # Check home team first (home team usually determines broadcast)
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
        else:
            mx_defaults = DEFAULT_LEAGUE_CHANNELS.get(league_slug, [])

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

        # Sort: known channels first, US regional last
        broadcasts.sort(key=lambda b: (1 if b.get("is_us_regional") else 0))

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
            "display": status_type.get("description", "Scheduled"),
            "clock": raw_clock,
            "period": period,
        }

        venue_raw = comp.get("venue", {})
        venue = venue_raw.get("fullName", "")

        sport_type = league_info[0] if isinstance(league_info, tuple) else ""

        events.append({
            "id": ev.get("id", ""),
            "league_slug": league_slug,
            "league_name": league_info[2] if isinstance(league_info, tuple) else league_slug,
            "emoji": league_info[3] if isinstance(league_info, tuple) and len(league_info) > 3 else "",
            "sport": sport_type,
            "date": ev.get("date", ""),
            "name": ev.get("name", f"{away['name']} vs {home['name']}"),
            "short_name": ev.get("shortName", ""),
            "home": home,
            "away": away,
            "status": status,
            "broadcasts": broadcasts,
            "venue": venue,
            "link": ev.get("links", [{}])[0].get("href", "") if ev.get("links") else "",
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

    tasks = []
    slugs = []

    # Use ALL_LEAGUES when filtering specific league/sport, LEAGUES for homepage
    source = ALL_LEAGUES if (league_filter or sport_filter) else LEAGUES

    for slug, (sport, league, name, emoji) in source.items():
        if league_filter and slug != league_filter:
            continue
        if sport_filter and sport != sport_filter:
            continue
        tasks.append(fetch_espn_scoreboard(sport, league, date_str))
        slugs.append(slug)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events = []
    for slug, result in zip(slugs, results):
        if isinstance(result, Exception):
            logger.error(f"Error fetching {slug}: {result}")
            continue
        # Use enriched parser with TheSportsDB TV data
        events = await parse_espn_events_enriched(result, slug, date_str)
        all_events.extend(events)

    all_events.sort(key=lambda e: e.get("date", ""))
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
}

# Standings cache: 1 hour TTL
_standings_cache = TTLCache(maxsize=50, ttl=3600)


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
    standings = await fetch_standings(sport, league)
    return standings[:limit]


# ── Recent & Upcoming Games (for enriched pages) ──────

async def get_recent_league_results(sport: str, league: str, days: int = 5, limit: int = 10) -> list[dict]:
    """
    Get completed games from the past N days for a league.
    Returns a list of simplified game dicts sorted by date desc.
    """
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


async def get_upcoming_league_games(sport: str, league: str, days: int = 5, limit: int = 10) -> list[dict]:
    """
    Get upcoming (not started) games for the next N days for a league.
    Returns simplified game dicts sorted by date asc.
    """
    now = datetime.now(TZ_MX)
    upcoming = []

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

            # Extract broadcast channels (normalized display names)
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
                    channels.append(display)

            upcoming.append({
                "id": event.get("id", ""),
                "home": home_c.get("team", {}).get("displayName", ""),
                "away": away_c.get("team", {}).get("displayName", ""),
                "home_logo": home_c.get("team", {}).get("logo", ""),
                "away_logo": away_c.get("team", {}).get("logo", ""),
                "date": event.get("date", ""),
                "channels": channels[:4],
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

    # Always use full markets to maximize cache hits
    markets = "h2h,spreads,totals"
    cache_key = f"odds:{odds_sport}"
    if cache_key in _odds_cache:
        return _odds_cache[cache_key]

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

            resp.raise_for_status()
            data = resp.json()
            _odds_cache[cache_key] = data
            return data
    except Exception as e:
        logger.warning(f"Odds API error for {odds_sport}: {e}")
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

            # Primary result: best odds across all bookmakers
            result = {
                "bookmaker": all_bookies[0]["bookmaker"],
                "home_odds": best_home_odds,
                "away_odds": best_away_odds,
                "draw_odds": best_draw_odds,
                "best_home_bookie": best_home_bookie,
                "best_away_bookie": best_away_bookie,
                "best_draw_bookie": best_draw_bookie,
                # Top bookmakers for comparison (max 5)
                "bookmakers": all_bookies[:5],
                "total_bookmakers": len(all_bookies),
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

            # Collect odds from multiple bookmakers
            preferred = ["draftkings", "fanduel", "betmgm", "pinnacle", "bet365"]
            bookie = None
            for pref in preferred:
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

_meli_cache = TTLCache(maxsize=500, ttl=86400)  # 24 hour cache


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
