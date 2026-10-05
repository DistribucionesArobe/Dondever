"""DondeVer X publishing: verified fixtures, viewing details, images, and polls."""

import tweepy
import asyncio
import logging
import random
import os
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from config import AFFILIATES, APP_URL, TZ_MX, HOME_LEFT_SPORTS, get_affiliate_url, get_short_affiliate_url
from game_card import generate_game_card, generate_live_card
from sports_api import get_todays_games, fetch_odds, match_odds_to_game

logger = logging.getLogger("dondever.twitter")

# League scope is configurable without changing the deployment schedule.
TWITTER_LEAGUES = {
    slug.strip() for slug in os.getenv("TWITTER_LEAGUES", "liga-mx,champions,nfl").split(",")
    if slug.strip()
}
_POSTS_FILE = Path(os.getenv("TWITTER_POSTS_FILE", os.path.join(
    os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "twitter_posts.json"
)))
_POSTED_FILE = Path(os.getenv("TWITTER_POSTED_FILE", os.path.join(
    os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "twitter_posted.json"
)))

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
    """Legacy helper: return only confirmed Mexican channels."""
    return " / ".join(_event_channels_by_country({"broadcasts": broadcasts}).get("MX", [])[:3])


def format_game_time_mx(date_str: str) -> str:
    """Convert to a date and time explicitly labeled as Mexico City time."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        days = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
        months = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
        return f"{days[mx.weekday()]} {mx.day} {months[mx.month - 1]} · {mx:%H:%M} h CDMX"
    except Exception:
        return ""


def _event_channels_by_country(game: dict) -> dict[str, list[str]]:
    """Only expose channels whose per-game data confirms their country."""
    country_names = {
        "MX", "US", "GT", "SV", "HN", "NI", "CR", "CU", "VE", "PA", "DO",
        "HT", "CO", "PE", "EC", "BO", "BR", "PY", "UY", "AR", "CL", "PR", "ES", "*",
    }
    result: dict[str, list[str]] = {"MX": [], "US": []}
    for country, channels in (game.get("channels_by_country") or {}).items():
        if country not in country_names:
            continue
        bucket = result.setdefault(country, [])
        for channel in channels:
            if channel and channel not in result[country]:
                bucket.append(channel)
    for broadcast in game.get("broadcasts") or []:
        if isinstance(broadcast, str):
            continue
        source = broadcast.get("source", "")
        if source in {"rights_estimate", "league_default"}:
            continue
        if source not in {"espn", "sportsdb_event"} and not game.get("channels_confirmed", False):
            continue
        channel = str(broadcast.get("channel") or "").strip()
        info = broadcast.get("info") or {}
        country = info.get("country")
        if not country and (broadcast.get("is_us_regional") or broadcast.get("market") in ("Home", "Away")):
            country = "US"
        if channel and country in country_names:
            bucket = result.setdefault(country, [])
            if channel not in bucket:
                bucket.append(channel)
    return result


def _confirmed_free_mx_channels(game: dict) -> list[str]:
    return list(dict.fromkeys(
        str(b.get("channel") or "").strip()
        for b in game.get("broadcasts") or []
        if isinstance(b, dict) and str(b.get("channel") or "").strip()
        and b.get("source", "") not in {"rights_estimate", "league_default"}
        and (b.get("source") in {"espn", "sportsdb_event"} or game.get("channels_confirmed", False))
        and (b.get("info") or {}).get("country") == "MX"
        and (b.get("info") or {}).get("type") == "free"
    ))


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text).strip("-"))


def _event_page_url(game: dict, content: str) -> str:
    """Build the same canonical event path as server._game_url, with GA4 UTMs."""
    if game.get("event_slug") and game.get("sport") in ("mma", "racing"):
        path = f"/evento/{game['event_slug']}"
    else:
        home = _slugify((game.get("home") or {}).get("name") or "home")
        away = _slugify((game.get("away") or {}).get("name") or "away")
        first, second = (home, away) if game.get("sport") in ("soccer", "boxing", "mma") else (away, home)
        try:
            dt = datetime.fromisoformat(str(game.get("date", "")).replace("Z", "+00:00"))
            game_day = dt.astimezone(TZ_MX).strftime("%Y-%m-%d")
        except Exception:
            game_day = ""
        path = f"/partido/{first}-vs-{second}-{game_day}"
    params = urlencode({
        "utm_source": "twitter", "utm_medium": "organic_social",
        "utm_campaign": "dondever_x", "utm_content": content,
    })
    return f"{APP_URL.rstrip('/')}{path}?{params}"


def _tweet_length(text: str) -> int:
    """Approximate X's 280-character rule, where every URL counts as 23."""
    return sum(23 if re.match(r"https?://", part) else len(part)
               for part in re.split(r"(https?://\S+)", text) if part)


def _country_channel_lines(game: dict) -> list[str]:
    by_country = _event_channels_by_country(game)
    flags = {
        "MX": "🇲🇽", "US": "🇺🇸", "GT": "🇬🇹", "SV": "🇸🇻", "HN": "🇭🇳", "NI": "🇳🇮",
        "CR": "🇨🇷", "CU": "🇨🇺", "VE": "🇻🇪", "PA": "🇵🇦", "DO": "🇩🇴", "HT": "🇭🇹",
        "CO": "🇨🇴", "PE": "🇵🇪", "EC": "🇪🇨", "BO": "🇧🇴", "BR": "🇧🇷", "PY": "🇵🇾",
        "UY": "🇺🇾", "AR": "🇦🇷", "CL": "🇨🇱", "PR": "🇵🇷", "ES": "🇪🇸", "*": "🌎",
    }
    lines = []
    if by_country["MX"]:
        lines.append("🇲🇽 México: " + ", ".join(by_country["MX"][:2]))
    if by_country["US"]:
        lines.append("🇺🇸 EE.UU.: " + ", ".join(by_country["US"][:2]))
    latam_codes = ("GT", "SV", "HN", "NI", "CR", "CU", "VE", "PA", "DO", "HT", "CO", "PE", "EC", "BO", "BR", "PY", "UY", "AR", "CL", "PR")
    latam = [f"{flags[code]} {code}: {', '.join(by_country[code][:1])}" for code in latam_codes if by_country.get(code)]
    if latam:
        lines.append("🌎 LATAM (por país): " + " · ".join(latam))
    if by_country.get("ES"):
        lines.append("🇪🇸 España: " + ", ".join(by_country["ES"][:2]))
    if by_country.get("*"):
        lines.append("🌐 Global: " + ", ".join(by_country["*"][:2]))
    return lines


