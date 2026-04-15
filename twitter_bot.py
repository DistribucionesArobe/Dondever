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
from config import AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS, get_affiliate_url, get_short_affiliate_url
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
    """
    Random BETTING affiliate CTA con link corto branded.
    Ej: 'Apostar en Betsson 👉 dondever.app/go/betsson?s=twitter'
    """
    betting_keys = [k for k in AFFILIATES if k in ("caliente", "betsson")]
    if not betting_keys:
        return ""
    key = random.choice(betting_keys)
    aff = AFFILIATES[key]
    short_url = get_short_affiliate_url(key, source="twitter")
    return f"{aff['cta']} 👉 {short_url}"


def get_team_order(game: dict) -> tuple[str, str]:
    """Return (first_team, second_team) respecting sport conventions."""
    sport = game.get("sport", "")
    if sport in HOME_LEFT_SPORTS:
        return game["home"]["name"], game["away"]["name"]
    return game["away"]["name"], game["home"]["name"]


HASHTAG_MAP = {
    "Liga MX": "#LigaMX", "MLS": "#MLS", "Premier League": "#PremierLeague",
    "La Liga": "#LaLiga", "Serie A": "#SerieA", "Bundesliga": "#Bundesliga",
    "Champions League": "#UCL", "Europa League": "#UEL",
    "NFL": "#NFL", "NBA": "#NBA", "MLB": "#MLB", "NHL": "#NHL",
    "UFC": "#UFC", "Formula 1": "#F1", "Ligue 1": "#Ligue1",
    "Copa del Mundo": "#Mundial", "Liga Expansion MX": "#LigaExpansion",
    "Concacaf Champions Cup": "#Concachampions",
}


# ── Engagement helpers ───────────────────────────────────
# Rotate templates y decidir si incluir betting CTA (solo 1 de cada 3)

PRE_GAME_OPENERS = [
    "{emoji} Hoy se juega:\n{first} vs {second}",
    "🔥 {first} vs {second}\nHoy a las {time} (MX)",
    "{emoji} ¿Quién gana?\n{first} vs {second} — {time} MX",
    "Ojo al partidazo 👀\n{first} vs {second} hoy {time} MX",
    "📺 {first} vs {second}\n{time} MX — {channels}",
    "{emoji} {league} HOY\n{first} vs {second} — {time} MX",
]

STARTED_OPENERS = [
    "🟢 EN VIVO\n{first} vs {second}",
    "¡Arrancó! {emoji}\n{first} vs {second}",
    "Ya rueda el balón ⚽\n{first} vs {second}" ,
    "🔴 EN VIVO ahora\n{first} vs {second}",
    "Empezó {emoji}\n{first} vs {second}",
]

GOAL_OPENERS = [
    "¡GOOOL! ⚽\n{first} {hs} - {as_} {second}",
    "GOLAZO 🔥\n{first} {hs} - {as_} {second}",
    "¡SE METIÓ! ⚽\n{first} {hs} - {as_} {second}",
    "¡GOL! 🚨\n{first} {hs} - {as_} {second}",
]

FINAL_OPENERS = [
    "🏁 FINAL\n{first} {hs} - {as_} {second}",
    "Se acabó.\n{first} {hs} - {as_} {second}",
    "⏱️ Final del partido\n{first} {hs} - {as_} {second}",
]

PICK_REASONS = [
    "viene caliente",
    "juega en casa",
    "mejor forma reciente",
    "histórico a favor",
    "favorito en momios",
    "defensa sólida últimos juegos",
]


def should_include_betting() -> bool:
    """Solo 1 de cada 3 tweets incluye link de casa de apuestas (evita shadowban)."""
    return random.random() < 0.33


# CTAs suaves que se rotan — siempre sale UNO (WhatsApp, sitio o casa)
SOFT_CTAS_WA = [
    "📲 Picks GRATIS por WhatsApp: wa.me/15715463202",
    "📲 Recibe picks diarios: wa.me/15715463202",
    "💬 Picks gratis en WhatsApp 👉 wa.me/15715463202",
    "📲 Alertas 1h antes del partido: wa.me/15715463202",
]

SOFT_CTAS_SITE = [
    "📺 Más juegos hoy: dondever.app",
    "🔗 Agenda completa: dondever.app",
    "👉 Ver todos los partidos: dondever.app",
]


