"""
Sports data fetcher — pulls schedules from ESPN's public API
and enriches with TV broadcast data from TheSportsDB.
"""

import httpx
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from cachetools import TTLCache

from config import ESPN_BASE, SPORTSDB_BASE, LEAGUES, CHANNEL_ALIASES

logger = logging.getLogger("dondever.sports")

# Cache: 5 min TTL, max 500 entries
_cache = TTLCache(maxsize=500, ttl=300)


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
        params["dates"] = date_str  # format: YYYYMMDD

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


def parse_espn_events(raw: dict, league_slug: str) -> list[dict]:
    """Parse ESPN scoreboard response into clean event dicts."""
    league_info = LEAGUES.get(league_slug, {})
    events = []

    for ev in raw.get("events", []):
        # Extract competitors
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

        # Extract broadcast info from ESPN
        broadcasts = []
        for geo_broadcast in comp.get("geoBroadcasts", []):
            market = geo_broadcast.get("market", {}).get("type", "")
            media = geo_broadcast.get("media", {})
            channel = media.get("shortName", "")
            if channel:
                broadcasts.append({
                    "channel": channel,
                    "market": market,  # "National", "Home", "Away"
                    "info": CHANNEL_ALIASES.get(channel, {}),
                })

        # Status
        status_type = ev.get("status", {}).get("type", {})
        status = {
            "state": status_type.get("state", "pre"),  # pre, in, post
            "detail": ev.get("status", {}).get("type", {}).get("detail", ""),
            "display": status_type.get("description", "Scheduled"),
        }

        # Venue
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


# ── TheSportsDB (TV Broadcasts enrichment) ───────────────

async def fetch_sportsdb_tv(event_name: str) -> list[dict]:
    """Try to find TV broadcast info from TheSportsDB by event name.
    Free tier is very limited, so we use this sparingly.
    """
    cache_key = f"sportsdb:tv:{event_name}"
    if cache_key in _cache:
        return _cache[cache_key]

    url = f"{SPORTSDB_BASE}/searchevents.php"
    params = {"e": event_name}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            events = data.get("event") or []
            tv_info = []
            for ev in events[:1]:  # just first match
                tv = ev.get("strTVStation", "")
                if tv:
                    tv_info = [{"channel": ch.strip()} for ch in tv.split(",")]
            _cache[cache_key] = tv_info
            return tv_info
        except Exception as e:
            logger.warning(f"TheSportsDB error: {e}")
            return []


# ── Main aggregator ──────────────────────────────────────

async def get_todays_games(
    date_str: Optional[str] = None,
    league_filter: Optional[str] = None,
    sport_filter: Optional[str] = None,
) -> list[dict]:
    """
    Fetch today's games across all configured leagues.
    Returns a flat list of events sorted by date.
    """
    if not date_str:
        # Use US Central time as default (good for MX/US)
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
        events = parse_espn_events(result, slug)
        all_events.extend(events)

    # Sort by date
    all_events.sort(key=lambda e: e.get("date", ""))
    return all_events


async def search_games(query: str, date_str: Optional[str] = None) -> list[dict]:
    """
    Search for games matching a query (team name, league, etc.)
    Used primarily by the WhatsApp bot.
    """
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