def _hashtags_for_games(games: list[dict]) -> str:
    """Add competition discovery plus the DondeVer brand, keeping tags concise."""
    competition_tags = {
        "liga-mx": "#LigaMX",
        "champions": "#ChampionsLeague",
        "nfl": "#NFL",
    }
    tags = []
    for game in games:
        tag = competition_tags.get(str(game.get("league_slug") or "").lower())
        if tag and tag not in tags:
            tags.append(tag)
    tags.append("#DondeVer")
    return " ".join(tags)


def get_betting_affiliate_text() -> str:
    """
    BETTING affiliate CTA con link geo-inteligente /go/bet.
    El servidor decide el casino según el país del visitante:
    MX → Jubilee/Vivento, resto → Betsson. El texto es neutro de
    marca para que nunca haya mismatch entre tweet y destino.
    """
    from config import APP_URL
    smart_url = f"{APP_URL.replace('https://', '')}/go/bet?s=twitter"
    ctas = [
        "Bono de bienvenida hasta $3,000 MXN",
        "Apuesta en vivo con bono de registro",
        "Casino legal en tu pais + bono",
    ]
    return f"🎁 {random.choice(ctas)} 👉 {smart_url}"


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
    # Pick-centric openers (lead with prediction, not just announcement)
    "🎯 PICK: {pick}\n{first} vs {second} — {time} MX",
    "🔥 {first} vs {second}\n¿Quién gana hoy? Mi pick: {pick}\n{time} MX",
    "{emoji} {league} HOY\n{first} vs {second} — {time} MX\n🎯 Pick: {pick}",
    "Ojo al partidazo 👀\n{first} vs {second} hoy {time} MX",
    "📺 {first} vs {second}\n{time} MX — {channels}",
    "{emoji} ¿Quién gana?\n{first} vs {second} — {time} MX",
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


def should_include_odds() -> bool:
    """~40% de tweets pre-game incluyen momios para variedad."""
    return random.random() < 0.40


async def get_odds_line(game: dict) -> str | None:
    """
    Fetch odds for a game and return a formatted line for tweets.
    Returns None if no odds available or API not configured.
    Ej: '📊 Momios: Hornets -162 | Magic +136'
    """
    try:
        league_slug = game.get("league_slug", "")
        odds_list = await fetch_odds(league_slug)
        odds = match_odds_to_game(game, odds_list)
        if not odds or not odds.get("home_odds"):
            return None

        first, second = get_team_order(game)
        # Use short names (first word) to save chars
        first_short = first.split()[-1] if len(first) > 12 else first
        second_short = second.split()[-1] if len(second) > 12 else second

        home_left = game.get("sport", "") in HOME_LEFT_SPORTS
        left_odds = odds["home_odds"] if home_left else odds["away_odds"]
        right_odds = odds["away_odds"] if home_left else odds["home_odds"]

        if odds.get("draw_odds"):
            return f"📊 {first_short} {left_odds} | Empate {odds['draw_odds']} | {second_short} {right_odds}"
        return f"📊 {first_short} {left_odds} | {second_short} {right_odds}"
    except Exception as e:
        logger.debug(f"Odds fetch for tweet failed: {e}")
        return None


# CTAs suaves que se rotan — siempre sale UNO (WhatsApp, sitio o casa)
SOFT_CTAS_WA = [
    "📲 Picks GRATIS diarios por WhatsApp: wa.me/15715463202",
    "📲 Alerta 1h antes + picks gratis: wa.me/15715463202",
    "💬 Recibe el parlay del dia gratis 👉 wa.me/15715463202",
    "📲 Gol alerts + picks en tu WhatsApp: wa.me/15715463202",
]

SOFT_CTAS_SITE = [
    "📺 Horarios + canales de hoy: dondever.app",
    "🔗 Donde ver todos los partidos: dondever.app",
    "👉 Comparar streaming deportivo: dondever.app/streaming",
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


def _compose_featured_tweet(game: dict, content: str = "featured") -> str:
    first, second = get_team_order(game)
    event_time = format_game_time_mx(str(game.get("date", "")))
    league = game.get("league_name") or "Partido"
    parts = [f"📺 {first} vs {second} · {league}"]
    if event_time:
        parts.append(event_time)
    parts.extend(_country_channel_lines(game))
    prompt = "¿Desde qué país lo vas a ver? 👇"
    tags = _hashtags_for_games([game])
    url = _event_page_url(game, content)
    with_prompt = "\n".join(parts + [prompt, tags, url])
    if _tweet_length(with_prompt) <= 280:
        return with_prompt
    return "\n".join(parts + [tags, url])


async def compose_game_tweet(game: dict) -> str:
    """Post centrado en el encuentro, la hora local, canales confirmados y su ficha."""
    return _compose_featured_tweet(game)


def compose_free_tv_tweet(games: list[dict], content: str = "free_tv", *, include_posted: bool = False) -> str:
    """Agenda corta de encuentros con señal abierta confirmada en México."""
    candidates = [g for g in _relevant_upcoming(games, include_posted=include_posted)
                  if _confirmed_free_mx_channels(g)]
    if not candidates:
        return ""
    lines = ["📡 Partidos por TV abierta en México (confirmado)"]
    included = []
    for game in candidates[:2]:
        first, second = get_team_order(game)
        channels = ", ".join(_confirmed_free_mx_channels(game)[:2])
        addition = [
            f"{game.get('league_name', '')}: {first} vs {second} · {format_game_time_mx(game['date'])}",
            f"México: {channels}",
            _event_page_url(game, content),
        ]
        tags = _hashtags_for_games(included + [game])
        if _tweet_length("\n".join(lines + addition + [tags])) > 280:
            break
        lines.extend(addition)
        included.append(game)
    if included:
        lines.append(_hashtags_for_games(included))
    return "\n".join(lines)

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


def compose_daily_summary_tweet(
    games: list[dict], content: str = "agenda", *, include_posted: bool = False
) -> str:
    """Agenda breve con hasta dos encuentros relevantes y enlaces individuales."""
    upcoming = _relevant_upcoming(games, include_posted=include_posted)
    if not upcoming:
        return ""
    lines = [f"📅 Próximos partidos · hora CDMX"]
    included = []
    prompt = "¿Qué partido agregamos a la próxima agenda? 👇"
    for game in upcoming[:2]:
        first, second = get_team_order(game)
        addition = [f"{game.get('league_name', '')}: {first} vs {second} · {format_game_time_mx(game['date'])}"]
        addition += _country_channel_lines(game) + [_event_page_url(game, content)]
        tags = _hashtags_for_games(included + [game])
        if _tweet_length("\n".join(lines + addition + [prompt, tags])) > 280:
            break
        lines.extend(addition)
        included.append(game)
    if included:
        lines.append(prompt)
    if included:
        lines.append(_hashtags_for_games(included))
    return "\n".join(lines)

def compose_pick_tweet(game: dict) -> str:
    """Compatibility wrapper: the old scheduled "pick" is now a game guide."""
    return _compose_featured_tweet(game, "featured")

# ── Post Functions ───────────────────────────────────────

# Rate limiter: max tweets per hour and per day (X reglas anti-spam)
from collections import deque
import time as _time

_tweet_timestamps: deque = deque()  # stores unix timestamps of recent tweets
MAX_TWEETS_PER_HOUR = 2   # max 2 por hora — conservador anti-ban
MAX_TWEETS_PER_DAY = 5    # max 5 al dia — evita shadowban
MIN_SECONDS_BETWEEN_TWEETS = 1800  # 30 min minimo entre tweets


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


def get_twitter_api_v1() -> tweepy.API | None:
    """Create Twitter API v1.1 client (needed for media upload)."""
    if not twitter_credentials_valid():
        return None
    auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET)
    auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
    return tweepy.API(auth)