def get_soft_cta() -> str:
    """
    Rota entre 3 tipos de CTA con distinta probabilidad:
    - 50% WhatsApp (capta suscriptores = valor largo plazo)
    - 30% sitio (tráfico orgánico)
    - 20% sin CTA (para no saturar)
    """
    r = random.random()
    if r < 0.50:
        return random.choice(SOFT_CTAS_WA)
    if r < 0.80:
        return random.choice(SOFT_CTAS_SITE)
    return ""


def get_pick_team(game: dict) -> str:
    """
    Pick a team for DondeVer Pick. Favors home team 60% of the time
    (home advantage bias makes it feel more credible).
    """
    home = game["home"]["name"]
    away = game["away"]["name"]
    return home if random.random() < 0.6 else away


def get_pick_line(game: dict) -> str:
    """Pick con razón corta para dar contexto creíble."""
    pick = get_pick_team(game)
    reason = random.choice(PICK_REASONS)
    return f"🎯 Pick: {pick} ({reason})"


def compose_game_tweet(game: dict) -> str:
    """
    Compose a tweet for a single game.
    Max 280 chars. Rotates templates, usa 1 hashtag, incluye pick con razón.
    Betting CTA solo 1 de cada 3 (evita shadowban por spam).
    """
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    first, second = get_team_order(game)
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])
    hashtag = HASHTAG_MAP.get(league, "#DondeVer")

    opener_tpl = random.choice(PRE_GAME_OPENERS)
    headline = opener_tpl.format(
        emoji=emoji, first=first, second=second,
        time=time_str, channels=channels, league=league,
    )

    pick_line = get_pick_line(game)

    parts = [headline, "", pick_line]

    # Agregar canales solo si el opener no los incluye
    if "{channels}" not in opener_tpl and channels and channels != "Por confirmar":
        parts.append(f"📺 {channels}")

    # Betting CTA solo 1 de cada 3, el resto lleva CTA suave (WA/sitio)
    if should_include_betting():
        betting = get_betting_affiliate_text()
        if betting:
            parts.append("")
            parts.append(betting)
    else:
        soft = get_soft_cta()
        if soft:
            parts.append("")
            parts.append(soft)

    parts.append(f"\n{hashtag}")
    tweet = "\n".join(parts)

    # Trim progresivo si se pasa de 280
    if len(tweet) > 280:
        parts = [headline, "", pick_line, "", get_soft_cta() or f"📲 wa.me/15715463202", f"\n{hashtag}"]
        tweet = "\n".join(parts)
    if len(tweet) > 280:
        parts = [headline, pick_line, f"\n{hashtag}"]
        tweet = "\n".join(parts)
    if len(tweet) > 280:
        tweet = f"{headline}\n{pick_line}\n{hashtag}"

    return tweet[:280]


PROMO_TWEETS = [
    "📺 Te decimos dónde ver cualquier partido en México y USA.\nPicks gratis todos los días 👉 wa.me/15715463202",
    "¿Cansado de buscar dónde pasan el partido?\nNosotros te lo decimos — México y USA.\nPicks gratis diarios 👇\nwa.me/15715463202",
    "No te vuelvas a perder un juego.\nCobertura MX + USA, todos los deportes.\nPicks gratis cada mañana 📲\nwa.me/15715463202",
    "✅ Liga MX\n✅ NFL, NBA, MLB\n✅ Champions, Premier, La Liga\nTe decimos dónde verlos + picks gratis diarios:\nwa.me/15715463202",
    "Si eres de los que abre 4 apps para encontrar dónde pasan el juego… te tenemos.\nMX + USA, sin vueltas.\nPicks gratis 👉 wa.me/15715463202",
    "Miles reciben picks gratis por WhatsApp cada día.\nAdemás te decimos dónde ver cualquier partido en MX y USA.\nÚnete 👇\nwa.me/15715463202",
    "Un mensaje. Todos los partidos. Picks gratis.\n🇲🇽🇺🇸 wa.me/15715463202",
    "GRATIS por WhatsApp:\n🎯 Picks diarios\n📺 Dónde ver cada juego (MX + USA)\n⏰ Alertas 1h antes del partido\nwa.me/15715463202",
    "oye 👋 si quieres saber dónde ver el partido de hoy y de paso un pick gratis, mándanos WhatsApp 👇\nwa.me/15715463202",
    "Hoy hay partidazo ¿ya sabes dónde verlo?\nNosotros sí — MX y USA.\nMándanos WhatsApp y te llegan los picks gratis cada mañana:\nwa.me/15715463202",
]


# Track which promos ya salieron hoy para no repetir
_posted_promo_idx: dict[str, list[int]] = {}


