"""
Twitter/X bot for DondeVer.app
Auto-posts before each game with where to watch + affiliate links.
Includes: game alerts, daily summary, pick del dia.
"""

import tweepy
import asyncio
import logging
import random
import os
from datetime import datetime, timezone, timedelta
from config import AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS, get_affiliate_url
from sports_api import get_todays_games

logger = logging.getLogger("dondever.twitter")

# ── Twitter API Setup ────────────────────────────────────

TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")

# WhatsApp link for CTA
WA_PICKS_LINK = "https://wa.me/15715463202?text=picks"


def twitter_credentials_valid() -> bool:
    """Check that all 4 Twitter credentials are set and non-empty."""
    return all([TWITTER_API_KEY, TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET])


def get_twitter_client() -> tweepy.Client | None:
    """Create Twitter API v2 client. Returns None if credentials missing."""
    if not twitter_credentials_valid():
        logger.error("Twitter credentials incomplete — cannot create client")
        return None
    return tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
    )


# ── Tweet Formatters ─────────────────────────────────────

def format_broadcast_short(broadcasts: list[dict]) -> str:
    """Short channel list for tweets."""
    if not broadcasts:
        return "Por confirmar"
    channels = [b["channel"] for b in broadcasts[:3]]
    return " / ".join(channels)