def _record_tweet(tweet_id: str, text: str, post_type: str = "") -> None:
    """Keep a small durable publication log; GA4 UTMs attribute incoming visits."""
    try:
        try:
            records = json.loads(_POSTS_FILE.read_text())
            if not isinstance(records, list):
                records = []
        except (OSError, json.JSONDecodeError):
            records = []
        match = re.search(r"utm_content=([a-z_]+)", text)
        records.append({
            "tweet_id": str(tweet_id), "published_at": datetime.now(timezone.utc).isoformat(),
            "format": post_type or (match.group(1) if match else "other"),
        })
        _POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _POSTS_FILE.write_text(json.dumps(records[-500:], indent=2))
    except OSError as exc:
        logger.warning("Could not persist X publication record: %s", exc)


def _upload_media(image_bytes: bytes) -> str | None:
    """Upload image to Twitter, return media_id string."""
    try:
        api = get_twitter_api_v1()
        if api is None:
            return None
        import io
        media = api.media_upload(filename="game_card.png", file=io.BytesIO(image_bytes))
        logger.info(f"Media uploaded: {media.media_id}")
        return str(media.media_id)
    except Exception as e:
        logger.warning(f"Media upload failed: {e}")
        return None


def _make_game_card(game: dict, pick_team: str = "", pick_reason: str = "") -> bytes | None:
    """Generate a game card image from a game dict. Returns PNG bytes or None."""
    try:
        sport = game.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        by_country = _event_channels_by_country(game)
        channels = " · ".join(
            f"{label}: {', '.join(by_country[code][:1])}"
            for code, label in (("MX", "MX"), ("US", "EE.UU."))
            if by_country.get(code)
        )
        time_str = format_game_time_mx(game["date"])

        # ESPN logo URLs (if available in game data)
        home_logo = game["home"].get("logo", "")
        away_logo = game["away"].get("logo", "")

        return generate_game_card(
            home_name=game["home"]["name"],
            away_name=game["away"]["name"],
            home_logo_url=home_logo,
            away_logo_url=away_logo,
            league_name=game.get("league_name", ""),
            emoji=game.get("emoji", ""),
            time_str=time_str,
            channels=channels,
            pick_team=pick_team,
            pick_reason=pick_reason,
            sport=sport,
            home_left=home_left,
        )
    except Exception as e:
        logger.warning(f"Game card generation failed: {e}")
        return None


def _make_live_card(game: dict, event_type: str) -> bytes | None:
    """Generate a live event card from a game dict."""
    try:
        sport = game.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        channels = format_broadcast_short(game["broadcasts"])

        home_logo = game["home"].get("logo", "")
        away_logo = game["away"].get("logo", "")

        return generate_live_card(
            home_name=game["home"]["name"],
            away_name=game["away"]["name"],
            home_score=str(game["home"]["score"] or "0"),
            away_score=str(game["away"]["score"] or "0"),
            home_logo_url=home_logo,
            away_logo_url=away_logo,
            league_name=game.get("league_name", ""),
            emoji=game.get("emoji", ""),
            event_type=event_type,
            channels=channels,
            sport=sport,
            home_left=home_left,
        )
    except Exception as e:
        logger.warning(f"Live card generation failed: {e}")
        return None


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
        _record_tweet(response.data["id"], text)
        logger.info(f"Tweet posted: {response.data['id']} ({len(_tweet_timestamps)}/{MAX_TWEETS_PER_DAY} hoy)")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return {"success": False, "error": str(e)}


def post_tweet_with_media(text: str, image_bytes: bytes) -> dict:
    """Post a tweet with an image attached. Falls back to text-only if upload fails."""
    media_id = _upload_media(image_bytes)
    if media_id:
        allowed, reason = _can_post_now()
        if not allowed:
            return {"success": False, "error": reason, "rate_limited": True}
        try:
            client = get_twitter_client()
            if client is None:
                return {"success": False, "error": "Twitter credentials not configured"}
            response = client.create_tweet(text=text, media_ids=[media_id])
            _tweet_timestamps.append(_time.time())
            _record_tweet(response.data["id"], text)
            logger.info(f"Tweet+media posted: {response.data['id']}")
            return {"success": True, "tweet_id": response.data["id"], "has_media": True}
        except Exception as e:
            logger.error(f"Tweet+media failed: {e}")
            # Fallback to text only
            return post_tweet(text)
    else:
        # Media upload failed, post text only
        return post_tweet(text)


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
        _record_tweet(response.data["id"], text, "poll")
        logger.info(f"Poll posted: {response.data['id']}")
        return {"success": True, "tweet_id": response.data["id"]}
    except Exception as e:
        logger.error(f"Poll failed: {e}")
        return {"success": False, "error": str(e)}


