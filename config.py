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
# Always ensure whatsapp: prefix regardless of how env var is set
_raw_wa = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202").strip()
TWILIO_WA_NUMBER = _raw_wa if _raw_wa.startswith("whatsapp:") else f"whatsapp:{_raw_wa}"

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
        "cta": "Apuesta HOY con $3,000 de bono",
        "cta_short": "Registrate → $3,000 gratis",
        "cta_twitter": "Bono $3,000 en Caliente",
        "bonus": "100% hasta $3,000 MXN en tu primer deposito",
    },
    "betsson": {
        "name": "Betsson",
        "url": os.getenv("AFFILIATE_BETSSON", "") or "https://record.betsson.mx/_HF2ZLLLzsI5GDKPB4tjc7WNd7ZgqdRLk/1/",
        "logo": "/static/affiliates/betsson.svg",
        "cta": "Registrate y recibe $100 GRATIS + bono",
        "cta_short": "$100 gratis al registrarte",
        "cta_twitter": "Bono + $100 freebet en Betsson",
        "bonus": "$3,000 bono + $100 freebet sin deposito",
    },
    "cj": {
        "name": "NordVPN",
        "url": os.getenv("AFFILIATE_CJ", "") or "https://www.anrdoezrs.net/click-101647648-16968809",
        "logo": "/static/affiliates/vpn.svg",
        "cta": "Ver partidos de tu region",
        "cta_short": "Desbloquea canales",
        "cta_twitter": "Ve partidos bloqueados con VPN",
        "bonus": "Hasta 72% de descuento",
    },
    "amazon": {
        "name": "Amazon Prime Video",
        "url": os.getenv("AFFILIATE_AMAZON", "") or "https://www.amazon.com/gp/video/offers?tag=dondever2000-20",
        "logo": "/static/affiliates/amazon.svg",
        "cta": "Ve deportes en Prime Video",
        "cta_short": "Prueba gratis 30 dias",
        "cta_twitter": "Ve deportes en Amazon Prime Video",
        "bonus": "30 dias gratis",
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
    # ── Mexico ──
    "TUDN": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Canal 5": {"name": "Canal 5", "country": "MX", "type": "broadcast"},
    "Azteca 7": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "Azteca Deportes": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "TV Azteca": {"name": "TV Azteca", "country": "MX", "type": "broadcast"},
    "Fox Sports MX": {"name": "Fox Sports MX", "country": "MX", "type": "cable"},
    "FOX Sports MX": {"name": "Fox Sports MX", "country": "MX", "type": "cable"},
    "ViX": {"name": "ViX", "country": "MX", "type": "streaming"},
    "ViX Premium": {"name": "ViX", "country": "MX", "type": "streaming"},
    "VIX": {"name": "ViX", "country": "MX", "type": "streaming"},
    "ESPN MX": {"name": "ESPN MX", "country": "MX", "type": "cable"},
    "Claro Sports": {"name": "Claro Sports", "country": "MX", "type": "cable"},
    "Claro Video": {"name": "Claro Video", "country": "MX", "type": "streaming"},
    "Imagen TV": {"name": "Imagen TV", "country": "MX", "type": "broadcast"},
    "Las Estrellas": {"name": "Las Estrellas", "country": "MX", "type": "broadcast"},
    "Caliente TV": {"name": "Caliente TV", "country": "MX", "type": "streaming"},
    "Afizzionados": {"name": "Afizzionados", "country": "MX", "type": "cable"},
    "ESPN Deportes": {"name": "ESPN Deportes", "country": "MX", "type": "cable"},
    "Blue To Go": {"name": "Blue To Go", "country": "MX", "type": "streaming"},
    # ── USA ──
    "ESPN": {"name": "ESPN", "country": "US", "type": "cable"},
    "ESPN2": {"name": "ESPN2", "country": "US", "type": "cable"},
    "ESPN+": {"name": "ESPN+", "country": "US", "type": "streaming"},
    "ESPNU": {"name": "ESPNU", "country": "US", "type": "cable"},
    "ESPNews": {"name": "ESPNews", "country": "US", "type": "cable"},
    "FOX": {"name": "FOX", "country": "US", "type": "broadcast"},
    "FS1": {"name": "FS1", "country": "US", "type": "cable"},
    "FS2": {"name": "FS2", "country": "US", "type": "cable"},
    "Fox Sports 1": {"name": "FS1", "country": "US", "type": "cable"},
    "Fox Sports 2": {"name": "FS2", "country": "US", "type": "cable"},
    "FOX One": {"name": "FOX", "country": "US", "type": "broadcast"},
    "NBC": {"name": "NBC", "country": "US", "type": "broadcast"},
    "NBCSN": {"name": "NBC Sports", "country": "US", "type": "cable"},
    "USA Network": {"name": "USA Network", "country": "US", "type": "cable"},
    "USA": {"name": "USA Network", "country": "US", "type": "cable"},
    "Peacock": {"name": "Peacock", "country": "US", "type": "streaming"},
    "CBS": {"name": "CBS", "country": "US", "type": "broadcast"},
    "Paramount+": {"name": "Paramount+", "country": "US", "type": "streaming"},
    "CBS Sports": {"name": "CBS Sports", "country": "US", "type": "cable"},
    "CBSSN": {"name": "CBS Sports", "country": "US", "type": "cable"},
    "TNT": {"name": "TNT", "country": "US", "type": "cable"},
    "TBS": {"name": "TBS", "country": "US", "type": "cable"},
    "truTV": {"name": "truTV", "country": "US", "type": "cable"},
    "ABC": {"name": "ABC", "country": "US", "type": "broadcast"},
    "Amazon Prime": {"name": "Prime Video", "country": "US", "type": "streaming"},
    "Prime Video": {"name": "Prime Video", "country": "US", "type": "streaming"},
    "Apple TV+": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "Apple TV": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "MLS Season Pass": {"name": "MLS Season Pass", "country": "US", "type": "streaming"},
    "MLB.TV": {"name": "MLB.TV", "country": "US", "type": "streaming"},
    "NFL+": {"name": "NFL+", "country": "US", "type": "streaming"},
    "NFL Network": {"name": "NFL Network", "country": "US", "type": "cable"},
    "NFLN": {"name": "NFL Network", "country": "US", "type": "cable"},
    "NBA TV": {"name": "NBA TV", "country": "US", "type": "cable"},
    "MLB Network": {"name": "MLB Network", "country": "US", "type": "cable"},
    "MLBN": {"name": "MLB Network", "country": "US", "type": "cable"},
    "MAX": {"name": "Max", "country": "US", "type": "streaming"},
    "Max": {"name": "Max", "country": "US", "type": "streaming"},
    "HBO Max": {"name": "Max", "country": "US", "type": "streaming"},
    # ── Both / Spanish US (shown under MX for our audience) ──
    "Univision": {"name": "Univision", "country": "MX", "type": "broadcast"},
    "UniMas": {"name": "UniMas", "country": "MX", "type": "broadcast"},
    "UniMás": {"name": "UniMas", "country": "MX", "type": "broadcast"},
    "TUDN USA": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Telemundo": {"name": "Telemundo", "country": "MX", "type": "broadcast"},
    "FOX Deportes": {"name": "FOX Deportes", "country": "MX", "type": "cable"},
    "FOXD": {"name": "FOX Deportes", "country": "MX", "type": "cable"},
}

# ESPN shortName often truncates channel names. This maps truncated → canonical.
ESPN_CHANNEL_NORMALIZE = {
    "Tele": "Telemundo",
    "Uni": "Univision",
    "UniMas": "UniMas",
    "UniMás": "UniMas",
    "FOXD": "FOX Deportes",
    "Fox Dep": "FOX Deportes",
    "FS1": "FS1",
    "FS2": "FS2",
    "FOX One": "FOX",
    "NBCSN": "NBC Sports",
    "CBSSN": "CBS Sports",
    "NFLN": "NFL Network",
    "MLBN": "MLB Network",
    "Paramount": "Paramount+",
    "Amazon": "Prime Video",
    "Apple": "Apple TV+",
    "VIX": "ViX",
    "TUDNxtra": "TUDN",
}
