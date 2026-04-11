"""
Twitter/X bot for DondeVer.app
Auto-posts before each game with where to watch + affiliate links.
"""

import tweepy
import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from config import AFFILIATES, APP_URL, TZ_MX, get_affiliate_url
from sports_api import get_todays_games

logger = logging.getLogger("dondever.twitter")

# ── Twitter API Setup ────────────────────────────────────
import os

TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")


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
        return "Canal por confirmar"
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


def get_random_affiliate_text() -> str:
    """Get a random affiliate CTA for the tweet with Twitter tracking."""
    options = []
    for key, aff in AFFILIATES.items():
        if aff["url"] != "#":
            tracked_url = get_affiliate_url(key, source="twitter")
            options.append(f"{aff['cta']}: {tracked_url}")
    if options:
        return random.choice(options)
    return ""


def compose_game_tweet(game: dict) -> str:
    """
    Compose a tweet for a single game.
    Max 280 chars, includes: teams, time, channels, link, affiliate.
    """
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    home = game["home"]["name"]
    away = game["away"]["name"]
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])

    # Build hashtags from league name
    league = game.get("league_name", "")
    hashtag_map = {
        "Liga MX": "#LigaMX", "MLS": "#MLS", "Premier League": "#PremierLeague",
        "La Liga": "#LaLiga", "Serie A": "#SerieA", "Bundesliga": "#Bundesliga",
        "Champions League": "#ChampionsLeague", "NFL": "#NFL", "NBA": "#NBA",
        "MLB": "#MLB", "NHL": "#NHL", "UFC": "#UFC", "Formula 1": "#F1",
        "Ligue 1": "#Ligue1", "Copa del Mundo": "#Mundial",
    }
    hashtag = hashtag_map.get(league, "")

    # Build tweet parts
    headline = f"{emoji} {away} vs {home}"
    time_line = f"Hoy {time_str} (hora centro)"
    channel_line = f"Donde verlo: {channels}"
    link = f"{APP_URL}"
    affiliate = get_random_affiliate_text()
    tags = f"{hashtag} #DondeVer" if hashtag else "#DondeVer"

    # Combine and check length
    parts = [headline, time_line, channel_line, link, tags]
    if affiliate:
        parts.insert(-1, f"\n{affiliate}")

    tweet = "\n".join(parts)

    # Trim if too long — remove parts in priority order
    if len(tweet) > 280:
        # Remove affiliate first
        parts = [headline, time_line, channel_line, link, tags]
        tweet = "\n".join(parts)

    if len(tweet) > 280:
        # Remove hashtags
        parts = [headline, time_line, channel_line, link]
        tweet = "\n".join(parts)

    if len(tweet) > 280:
        tweet = f"{headline}\n{time_str} - {channels}\n{link}"

    return tweet[:280]


def compose_daily_summary_tweet(games: list[dict]) -> str:
    """Compose a summary tweet with game count."""
    count = len(games)
    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d/%m")

    sports = set()
    for g in games:
        sports.add(g.get("league_name", ""))

    leagues_text = ", ".join(list(sports)[:5])
    if len(sports) > 5:
        leagues_text += f" y {len(sports) - 5} mas"

    tweet = (
        f"Hoy {date_str} hay {count} juegos en vivo\n\n"
        f"{leagues_text}\n\n"
        f"Ve donde verlos todos: {APP_URL}"
    )

    affiliate = get_random_affiliate_text()
    if affiliate and len(tweet) + len(affiliate) + 2 < 280:
        tweet += f"\n\n{affiliate}"

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


# ── Scheduler Integration ────────────────────────────────

def setup_twitter_scheduler(scheduler):
    """
    Add Twitter bot jobs to APScheduler (AsyncIOScheduler).
    Call this from server.py on startup.
    Jobs are async functions — AsyncIOScheduler handles them natively.
    """
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    if not twitter_credentials_valid():
        logger.warning("Twitter credentials incomplete — scheduler NOT started")
        return

    # Check for upcoming games every 10 minutes
    scheduler.add_job(
        post_game_tweets,
        IntervalTrigger(minutes=10),
        id="twitter_game_posts",
        name="Post tweets for upcoming games",
        replace_existing=True,
        kwargs={"minutes_before": 15},
    )

    # Daily summary at 8 AM MX time (14:00 UTC)
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

    logger.info("Twitter bot scheduler configured")
