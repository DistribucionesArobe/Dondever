"""
Sports data fetcher — pulls schedules from ESPN's public API
and enriches with TV broadcast data from TheSportsDB Premium.
"""

import httpx
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from cachetools import TTLCache

from config import (
    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,
    LEAGUES, CHANNEL_ALIASES
)

logger = logging.getLogger("dondever.sports")

# Cache: 5 min TTL, max 500 entries
_cache = TTLCache(maxsize=500, ttl=300)
# TV cache: 30 min TTL (channels don't change often)
_tv_cache = TTLCache(maxsize=1000, ttl=1800)


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
    """
    cache_key = f"sportsdb:tv:{event_id}"
    if cache_key in _tv_cache:
        return _tv_cache[cache_key]

    url = f"{SPORTSDB_BASE}/lookupeventtv.php"
    params = {"id": event_id}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
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

    # Try to match by team names
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    for ev in events:
        db_home = (ev.get("strHomeTeam") or "").lower()
        db_away = (ev.get("strAwayTeam") or "").lower()

        # Fuzzy match: check if ESPN team name is contained in SportsDB name or vice versa
        home_match = (home_lower in db_home or db_home in home_lower or
                      home_lower.split()[-1] in db_home)
        away_match = (away_lower in db_away or db_away in away_lower or
                      away_lower.split()[-1] in db_away)

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
    league_info = LEAGUES.get(league_slug, {})
    events = []

    for ev in raw.get("events", []):
        competitions = ev.get("competitions", [{}])
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors", [])

        home = away = None
        for team_data in competitors:
            team_info = {
                "name": team_data.get("team", {}).get("displayName", "TBD"),
                "short": team_data.get("team", {}).get("abbreviation", ""),
                "logo": team_data.get("team", {}).get("logo", ""),
                "score": team_data.get("score", ""),
            }
            if team_data.get("homeAway") == "home":
                home = team_info
            else:
                away = team_info

        if not home:
            home = {"name": "TBD", "short": "", "logo": "", "score": ""}
        if not away:
            away = {"name": "TBD", "short": "", "logo": "", "score": ""}

        # 1) Get ESPN broadcast info
        espn_broadcasts = []
        for geo_broadcast in comp.get("geoBroadcasts", []):
            market = geo_broadcast.get("market", {}).get("type", "")
            media = geo_broadcast.get("media", {})
            channel = media.get("shortName", "")
            if channel:
                espn_broadcasts.append({
                    "channel": channel,
                    "market": market,
                    "info": CHANNEL_ALIASES.get(channel, {}),
                })

        # 2) Try TheSportsDB for better TV data
        sportsdb_tv = await get_sportsdb_tv_for_teams(
            home["name"], away["name"], league_slug, date_str
        )

        # Merge: prefer TheSportsDB if it has data, fall back to ESPN
        if sportsdb_tv:
            broadcasts = sportsdb_tv
        else:
            broadcasts = espn_broadcasts

        # Status
        status_type = ev.get("status", {}).get("type", {})
        status = {
            "state": status_type.get("state", "pre"),
            "detail": ev.get("status", {}).get("type", {}).get("detail", ""),
            "display": status_type.get("description", "Scheduled"),
        }

        venue_raw = comp.get("venue", {})
        venue = venue_raw.get("fullName", "")

        events.append({
            "id": ev.get("id", ""),
            "league_slug": league_slug,
            "league_name": league_info[2] if isinstance(league_info, tuple) else league_slug,
            "emoji": league_info[3] if isinstance(league_info, tuple) and len(league_info) > 3 else "",
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
        now = datetime.now(timezone(timedelta(hours=-6)))
        date_str = now.strftime("%Y%m%d")

    tasks = []
    slugs = []

    for slug, (sport, league, name, emoji) in LEAGUES.items():
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
    query_lower = query.lower()
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

        if query_lower in searchable:
            matches.append(game)

    return matches
