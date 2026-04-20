"""
Sports data fetcher — pulls schedules from ESPN's public API
and enriches with TV broadcast data from TheSportsDB Premium.
"""

import httpx
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from cachetools import TTLCache

from config import (
    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,
    LEAGUES, ALL_LEAGUES, CHANNEL_ALIASES, TZ_MX, TEAM_ALIASES
)

logger = logging.getLogger("dondever.sports")

# Cache: 5 min TTL, max 500 entries
_cache = TTLCache(maxsize=500, ttl=300)
# TV cache: 4 hour TTL — TheSportsDB free tier has aggressive rate limits (429)
_tv_cache = TTLCache(maxsize=1000, ttl=14400)
# Track when TheSportsDB is rate-limiting us to avoid flooding with 429s
_sportsdb_blocked_until = 0  # timestamp when we can retry
# Odds cache: 4 hour TTL — free tier only has 500 req/month, must conserve
_odds_cache = TTLCache(maxsize=200, ttl=14400)

# ── Odds API (the-odds-api.com) ────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"

# Map our league slugs to the-odds-api sport keys
# NOTE: Limited to high-traffic leagues to conserve free tier (500 req/month)
# With 4h cache + 6 leagues ≈ ~6 req/day × 30 days = ~180 req/month (safe margin)
# Add more leagues back when upgrading to paid plan
ODDS_SPORT_MAP = {
    "liga-mx": "soccer_mexico_ligamx",
    "premier-league": "soccer_epl",
    "champions": "soccer_uefa_champs_league",
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
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

        # 2) TheSportsDB disabled — free API key only returns 404s
        # ESPN geoBroadcasts + CHANNEL_ALIASES is sufficient
        # Re-enable if we get a premium TheSportsDB key in the future

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
    # NBA
    "lakers": ("basketball", "nba"), "celtics": ("basketball", "nba"),
    "warriors": ("basketball", "nba"), "bulls": ("basketball", "nba"),
    "heat": ("basketball", "nba"), "knicks": ("basketball", "nba"),
    # NFL
    "cowboys": ("football", "nfl"), "chiefs": ("football", "nfl"),
    "49ers": ("football", "nfl"), "eagles": ("football", "nfl"),
    "packers": ("football", "nfl"),
    # MLB
    "dodgers": ("baseball", "mlb"), "yankees": ("baseball", "mlb"),
    "red-sox": ("baseball", "mlb"), "astros": ("baseball", "mlb"),
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


async def get_league_standings(sport: str, league: str, limit: int = 10) -> list[dict]:
    """Get top N standings for a league."""
    standings = await fetch_standings(sport, league)
    return standings[:limit]


# ── Odds API Functions ──────────────────────────────────

async def fetch_odds(league_slug: str) -> list[dict]:
    """
    Fetch odds from the-odds-api.com for a given league.
    Returns list of games with odds from top bookmakers.
    Requires ODDS_API_KEY env var.
    Free tier: 500 requests/month — use caching aggressively.
    """
    if not ODDS_API_KEY:
        return []

    odds_sport = ODDS_SPORT_MAP.get(league_slug)
    if not odds_sport:
        return []

    cache_key = f"odds:{odds_sport}"
    if cache_key in _odds_cache:
        return _odds_cache[cache_key]

    url = f"{ODDS_API_BASE}/{odds_sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us,eu",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            _odds_cache[cache_key] = data
            return data
    except Exception as e:
        logger.warning(f"Odds API error for {odds_sport}: {e}")
        return []


def match_odds_to_game(game: dict, odds_list: list[dict]) -> dict | None:
    """
    Match a game (from ESPN) to odds data (from the-odds-api).
    Returns dict with odds info or None if no match found.
    Uses fuzzy team name matching.
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
            # Extract best odds from first bookmaker
            bookmakers = odds_game.get("bookmakers", [])
            if not bookmakers:
                return None

            # Try to find a well-known bookmaker first
            preferred = ["draftkings", "fanduel", "betmgm", "pinnacle", "bet365"]
            bookie = None
            for pref in preferred:
                bookie = next((b for b in bookmakers if pref in b["key"].lower()), None)
                if bookie:
                    break
            if not bookie:
                bookie = bookmakers[0]

            markets = bookie.get("markets", [])
            h2h = next((m for m in markets if m["key"] == "h2h"), None)
            if not h2h:
                return None

            outcomes = h2h.get("outcomes", [])
            result = {
                "bookmaker": bookie.get("title", ""),
                "home_odds": None,
                "away_odds": None,
                "draw_odds": None,
            }
            for outcome in outcomes:
                name = outcome.get("name", "").lower()
                price = outcome.get("price", 0)
                if "draw" in name:
                    result["draw_odds"] = _format_american_odds(price)
                elif any(w in name for w in home_name.split() if len(w) > 3):
                    result["home_odds"] = _format_american_odds(price)
                elif any(w in name for w in away_name.split() if len(w) > 3):
                    result["away_odds"] = _format_american_odds(price)

            # Fallback: assign by position if name matching failed
            if not result["home_odds"] and len(outcomes) >= 2:
                result["home_odds"] = _format_american_odds(outcomes[0].get("price", 0))
                result["away_odds"] = _format_american_odds(outcomes[1].get("price", 0))
                if len(outcomes) >= 3:
                    result["draw_odds"] = _format_american_odds(outcomes[2].get("price", 0))

            return result

    return None


def _format_american_odds(price: int) -> str:
    """Format american odds with + or - prefix."""
    if price >= 0:
        return f"+{price}"
    return str(price)