async def post_daily_poll():
    """Daily viewing question linked to the relevant event; contains no betting pick."""
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sentinel = f"__daily_poll__{today_key}"
    if _already_posted(sentinel):
        return None

    games = await get_todays_games()
    relevant = _relevant_upcoming(games)
    if not relevant:
        logger.info("No daily poll: sin juegos upcoming")
        return None
    game = relevant[0]

    first, second = get_team_order(game)
    league = game.get("league_name", "")
    time_str = format_game_time_mx(game["date"])

    text = (
        f"📺 ¿Qué partido vas a ver hoy?\n"
        f"{first} vs {second}\n"
        f"{league} — {time_str}\n"
        f"{' | '.join(_country_channel_lines(game))}\n"
        f"{_event_page_url(game, 'poll')}"
    )

    options = [first, second]
    if game.get("sport") == "soccer":
        options.append("Empate")
    result = post_poll(text, options, duration_min=720)
    if result["success"]:
        _mark_posted(sentinel)
        _mark_posted(str(game.get("id") or game.get("name") or ""))
    return result


# Dedup: game IDs already tweeted (resets cada dia con la fecha)
_posted_games: dict[str, set] = {}  # {"2026-04-14": {"game_id_1", ...}}
try:
    _posted_store = json.loads(_POSTED_FILE.read_text())
    if not isinstance(_posted_store, dict):
        _posted_store = {}
except (OSError, json.JSONDecodeError):
    _posted_store = {}

def _already_posted(game_id: str) -> bool:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if game_id in _posted_games.get(today_key, set()):
        return True
    posted_at = _posted_store.get(str(game_id))
    if not posted_at:
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(posted_at)).days < 45
    except (TypeError, ValueError):
        return False

def _mark_posted(game_id: str) -> None:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Reset otros dias (libera memoria)
    for k in list(_posted_games.keys()):
        if k != today_key:
            del _posted_games[k]
    _posted_games.setdefault(today_key, set()).add(game_id)
    _posted_store[str(game_id)] = datetime.now(timezone.utc).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(days=45)
    for key, stamp in list(_posted_store.items()):
        try:
            if datetime.fromisoformat(stamp) < cutoff:
                del _posted_store[key]
        except (TypeError, ValueError):
            del _posted_store[key]
    try:
        _POSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _POSTED_FILE.write_text(json.dumps(_posted_store, indent=2))
    except OSError as exc:
        logger.warning("Could not persist X duplicate guard: %s", exc)


def _relevant_upcoming(games: list[dict], *, include_posted: bool = False) -> list[dict]:
    """Configured competitions only; skip unknown teams, completed and past events."""
    now = datetime.now(timezone.utc)
    league_order = {slug: i for i, slug in enumerate(("liga-mx", "champions", "nfl"))}
    upcoming = []
    for game in games or []:
        if game.get("league_slug") not in TWITTER_LEAGUES:
            continue
        if game.get("status", {}).get("state") != "pre":
            continue
        home = (game.get("home") or {}).get("name", "")
        away = (game.get("away") or {}).get("name", "")
        if not home or not away or "TBD" in (home.upper(), away.upper()):
            continue
        try:
            starts = datetime.fromisoformat(str(game.get("date", "")).replace("Z", "+00:00"))
            if starts <= now:
                continue
        except (TypeError, ValueError):
            continue
        gid = str(game.get("id") or game.get("name") or "")
        if not gid or (not include_posted and _already_posted(gid)):
            continue
        upcoming.append((league_order.get(game.get("league_slug"), 99), starts, game))
    # Prefer the next kickoff first; league priority breaks ties on the same date.
    return [item[2] for item in sorted(upcoming, key=lambda item: (item[1], item[0]))]


ENGAGEMENT_REPLIES = [
    "¿A quién le van? 👇",
    "¿Quién gana este? Comenta 👇",
    "¿Lo vas a ver? ¿En qué canal? 👇",
    "Dale RT si vas con {first} ♻️\nLike si vas con {second} ❤️",
    "Predicción de marcador? 👇",
    "¿Quién es favorito para ti? 👇",
]


def _post_engagement_reply(parent_tweet_id: str, game: dict):
    """Reply al tweet principal con pregunta de engagement."""
    try:
        sport = game.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        first = game["home"]["name"] if home_left else game["away"]["name"]
        second = game["away"]["name"] if home_left else game["home"]["name"]

        reply_tpl = random.choice(ENGAGEMENT_REPLIES)
        reply_text = reply_tpl.format(first=first, second=second)
        post_tweet(reply_text, reply_to=parent_tweet_id)
    except Exception as e:
        logger.warning(f"Engagement reply failed: {e}")


# Store tweet IDs for quote-tweet results later
_pregame_tweet_ids: dict[str, str] = {}  # game_id -> tweet_id

RESULT_QUOTE_TEMPLATES = [
    "¿Le atinamos? 🎯\n{first} {hs} - {as_} {second}\n\n{verdict}",
    "Resultado final:\n{first} {hs} - {as_} {second}\n\n{verdict}",
    "Se acabó 🏁\n{first} {hs} - {as_} {second}\n\n{verdict}",
]


def _post_result_quote(game_id: str, game: dict):
    """Quote-tweet del pregame con el resultado final — conecta ambos tweets."""
    original_tweet_id = _pregame_tweet_ids.get(game_id)
    if not original_tweet_id:
        return  # no hay tweet de arranque que quotear

    try:
        sport = game.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        first = game["home"]["name"] if home_left else game["away"]["name"]
        second = game["away"]["name"] if home_left else game["home"]["name"]
        hs = str(game["home"]["score"] or 0) if home_left else str(game["away"]["score"] or 0)
        as_ = str(game["away"]["score"] or 0) if home_left else str(game["home"]["score"] or 0)

        # Determinar si nuestro pick le atinó
        pick_rng = random.Random(str(game_id) + datetime.now(TZ_MX).strftime("%Y%m%d"))
        our_pick = game["home"]["name"] if pick_rng.random() < 0.65 else game["away"]["name"]

        home_s = int(game["home"]["score"] or 0)
        away_s = int(game["away"]["score"] or 0)
        if home_s > away_s:
            winner = game["home"]["name"]
        elif away_s > home_s:
            winner = game["away"]["name"]
        else:
            winner = None  # empate

        if winner and winner == our_pick:
            verdict = "✅ Pick acertado! 🔥"
        elif winner is None:
            verdict = "🤝 Empate — nadie gana"
        else:
            verdict = "❌ No le atinamos esta vez"

        tpl = random.choice(RESULT_QUOTE_TEMPLATES)
        text = tpl.format(first=first, second=second, hs=hs, as_=as_, verdict=verdict)

        # Quote tweet via API v2
        allowed, reason = _can_post_now()
        if not allowed:
            return
        client = get_twitter_client()
        if client:
            response = client.create_tweet(
                text=text[:280],
                quote_tweet_id=original_tweet_id,
            )
            _tweet_timestamps.append(_time.time())
            logger.info(f"Result quote-tweet posted: {response.data['id']} — {verdict}")

        # Cleanup
        del _pregame_tweet_ids[game_id]
    except Exception as e:
        logger.warning(f"Result quote-tweet failed: {e}")


