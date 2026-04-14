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

# WhatsApp links for CTAs (rotate to show different features)
WA_PICKS_LINK = "https://wa.me/15715463202?text=picks"
WA_HOY_LINK = "https://wa.me/15715463202?text=hoy"

WA_CTAS = [
    ("Picks gratis por WhatsApp", "https://wa.me/15715463202?text=picks"),
    ("Alertas de gol por WhatsApp", "https://wa.me/15715463202?text=alerta"),
    ("Juegos de hoy por WhatsApp", "https://wa.me/15715463202?text=hoy"),
    ("Recibe alertas 1h antes del partido", "https://wa.me/15715463202?text=alerta"),
]

def get_wa_cta() -> str:
    """Get a random WhatsApp CTA for tweets."""
    text, link = random.choice(WA_CTAS)
    return f"{text}: {link}"


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
    betting_keys = [k for k in AFFILIATES if k in ("caliente", "1xbet", "betsson")]
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


def get_pick_team(game: dict) -> str:
    """
    Pick a team for DondeVer Pick. Favors home team 60% of the time
    (home advantage bias makes it feel more credible).
    """
    home = game["home"]["name"]
    away = game["away"]["name"]
    return home if random.random() < 0.6 else away


def compose_game_tweet(game: dict) -> str:
    """
    Compose a tweet for a single game.
    Max 280 chars. Every tweet includes DondeVer Pick + betting link.
    """
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    first, second = get_team_order(game)
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])
    hashtag = HASHTAG_MAP.get(league, "")

    # DondeVer Pick + betting CTA
    pick_team = get_pick_team(game)
    betting = get_betting_affiliate_text()

    # Build tweet
    headline = f"{emoji} {first} vs {second}"
    time_line = f"Hoy {time_str} (MX)"
    channel_line = f"Donde verlo: {channels}"
    pick_line = f"DondeVer Pick: {pick_team}"
    tags = f"{hashtag} #DondeVer" if hashtag else "#DondeVer"

    # Full version: headline + time + channels + pick + betting + tags
    parts = [headline, time_line, channel_line, f"\n{pick_line}"]
    if betting:
        parts.append(betting)
    parts.append(tags)

    tweet = "\n".join(parts)

    # Trim: drop betting if too long
    if len(tweet) > 280:
        parts = [headline, time_line, channel_line, f"\n{pick_line}", tags]
        tweet = "\n".join(parts)

    # Trim: drop channels if still too long
    if len(tweet) > 280:
        parts = [headline, time_line, f"\n{pick_line}", tags]
        tweet = "\n".join(parts)

    if len(tweet) > 280:
        tweet = f"{headline}\n{time_str} - {channels}\n{site_link}"

    return tweet[:280]


def compose_daily_summary_tweet(games: list[dict]) -> str:
    """Compose a summary tweet with game count + DondeVer Pick of the day."""
    count = len(games)
    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d/%m")

    sports = set()
    for g in games:
        sports.add(g.get("league_name", ""))

    leagues_text = ", ".join(list(sports)[:5])
    if len(sports) > 5:
        leagues_text += f" y {len(sports) - 5} mas"

    # Pick the top game for DondeVer Pick
    betting = get_betting_affiliate_text()
    pick_text = ""
    if games:
        top_game = games[0]
        pick_team = get_pick_team(top_game)
        first, second = get_team_order(top_game)
        pick_text = f"\nDondeVer Pick: {pick_team} ({first} vs {second})"

    tweet = (
        f"Hoy {date_str} hay {count} juegos en vivo\n\n"
        f"{leagues_text}\n"
        f"{pick_text}\n\n"
        f"Ve donde verlos: {APP_URL}"
    )

    if betting and len(tweet) + len(betting) + 2 <= 280:
        tweet += f"\n\n{betting}"

    # Add WhatsApp CTA if it fits (rotate features)
    wa_cta_text = f"\n\n{get_wa_cta()}"
    if len(tweet) + len(wa_cta_text) <= 280:
        tweet += wa_cta_text

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

    tweet += f"\n{get_wa_cta()}\n\n"

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

# Rate limiter: max tweets per hour and per day (X reglas anti-spam)
from collections import deque
import time as _time

_tweet_timestamps: deque = deque()  # stores unix timestamps of recent tweets
MAX_TWEETS_PER_HOUR = 8  # conservador, muy por debajo de limites de X
MAX_TWEETS_PER_DAY = 40  # conservador para cuenta automatizada
MIN_SECONDS_BETWEEN_TWEETS = 180  # 3 min minimo entre tweets (evita burst posting)


def _can_post_now() -> tuple[bool, str]:
    """Check rate limits. Returns (allowed, reason_if_denied)."""
    now = _time.time()
    # Purge old timestamps (keep last 24h)
    while _tweet_timestamps and _tweet_timestamps[0] < now - 86400:
        _tweet_timestamps.popleft()

    # Daily limit
    if len(_tweet_timestamps) >= MAX_TWEETS_PER_DAY:
        return False, f"rate_limit: {MAX_TWEETS_PER_DAY}/dia alcanzado"

    # Hourly limit
    recent_hour = sum(1 for t in _tweet_timestamps if t > now - 3600)
    if recent_hour >= MAX_TWEETS_PER_HOUR:
        return False, f"rate_limit: {MAX_TWEETS_PER_HOUR}/hora alcanzado"

    # Min gap between tweets
    if _tweet_timestamps and (now - _tweet_timestamps[-1]) < MIN_SECONDS_BETWEEN_TWEETS:
        gap = int(now - _tweet_timestamps[-1])
        return False, f"rate_limit: minimo {MIN_SECONDS_BETWEEN_TWEETS}s entre tweets (actual {gap}s)"

    return True, ""


