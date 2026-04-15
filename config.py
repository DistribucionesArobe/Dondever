import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ── Timezones (DST-aware) ───────────────────────────────
TZ_MX = ZoneInfo("America/Mexico_City")
TZ_ET = ZoneInfo("America/New_York")

# ── Database ──────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost:5432/dondever")

# ── Twilio / WhatsApp ────────────────────────────────────
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")

# ── APIs ─────────────────────────────────────────────────
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
SPORTSDB_KEY = os.getenv("SPORTSDB_API_KEY", "154704")
SPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}"

# ── App ──────────────────────────────────────────────────
APP_URL = os.getenv("APP_URL", "https://dondever.app")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

# ── Affiliate links ─────────────────────────────────────
AFFILIATES = {
    "caliente": {
        "name": "Caliente",
        "url": os.getenv("AFFILIATE_CALIENTE", "") or "https://online.caliente.mx/page?member=Dondever&campaign=DEFAULT&channel=DEFAULT&zone=68997593&lp=68997591",
        "logo": "/static/affiliates/caliente.svg",
        "cta": "Apuesta en Caliente",
    },
    "betsson": {
        "name": "Betsson",
        "url": os.getenv("AFFILIATE_BETSSON", "") or "https://record.betsson.mx/_HF2ZLLLzsI5GDKPB4tjc7WNd7ZgqdRLk/1/",
        "logo": "/static/affiliates/betsson.svg",
        "cta": "Apostar en Betsson",
    },
    "cj": {
        "name": "NordVPN",
        "url": os.getenv("AFFILIATE_CJ", "") or "https://www.anrdoezrs.net/click-101647648-16968809",
        "logo": "/static/affiliates/vpn.svg",
        "cta": "Desbloquea con VPN",
    },
}

# ── ESPN Sports & Leagues ────────────────────────────────
# slug -> (sport, league, display_name, emoji)
LEAGUES = {
    # Futbol
    "liga-mx":       ("soccer", "mex.1",        "Liga MX",          "\u26bd"),
    # "liga-mx-femenil": ("soccer", "mex.w1",     "Liga MX Femenil",  "\u26bd"),  # fuera de temporada
    "mls":           ("soccer", "usa.1",         "MLS",              "\u26bd"),
    "liga-expansion": ("soccer", "mex.2",        "Liga Expansion MX","\u26bd"),
    "premier-league":("soccer", "eng.1",         "Premier League",   "\u26bd"),
    "la-liga":       ("soccer", "esp.1",         "La Liga",          "\u26bd"),
    "serie-a":       ("soccer", "ita.1",         "Serie A",          "\u26bd"),
    "bundesliga":    ("soccer", "ger.1",         "Bundesliga",       "\u26bd"),
    "ligue-1":       ("soccer", "fra.1",         "Ligue 1",         "\u26bd"),
    "champions":     ("soccer", "uefa.champions","Champions League", "\u26bd"),
    "europa-league": ("soccer", "uefa.europa",   "Europa League",    "\u26bd"),
    "concacaf-cl":   ("soccer", "concacaf.champions", "Concacaf Champions Cup", "\u26bd"),
    "copa-america":  ("soccer", "conmebol.america", "Copa America",  "\u26bd"),
    "world-cup":     ("soccer", "fifa.world",    "Copa del Mundo",   "\u26bd"),
    "club-friendly": ("soccer", "fifa.friendly", "Amistosos",        "\u26bd"),
    # Futbol Americano
    "nfl":           ("football", "nfl",         "NFL",              "NFL"),
    "college-football": ("football", "college-football", "College Football", "NFL"),
    # Basquetbol
    "nba":           ("basketball", "nba",       "NBA",              "NBA"),
    "wnba":          ("basketball", "wnba",      "WNBA",            "NBA"),
    # Beisbol
    "mlb":           ("baseball", "mlb",         "MLB",              "\u26be"),
    # "lmp":           ("baseball", "mex.pacific", "Liga Mexicana del Pacifico", "\u26be"),  # fuera de temporada (oct-ene)
    # Hockey
    "nhl":           ("hockey", "nhl",           "NHL",              "NHL"),
    # Combate
    "ufc":           ("mma", "ufc",             "UFC",              "BOX"),
    # "boxing":        ("boxing", "boxing",        "Boxeo",            "BOX"),  # ESPN no soporta scoreboard con fecha
}

# Leagues that don't show team names well (individual sports)
# Only loaded when explicitly filtered, not on homepage
LEAGUES_INDIVIDUAL = {
    "f1":            ("racing", "f1",            "Formula 1",        "F1"),
    "nascar":        ("racing", "nascar",        "NASCAR",           "CAR"),
    "atp":           ("tennis", "atp",           "ATP Tennis",       "TEN"),
    "wta":           ("tennis", "wta",           "WTA Tennis",       "TEN"),
    "pga":           ("golf", "pga",             "PGA Tour",         "\u26f3"),
}