async def post_game_tweets(minutes_before: int = 60, max_tweets: int = 8):
    """
    Check for games starting soon and post tweets for them.
    Solo ligas relevantes para audiencia MX.
    max_tweets: cuántos tweets postear como máximo en esta corrida.
    """
    games = await get_todays_games()
    now = datetime.now(timezone.utc)

    posted = []
    for game in _relevant_upcoming(games):
        gid = str(game.get("id", "")) or game.get("name", "")
        try:
            game_time = datetime.fromisoformat(
                game["date"].replace("Z", "+00:00")
            )
        except Exception:
            continue

        # Post if game starts within the next `minutes_before` minutes
        diff = (game_time - now).total_seconds() / 60
        if 0 < diff <= minutes_before:
            tweet_text = await compose_game_tweet(game)
            result = post_tweet(tweet_text)
            if result["success"]:
                _mark_posted(gid)
                _pregame_tweet_ids[gid] = result["tweet_id"]  # guardar para quote-tweet al final
                posted.append({
                    "game": game["name"],
                    "tweet_id": result["tweet_id"],
                })
                # Respetar max_tweets
                if len(posted) >= max_tweets:
                    break

    return posted


async def post_next_top_game():
    """
    Postea el proximo juego 'top' (liga popular) aunque sea en 2-4h.
    1 vez por dia. Si no hay top game, no postea.
    """
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sentinel = f"__next_top__{today_key}"
    if _already_posted(sentinel):
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
    for g in _relevant_upcoming(games):
        gid = str(g.get("id", "")) or g.get("name", "")
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
    tweet_text = await compose_game_tweet(best)
    result = post_tweet(tweet_text)
    if result["success"]:
        gid = str(best.get("id", "")) or best.get("name", "")
        _mark_posted(gid)
        _mark_posted(sentinel)  # bloquea otro "next top" hoy
        return {"game": best["name"], "tweet_id": result["tweet_id"]}
    return None


async def post_pick_del_dia():
    """Scheduled slot retained; now highlights a relevant game with viewing info."""
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sentinel = f"__featured_game__{today_key}"
    if _already_posted(sentinel):
        return None
    games = await get_todays_games()
    upcoming = _relevant_upcoming(games)
    if not upcoming:
        logger.info("No featured game available — no relevant upcoming game")
        return None

    game = upcoming[0]
    tweet_text = _compose_featured_tweet(game, "featured")
    result = post_tweet(tweet_text)
    if result["success"]:
        _mark_posted(str(game.get("id") or game.get("name") or ""))
        _mark_posted(sentinel)
        logger.info("Featured game posted: %s", game.get("name"))
    return result


# ── Value Threads (Hilos de valor) ──────────────────────

THREAD_REASONS_TEMPLATES = [
    [
        "1/ {emoji} {first} vs {second} — {league}\n\n3 razones para apostar a {pick}:\n\n🔥 {reason1}",
        "2/ {stat_line}\n\nAdemás, {reason2}",
        "3/ {reason3}\n\n{odds_line}\n\n{betting_cta}\n\n{hashtag}",
    ],
    [
        "1/ 🎯 ANÁLISIS: {first} vs {second}\n{league} — {time} MX\n\n¿Quién gana hoy? Aquí mi pick 👇",
        "2/ Pick: {pick}\n\n{reason1}. {reason2}.\n\n📺 Donde verlo: {channels}",
        "3/ {odds_line}\n\n{betting_cta}\n\n📲 Picks gratis diarios: wa.me/15715463202\n\n{hashtag}",
    ],
]

THREAD_STAT_LINES = [
    "En los últimos 5 partidos: 4 victorias como local",
    "Lleva 3 partidos seguidos anotando",
    "No ha perdido en casa en las últimas 6 fechas",
    "Mejor defensa de la liga en los últimos 10 juegos",
    "Promedio de 2.1 goles por partido esta temporada",
    "Ha ganado 5 de los últimos 7 enfrentamientos directos",
]

THREAD_EXTRA_REASONS = [
    "el rival viene con varias bajas importantes",
    "juegan en casa con la afición a favor",
    "están peleando por calificar y necesitan los 3 puntos",
    "el DT tiene un excelente historial en este tipo de partidos",
    "vienen de una racha positiva de 4 juegos sin perder",
    "el rival ha tenido problemas defensivos toda la temporada",
    "tienen el mejor ataque del torneo en los últimos 5 juegos",
]


async def post_value_thread(game: dict) -> dict | None:
    """
    Post a 3-tweet thread with analysis/reasons for a pick.
    Solo para partidos de ligas top. ~1 hilo por día max.
    """
    sentinel = f"thread:{datetime.now(TZ_MX).strftime('%Y%m%d')}"
    if _already_posted(sentinel):
        return None

    first, second = get_team_order(game)
    pick_team = get_pick_team(game)
    emoji = game.get("emoji", "")
    league = game.get("league_name", "")
    time_str = format_game_time_mx(game["date"])
    channels = format_broadcast_short(game["broadcasts"])
    hashtag = HASHTAG_MAP.get(league, "#DondeVer")
    betting_cta = get_betting_affiliate_text()

    # Get odds line if available
    odds_line = ""
    try:
        odds_text = await get_odds_line(game)
        if odds_text:
            odds_line = odds_text
    except Exception:
        pass
    if not odds_line:
        odds_line = f"📊 Momios disponibles en dondever.app"

    # Pick random reasons
    reasons = random.sample(PICK_REASONS, min(3, len(PICK_REASONS)))
    extra_reasons = random.sample(THREAD_EXTRA_REASONS, min(2, len(THREAD_EXTRA_REASONS)))
    stat_line = random.choice(THREAD_STAT_LINES)

    # Pick a thread template
    tpl = random.choice(THREAD_REASONS_TEMPLATES)

    tweets = []
    for tweet_tpl in tpl:
        tweet = tweet_tpl.format(
            emoji=emoji, first=first, second=second,
            league=league, pick=pick_team, time=time_str,
            channels=channels, hashtag=hashtag,
            reason1=reasons[0], reason2=extra_reasons[0],
            reason3=extra_reasons[1] if len(extra_reasons) > 1 else reasons[-1],
            stat_line=stat_line, odds_line=odds_line,
            betting_cta=betting_cta,
        )
        # Trim if over 280
        if len(tweet) > 280:
            tweet = tweet[:277] + "..."
        tweets.append(tweet)

    # Post thread: first tweet, then replies
    first_result = post_tweet(tweets[0])
    if not first_result["success"]:
        return first_result

    last_id = first_result["tweet_id"]
    for tweet in tweets[1:]:
        import time as _t
        _t.sleep(1)  # small delay between thread tweets
        reply_result = post_tweet(tweet, reply_to=last_id)
        if reply_result["success"]:
            last_id = reply_result["tweet_id"]

    _mark_posted(sentinel)
    logger.info(f"Value thread posted for {first} vs {second} ({len(tweets)} tweets)")
    return {"success": True, "thread_tweets": len(tweets), "game": game.get("name", "")}