def format_game_time_mx(date_str: str) -> str:
    """Convert to MX time string."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


def get_betting_affiliate_text() -> str:
    """Get a random BETTING affiliate CTA (no VPN) with Twitter tracking."""
    betting_keys = [k for k in AFFILIATES if k in ("1xbet", "betsson")]
    if not betting_keys:
        return ""
    key = random.choice(betting_keys)
    aff = AFFILIATES[key]
    tracked_url = get_affiliate_url(key, source="twitter")
    return f"{aff['cta']}: {tracked_url}"


def get_team_order(game: dict) -> tuple[str, str]:
    """Return (first_team, second_team) respecting sport conventions."""
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


HASHTAG_MAP = {
    "Liga MX": "#LigaMX", "MLS": "#MLS", "Premier League": "#PremierLeague",
    "La Liga": "#LaLiga", "Serie A": "#SerieA", "Bundesliga": "#Bundesliga",
    "Champions League": "#ChampionsLeague", "Europa League": "#EuropaLeague",
    "NFL": "#NFL", "NBA": "#NBA", "MLB": "#MLB", "NHL": "#NHL",
    "UFC": "#UFC", "Formula 1": "#F1", "Ligue 1": "#Ligue1",
    "Copa del Mundo": "#Mundial", "Liga Expansion MX": "#LigaExpansion",
    "Concacaf Champions Cup": "#Concacaf",
}


def compose_game_tweet(game: dict) -> str:
    """
    Compose a tweet for a single game.
    Max 280 chars. Includes: teams, time, channels, betting link.
    """
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    first, second = get_team_order(game)
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])
    hashtag = HASHTAG_MAP.get(league, "")

    # Betting CTA
    betting = get_betting_affiliate_text()

    # Build tweet
    headline = f"{emoji} {first} vs {second}"
    time_line = f"Hoy {time_str} (MX)"
    channel_line = f"Donde verlo: {channels}"
    site_link = APP_URL
    tags = f"{hashtag} #DondeVer" if hashtag else "#DondeVer"

    # Try full version with betting link
    parts = [headline, time_line, channel_line]
    if betting:
        parts.append(f"\n{betting}")
    parts.append(site_link)
    parts.append(tags)

    tweet = "\n".join(parts)

    # Trim if too long
    if len(tweet) > 280:
        parts = [headline, time_line, channel_line, site_link, tags]
        tweet = "\n".join(parts)

    if len(tweet) > 280:
        parts = [headline, time_line, channel_line, site_link]
        tweet = "\n".join(parts)

    if len(tweet) > 280:
        tweet = f"{headline}\n{time_str} - {channels}\n{site_link}"

    return tweet[:280]


def compose_daily_summary_tweet(games: list[dict]) -> str:
    """Compose a summary tweet with game count + WhatsApp picks CTA."""
    count = len(games)
    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d/%m")

    sports = set()
    for g in games:
        sports.add(g.get("league_name", ""))

    leagues_text = ", ".join(list(sports)[:5])
    if len(sports) > 5:
        leagues_text += f" y {len(sports) - 5} mas"

    betting = get_betting_affiliate_text()

    tweet = (
        f"Hoy {date_str} hay {count} juegos en vivo\n\n"
        f"{leagues_text}\n\n"
        f"Ve donde verlos: {APP_URL}\n\n"
        f"Recibe picks gratis diario por WhatsApp:\n"
        f"{WA_PICKS_LINK}"
    )

    # Add betting if it fits
    if betting and len(tweet) + len(betting) + 2 <= 280:
        tweet += f"\n\n{betting}"

    return tweet[:280]


def compose_pick_tweet(game: dict) -> str:
    """Compose a PICK DEL DIA tweet — the money tweet."""
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    first, second = get_team_order(game)
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])
    hashtag = HASHTAG_MAP.get(league, "")
    betting = get_betting_affiliate_text()

    tweet = (
        f"PICK DEL DIA\n\n"
        f"{emoji} {first} vs {second}\n"
        f"{league} - {time_str} (MX)\n"
        f"Donde verlo: {channels}\n"
    )

    if betting:
        tweet += f"\n{betting}\n"

    tweet += (
        f"\nRecibe picks diarios gratis:\n"
        f"{WA_PICKS_LINK}\n\n"
    )

    if hashtag:
        tweet += f"{hashtag} #DondeVer"
    else:
        tweet += "#DondeVer"

    # Trim disclaimer text first if too long
    if len(tweet) > 280:
        tweet = (
            f"PICK DEL DIA\n\n"
            f"{emoji} {first} vs {second}\n"
            f"{league} - {time_str} (MX)\n"
            f"Donde verlo: {channels}\n"
        )
        if betting:
            tweet += f"\n{betting}\n"
        tweet += f"\n{APP_URL}\n{hashtag} #DondeVer" if hashtag else f"\n{APP_URL}\n#DondeVer"

    return tweet[:280]


# ── Post Functions ───────────────────────────────────────

def post_tweet(text: str) -> dict:
    """Post a tweet via Twitter API v2."""
    try:
        client = get_twitter_client()
        if client is None:
            return {"success": False, "error": "Twitter credentials not configured"}
        response = client.create_tweet(text=text)
        logger.info(f"Tweet posted: {response.data['id']}")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return {"success": False, "error": str(e)}


async def post_game_tweets(minutes_before: int = 30):
    """
    Check for games starting soon and post tweets for them.
    Call this periodically (e.g., every 10 minutes via scheduler).
    """
    games = await get_todays_games()
    now = datetime.now(timezone.utc)

    posted = []
    for game in games:
        if game["status"]["state"] != "pre":
            continue

        try:
            game_time = datetime.fromisoformat(
                game["date"].replace("Z", "+00:00")
            )
        except Exception:
            continue

        # Post if game starts within the next `minutes_before` minutes
        diff = (game_time - now).total_seconds() / 60
        if 0 < diff <= minutes_before:
            tweet_text = compose_game_tweet(game)
            result = post_tweet(tweet_text)
            if result["success"]:
                posted.append({
                    "game": game["name"],
                    "tweet_id": result["tweet_id"],
                })

    return posted


async def post_pick_del_dia():
    """Post the pick del dia tweet — best upcoming game with betting CTA."""
    games = await get_todays_games()
    priority = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga"]

    # Find best upcoming game with broadcasts
    upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]
    pick = None
    for pl in priority:
        pick = next((g for g in upcoming if g["league_slug"] == pl), None)
        if pick:
            break
    if not pick and upcoming:
        pick = upcoming[0]

    if not pick:
        logger.info("No pick del dia available — no upcoming games with broadcasts")
        return None

    tweet_text = compose_pick_tweet(pick)
    result = post_tweet(tweet_text)
    if result["success"]:
        logger.info(f"Pick del dia posted: {pick['name']}")
    return result


# ── Live Game Monitor (Reactive Tweets) ─────────────────

# Track scores to detect changes (goals, etc.)
_last_scores: dict[str, dict] = {}


def compose_live_tweet(game: dict, event_type: str, detail: str = "") -> str:
    """
    Compose a reactive tweet for a live game event.
    event_type: 'goal', 'started', 'halftime', 'final'
    """
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    first, second = get_team_order(game)
    first_score = game["home"]["score"] if game.get("sport", "") in HOME_LEFT_SPORTS else game["away"]["score"]
    second_score = game["away"]["score"] if game.get("sport", "") in HOME_LEFT_SPORTS else game["home"]["score"]
    hashtag = HASHTAG_MAP.get(league, "")
    channels = format_broadcast_short(game["broadcasts"])
    betting = get_betting_affiliate_text()

    if event_type == "goal":
        exclamations = ["GOOOL!", "GOL!", "GOLAZO!", "SE METIO!"]
        excl = random.choice(exclamations)
        headline = f"{excl} {emoji}\n\n{first} {first_score} - {second_score} {second}"
    elif event_type == "score_change":
        headline = f"ANOTACION! {emoji}\n\n{first} {first_score} - {second_score} {second}"
    elif event_type == "started":
        headline = f"ARRANCA! {emoji}\n\n{first} vs {second}\nEN VIVO ahora"
    elif event_type == "halftime":
        headline = f"MEDIO TIEMPO {emoji}\n\n{first} {first_score} - {second_score} {second}"
    elif event_type == "final":
        headline = f"FINAL! {emoji}\n\n{first} {first_score} - {second_score} {second}"
    else:
        headline = f"{emoji} {first} {first_score} - {second_score} {second}"

    parts = [headline, f"{league}"]

    if event_type in ("started", "goal", "score_change"):
        parts.append(f"Donde verlo: {channels}")

    if betting:
        parts.append(f"\n{betting}")

    parts.append(APP_URL)

    if hashtag:
        parts.append(f"{hashtag} #DondeVer")
    else:
        parts.append("#DondeVer")

    tweet = "\n".join(parts)

    # Trim if needed
    if len(tweet) > 280:
        parts = [headline, league, APP_URL]
        if hashtag:
            parts.append(f"{hashtag} #DondeVer")
        tweet = "\n".join(parts)

    return tweet[:280]


async def monitor_live_games():
    """
    Monitor live games for score changes and key events.
    Called every 2 minutes by scheduler.
    Posts reactive tweets when:
    - A game starts (state changes to 'in')
    - Score changes (goal/touchdown/run)
    - Halftime
    - Game ends (state changes to 'post')

    Only tweets for priority leagues to avoid spam.
    """
    global _last_scores

    priority_leagues = {
        "liga-mx", "premier-league", "champions", "la-liga",
        "nfl", "nba", "mlb", "serie-a", "bundesliga",
        "europa-league", "concacaf-cl", "mls",
    }

    games = await get_todays_games()
    posted = []

    for game in games:
        game_id = game["id"]
        slug = game["league_slug"]

        # Only monitor priority leagues
        if slug not in priority_leagues:
            continue

        state = game["status"]["state"]
        home_score = game["home"]["score"] or "0"
        away_score = game["away"]["score"] or "0"
        detail = game["status"].get("detail", "")

        current = {
            "state": state,
            "home_score": str(home_score),
            "away_score": str(away_score),
            "detail": detail,
        }

        prev = _last_scores.get(game_id)

        if prev is None:
            # First time seeing this game — just store it
            _last_scores[game_id] = current
            continue

        # Detect events
        event_type = None

        # Game just started
        if prev["state"] == "pre" and state == "in":
            event_type = "started"

        # Game just ended
        elif prev["state"] == "in" and state == "post":
            event_type = "final"

        # Score changed (GOAL / TOUCHDOWN / etc.)
        elif state == "in" and (
            prev["home_score"] != str(home_score) or
            prev["away_score"] != str(away_score)
        ):
            sport = game.get("sport", "")
            if sport == "soccer":
                event_type = "goal"
            else:
                event_type = "score_change"

        # Halftime detection (check detail string)
        elif state == "in" and "half" in detail.lower() and "half" not in prev.get("detail", "").lower():
            event_type = "halftime"

        # Post tweet if event detected
        if event_type:
            # Don't tweet every score change in high-scoring sports — only big moments
            if event_type == "score_change":
                sport = game.get("sport", "")
                # For basketball, only tweet every ~10 points; for baseball every run
                if sport == "basketball":
                    total_now = int(home_score or 0) + int(away_score or 0)
                    total_prev = int(prev["home_score"] or 0) + int(prev["away_score"] or 0)
                    if (total_now - total_prev) < 8:
                        _last_scores[game_id] = current
                        continue
                elif sport == "hockey":
                    # Tweet every goal in hockey
                    pass
                # Football: tweet touchdowns (6+ point changes)
                elif sport == "football":
                    home_diff = abs(int(home_score or 0) - int(prev["home_score"] or 0))
                    away_diff = abs(int(away_score or 0) - int(prev["away_score"] or 0))
                    if max(home_diff, away_diff) < 6:
                        _last_scores[game_id] = current
                        continue

            tweet_text = compose_live_tweet(game, event_type, detail)
            result = post_tweet(tweet_text)
            if result["success"]:
                posted.append({
                    "game": game["name"],
                    "event": event_type,
                    "tweet_id": result["tweet_id"],
                })
                logger.info(f"Live tweet: {event_type} — {game['name']}")

        # Update stored state
        _last_scores[game_id] = current

    # Clean up old games (not in today's list)
    current_ids = {g["id"] for g in games}
    stale = [gid for gid in _last_scores if gid not in current_ids]
    for gid in stale:
        del _last_scores[gid]

    if posted:
        logger.info(f"Live monitor: {len(posted)} tweets posted")

    return posted


# ── Scheduler Integration ────────────────────────────────

def setup_twitter_scheduler(scheduler):
    """
    Add Twitter bot jobs to APScheduler (AsyncIOScheduler).
    Call this from server.py on startup.
    """
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    if not twitter_credentials_valid():
        logger.warning("Twitter credentials incomplete — scheduler NOT started")
        return

    # 1) Check for upcoming games every 10 minutes
    scheduler.add_job(
        post_game_tweets,
        IntervalTrigger(minutes=10),
        id="twitter_game_posts",
        name="Post tweets for upcoming games",
        replace_existing=True,
        kwargs={"minutes_before": 15},
    )

    # 2) Daily summary at 8 AM MX time (14:00 UTC)
    async def post_daily():
        games = await get_todays_games()
        if games:
            tweet = compose_daily_summary_tweet(games)
            post_tweet(tweet)

    scheduler.add_job(
        post_daily,
        CronTrigger(hour=14, minute=0),
        id="twitter_daily_summary",
        name="Daily game summary tweet",
        replace_existing=True,
    )

    # 3) Pick del dia at 10 AM MX time (16:00 UTC)
    scheduler.add_job(
        post_pick_del_dia,
        CronTrigger(hour=16, minute=0),
        id="twitter_pick_del_dia",
        name="Pick del dia tweet",
        replace_existing=True,
    )

    # 4) Live game monitor — every 2 minutes, check for goals/events
    scheduler.add_job(
        monitor_live_games,
        IntervalTrigger(minutes=2),
        id="twitter_live_monitor",
        name="Live game monitor (goals, starts, finals)",
        replace_existing=True,
    )

    logger.info("Twitter bot scheduler configured (4 jobs: games, summary, pick, live monitor)")