# Combined for lookups
ALL_LEAGUES = {**LEAGUES, **LEAGUES_INDIVIDUAL}


def get_affiliate_url(key: str, source: str = "web") -> str:
    """
    Get affiliate URL with source tracking parameter.
    source: 'web', 'twitter', 'whatsapp'
    Most affiliate networks accept sub-tracking via URL params.
    """
    aff = AFFILIATES.get(key, {})
    url = aff.get("url", "")
    if not url or url == "#":
        return "#"
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}sub1={source}"


def get_short_affiliate_url(key: str, source: str = "web") -> str:
    """
    Short branded link like https://dondever.app/go/betsson?s=twitter.
    Used in tweets/WhatsApp for cleaner display — server redirects to real URL.
    """
    if key not in AFFILIATES:
        return APP_URL
    return f"{APP_URL}/go/{key}?s={source}"


# ── Team Aliases (common names → ESPN names) ───────────
# Allows WhatsApp bot and search to find teams by nicknames
TEAM_ALIASES = {
    # Liga MX
    "chivas": "guadalajara",
    "america": "america",
    "aguilas": "america",
    "las aguilas": "america",
    "pumas": "unam",
    "cougars": "unam",
    "azul": "cruz azul",
    "la maquina": "cruz azul",
    "tuzos": "pachuca",
    "rayados": "monterrey",
    "tigres": "tigres uanl",
    "santos": "santos laguna",
    "diablos": "toluca",
    "xolos": "tijuana",
    "atlas": "atlas",
    "zorros": "atlas",
    "leon": "leon",
    "necaxa": "necaxa",
    "puebla": "puebla",
    "queretaro": "queretaro",
    "mazatlan": "mazatlan",
    "juarez": "juarez",
    # NFL
    "pats": "patriots",
    "niners": "49ers",
    "pack": "packers",
    "bolts": "chargers",
    "birds": "eagles",
    "fins": "dolphins",
    "boys": "cowboys",
    "vaqueros": "cowboys",
    # NBA
    "lakers": "lakers",
    "warriors": "warriors",
    "dubs": "warriors",
    "celtics": "celtics",
    "heat": "heat",
    "bulls": "bulls",
    # MLB
    "dodgers": "dodgers",
    "yankees": "yankees",
    "yanquis": "yankees",
    "medias rojas": "red sox",
    "cachorros": "cubs",
}

# ── Sports display conventions ──────────────────────────
# Sports where home team (local) goes on the LEFT side
# In soccer/futbol, convention is Local vs Visitante
HOME_LEFT_SPORTS = {"soccer", "boxing", "mma"}


# Known TV channel mappings for Mexico/US (manual enrichment)
CHANNEL_ALIASES = {
    # Mexico
    "TUDN": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Canal 5": {"name": "Canal 5", "country": "MX", "type": "broadcast"},
    "Azteca 7": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "Fox Sports MX": {"name": "Fox Sports Mexico", "country": "MX", "type": "cable"},
    "ViX": {"name": "ViX Premium", "country": "MX", "type": "streaming"},
    "ESPN MX": {"name": "ESPN Mexico", "country": "MX", "type": "cable"},
    "Claro Sports": {"name": "Claro Sports", "country": "MX", "type": "cable"},
    # USA
    "ESPN": {"name": "ESPN", "country": "US", "type": "cable"},
    "ESPN2": {"name": "ESPN2", "country": "US", "type": "cable"},
    "ESPN+": {"name": "ESPN+", "country": "US", "type": "streaming"},
    "FOX": {"name": "FOX", "country": "US", "type": "broadcast"},
    "FS1": {"name": "Fox Sports 1", "country": "US", "type": "cable"},
    "NBC": {"name": "NBC", "country": "US", "type": "broadcast"},
    "Peacock": {"name": "Peacock", "country": "US", "type": "streaming"},
    "CBS": {"name": "CBS", "country": "US", "type": "broadcast"},
    "Paramount+": {"name": "Paramount+", "country": "US", "type": "streaming"},
    "TNT": {"name": "TNT", "country": "US", "type": "cable"},
    "ABC": {"name": "ABC", "country": "US", "type": "broadcast"},
    "Amazon Prime": {"name": "Amazon Prime Video", "country": "US", "type": "streaming"},
    "Apple TV+": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "Univision": {"name": "Univision", "country": "US", "type": "broadcast"},
    "TUDN USA": {"name": "TUDN USA", "country": "US", "type": "cable"},
}