async def maybe_post_thread():
    """
    Check if there's a good game for a value thread today.
    Solo ligas top, solo 1 hilo por día, ~50% de probabilidad.
    """
    # Solo 50% de los días para no saturar
    if random.random() > 0.50:
        return None

    games = await get_todays_games()
    top_leagues = ["liga-mx", "premier-league", "champions", "nba", "nfl", "la-liga"]

    upcoming = [
        g for g in games
        if g["status"]["state"] == "pre"
        and g["broadcasts"]
        and g["league_slug"] in top_leagues
    ]

    if not upcoming:
        return None

    # Pick the best game (priority by league order)
    best = None
    for pl in top_leagues:
        best = next((g for g in upcoming if g["league_slug"] == pl), None)
        if best:
            break

    if not best:
        return None

    return await post_value_thread(best)


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

        # Detect events — solo los más relevantes para no spammear
        event_type = None

        # Game just started (solo ligas top: liga-mx, champions, nba, nfl)
        top_start_leagues = {"liga-mx", "champions", "nba", "nfl", "premier-league", "la-liga"}
        if prev["state"] == "pre" and state == "in" and slug in top_start_leagues:
            event_type = "started"

        # Game just ended (todas las ligas priority)
        elif prev["state"] == "in" and state == "post":
            event_type = "final"

        # Goles de soccer solamente (no score changes de otros deportes)
        elif state == "in" and game.get("sport") == "soccer" and (
            prev["home_score"] != str(home_score) or
            prev["away_score"] != str(away_score)
        ):
            event_type = "goal"

            tweet_text = compose_live_tweet(game, event_type, detail)
            # Generate live card for important events (started, goal, final)
            if event_type in ("started", "goal", "final"):
                card = _make_live_card(game, event_type)
                result = post_tweet_with_media(tweet_text, card) if card else post_tweet(tweet_text)
            else:
                result = post_tweet(tweet_text)
            if result["success"]:
                posted.append({
                    "game": game["name"],
                    "event": event_type,
                    "tweet_id": result["tweet_id"],
                })
                logger.info(f"Live tweet: {event_type} — {game['name']}")

                # Reply thread cuando arranca
                if event_type == "started":
                    _pregame_tweet_ids[game_id] = result["tweet_id"]
                    _post_engagement_reply(result["tweet_id"], game)

                # Quote-tweet resultado final con referencia al tweet de arranque
                if event_type == "final":
                    _post_result_quote(game_id, game)

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

    MODO BALANCEADO (anti-ban pero activo):
    - 5-7 tweets/dia — suficiente para crecer, no tanto para parecer bot
    - Sin monitor en vivo cada 5 min (eso causó el ban anterior)
    - Pre-game tweets solo 2x al dia en horarios fijos
    - Contenido variado: resumen, pick, previews, encuesta
    """
    from apscheduler.triggers.cron import CronTrigger

    if not twitter_credentials_valid():
        logger.warning("Twitter credentials incomplete — scheduler NOT started")
        return

    # 1) Daily agenda at 9 AM MX (15:00 UTC)
    async def post_daily():
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sentinel = f"__daily_agenda__{today_key}"
        if _already_posted(sentinel):
            return
        games = await get_todays_games()
        candidates = _relevant_upcoming(games)
        if not candidates:
            return
        free_tv = compose_free_tv_tweet(games)
        # Alternate agendas with confirmed Mexican open-air listings when available.
        use_free_tv = bool(free_tv and datetime.now(TZ_MX).day % 2 == 0)
        tweet = free_tv if use_free_tv else compose_daily_summary_tweet(games)
        result = post_tweet(tweet) if tweet else {"success": False}
        if result.get("success"):
            _mark_posted(sentinel)
            content_key = "free_tv" if use_free_tv else "agenda"
            selected = [g for g in candidates
                        if _event_page_url(g, content_key) in tweet]
            for game in selected:
                _mark_posted(str(game.get("id") or game.get("name") or ""))

    scheduler.add_job(
        post_daily,
        CronTrigger(hour=15, minute=0),
        id="twitter_daily_summary",
        name="Daily game summary (9AM MX)",
        replace_existing=True,
    )

    # 2) Encuesta at 10 AM MX (16:00 UTC) — qué partido verá la audiencia
    scheduler.add_job(
        post_daily_poll,
        CronTrigger(hour=16, minute=0),
        id="twitter_daily_poll",
        name="Encuesta diaria (10AM MX)",
        replace_existing=True,
    )

    # 3) Partido destacado at 12 PM MX (18:00 UTC)
    scheduler.add_job(
        post_pick_del_dia,
        CronTrigger(hour=18, minute=0),
        id="twitter_pick_del_dia",
        name="Partido destacado (12PM MX)",
        replace_existing=True,
    )

    # 4) Pre-game tweets at 3 PM MX (21:00 UTC) — max 2 juegos de la tarde
    scheduler.add_job(
        post_game_tweets,
        CronTrigger(hour=21, minute=0),
        id="twitter_afternoon_games",
        name="Afternoon game previews (3PM MX)",
        replace_existing=True,
        kwargs={"minutes_before": 240, "max_tweets": 2},
    )

    # 5) Pre-game tweets at 7 PM MX (01:00 UTC+1) — max 2 juegos de la noche
    scheduler.add_job(
        post_game_tweets,
        CronTrigger(hour=1, minute=0),
        id="twitter_evening_games",
        name="Evening game previews (7PM MX)",
        replace_existing=True,
        kwargs={"minutes_before": 240, "max_tweets": 2},
    )

    # DESACTIVADOS (causaron el ban anterior):
    # - monitor_live_games — tweeteaba cada 5 min, parecía bot
    # - post_promo_tweet — spam de WhatsApp en cada tweet
    # - maybe_post_thread — contenido excesivo
    # Reactivar cuando la cuenta tenga +500 followers

    logger.info("Twitter bot scheduler: agenda 9AM, encuesta 10AM, partido destacado 12PM, previas 3PM+7PM MX")


async def fetch_preview_games(days: int = 14) -> list[dict]:
    """Read upcoming ESPN schedules for configured leagues; no posting or state writes."""
    start = datetime.now(TZ_MX).date()
    all_games: dict[str, dict] = {}
    for offset in range(max(1, min(days, 21))):
        date_str = (start + timedelta(days=offset)).strftime("%Y%m%d")
        for league in sorted(TWITTER_LEAGUES):
            try:
                for game in await get_todays_games(
                    date_str=date_str, league_filter=league, persist=False
                ):
                    key = str(game.get("id") or game.get("name") or "")
                    if key:
                        all_games[key] = game
            except Exception as exc:
                logger.warning("Preview schedule unavailable for %s %s: %s", league, date_str, exc)
    return _relevant_upcoming(list(all_games.values()), include_posted=True)


async def preview_examples(count: int = 6, days: int = 14) -> list[dict]:
    """Build preview examples without calling any X/Twitter publishing method."""
    games = await fetch_preview_games(days)
    if not games:
        return []
    examples: list[dict] = []
    examples.append({"format": "featured", "text": _compose_featured_tweet(games[0], "featured"), "games": [games[0]]})
    if len(games) > 1:
        examples.append({"format": "agenda", "text": compose_daily_summary_tweet(games[:2]), "games": games[:2]})
    free_tv_games = [g for g in games if _confirmed_free_mx_channels(g)]
    if free_tv_games:
        examples.append({"format": "free_tv", "text": compose_free_tv_tweet(free_tv_games), "games": free_tv_games[:2]})
    for game in games[1:]:
        examples.append({"format": "featured", "text": _compose_featured_tweet(game, "featured"), "games": [game]})
        if len(examples) >= count:
            break
    for game in games[1:]:
        if len(examples) >= count:
            break
        examples.append({"format": "agenda", "text": compose_daily_summary_tweet([game]), "games": [game]})
    return examples[:max(count, 6)]


async def preview_daily_slots() -> list[dict]:
    """Preview slot formats from the next week without calling X or persisting data."""
    games = await fetch_preview_games(days=7)
    if not games:
        return []
    morning_games = games[:2]
    previews = [{
        "slot": "09:00 · agenda con tarjeta",
        "text": compose_daily_summary_tweet(morning_games, "agenda_morning", include_posted=True),
        "media": "Tarjeta de partido 1200 × 675",
        "games": morning_games,
    }]
    used_paths = {urlparse(_event_page_url(game, "preview")).path.rstrip("/") for game in morning_games}
    remaining = [game for game in games if urlparse(_event_page_url(game, "preview")).path.rstrip("/") not in used_paths]
    if remaining:
        midday = remaining.pop(0)
        previews.append({
            "slot": "13:00 · partido destacado con tarjeta",
            "text": _compose_featured_tweet(midday, "featured_midday"),
            "media": "Tarjeta de partido 1200 × 675",
            "games": [midday],
        })
    free_tv = [game for game in remaining if _confirmed_free_mx_channels(game)]
    if free_tv:
        previews.append({
            "slot": "19:00 · TV abierta confirmada",
            "text": compose_free_tv_tweet(free_tv, "free_tv_night", include_posted=True),
            "media": "Solo partidos con señal abierta confirmada en México",
            "games": free_tv[:2],
        })
    elif remaining:
        night = remaining[0]
        poll_text, poll_options = _compose_viewing_poll(night, "night")
        previews.append({
            "slot": "19:00 · encuesta",
            "text": poll_text,
            "options": poll_options,
            "media": "Encuesta de X (12 horas)",
            "games": [night],
        })
    if len(previews) == 1:
        previews.append({
            "slot": "13:00 · se omite si no hay otro partido distinto",
            "text": "No se repiten eventos ya incluidos en la agenda de la mañana.",
            "media": "Sin publicación",
            "games": [],
        })
    if len(previews) < 3:
        previews.append({
            "slot": "19:00 · se omite si no hay otro partido distinto",
            "text": "No se repiten eventos ya incluidos en la agenda de la mañana.",
            "media": "Sin publicación",
            "games": [],
        })
    return previews


async def _preview_cli(count: int = 6, days: int = 14) -> None:
    examples = await preview_examples(count=count, days=days)
    for index, example in enumerate(examples, 1):
        print(f"--- Ejemplo {index} · {example['format']} ---")
        print(example["text"])
        for game in example["games"]:
            print(f"Verificación de evento: {game.get('league_name')} | ESPN id {game.get('id')} | {game.get('date')} | canales_confirmed={game.get('channels_confirmed', True)}")
    print(f"\nTotal: {len(examples)} ejemplos de vista previa; no se publicó en X.")
    print("\n=== Secuencia diaria propuesta ===")
    daily_previews = await preview_daily_slots()
    if not daily_previews:
        print("No hay eventos próximos confirmados para publicar hoy.")
    for preview in daily_previews:
        print(f"--- {preview['slot']} ---")
        print(preview["text"])
        print(f"Formato visual: {preview['media']}")
        if preview.get("options"):
            print("Opciones: " + " · ".join(preview["options"]))


def _x_recent_tracked_tweets(days: int = 7) -> list:
    """Fetch recent DondeVer campaign tweets for slot and event deduplication."""
    client = get_twitter_client()
    if client is None:
        raise RuntimeError("Faltan credenciales X para revisar duplicados")
    me = client.get_me(user_auth=True)
    if not me or not me.data:
        raise RuntimeError("X no devolvió la cuenta autenticada; se cancela para evitar duplicados")
    timeline = client.get_users_tweets(
        id=me.data.id,
        max_results=100,
        tweet_fields=["created_at", "entities"],
        user_auth=True,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tracked = []
    for tweet in (timeline.data or []) if timeline else []:
        created = getattr(tweet, "created_at", None)
        if not created or created < cutoff:
            continue
        urls = (getattr(tweet, "entities", None) or {}).get("urls", [])
        if any("utm_campaign=dondever_x" in (url.get("expanded_url") or url.get("url") or "")
               for url in urls):
            tracked.append(tweet)
    return tracked


def _publishing_slot(now: datetime | None = None) -> str:
    """Map Render's UTC cron runs to morning, midday, and evening in Monterrey."""
    local = (now or datetime.now(TZ_MX)).astimezone(TZ_MX)
    if 5 <= local.hour < 11:
        return "morning"
    if 11 <= local.hour < 17:
        return "midday"
    return "night"