async def post_promo_tweet():
    """Postea una promo aleatoria del pool, evitando repetir las del día."""
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = set(_posted_promo_idx.get(today_key, []))
    available = [i for i in range(len(PROMO_TWEETS)) if i not in used]
    if not available:
        # Reset si ya se usaron todas
        available = list(range(len(PROMO_TWEETS)))
        used = set()

    idx = random.choice(available)
    text = PROMO_TWEETS[idx]
    result = post_tweet(text)
    if result.get("success"):
        _posted_promo_idx.setdefault(today_key, []).append(idx)
        # Limpia días viejos
        for k in list(_posted_promo_idx.keys()):
            if k != today_key:
                del _posted_promo_idx[k]
        logger.info(f"Promo tweet #{idx} posted")
    return result


DAILY_OPENERS = [
    "☕ Agenda deportiva del día",
    "📅 Lo que se juega hoy",
    "🔥 Partidazos de hoy",
    "🏟️ Hoy hay fútbol (y más)",
    "👀 No te pierdas hoy:",
]


def compose_daily_summary_tweet(games: list[dict]) -> str:
    """Resumen diario con opener variado + top 3 ligas + pregunta final."""
    count = len(games)
    now = datetime.now(TZ_MX)
    date_str = now.strftime("%d/%m")

    # Top ligas del día (las más frecuentes)
    from collections import Counter
    league_counts = Counter(g.get("league_name", "") for g in games if g.get("league_name"))
    top_leagues = [lg for lg, _ in league_counts.most_common(3)]
    leagues_text = " · ".join(top_leagues) if top_leagues else ""

    opener = random.choice(DAILY_OPENERS)

    # Pick destacado
    pick_text = ""
    if games:
        top_game = games[0]
        first, second = get_team_order(top_game)
        pick_text = f"\n🎯 Pick: {get_pick_team(top_game)} en {first} vs {second}"

    # Pregunta para engagement
    questions = [
        "¿Qué partido vas a ver? 👇",
        "¿A quién le vas hoy?",
        "Dime en comentarios qué juego no te pierdes 👇",
    ]
    q = random.choice(questions)

    tweet = (
        f"{opener} ({date_str})\n\n"
        f"{count} juegos en vivo"
    )
    if leagues_text:
        tweet += f"\n{leagues_text}"
    tweet += pick_text
    tweet += f"\n\n{q}\n\ndondever.app"

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


def post_tweet(text: str, reply_to: str | None = None) -> dict:
    """Post a tweet via Twitter API v2 — with rate limiting. Soporta replies."""
    allowed, reason = _can_post_now()
    if not allowed:
        logger.warning(f"Tweet skipped: {reason}")
        return {"success": False, "error": reason, "rate_limited": True}

    try:
        client = get_twitter_client()
        if client is None:
            return {"success": False, "error": "Twitter credentials not configured"}
        kwargs = {"text": text}
        if reply_to:
            kwargs["in_reply_to_tweet_id"] = reply_to
        response = client.create_tweet(**kwargs)
        _tweet_timestamps.append(_time.time())
        logger.info(f"Tweet posted: {response.data['id']} ({len(_tweet_timestamps)}/{MAX_TWEETS_PER_DAY} hoy)")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return {"success": False, "error": str(e)}


def post_poll(text: str, options: list[str], duration_min: int = 720) -> dict:
    """Post a poll (encuesta). 2-4 opciones, duración en minutos (default 12h)."""
    allowed, reason = _can_post_now()
    if not allowed:
        return {"success": False, "error": reason, "rate_limited": True}
    try:
        client = get_twitter_client()
        if client is None:
            return {"success": False, "error": "Twitter credentials not configured"}
        # Twitter exige 2-4 opciones, max 25 chars cada una
        opts = [o[:25] for o in options[:4]]
        if len(opts) < 2:
            return {"success": False, "error": "poll needs >=2 options"}
        response = client.create_tweet(
            text=text[:280],
            poll_options=opts,
            poll_duration_minutes=duration_min,
        )
        _tweet_timestamps.append(_time.time())
        logger.info(f"Poll posted: {response.data['id']}")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Poll failed: {e}")
        return {"success": False, "error": str(e)}