def post_tweet(text: str) -> dict:
    """Post a tweet via Twitter API v2 — with rate limiting."""
    allowed, reason = _can_post_now()
    if not allowed:
        logger.warning(f"Tweet skipped: {reason}")
        return {"success": False, "error": reason, "rate_limited": True}

    try:
        client = get_twitter_client()
        if client is None:
            return {"success": False, "error": "Twitter credentials not configured"}
        response = client.create_tweet(text=text)
        _tweet_timestamps.append(_time.time())
        logger.info(f"Tweet posted: {response.data['id']} ({len(_tweet_timestamps)}/{MAX_TWEETS_PER_DAY} hoy)")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return {"success": False, "error": str(e)}


# Dedup: game IDs already tweeted (resets cada dia con la fecha)
_posted_games: dict[str, set] = {}  # {"2026-04-14": {"game_id_1", ...}}

def _already_posted(game_id: str) -> bool:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return game_id in _posted_games.get(today_key, set())

def _mark_posted(game_id: str) -> None:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Reset otros dias (libera memoria)
    for k in list(_posted_games.keys()):
        if k != today_key:
            del _posted_games[k]
    _posted_games.setdefault(today_key, set()).add(game_id)


async def post_game_tweets(minutes_before: int = 60):
    """
    Check for games starting soon and post tweets for them.
    Uses dedup para no tuitear el mismo juego 2 veces el mismo dia.
    """
    games = await get_todays_games()
    now = datetime.now(timezone.utc)

    posted = []
    for game in games:
        if game["status"]["state"] != "pre":
            continue

        gid = str(game.get("id", "")) or game.get("name", "")
        if _already_posted(gid):
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
                _mark_posted(gid)
                posted.append({
                    "game": game["name"],
                    "tweet_id": result["tweet_id"],
                })

    return posted


async def post_next_top_game():
    """
    Postea el proximo juego 'top' (liga popular) aunque sea en 2-4h.
    1 vez por dia. Si no hay top game, no postea.
    """
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sentinel = f"__next_top__{today_key}"
    if sentinel in _posted_games.get(today_key, set()):
        return None  # ya se posteo hoy

    games = await get_todays_games()
    now = datetime.now(timezone.utc)

    priority_leagues = [
        "liga-mx", "champions", "nfl", "nba", "premier-league",
        "la-liga", "mlb", "concacaf",
    ]

    def top_score(g):
        league = (g.get("league", "") or "").lower()
        for i, p in enumerate(priority_leagues):
            if p in league:
                return i
        return 99

    upcoming = []
    for g in games:
        if g["status"]["state"] != "pre":
            continue
        gid = str(g.get("id", "")) or g.get("name", "")
        if _already_posted(gid):
            continue
        try:
            gt = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
            diff_min = (gt - now).total_seconds() / 60
            if 60 < diff_min <= 240:  # entre 1h y 4h
                upcoming.append((top_score(g), diff_min, g))
        except Exception:
            continue

    if not upcoming:
        return None

    upcoming.sort(key=lambda x: (x[0], x[1]))  # mejor liga + mas pronto
    _, _, best = upcoming[0]
    tweet_text = compose_game_tweet(best)
    result = post_tweet(tweet_text)
    if result["success"]:
        gid = str(best.get("id", "")) or best.get("name", "")
        _mark_posted(gid)
        _mark_posted(sentinel)  # bloquea otro "next top" hoy
        return {"game": best["name"], "tweet_id": result["tweet_id"]}
    return None


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

    # DondeVer Pick for live tweets
    pick_team = get_pick_team(game)
    pick_line = f"DondeVer Pick: {pick_team}"

    parts = [headline, f"{league}"]

    if event_type in ("started", "goal", "score_change"):
        parts.append(f"Donde verlo: {channels}")

    parts.append(f"\n{pick_line}")
    if betting:
        parts.append(betting)

    if hashtag:
        parts.append(f"{hashtag} #DondeVer")
    else:
        parts.append("#DondeVer")

    tweet = "\n".join(parts)

    # Trim: drop betting if too long
    if len(tweet) > 280:
        parts = [headline, league, f"\n{pick_line}"]
        if hashtag:
            parts.append(f"{hashtag} #DondeVer")
        else:
            parts.append("#DondeVer")
        tweet = "\n".join(parts)

    # Trim: drop pick if still too long
    if len(tweet) > 280:
        parts = [headline, league]
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

            # Send WhatsApp goal/event alerts to subscribers with favorite teams
            try:
                from whatsapp_alerts import send_goal_alerts
                await send_goal_alerts(game, event_type)
            except Exception as e:
                logger.warning(f"Goal alert failed: {e}")

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

    # 1) Check for upcoming games every 15 min — ventana 60 min con dedup
    scheduler.add_job(
        post_game_tweets,
        IntervalTrigger(minutes=15),
        id="twitter_game_posts",
        name="Post tweets for upcoming games",
        replace_existing=True,
        kwargs={"minutes_before": 60},
    )

    # 1b) Post next top game (1-4h away) — 1 vez al dia a las 12:00 MX (18:00 UTC)
    scheduler.add_job(
        post_next_top_game,
        CronTrigger(hour=18, minute=0),
        id="twitter_next_top_game",
        name="Post next top game (1-4h ahead)",
        replace_existing=True,
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

    # 4) Live game monitor — every 5 min (suficiente para goles sin spammear)
    scheduler.add_job(
        monitor_live_games,
        IntervalTrigger(minutes=5),
        id="twitter_live_monitor",
        name="Live game monitor (goals, starts, finals)",
        replace_existing=True,
    )

    logger.info("Twitter bot scheduler configured (4 jobs: games, summary, pick, live monitor)")