def _tracked_tweet_urls(tweet) -> list[str]:
    urls = (getattr(tweet, "entities", None) or {}).get("urls", [])
    return [str(url.get("expanded_url") or url.get("url") or "") for url in urls]


def _x_published_in_slot(tweets: list, slot: str) -> bool:
    """Detect this slot on the current local date, using UTM labels where available."""
    today = datetime.now(TZ_MX).date()
    for tweet in tweets:
        created = getattr(tweet, "created_at", None)
        if not created or created.astimezone(TZ_MX).date() != today:
            continue
        for url in _tracked_tweet_urls(tweet):
            content = parse_qs(urlparse(url).query).get("utm_content", [""])[0]
            if content.endswith(f"_{slot}"):
                return True
        if created and _publishing_slot(created) == slot:
            return True
    return False


def _x_published_event_paths(tweets: list) -> set[str]:
    """Return event pages linked recently so future fixtures aren't repeated."""
    paths = set()
    for tweet in tweets:
        for url in _tracked_tweet_urls(tweet):
            path = urlparse(url).path.rstrip("/")
            if path.startswith(("/partido/", "/evento/")):
                paths.add(path)
    return paths


def _compose_viewing_poll(game: dict, slot: str) -> tuple[str, list[str]]:
    first, second = get_team_order(game)
    league = game.get("league_name") or "Partido"
    channels = _event_channels_by_country(game)
    channel_lines = _country_channel_lines(game)
    prompt = "📊 ¿A quién apoyas?" if game.get("sport") == "soccer" else "📊 ¿Quién gana?"
    lines = [prompt, f"{first} vs {second} · {league}", format_game_time_mx(str(game.get("date", "")))]
    lines.extend(channel_lines)
    lines.append("¿Desde qué país lo vas a ver? 👇")
    lines.append(_hashtags_for_games([game]))
    lines.append(_event_page_url(game, f"poll_{slot}"))
    text = "\n".join(line for line in lines if line)
    if _tweet_length(text) > 280:
        lines = [prompt, f"{first} vs {second} · {format_game_time_mx(str(game.get('date', '')))}"]
        lines.append("¿Desde qué país lo vas a ver? 👇")
        lines.append(_hashtags_for_games([game]))
        lines.append(_event_page_url(game, f"poll_{slot}"))
        text = "\n".join(lines)
    options = [first, second]
    if game.get("sport") == "soccer":
        options.append("Empate")
    return text, options