async def post_daily_poll():
    """Encuesta diaria sobre el partido top del día. Alto engagement garantizado."""
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sentinel = f"__daily_poll__{today_key}"
    if sentinel in _posted_games.get(today_key, set()):
        return None

    games = await get_todays_games()
    priority = ["liga-mx", "champions", "premier-league", "la-liga", "nfl", "nba", "mlb"]
    upcoming = [g for g in games if g["status"]["state"] == "pre"]

    pick = None
    for pl in priority:
        pick = next((g for g in upcoming if g.get("league_slug") == pl), None)
        if pick:
            break
    if not pick and upcoming:
        pick = upcoming[0]
    if not pick:
        logger.info("No daily poll: sin juegos upcoming")
        return None

    first, second = get_team_order(pick)
    emoji = pick.get("emoji", "")
    league = pick.get("league_name", "")
    time_str = format_game_time_mx(pick["date"])
    hashtag = HASHTAG_MAP.get(league, "#DondeVer")

    text = (
        f"{emoji} ¿Quién gana hoy?\n"
        f"{first} vs {second}\n"
        f"{league} — {time_str} MX\n\n"
        f"{hashtag}"
    )

    options = [first, second]
    if pick.get("sport") == "soccer":
        options.append("Empate")
    result = post_poll(text, options, duration_min=720)
    if result["success"]:
        _mark_posted(sentinel)
    return result


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

    hs, as_ = first_score, second_score

    if event_type == "goal":
        headline = random.choice(GOAL_OPENERS).format(first=first, hs=hs, as_=as_, second=second)
    elif event_type == "score_change":
        headline = f"🔔 {emoji} {first} {hs} - {as_} {second}"
    elif event_type == "started":
        headline = random.choice(STARTED_OPENERS).format(emoji=emoji, first=first, second=second)
    elif event_type == "halftime":
        headline = f"⏸️ Medio tiempo\n{first} {hs} - {as_} {second}"
    elif event_type == "final":
        headline = random.choice(FINAL_OPENERS).format(first=first, hs=hs, as_=as_, second=second)
    else:
        headline = f"{emoji} {first} {hs} - {as_} {second}"

    tag = hashtag if hashtag else "#DondeVer"
    parts = [headline]

    # "Started" = tweet largo con pick, canales y CTA (máximo valor)
    if event_type == "started":
        pick_line = get_pick_line(game)
        parts.append("")
        parts.append(pick_line)
        if channels and channels != "Por confirmar":
            parts.append(f"📺 {channels}")

        # Rota: 33% betting, 50% soft CTA (WA/sitio), 17% nada
        if should_include_betting() and betting:
            parts.append("")
            parts.append(betting)
        else:
            soft = get_soft_cta()
            if soft:
                parts.append("")
                parts.append(soft)

    # "Goal" / "score_change" = tweet corto y rápido, con 1 CTA suave rotativo
    elif event_type in ("goal", "score_change"):
        # 60% de estos llevan CTA suave (sin betting, para no saturar en goles)
        if random.random() < 0.6:
            soft = get_soft_cta()
            if soft:
                parts.append("")
                parts.append(soft)

    # "Final" = marcador final + CTA suave
    elif event_type == "final":
        soft = get_soft_cta()
        if soft:
            parts.append("")
            parts.append(soft)

    # "Halftime" y otros = sin CTA (es info rápida)

    parts.append(f"\n{tag}")
    tweet = "\n".join(parts)

    # Trim progresivo
    if len(tweet) > 280:
        # Quita el último bloque antes del hashtag (CTA)
        parts = [p for p in parts if p != ""][:-1] + [f"\n{tag}"]
        tweet = "\n".join(parts)
    if len(tweet) > 280:
        tweet = f"{headline}\n{tag}"

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

    # 3b) Encuesta diaria a las 9 AM MX (15:00 UTC) — alto engagement
    scheduler.add_job(
        post_daily_poll,
        CronTrigger(hour=15, minute=0),
        id="twitter_daily_poll",
        name="Encuesta diaria (quién gana)",
        replace_existing=True,
    )

    # 3c) Promos del WhatsApp — 2 veces al día, 11:00 AM y 6:00 PM MX
    scheduler.add_job(
        post_promo_tweet,
        CronTrigger(hour=17, minute=0),  # 11 AM MX = 17 UTC
        id="twitter_promo_am",
        name="Promo WhatsApp (mañana)",
        replace_existing=True,
    )
    scheduler.add_job(
        post_promo_tweet,
        CronTrigger(hour=0, minute=0),  # 6 PM MX = 00 UTC del dia siguiente
        id="twitter_promo_pm",
        name="Promo WhatsApp (tarde)",
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

    logger.info("Twitter bot scheduler configured (games, summary, pick, poll, live monitor)")
