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
    "1xbet": {
        "name": "1xBet",
        "url": os.getenv("AFFILIATE_1XBET", "https://reffpa.com/L?tag=d_5182312m_1599c_&site=5182312&ad=1599"),
        "logo": "/static/affiliates/1xbet.svg",
        "cta": "Apuesta aqui",
    },
    "betsson": {
        "name": "Betsson",
        "url": os.getenv("AFFILIATE_BETSSON", "https://record.betsson.mx/_HF2ZLLLzsI4k5VDSMnChDGCjLk9Ro7mn/1/"),
        "logo": "/static/affiliates/betsson.svg",
        "cta": "Apostar en Betsson",
    },
    "cj": {
        "name": "NordVPN",
        "url": os.getenv("AFFILIATE_CJ", "https://www.anrdoezrs.net/click-101647648-16968809"),
        "logo": "/static/affiliates/vpn.svg",
        "cta": "Desbloquea con VPN",
    },
}

# ── ESPN Sports & Leagues ────────────────────────────────
# slug -> (sport, league, display_name, emoji)
LEAGUES = {
    # Futbol
    "liga-mx":       ("soccer", "mex.1",        "Liga MX",          "\u26bd"),
    "liga-mx-femenil": ("soccer", "mex.w1",     "Liga MX Femenil",  "\u26bd"),
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
    "lmp":           ("baseball", "mex.pacific", "Liga Mexicana del Pacifico", "\u26be"),
    # Hockey
    "nhl":           ("hockey", "nhl",           "NHL",              "NHL"),
    # Combate
    "ufc":           ("mma", "ufc",             "UFC",              "BOX"),
    "boxing":        ("boxing", "boxing",        "Boxeo",            "BOX"),
    # Motor
    "f1":            ("racing", "f1",            "Formula 1",        "F1"),
    "nascar":        ("racing", "nascar",        "NASCAR",           "CAR"),
    # Tenis
    "atp":           ("tennis", "atp",           "ATP Tennis",       "TEN"),
    "wta":           ("tennis", "wta",           "WTA Tennis",       "TEN"),
    # Golf
    "pga":           ("golf", "pga",             "PGA Tour",         "\u26f3"),
}


def get_affiliate_url(key: str, source: str = "web") -> str:
    """
    Get affiliate URL with source tracking parameter.
    source: 'web', 'twitter', 'whatsapp'
    Most affiliate networks accept sub-tracking via URL params.
    """
    aff = AFFILIATES.get(key, {})
    url = aff.get("url", "#")
    if url == "#":
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}sub1={source}"


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