async def post_daily_guide() -> dict:
    """Publish one verified slot-specific guide; Render calls this at 9, 13, and 19 MX."""
    if not twitter_credentials_valid():
        raise RuntimeError("Faltan las cuatro credenciales de X")
    now_local = datetime.now(TZ_MX)
    slot = _publishing_slot(now_local)
    sentinel = f"__daily_guide__{now_local:%Y-%m-%d}_{slot}"
    tracked_tweets = _x_recent_tracked_tweets()
    if _already_posted(sentinel) or _x_published_in_slot(tracked_tweets, slot):
        logger.info("Daily X guide skipped: slot=%s already published", slot)
        return {"success": True, "skipped": "duplicate_slot", "slot": slot}

    # Plan over the next week so slots remain useful on dates without fixtures.
    # This helper queries only configured competitions and does not persist preview data.
    games = await fetch_preview_games(days=7)
    posted_paths = _x_published_event_paths(tracked_tweets)
    candidates = [
        game for game in _relevant_upcoming(games)
        if urlparse(_event_page_url(game, "slot")).path.rstrip("/") not in posted_paths
    ]
    if not candidates:
        logger.info("Daily X guide skipped: no verified, unposted upcoming games")
        return {"success": True, "skipped": "no_upcoming_games", "slot": slot}

    if slot == "morning":
        content = "agenda_morning"
        tweet = compose_daily_summary_tweet(candidates, content, include_posted=True)
        image_game = candidates[0]
        included_games = candidates[:2]
        post_as_poll = False
    elif slot == "midday":
        content = "featured_midday"
        image_game = candidates[0]
        tweet = _compose_featured_tweet(image_game, content)
        included_games = [image_game]
        post_as_poll = False
    else:
        open_tv_games = [game for game in candidates if _confirmed_free_mx_channels(game)]
        if open_tv_games:
            content = "free_tv_night"
            tweet = compose_free_tv_tweet(open_tv_games, content, include_posted=True)
            image_game = open_tv_games[0]
            included_games = open_tv_games[:2]
            post_as_poll = False
        else:
            content = "poll_night"
            image_game = None
            tweet, poll_options = _compose_viewing_poll(candidates[0], "night")
            included_games = [candidates[0]]
            post_as_poll = True
    if not tweet:
        logger.info("Daily X guide skipped: no eligible format")
        return {"success": True, "skipped": "no_eligible_format", "slot": slot}

    if post_as_poll:
        result = post_poll(tweet, poll_options, duration_min=720)
    elif image_game:
        card = _make_game_card(image_game)
        result = post_tweet_with_media(tweet, card) if card else post_tweet(tweet)
    else:
        result = post_tweet(tweet)
    if not result.get("success"):
        raise RuntimeError(f"X rechazó la publicación: {result.get('error', 'error desconocido')}")
    _mark_posted(sentinel)
    for game in included_games:
        game_id = str(game.get("id") or game.get("name") or "")
        if game_id:
            _mark_posted(game_id)
    logger.info("Daily X guide posted: slot=%s format=%s tweet_id=%s", slot, content, result.get("tweet_id"))
    return {**result, "slot": slot, "format": content}


async def _publish_daily_cli() -> None:
    print(json.dumps(await post_daily_guide(), ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vista previa local de las publicaciones de DondeVer en X")
    parser.add_argument("--preview", action="store_true", help="Obtiene partidos próximos y solo imprime ejemplos")
    parser.add_argument("--publish-daily", action="store_true", help="Publica una guía diaria; reservado para el Cron de Render")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    if args.preview == args.publish_daily:
        parser.error("Elige exactamente una opción: --preview (no publica) o --publish-daily")
    if args.publish_daily:
        asyncio.run(_publish_daily_cli())
    else:
        asyncio.run(_preview_cli(args.count, args.days))
