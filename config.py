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
        "cta_short": "Ver en Prime Video",
        "cta_twitter": "Ve deportes en Amazon Prime Video",
        "bonus": "Deportes en vivo en Prime Video",
    },
    "surfshark": {
        "name": "Surfshark VPN",
        "url": os.getenv("AFFILIATE_SURFSHARK", "") or "https://www.jdoqocy.com/click-101647648-15741703",
        "logo": "/static/affiliates/vpn.svg",
        "cta": "Desbloquea contenido deportivo con VPN",
        "cta_short": "VPN para deportes",
        "cta_twitter": "Ve partidos de cualquier region con VPN",
        "bonus": "Hasta 86% de descuento + 3 meses gratis",
    },
    "jubilee": {
        "name": "Jubilee",
        "url": os.getenv("AFFILIATE_JUBILEE", "") or "https://www.jubilee.mx/",
        "logo": "/static/affiliates/jubilee.svg",
        "cta": "Apuesta en Jubilee — Betbuilder + Cash Out en vivo",
        "cta_short": "Betbuilder + Cash Out",
        "cta_twitter": "Betbuilder y Pago anticipado en Jubilee",
        "bonus": "Betbuilder + Pago anticipado + Cuotas mejoradas",
    },
    "vivento": {
        "name": "Vivento",
        "url": os.getenv("AFFILIATE_VIVENTO", "") or "https://vivento.mx/",
        "logo": "/static/affiliates/vivento.svg",
        "cta": "Apuesta en Vivento — Cuotas mejoradas + Cash Out",
        "cta_short": "Cuotas mejoradas + Cash Out",
        "cta_twitter": "Cuotas mejoradas y Pago anticipado en Vivento",
        "bonus": "Cuotas mejoradas + Pago anticipado + Acumulador",
    },
    "1xbet": {
        "name": "1xBet",
        "url": os.getenv("AFFILIATE_1XBET", "") or "https://reffpa.com/L?tag=d_5182312m_1599c_&site=5182312&ad=1599",
        "logo": "/static/affiliates/1xbet.svg",
        "cta": "Apuesta en 1xBet — Bono de bienvenida hasta $6,500 MXN",
        "cta_short": "Bono hasta $6,500",
        "cta_twitter": "Bono de bienvenida en 1xBet",
        "bonus": "Bono primer deposito hasta $6,500 MXN",
    },
}

# ── MercadoLibre Afiliados (Mexico / LATAM) ──────────────
# Profile: Distribuciones Arobe — tracked short links via meli.la
# Etiqueta: du20260520124652  (contacto@arobegroup.com)
# Commissions: up to 24% (ropa/calzado 20%, electrónica 10%)
# Cookie window: 30 days from click
# Links generated via "Generador de productos recomendados" in Central de Afiliados
MELI_TAG = "du20260520124652"
MELI_AFF_PARAM = ""                      # not used — ML uses meli.la short links instead
MELI_BASE = "https://listado.mercadolibre.com.mx"

# Curated MercadoLibre affiliate links per team (meli.la tracked URLs)
# ALL links generated with account "Distribuciones Arobe" (du20260520124652)
# Teams not listed fall back to direct search URL in the template.
# To add a team: go to Central de Afiliados > Generador de productos recomendados,
# paste https://listado.mercadolibre.com.mx/jersey-{slug}, click Generar.
TEAM_SHOP_MELI = {
    # ── Liga MX (17) ──
    "chivas":           "https://meli.la/2WwgT8Y",
    "america":          "https://meli.la/27sRtKY",
    "cruz-azul":        "https://meli.la/2w5ZTWB",
    "pumas":            "https://meli.la/2wT43sv",
    "tigres":           "https://meli.la/1AvBrRh",
    "monterrey":        "https://meli.la/2SunN3R",
    "toluca":           "https://meli.la/2XE7pBU",
    "santos":           "https://meli.la/1PyuVpY",
    "leon":             "https://meli.la/1VHy586",
    "pachuca":          "https://meli.la/2SJBpA1",
    "atlas":            "https://meli.la/2ruFZc7",
    "necaxa":           "https://meli.la/2aV1G75",
    "puebla":           "https://meli.la/1skuqJp",
    "queretaro":        "https://meli.la/33L4r7T",
    "mazatlan":         "https://meli.la/2MG5BTN",
    "tijuana":          "https://meli.la/2mwWWdZ",
    "juarez":           "https://meli.la/162PcPo",
    # ── Europa (12) ──
    "real-madrid":      "https://meli.la/1cDyGUy",
    "barcelona":        "https://meli.la/2uRtp8K",
    "manchester-city":  "https://meli.la/1aHvoBY",
    "liverpool":        "https://meli.la/2cnCHuQ",
    "manchester-united":"https://meli.la/2VMYG2C",
    "chelsea":          "https://meli.la/2YazxNT",
    "arsenal":          "https://meli.la/29UQkiU",
    "juventus":         "https://meli.la/2rhDp3G",
    "inter-milan":      "https://meli.la/1PMUtvH",
    "ac-milan":         "https://meli.la/2kuJvFe",
    "psg":              "https://meli.la/1enphi7",
    "bayern":           "https://meli.la/1F2c9cp",
    # ── Europa extra (5) ──
    "atletico-madrid":  "https://meli.la/2vu6tt2",
    "napoli":           "https://meli.la/29PaRB5",
    "borussia-dortmund":"https://meli.la/1F8oZTg",
    "tottenham":        "https://meli.la/18Jwx83",
    "aston-villa":      "https://meli.la/2pGNmZt",
    # ── MLS (6) ──
    "lafc":             "https://meli.la/1ji6svF",
    "la-galaxy":        "https://meli.la/1vYyUFr",
    "inter-miami":      "https://meli.la/2Gzbo7V",
    "austin-fc":        "https://meli.la/2A1VXN7",
    "houston-dynamo":   "https://meli.la/1vz12NL",
    "fc-dallas":        "https://meli.la/1owWiQj",
    # ── NBA (16) ──
    "lakers":           "https://meli.la/1QEtPnz",
    "celtics":          "https://meli.la/2M1VQwg",
    "warriors":         "https://meli.la/24C9hL2",
    "bulls":            "https://meli.la/1qYTcGE",
    "heat":             "https://meli.la/16S6CPe",
    "knicks":           "https://meli.la/16ozTqe",
    "nuggets":          "https://meli.la/2vgWwDr",
    "bucks":            "https://meli.la/1dMT8oE",
    "mavericks":        "https://meli.la/1SGgBx3",
    "clippers":         "https://meli.la/1h8CKav",
    "suns":             "https://meli.la/1zNB3ew",
    "spurs-nba":        "https://meli.la/1Uf1qC7",
    "76ers":            "https://meli.la/2ENrkk5",
    "thunder":          "https://meli.la/2WybVXk",
    "timberwolves":     "https://meli.la/25FMuG8",
    "cavaliers":        "https://meli.la/1kdP7vQ",
    # ── NFL (31) ──
    "cowboys":          "https://meli.la/33kNJNh",
    "chiefs":           "https://meli.la/1AgNcTW",
    "49ers":            "https://meli.la/1v9FDCT",
    "eagles":           "https://meli.la/1gMCii8",
    "packers":          "https://meli.la/1LVgMLT",
    "steelers":         "https://meli.la/2TTzdea",
    "raiders":          "https://meli.la/1ujxEz3",
    "dolphins":         "https://meli.la/322VVA8",
    "patriots":         "https://meli.la/324Rcko",
    "texans":           "https://meli.la/2xK4sS4",
    "ravens":           "https://meli.la/2ukPPPy",
    "bears":            "https://meli.la/2y1zvMn",
    "rams":             "https://meli.la/2kaYQoh",
    "chargers":         "https://meli.la/2TGms1j",
    "broncos":          "https://meli.la/2mpYPSE",
    "bills":            "https://meli.la/32BvBKS",
    "lions":            "https://meli.la/1NFXqyv",
    "vikings":          "https://meli.la/2R4fu4N",
    "bengals":          "https://meli.la/1H4Df7B",
    "giants-nfl":       "https://meli.la/15V9Wfb",
    "jets":             "https://meli.la/2C55cAK",
    "saints":           "https://meli.la/1RvY3tM",
    "seahawks":         "https://meli.la/2wRcXdi",
    "commanders":       "https://meli.la/2jdfYeT",
    "cardinals-nfl":    "https://meli.la/176Fch4",
    "buccaneers":       "https://meli.la/1oeAzda",
    "falcons":          "https://meli.la/1zsMaG1",
    "panthers-nfl":     "https://meli.la/1Xbkeva",
    "colts":            "https://meli.la/2eLcA88",
    "jaguars":          "https://meli.la/1sEdZ6V",
    "titans":           "https://meli.la/2xqwFZb",
    # ── MLB (30) ──
    "dodgers":          "https://meli.la/2aNHaUL",
    "yankees":          "https://meli.la/2v9wmgE",
    "red-sox":          "https://meli.la/1QECfNR",
    "astros":           "https://meli.la/1jukqoN",
    "mets":             "https://meli.la/2ai8mnK",
    "padres":           "https://meli.la/2cy2smF",
    "angels":           "https://meli.la/2UVw9pr",
    "athletics":        "https://meli.la/2hVGZpm",
    "blue-jays":        "https://meli.la/1ehJrAc",
    "braves":           "https://meli.la/2BVAosa",
    "brewers":          "https://meli.la/32pPJJ5",
    "cardinals":        "https://meli.la/2HupbyX",
    "cubs":             "https://meli.la/16Gy7xd",
    "diamondbacks":     "https://meli.la/1sSLofH",
    "giants":           "https://meli.la/2ySr7LU",
    "guardians":        "https://meli.la/21S595B",
    "mariners":         "https://meli.la/1xWMFHe",
    "marlins":          "https://meli.la/1nwtUst",
    "nationals":        "https://meli.la/2Nb1L7F",
    "orioles":          "https://meli.la/1uzr9dr",
    "phillies":         "https://meli.la/2PdgDH4",
    "pirates":          "https://meli.la/1QBfXF2",
    "rangers":          "https://meli.la/2bgk6FG",
    "rays":             "https://meli.la/1AC66Wv",
    "reds":             "https://meli.la/2WDoMQi",
    "rockies":          "https://meli.la/2z2Qs25",
    "royals":           "https://meli.la/2k91vkf",
    "tigers":           "https://meli.la/1y6JK25",
    "twins":            "https://meli.la/2gpDasW",
    "white-sox":        "https://meli.la/29nnWB5",
    # ── NHL (11) ──
    "bruins":           "https://meli.la/2k2SQ2b",
    "golden-knights":   "https://meli.la/1K3rSQm",
    "avalanche":        "https://meli.la/2SnqL1v",
    "panthers-nhl":     "https://meli.la/2pjCi1H",
    "rangers-nhl":      "https://meli.la/1WYX4XV",
    "maple-leafs":      "https://meli.la/2ejK5p1",
    "oilers":           "https://meli.la/2kJEQPg",
    "stars":            "https://meli.la/26Njw89",
    "blackhawks":       "https://meli.la/2VLRRdt",
    "penguins":         "https://meli.la/2ZANL9u",
    "capitals":         "https://meli.la/2nG9nSn",
    # ── Premier League (remaining 13) ──
    "bournemouth":      "https://listado.mercadolibre.com.mx/jersey-bournemouth",
    "brentford":        "https://listado.mercadolibre.com.mx/jersey-brentford",
    "brighton":         "https://listado.mercadolibre.com.mx/jersey-brighton",
    "crystal-palace":   "https://listado.mercadolibre.com.mx/jersey-crystal-palace",
    "everton":          "https://listado.mercadolibre.com.mx/jersey-everton",
    "fulham":           "https://listado.mercadolibre.com.mx/jersey-fulham",
    "ipswich":          "https://listado.mercadolibre.com.mx/jersey-ipswich-town",
    "leicester":        "https://listado.mercadolibre.com.mx/jersey-leicester-city",
    "newcastle":        "https://listado.mercadolibre.com.mx/jersey-newcastle",
    "nottingham-forest":"https://listado.mercadolibre.com.mx/jersey-nottingham-forest",
    "southampton":      "https://listado.mercadolibre.com.mx/jersey-southampton",
    "west-ham":         "https://listado.mercadolibre.com.mx/jersey-west-ham",
    "wolverhampton":    "https://listado.mercadolibre.com.mx/jersey-wolverhampton-wolves",
    # ── La Liga (remaining 17) ──
    "alaves":           "https://listado.mercadolibre.com.mx/jersey-alaves",
    "athletic-club":    "https://listado.mercadolibre.com.mx/jersey-athletic-bilbao",
    "celta-vigo":       "https://listado.mercadolibre.com.mx/jersey-celta-vigo",
    "espanyol":         "https://listado.mercadolibre.com.mx/jersey-espanyol",
    "getafe":           "https://listado.mercadolibre.com.mx/jersey-getafe",
    "girona":           "https://listado.mercadolibre.com.mx/jersey-girona-fc",
    "las-palmas":       "https://listado.mercadolibre.com.mx/jersey-las-palmas",
    "leganes":          "https://listado.mercadolibre.com.mx/jersey-leganes",
    "mallorca":         "https://listado.mercadolibre.com.mx/jersey-mallorca",
    "osasuna":          "https://listado.mercadolibre.com.mx/jersey-osasuna",
    "rayo-vallecano":   "https://listado.mercadolibre.com.mx/jersey-rayo-vallecano",
    "real-betis":       "https://listado.mercadolibre.com.mx/jersey-real-betis",
    "real-sociedad":    "https://listado.mercadolibre.com.mx/jersey-real-sociedad",
    "real-valladolid":  "https://listado.mercadolibre.com.mx/jersey-real-valladolid",
    "sevilla":          "https://listado.mercadolibre.com.mx/jersey-sevilla-fc",
    "valencia":         "https://listado.mercadolibre.com.mx/jersey-valencia-cf",
    "villarreal":       "https://listado.mercadolibre.com.mx/jersey-villarreal",
    # ── Serie A (remaining 16) ──
    "as-roma":          "https://listado.mercadolibre.com.mx/jersey-as-roma",
    "atalanta":         "https://listado.mercadolibre.com.mx/jersey-atalanta",
    "bologna":          "https://listado.mercadolibre.com.mx/jersey-bologna-fc",
    "cagliari":         "https://listado.mercadolibre.com.mx/jersey-cagliari",
    "como":             "https://listado.mercadolibre.com.mx/jersey-como-1907",
    "empoli":           "https://listado.mercadolibre.com.mx/jersey-empoli",
    "fiorentina":       "https://listado.mercadolibre.com.mx/jersey-fiorentina",
    "genoa":            "https://listado.mercadolibre.com.mx/jersey-genoa-cfc",
    "lazio":            "https://listado.mercadolibre.com.mx/jersey-lazio",
    "lecce":            "https://listado.mercadolibre.com.mx/jersey-lecce",
    "monza":            "https://listado.mercadolibre.com.mx/jersey-monza",
    "parma":            "https://listado.mercadolibre.com.mx/jersey-parma",
    "torino":           "https://listado.mercadolibre.com.mx/jersey-torino-fc",
    "udinese":          "https://listado.mercadolibre.com.mx/jersey-udinese",
    "venezia":          "https://listado.mercadolibre.com.mx/jersey-venezia-fc",
    "verona":           "https://listado.mercadolibre.com.mx/jersey-hellas-verona",
    # ── Bundesliga (remaining 15) ──
    "augsburg":         "https://listado.mercadolibre.com.mx/jersey-augsburg",
    "frankfurt":        "https://listado.mercadolibre.com.mx/jersey-eintracht-frankfurt",
    "freiburg":         "https://listado.mercadolibre.com.mx/jersey-freiburg",
    "gladbach":         "https://listado.mercadolibre.com.mx/jersey-borussia-monchengladbach",
    "heidenheim":       "https://listado.mercadolibre.com.mx/jersey-heidenheim",
    "hoffenheim":       "https://listado.mercadolibre.com.mx/jersey-hoffenheim",
    "koln":             "https://listado.mercadolibre.com.mx/jersey-koln-colonia",
    "leverkusen":       "https://listado.mercadolibre.com.mx/jersey-bayer-leverkusen",
    "mainz":            "https://listado.mercadolibre.com.mx/jersey-mainz",
    "rb-leipzig":       "https://listado.mercadolibre.com.mx/jersey-rb-leipzig",
    "st-pauli":         "https://listado.mercadolibre.com.mx/jersey-st-pauli",
    "stuttgart":        "https://listado.mercadolibre.com.mx/jersey-stuttgart",
    "union-berlin":     "https://listado.mercadolibre.com.mx/jersey-union-berlin",
    "werder-bremen":    "https://listado.mercadolibre.com.mx/jersey-werder-bremen",
    "wolfsburg":        "https://listado.mercadolibre.com.mx/jersey-wolfsburg",
    # ── Ligue 1 (remaining 16) ──
    "angers":           "https://listado.mercadolibre.com.mx/jersey-angers",
    "auxerre":          "https://listado.mercadolibre.com.mx/jersey-auxerre",
    "brest":            "https://listado.mercadolibre.com.mx/jersey-stade-brestois",
    "le-havre":         "https://listado.mercadolibre.com.mx/jersey-le-havre",
    "lens":             "https://listado.mercadolibre.com.mx/jersey-rc-lens",
    "lille":            "https://listado.mercadolibre.com.mx/jersey-lille",
    "lyon":             "https://listado.mercadolibre.com.mx/jersey-olympique-lyon",
    "marseille":        "https://listado.mercadolibre.com.mx/jersey-olympique-marsella",
    "monaco":           "https://listado.mercadolibre.com.mx/jersey-as-monaco",
    "montpellier":      "https://listado.mercadolibre.com.mx/jersey-montpellier",
    "nantes":           "https://listado.mercadolibre.com.mx/jersey-nantes",
    "nice":             "https://listado.mercadolibre.com.mx/jersey-niza-ogc-nice",
    "reims":            "https://listado.mercadolibre.com.mx/jersey-stade-reims",
    "rennes":           "https://listado.mercadolibre.com.mx/jersey-rennes",
    "strasbourg":       "https://listado.mercadolibre.com.mx/jersey-strasbourg",
    "toulouse":         "https://listado.mercadolibre.com.mx/jersey-toulouse",
    # ── Liga Portugal (15) ──
    "arouca":           "https://listado.mercadolibre.com.mx/jersey-arouca",
    "benfica":          "https://listado.mercadolibre.com.mx/jersey-benfica",
    "braga":            "https://listado.mercadolibre.com.mx/jersey-sporting-braga",
    "casa-pia":         "https://listado.mercadolibre.com.mx/jersey-casa-pia",
    "estoril":          "https://listado.mercadolibre.com.mx/jersey-estoril",
    "estrela":          "https://listado.mercadolibre.com.mx/jersey-estrela-amadora",
    "famalicao":        "https://listado.mercadolibre.com.mx/jersey-famalicao",
    "gil-vicente":      "https://listado.mercadolibre.com.mx/jersey-gil-vicente",
    "guimaraes":        "https://listado.mercadolibre.com.mx/jersey-vitoria-guimaraes",
    "moreirense":       "https://listado.mercadolibre.com.mx/jersey-moreirense",
    "nacional":         "https://listado.mercadolibre.com.mx/jersey-cd-nacional-madeira",
    "porto":            "https://listado.mercadolibre.com.mx/jersey-fc-porto",
    "rio-ave":          "https://listado.mercadolibre.com.mx/jersey-rio-ave",
    "santa-clara":      "https://listado.mercadolibre.com.mx/jersey-santa-clara",
    "sporting-cp":      "https://listado.mercadolibre.com.mx/jersey-sporting-lisboa",
    # ── Eredivisie (14) ──
    "ajax":             "https://listado.mercadolibre.com.mx/jersey-ajax-amsterdam",
    "az-alkmaar":       "https://listado.mercadolibre.com.mx/jersey-az-alkmaar",
    "fc-twente":        "https://listado.mercadolibre.com.mx/jersey-fc-twente",
    "fc-utrecht":       "https://listado.mercadolibre.com.mx/jersey-fc-utrecht",
    "feyenoord":        "https://listado.mercadolibre.com.mx/jersey-feyenoord",
    "fortuna-sittard":  "https://listado.mercadolibre.com.mx/jersey-fortuna-sittard",
    "go-ahead-eagles":  "https://listado.mercadolibre.com.mx/jersey-go-ahead-eagles",
    "groningen":        "https://listado.mercadolibre.com.mx/jersey-groningen",
    "heerenveen":       "https://listado.mercadolibre.com.mx/jersey-heerenveen",
    "heracles":         "https://listado.mercadolibre.com.mx/jersey-heracles-almelo",
    "nac-breda":        "https://listado.mercadolibre.com.mx/jersey-nac-breda",
    "nec":              "https://listado.mercadolibre.com.mx/jersey-nec-nijmegen",
    "psv":              "https://listado.mercadolibre.com.mx/jersey-psv-eindhoven",
    "sparta-rotterdam": "https://listado.mercadolibre.com.mx/jersey-sparta-rotterdam",
    # ── Liga Colombia (12) ──
    "america-cali":     "https://listado.mercadolibre.com.mx/jersey-america-de-cali",
    "atletico-nacional":"https://listado.mercadolibre.com.mx/jersey-atletico-nacional",
    "bucaramanga":      "https://listado.mercadolibre.com.mx/jersey-atletico-bucaramanga",
    "deportes-tolima":  "https://listado.mercadolibre.com.mx/jersey-deportes-tolima",
    "deportivo-cali":   "https://listado.mercadolibre.com.mx/jersey-deportivo-cali",
    "deportivo-pasto":  "https://listado.mercadolibre.com.mx/jersey-deportivo-pasto",
    "deportivo-pereira":"https://listado.mercadolibre.com.mx/jersey-deportivo-pereira",
    "independiente-medellin":"https://listado.mercadolibre.com.mx/jersey-independiente-medellin",
    "independiente-santa-fe":"https://listado.mercadolibre.com.mx/jersey-santa-fe-bogota",
    "junior-barranquilla":"https://listado.mercadolibre.com.mx/jersey-junior-barranquilla",
    "millonarios":      "https://listado.mercadolibre.com.mx/jersey-millonarios",
    "once-caldas":      "https://listado.mercadolibre.com.mx/jersey-once-caldas",
    # ── Liga Argentina (19) ──
    "argentinos-juniors":"https://listado.mercadolibre.com.mx/jersey-argentinos-juniors",
    "banfield":         "https://listado.mercadolibre.com.mx/jersey-banfield",
    "belgrano":         "https://listado.mercadolibre.com.mx/jersey-belgrano-cordoba",
    "boca-juniors":     "https://listado.mercadolibre.com.mx/jersey-boca-juniors",
    "defensa-y-justicia":"https://listado.mercadolibre.com.mx/jersey-defensa-justicia",
    "estudiantes":      "https://listado.mercadolibre.com.mx/jersey-estudiantes-la-plata",
    "gimnasia-lp":      "https://listado.mercadolibre.com.mx/jersey-gimnasia-la-plata",
    "huracan":          "https://listado.mercadolibre.com.mx/jersey-huracan",
    "independiente-arg":"https://listado.mercadolibre.com.mx/jersey-independiente-argentina",
    "lanus":            "https://listado.mercadolibre.com.mx/jersey-lanus",
    "newells":          "https://listado.mercadolibre.com.mx/jersey-newells-old-boys",
    "platense":         "https://listado.mercadolibre.com.mx/jersey-platense",
    "racing-club":      "https://listado.mercadolibre.com.mx/jersey-racing-club",
    "river-plate":      "https://listado.mercadolibre.com.mx/jersey-river-plate",
    "rosario-central":  "https://listado.mercadolibre.com.mx/jersey-rosario-central",
    "san-lorenzo":      "https://listado.mercadolibre.com.mx/jersey-san-lorenzo",
    "talleres":         "https://listado.mercadolibre.com.mx/jersey-talleres-cordoba",
    "tigre":            "https://listado.mercadolibre.com.mx/jersey-club-tigre",
    "velez-sarsfield":  "https://listado.mercadolibre.com.mx/jersey-velez-sarsfield",
    # ── LigaPro Ecuador (10) ──
    "aucas":            "https://listado.mercadolibre.com.mx/jersey-aucas-ecuador",
    "barcelona-sc":     "https://listado.mercadolibre.com.mx/jersey-barcelona-guayaquil",
    "delfin":           "https://listado.mercadolibre.com.mx/jersey-delfin-sc",
    "deportivo-cuenca": "https://listado.mercadolibre.com.mx/jersey-deportivo-cuenca",
    "emelec":           "https://listado.mercadolibre.com.mx/jersey-emelec",
    "independiente-del-valle":"https://listado.mercadolibre.com.mx/jersey-independiente-del-valle",
    "ldu-quito":        "https://listado.mercadolibre.com.mx/jersey-liga-de-quito",
    "mushuc-runa":      "https://listado.mercadolibre.com.mx/jersey-mushuc-runa",
    "orense":           "https://listado.mercadolibre.com.mx/jersey-orense-sc",
    "tecnico-universitario":"https://listado.mercadolibre.com.mx/jersey-tecnico-universitario",
    # ── Primera Chile (10) ──
    "audax-italiano":   "https://listado.mercadolibre.com.mx/jersey-audax-italiano",
    "cobreloa":         "https://listado.mercadolibre.com.mx/jersey-cobreloa",
    "cobresal":         "https://listado.mercadolibre.com.mx/jersey-cobresal",
    "colo-colo":        "https://listado.mercadolibre.com.mx/jersey-colo-colo",
    "everton-chile":    "https://listado.mercadolibre.com.mx/jersey-everton-vina-del-mar",
    "huachipato":       "https://listado.mercadolibre.com.mx/jersey-huachipato",
    "ohiggins":         "https://listado.mercadolibre.com.mx/jersey-ohiggins",
    "palestino":        "https://listado.mercadolibre.com.mx/jersey-palestino-chile",
    "universidad-catolica":"https://listado.mercadolibre.com.mx/jersey-universidad-catolica-chile",
    "universidad-chile":"https://listado.mercadolibre.com.mx/jersey-universidad-de-chile",
    # ── Liga 1 Perú (8) ──
    "alianza-lima":     "https://listado.mercadolibre.com.mx/jersey-alianza-lima",
    "cienciano":        "https://listado.mercadolibre.com.mx/jersey-cienciano",
    "cusco-fc":         "https://listado.mercadolibre.com.mx/jersey-cusco-fc",
    "melgar":           "https://listado.mercadolibre.com.mx/jersey-melgar-arequipa",
    "sport-boys":       "https://listado.mercadolibre.com.mx/jersey-sport-boys-callao",
    "sport-huancayo":   "https://listado.mercadolibre.com.mx/jersey-sport-huancayo",
    "sporting-cristal": "https://listado.mercadolibre.com.mx/jersey-sporting-cristal",
    "universitario":    "https://listado.mercadolibre.com.mx/jersey-universitario-deportes-peru",
}

# ── Curated Amazon Products per team ─────────────────────
# Featured products with real ASINs for top teams.
# Teams not listed fall back to search URL in the template.
# tag is appended in the template: amazon.com/dp/{asin}?tag=dondever2000-20
# To update: search the product on Amazon, copy ASIN from URL.
TEAM_SHOP = {
    # ── Liga MX ──
    "america": {
        "jersey": {"asin": "B0FH7M43MM", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": "https://m.media-amazon.com/images/I/51-gle5XsOL._AC_SX200_.jpg"},
        "gorra":  {"asin": "B08BMJ5F8Y", "name": "Gorra oficial", "brand": "Fan Ink",
                   "img": "https://m.media-amazon.com/images/I/817C4FrDFvL._AC_SX200_.jpg"},
    },
    "chivas": {
        "jersey": {"asin": "B0F1DSZR1C", "name": "Jersey Réplica 25/26", "brand": "Puma",
                   "img": "https://m.media-amazon.com/images/I/51Fq57hvNVL._AC_SX200_.jpg"},
        "gorra":  {"asin": "B0FJQ7GT33", "name": "Gorra oficial", "brand": "Icon Sports",
                   "img": "https://m.media-amazon.com/images/I/71Ki1YPXvtL._AC_SX200_.jpg"},
    },
    "cruz-azul": {
        "jersey": {"asin": "B0H7M99NVQ", "name": "Jersey Local 26/27", "brand": "Pirma",
                   "img": "https://m.media-amazon.com/images/I/71PRdGlPmqL._AC_SX200_.jpg"},
    },
    "tigres": {
        "jersey": {"asin": "B0FKCZYXZR", "name": "Jersey Visitante 25/26", "brand": "adidas",
                   "img": "https://m.media-amazon.com/images/I/81dfyEFYX8L._AC_SX200_.jpg"},
    },
    "pumas": {
        "jersey": {"asin": "B0FP2RZ263", "name": "Jersey Visitante 25/26", "brand": "Puma",
                   "img": "https://m.media-amazon.com/images/I/719BgkwP4qL._AC_SX200_.jpg"},
    },
    "monterrey": {
        "jersey": {"asin": "B0GL7DXJZC", "name": "Jersey Local", "brand": "Puma",
                   "img": "https://m.media-amazon.com/images/I/51FaBYwo-KL._AC_SX200_.jpg"},
    },
    # ── MLB ──
    "dodgers": {
        "jersey": {"asin": "B0D9KPCKDH", "name": "Jersey Ohtani Alt.", "brand": "Outerstuff",
                   "img": "https://m.media-amazon.com/images/I/710WPsyxp+L._AC_SX200_.jpg"},
    },
    "yankees": {
        "jersey": {"asin": "B0D9MTYCRX", "name": "Jersey Aaron Judge", "brand": "Outerstuff",
                   "img": "https://m.media-amazon.com/images/I/81nnAt+GOYL._AC_SX200_.jpg"},
    },
    # ── NFL ──
    "cowboys": {
        "jersey": {"asin": "B0F45WPRLY", "name": "Camiseta oficial", "brand": "FOCO",
                   "img": "https://m.media-amazon.com/images/I/71qp-FWjBrL._AC_SX200_.jpg"},
    },
    "chiefs": {
        "jersey": {"asin": "B0FY7NNM3Y", "name": "Jersey Mahomes", "brand": "NFL Pro Line",
                   "img": "https://m.media-amazon.com/images/I/81yl1CeEQEL._AC_SX200_.jpg"},
    },
    "raiders": {
        "jersey": {"asin": "B0FTTNDCNH", "name": "Jersey Raiders", "brand": "NFL Pro Line",
                   "img": "https://m.media-amazon.com/images/I/71svSxym4WL._AC_SX200_.jpg"},
    },
    # ── NBA ──
    "lakers": {
        "jersey": {"asin": "B0CKCZF66D", "name": "Jersey LeBron James", "brand": "Nike",
                   "img": "https://m.media-amazon.com/images/I/71rKzitOGfL._AC_SX200_.jpg"},
    },
    "warriors": {
        "jersey": {"asin": "B0CKD6LX56", "name": "Jersey Stephen Curry", "brand": "Nike",
                   "img": "https://m.media-amazon.com/images/I/61oNp4b6EqL._AC_SX200_.jpg"},
    },
    # ── Europa ──
    "real-madrid": {
        "jersey": {"asin": "B0FH7CJT1C", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": "https://m.media-amazon.com/images/I/61VBS366HwL._AC_SX200_.jpg"},
    },
    "barcelona": {
        "jersey": {"asin": "B0FP2QD1QM", "name": "Jersey 25/26", "brand": "Nike",
                   "img": "https://m.media-amazon.com/images/I/616x14NyV1L._AC_SX200_.jpg"},
    },
    "inter-miami": {
        "jersey": {"asin": "B0D6Z3918S", "name": "Jersey Messi 25/26", "brand": "adidas",
                   "img": "https://m.media-amazon.com/images/I/61n9+sNi1JL._AC_SX200_.jpg"},
    },
    # ── Europa (nuevos) ──
    "as-roma": {
        "jersey": {"asin": "B0FKDBM9YY", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": ""},
    },
    "leverkusen": {
        "jersey": {"asin": "B0GWFY1P53", "name": "Jersey Visitante 25/26", "brand": "New Balance",
                   "img": ""},
    },
    "ajax": {
        "jersey": {"asin": "B0FJPXX6KC", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": ""},
    },
    "benfica": {
        "jersey": {"asin": "B0GHPBP8D4", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": ""},
    },
    "porto": {
        "jersey": {"asin": "B0CD4X4Z65", "name": "Jersey Local", "brand": "New Balance",
                   "img": ""},
    },
    # ── Sudamérica (nuevos) ──
    "boca-juniors": {
        "jersey": {"asin": "B0FKBCK6DW", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": ""},
    },
    "river-plate": {
        "jersey": {"asin": "B0FKD17BG1", "name": "Jersey Local 25/26", "brand": "adidas",
                   "img": ""},
    },
    "barcelona-sc": {
        "jersey": {"asin": "B0FQY7VSSG", "name": "Jersey Centenario", "brand": "BSC Oficial",
                   "img": ""},
    },
}

# ── Streaming Providers (central config) ─────────────────
# Single source of truth for streaming platforms. Maps channel display
# names → provider info for contextual monetization: when a game
# broadcasts on one of these channels, the channel tag becomes a link.
# Keys must match the CHANNEL_ALIASES display name (after normalization).
#
# IMPORTANT (policy):
# - "is_affiliate": True ONLY when we have a REAL affiliate program
#   (tracked URL). Otherwise False and "affiliate_url" stays None.
# - CTAs must be NEUTRAL ("Ver en X"). NO invented promos ("30 días
#   gratis", "prueba gratis", "descuento") unless the deal is confirmed —
#   then set "cta_promo" explicitly along with the affiliate program.
# - When an affiliate deal lands: set AFFILIATE_<KEY> env var (or
#   affiliate_url here) and flip is_affiliate=True. Nothing else changes.
STREAMING_AFFILIATES = {
    "Prime Video": {
        "key": "amazon",
        "name": "Prime Video",
        "aliases": ["prime video", "amazon prime", "amazon"],
        "url": os.getenv("AFFILIATE_AMAZON", "") or "https://www.amazon.com/gp/video/offers?tag=dondever2000-20",
        "affiliate_url": os.getenv("AFFILIATE_AMAZON", "") or None,
        "cta": "Ver en Prime Video",
        "countries": ["MX", "US"],
        "is_affiliate": True,  # Amazon Associates tag activo (dondever2000-20)
        "bg": "#00a8e1", "color": "white",
    },
    "ViX": {
        "key": "vix",
        "name": "ViX",
        "aliases": ["vix", "vix premium"],
        "url": os.getenv("AFFILIATE_VIX", "") or "https://www.vix.com/es-es/on-demand",
        "affiliate_url": os.getenv("AFFILIATE_VIX", "") or None,
        "cta": "Ver en ViX",
        "countries": ["MX", "US"],
        "is_affiliate": False,
        "bg": "#6d28d9", "color": "white",
    },
    "ESPN+": {
        "key": "espnplus",
        "name": "ESPN+",
        "aliases": ["espn+", "espn plus"],
        "url": os.getenv("AFFILIATE_ESPNPLUS", "") or "https://plus.espn.com/",
        "affiliate_url": os.getenv("AFFILIATE_ESPNPLUS", "") or None,
        "cta": "Ver en ESPN+",
        "countries": ["US"],
        "is_affiliate": False,
        "bg": "#d00", "color": "white",
    },
    "Peacock": {
        "key": "peacock",
        "name": "Peacock",
        "aliases": ["peacock", "peacock tv"],
        "url": os.getenv("AFFILIATE_PEACOCK", "") or "https://www.peacocktv.com/sports",
        "affiliate_url": os.getenv("AFFILIATE_PEACOCK", "") or None,
        "cta": "Ver en Peacock",
        "countries": ["US"],
        "is_affiliate": False,
        "bg": "#000", "color": "#c8ff00",
    },
    "Paramount+": {
        "key": "paramount",
        "name": "Paramount+",
        "aliases": ["paramount+", "paramount plus"],
        "url": os.getenv("AFFILIATE_PARAMOUNT", "") or "https://www.paramountplus.com/sports/",
        "affiliate_url": os.getenv("AFFILIATE_PARAMOUNT", "") or None,
        "cta": "Ver en Paramount+",
        "countries": ["MX", "US"],
        "is_affiliate": False,
        "bg": "#0064ff", "color": "white",
    },
    "Apple TV+": {
        "key": "appletv",
        "name": "Apple TV+",
        "aliases": ["apple tv+", "apple tv"],
        "url": os.getenv("AFFILIATE_APPLETV", "") or "https://tv.apple.com/",
        "affiliate_url": os.getenv("AFFILIATE_APPLETV", "") or None,
        "cta": "Ver en Apple TV+",
        "countries": ["MX", "US"],
        "is_affiliate": False,
        "bg": "#1d1d1f", "color": "white",
    },
    "MLS Season Pass": {
        "key": "appletv",
        "name": "MLS Season Pass",
        "aliases": ["mls season pass"],
        "url": os.getenv("AFFILIATE_APPLETV", "") or "https://tv.apple.com/channel/tvs.sbd.7000",
        "affiliate_url": os.getenv("AFFILIATE_APPLETV", "") or None,
        "cta": "Ir a MLS Season Pass",
        "countries": ["MX", "US"],
        "is_affiliate": False,
        "bg": "#1d1d1f", "color": "white",
    },
    "MLB.TV": {
        "key": "mlbtv",
        "name": "MLB.TV",
        "aliases": ["mlb.tv", "mlb tv"],
        "url": os.getenv("AFFILIATE_MLBTV", "") or "https://www.mlb.com/tv",
        "affiliate_url": os.getenv("AFFILIATE_MLBTV", "") or None,
        "cta": "Ir a MLB.TV",
        "countries": ["MX", "US", "VE", "DO"],
        "is_affiliate": False,
        "bg": "#002d72", "color": "white",
    },
    "NFL+": {
        "key": "nflplus",
        "name": "NFL+",
        "aliases": ["nfl+", "nfl plus"],
        "url": os.getenv("AFFILIATE_NFLPLUS", "") or "https://www.nfl.com/plus/",
        "affiliate_url": os.getenv("AFFILIATE_NFLPLUS", "") or None,
        "cta": "Ir a NFL+",
        "countries": ["US"],
        "is_affiliate": False,
        "bg": "#013369", "color": "white",
    },
    "Max": {
        "key": "max",
        "name": "Max",
        "aliases": ["max", "hbo max"],
        "url": os.getenv("AFFILIATE_MAX", "") or "https://www.max.com/",
        "affiliate_url": os.getenv("AFFILIATE_MAX", "") or None,
        "cta": "Ver en Max",
        "countries": ["MX", "US"],
        "is_affiliate": False,
        "bg": "#002be7", "color": "white",
    },
}

# Alias for new code — same object, clearer name
PROVIDERS = STREAMING_AFFILIATES

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
    "liga-portugal": ("soccer", "por.1",         "Liga Portugal",    "\u26bd"),
    "eredivisie":    ("soccer", "ned.1",         "Eredivisie",       "\u26bd"),
    "concacaf-cl":   ("soccer", "concacaf.champions", "Concacaf Champions Cup", "\u26bd"),
    "copa-america":  ("soccer", "conmebol.america", "Copa America",  "\u26bd"),
    "world-cup":     ("soccer", "fifa.world",    "Copa del Mundo",   "\u26bd"),
    "liga-colombia":  ("soccer", "col.1",         "Liga BetPlay",     "\u26bd"),
    "liga-argentina": ("soccer", "arg.1",         "Liga Argentina",   "\u26bd"),
    "liga-ecuador":   ("soccer", "ecu.1",         "LigaPro Ecuador",  "\u26bd"),
    "liga-panama":    ("soccer", "pan.1",         "LPF Panam\u00e1",  "\u26bd"),
    "liga-chile":     ("soccer", "chi.1",         "Primera Chile",    "\u26bd"),
    "liga-peru":      ("soccer", "per.1",         "Liga 1 Per\u00fa", "\u26bd"),
    "libertadores":   ("soccer", "conmebol.libertadores", "Copa Libertadores", "\u26bd"),
    "sudamericana":   ("soccer", "conmebol.sudamericana", "Copa Sudamericana", "\u26bd"),
    # Copas dom\u00e9sticas
    "copa-del-rey":   ("soccer", "esp.copa_del_rey",     "Copa del Rey",      "\u26bd"),
    "fa-cup":         ("soccer", "eng.fa",               "FA Cup",            "\u26bd"),
    "carabao-cup":    ("soccer", "eng.league_cup",       "Carabao Cup",       "\u26bd"),
    "dfb-pokal":      ("soccer", "ger.dfb_pokal",        "DFB-Pokal",         "\u26bd"),
    "coppa-italia":   ("soccer", "ita.coppa_italia",     "Coppa Italia",      "\u26bd"),
    "coupe-de-france":("soccer", "fra.coupe_de_france",  "Coupe de France",   "\u26bd"),
    "us-open-cup":    ("soccer", "usa.open",             "US Open Cup",       "\u26bd"),
    "copa-argentina": ("soccer", "arg.copa",             "Copa Argentina",    "\u26bd"),
    # Copas internacionales
    "leagues-cup":    ("soccer", "concacaf.leagues.cup",  "Leagues Cup",       "\u26bd"),
    "club-world-cup": ("soccer", "fifa.cwc",             "Club World Cup",    "\u26bd"),
    # Selecciones
    "euro":           ("soccer", "uefa.euro",            "Eurocopa",          "\u26bd"),
    "gold-cup":       ("soccer", "concacaf.gold",        "Copa Oro",          "\u26bd"),
    "wcq-conmebol":   ("soccer", "fifa.worldq.conmebol", "Eliminatorias CONMEBOL", "\u26bd"),
    "wcq-concacaf":   ("soccer", "fifa.worldq.concacaf", "Eliminatorias CONCACAF", "\u26bd"),
    "nations-league": ("soccer", "uefa.nations",         "UEFA Nations League", "\u26bd"),
    "concacaf-nations":("soccer","concacaf.nations.league","CONCACAF Nations League","\u26bd"),
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


# Deep link sport slugs per casino
_BETSSON_SPORTS = {
    "soccer": "futbol", "baseball": "beisbol", "basketball": "basquetbol",
    "football": "futbol-americano", "hockey": "hockey-sobre-hielo",
    "mma": "artes-marciales-mixtas", "boxing": "boxeo",
    "motorsports": "automovilismo", "tennis": "tenis",
}


def get_affiliate_url(key: str, source: str = "web", sport: str = "") -> str:
    """
    Get affiliate URL with source tracking and optional sport deep link.
    sport: ESPN sport key (soccer, baseball, basketball, etc.)
    """
    aff = AFFILIATES.get(key, {})
    url = aff.get("url", "")
    # Also check streaming affiliates by key
    if not url or url == "#":
        for _ch, saff in STREAMING_AFFILIATES.items():
            if saff["key"] == key:
                url = saff["url"]
                break
    if not url or url == "#":
        return "#"

    # Deep link to sport section when possible
    if sport:
        if key == "betsson" and sport in _BETSSON_SPORTS:
            # Betsson: /apuestas-deportivas/{deporte}
            base = url.split("?")[0].rstrip("/")
            # Betsson affiliate URL is a tracking redirect — append sport as sub2
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}sub1={source}&sub2={sport}"
        elif key in ("jubilee", "vivento"):
            # Jubilee/Vivento: /deportes (SPA, no deeper routes)
            base_domain = "https://www.jubilee.mx" if key == "jubilee" else "https://vivento.mx"
            separator = "&" if "?" in url else "?"
            return f"{base_domain}/deportes?sub1={source}&sub2={sport}"

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
# ── Popular teams for smart search panel ─────────────────
POPULAR_TEAMS = {
    # Liga MX
    "america": {"name": "Club América", "league": "Liga MX"},
    "chivas": {"name": "Chivas Guadalajara", "league": "Liga MX"},
    "cruz-azul": {"name": "Cruz Azul", "league": "Liga MX"},
    "pumas": {"name": "Pumas UNAM", "league": "Liga MX"},
    "tigres": {"name": "Tigres UANL", "league": "Liga MX"},
    "monterrey": {"name": "Rayados de Monterrey", "league": "Liga MX"},
    "toluca": {"name": "Toluca", "league": "Liga MX"},
    "santos": {"name": "Santos Laguna", "league": "Liga MX"},
    "leon": {"name": "Club León", "league": "Liga MX"},
    "pachuca": {"name": "Pachuca", "league": "Liga MX"},
    "atlas": {"name": "Atlas", "league": "Liga MX"},
    "necaxa": {"name": "Necaxa", "league": "Liga MX"},
    "puebla": {"name": "Puebla", "league": "Liga MX"},
    "queretaro": {"name": "Querétaro", "league": "Liga MX"},
    "mazatlan": {"name": "Mazatlán FC", "league": "Liga MX"},
    "tijuana": {"name": "Club Tijuana", "league": "Liga MX"},
    "juarez": {"name": "FC Juárez", "league": "Liga MX"},
    # Premier League
    "liverpool": {"name": "Liverpool", "league": "Premier League"},
    "manchester-city": {"name": "Manchester City", "league": "Premier League"},
    "manchester-united": {"name": "Manchester United", "league": "Premier League"},
    "arsenal": {"name": "Arsenal", "league": "Premier League"},
    "chelsea": {"name": "Chelsea", "league": "Premier League"},
    "tottenham": {"name": "Tottenham", "league": "Premier League"},
    "aston-villa": {"name": "Aston Villa", "league": "Premier League"},
    # La Liga
    "real-madrid": {"name": "Real Madrid", "league": "La Liga"},
    "barcelona": {"name": "FC Barcelona", "league": "La Liga"},
    "atletico-madrid": {"name": "Atlético de Madrid", "league": "La Liga"},
    # Serie A
    "juventus": {"name": "Juventus", "league": "Serie A"},
    "inter-milan": {"name": "Inter de Milán", "league": "Serie A"},
    "ac-milan": {"name": "AC Milan", "league": "Serie A"},
    "napoli": {"name": "Napoli", "league": "Serie A"},
    # Bundesliga
    "bayern": {"name": "Bayern Múnich", "league": "Bundesliga"},
    "borussia-dortmund": {"name": "Borussia Dortmund", "league": "Bundesliga"},
    # Ligue 1
    "psg": {"name": "Paris Saint-Germain", "league": "Ligue 1"},
    # MLS
    "inter-miami": {"name": "Inter Miami", "league": "MLS"},
    "lafc": {"name": "LAFC", "league": "MLS"},
    "la-galaxy": {"name": "LA Galaxy", "league": "MLS"},
    "austin-fc": {"name": "Austin FC", "league": "MLS"},
    "houston-dynamo": {"name": "Houston Dynamo", "league": "MLS"},
    "fc-dallas": {"name": "FC Dallas", "league": "MLS"},
    # NFL
    "cowboys": {"name": "Dallas Cowboys", "league": "NFL"},
    "chiefs": {"name": "Kansas City Chiefs", "league": "NFL"},
    "raiders": {"name": "Las Vegas Raiders", "league": "NFL"},
    "49ers": {"name": "San Francisco 49ers", "league": "NFL"},
    "eagles": {"name": "Philadelphia Eagles", "league": "NFL"},
    "packers": {"name": "Green Bay Packers", "league": "NFL"},
    "steelers": {"name": "Pittsburgh Steelers", "league": "NFL"},
    "dolphins": {"name": "Miami Dolphins", "league": "NFL"},
    "patriots": {"name": "New England Patriots", "league": "NFL"},
    "texans": {"name": "Houston Texans", "league": "NFL"},
    "ravens": {"name": "Baltimore Ravens", "league": "NFL"},
    "bears": {"name": "Chicago Bears", "league": "NFL"},
    "rams": {"name": "Los Angeles Rams", "league": "NFL"},
    "chargers": {"name": "Los Angeles Chargers", "league": "NFL"},
    "broncos": {"name": "Denver Broncos", "league": "NFL"},
    "bills": {"name": "Buffalo Bills", "league": "NFL"},
    "lions": {"name": "Detroit Lions", "league": "NFL"},
    "vikings": {"name": "Minnesota Vikings", "league": "NFL"},
    "bengals": {"name": "Cincinnati Bengals", "league": "NFL"},
    "giants-nfl": {"name": "New York Giants", "league": "NFL"},
    "jets": {"name": "New York Jets", "league": "NFL"},
    "saints": {"name": "New Orleans Saints", "league": "NFL"},
    "seahawks": {"name": "Seattle Seahawks", "league": "NFL"},
    "commanders": {"name": "Washington Commanders", "league": "NFL"},
    "cardinals-nfl": {"name": "Arizona Cardinals", "league": "NFL"},
    "buccaneers": {"name": "Tampa Bay Buccaneers", "league": "NFL"},
    "falcons": {"name": "Atlanta Falcons", "league": "NFL"},
    "panthers-nfl": {"name": "Carolina Panthers", "league": "NFL"},
    "colts": {"name": "Indianapolis Colts", "league": "NFL"},
    "jaguars": {"name": "Jacksonville Jaguars", "league": "NFL"},
    "titans": {"name": "Tennessee Titans", "league": "NFL"},
    # NBA
    "lakers": {"name": "Los Angeles Lakers", "league": "NBA"},
    "warriors": {"name": "Golden State Warriors", "league": "NBA"},
    "celtics": {"name": "Boston Celtics", "league": "NBA"},
    "bulls": {"name": "Chicago Bulls", "league": "NBA"},
    "heat": {"name": "Miami Heat", "league": "NBA"},
    "knicks": {"name": "New York Knicks", "league": "NBA"},
    "nuggets": {"name": "Denver Nuggets", "league": "NBA"},
    "bucks": {"name": "Milwaukee Bucks", "league": "NBA"},
    "mavericks": {"name": "Dallas Mavericks", "league": "NBA"},
    "clippers": {"name": "LA Clippers", "league": "NBA"},
    "suns": {"name": "Phoenix Suns", "league": "NBA"},
    "spurs-nba": {"name": "San Antonio Spurs", "league": "NBA"},
    "76ers": {"name": "Philadelphia 76ers", "league": "NBA"},
    "thunder": {"name": "Oklahoma City Thunder", "league": "NBA"},
    "timberwolves": {"name": "Minnesota Timberwolves", "league": "NBA"},
    "cavaliers": {"name": "Cleveland Cavaliers", "league": "NBA"},
    # MLB
    "dodgers": {"name": "Los Angeles Dodgers", "league": "MLB"},
    "yankees": {"name": "New York Yankees", "league": "MLB"},
    "red-sox": {"name": "Boston Red Sox", "league": "MLB"},
    "astros": {"name": "Houston Astros", "league": "MLB"},
    "mets": {"name": "New York Mets", "league": "MLB"},
    "padres": {"name": "San Diego Padres", "league": "MLB"},
    "angels": {"name": "Los Angeles Angels", "league": "MLB"},
    "athletics": {"name": "Oakland Athletics", "league": "MLB"},
    "blue-jays": {"name": "Toronto Blue Jays", "league": "MLB"},
    "braves": {"name": "Atlanta Braves", "league": "MLB"},
    "brewers": {"name": "Milwaukee Brewers", "league": "MLB"},
    "cardinals": {"name": "St. Louis Cardinals", "league": "MLB"},
    "cubs": {"name": "Chicago Cubs", "league": "MLB"},
    "diamondbacks": {"name": "Arizona Diamondbacks", "league": "MLB"},
    "giants": {"name": "San Francisco Giants", "league": "MLB"},
    "guardians": {"name": "Cleveland Guardians", "league": "MLB"},
    "mariners": {"name": "Seattle Mariners", "league": "MLB"},
    "marlins": {"name": "Miami Marlins", "league": "MLB"},
    "nationals": {"name": "Washington Nationals", "league": "MLB"},
    "orioles": {"name": "Baltimore Orioles", "league": "MLB"},
    "phillies": {"name": "Philadelphia Phillies", "league": "MLB"},
    "pirates": {"name": "Pittsburgh Pirates", "league": "MLB"},
    "rangers": {"name": "Texas Rangers", "league": "MLB"},
    "rays": {"name": "Tampa Bay Rays", "league": "MLB"},
    "reds": {"name": "Cincinnati Reds", "league": "MLB"},
    "rockies": {"name": "Colorado Rockies", "league": "MLB"},
    "royals": {"name": "Kansas City Royals", "league": "MLB"},
    "tigers": {"name": "Detroit Tigers", "league": "MLB"},
    "twins": {"name": "Minnesota Twins", "league": "MLB"},
    "white-sox": {"name": "Chicago White Sox", "league": "MLB"},
    # NHL
    "bruins": {"name": "Boston Bruins", "league": "NHL"},
    "golden-knights": {"name": "Vegas Golden Knights", "league": "NHL"},
    "avalanche": {"name": "Colorado Avalanche", "league": "NHL"},
    "panthers-nhl": {"name": "Florida Panthers", "league": "NHL"},
    "rangers-nhl": {"name": "New York Rangers", "league": "NHL"},
    "maple-leafs": {"name": "Toronto Maple Leafs", "league": "NHL"},
    "oilers": {"name": "Edmonton Oilers", "league": "NHL"},
    "stars": {"name": "Dallas Stars", "league": "NHL"},
    "blackhawks": {"name": "Chicago Blackhawks", "league": "NHL"},
    "penguins": {"name": "Pittsburgh Penguins", "league": "NHL"},
    "capitals": {"name": "Washington Capitals", "league": "NHL"},
    # ── Premier League (remaining) ──
    "bournemouth": {"name": "AFC Bournemouth", "league": "Premier League"},
    "brentford": {"name": "Brentford", "league": "Premier League"},
    "brighton": {"name": "Brighton", "league": "Premier League"},
    "crystal-palace": {"name": "Crystal Palace", "league": "Premier League"},
    "everton": {"name": "Everton", "league": "Premier League"},
    "fulham": {"name": "Fulham", "league": "Premier League"},
    "ipswich": {"name": "Ipswich Town", "league": "Premier League"},
    "leicester": {"name": "Leicester City", "league": "Premier League"},
    "newcastle": {"name": "Newcastle United", "league": "Premier League"},
    "nottingham-forest": {"name": "Nottingham Forest", "league": "Premier League"},
    "southampton": {"name": "Southampton", "league": "Premier League"},
    "west-ham": {"name": "West Ham United", "league": "Premier League"},
    "wolverhampton": {"name": "Wolverhampton", "league": "Premier League"},
    # ── La Liga (remaining) ──
    "alaves": {"name": "Alavés", "league": "La Liga"},
    "athletic-club": {"name": "Athletic Club", "league": "La Liga"},
    "celta-vigo": {"name": "Celta de Vigo", "league": "La Liga"},
    "espanyol": {"name": "Espanyol", "league": "La Liga"},
    "getafe": {"name": "Getafe", "league": "La Liga"},
    "girona": {"name": "Girona", "league": "La Liga"},
    "las-palmas": {"name": "Las Palmas", "league": "La Liga"},
    "leganes": {"name": "Leganés", "league": "La Liga"},
    "mallorca": {"name": "Mallorca", "league": "La Liga"},
    "osasuna": {"name": "Osasuna", "league": "La Liga"},
    "rayo-vallecano": {"name": "Rayo Vallecano", "league": "La Liga"},
    "real-betis": {"name": "Real Betis", "league": "La Liga"},
    "real-sociedad": {"name": "Real Sociedad", "league": "La Liga"},
    "real-valladolid": {"name": "Real Valladolid", "league": "La Liga"},
    "sevilla": {"name": "Sevilla FC", "league": "La Liga"},
    "valencia": {"name": "Valencia", "league": "La Liga"},
    "villarreal": {"name": "Villarreal", "league": "La Liga"},
    # ── Serie A (remaining) ──
    "as-roma": {"name": "AS Roma", "league": "Serie A"},
    "atalanta": {"name": "Atalanta", "league": "Serie A"},
    "bologna": {"name": "Bologna", "league": "Serie A"},
    "cagliari": {"name": "Cagliari", "league": "Serie A"},
    "como": {"name": "Como", "league": "Serie A"},
    "empoli": {"name": "Empoli", "league": "Serie A"},
    "fiorentina": {"name": "Fiorentina", "league": "Serie A"},
    "genoa": {"name": "Genoa", "league": "Serie A"},
    "lazio": {"name": "Lazio", "league": "Serie A"},
    "lecce": {"name": "Lecce", "league": "Serie A"},
    "monza": {"name": "Monza", "league": "Serie A"},
    "parma": {"name": "Parma", "league": "Serie A"},
    "torino": {"name": "Torino", "league": "Serie A"},
    "udinese": {"name": "Udinese", "league": "Serie A"},
    "venezia": {"name": "Venezia", "league": "Serie A"},
    "verona": {"name": "Hellas Verona", "league": "Serie A"},
    # ── Bundesliga (remaining) ──
    "augsburg": {"name": "FC Augsburg", "league": "Bundesliga"},
    "frankfurt": {"name": "Eintracht Frankfurt", "league": "Bundesliga"},
    "freiburg": {"name": "SC Freiburg", "league": "Bundesliga"},
    "heidenheim": {"name": "1. FC Heidenheim", "league": "Bundesliga"},
    "hoffenheim": {"name": "TSG Hoffenheim", "league": "Bundesliga"},
    "koln": {"name": "1. FC Köln", "league": "Bundesliga"},
    "leverkusen": {"name": "Bayer Leverkusen", "league": "Bundesliga"},
    "mainz": {"name": "Mainz 05", "league": "Bundesliga"},
    "gladbach": {"name": "Borussia M'gladbach", "league": "Bundesliga"},
    "rb-leipzig": {"name": "RB Leipzig", "league": "Bundesliga"},
    "st-pauli": {"name": "St. Pauli", "league": "Bundesliga"},
    "stuttgart": {"name": "VfB Stuttgart", "league": "Bundesliga"},
    "union-berlin": {"name": "Union Berlin", "league": "Bundesliga"},
    "werder-bremen": {"name": "Werder Bremen", "league": "Bundesliga"},
    "wolfsburg": {"name": "VfL Wolfsburg", "league": "Bundesliga"},
    # ── Ligue 1 (remaining) ──
    "angers": {"name": "Angers SCO", "league": "Ligue 1"},
    "auxerre": {"name": "AJ Auxerre", "league": "Ligue 1"},
    "brest": {"name": "Stade Brestois", "league": "Ligue 1"},
    "le-havre": {"name": "Le Havre AC", "league": "Ligue 1"},
    "lens": {"name": "RC Lens", "league": "Ligue 1"},
    "lille": {"name": "LOSC Lille", "league": "Ligue 1"},
    "lyon": {"name": "Olympique Lyonnais", "league": "Ligue 1"},
    "marseille": {"name": "Olympique de Marseille", "league": "Ligue 1"},
    "monaco": {"name": "AS Monaco", "league": "Ligue 1"},
    "montpellier": {"name": "Montpellier", "league": "Ligue 1"},
    "nantes": {"name": "FC Nantes", "league": "Ligue 1"},
    "nice": {"name": "OGC Nice", "league": "Ligue 1"},
    "reims": {"name": "Stade de Reims", "league": "Ligue 1"},
    "rennes": {"name": "Stade Rennais", "league": "Ligue 1"},
    "strasbourg": {"name": "RC Strasbourg", "league": "Ligue 1"},
    "toulouse": {"name": "Toulouse FC", "league": "Ligue 1"},
    # ── Liga Portugal ──
    "arouca": {"name": "FC Arouca", "league": "Liga Portugal"},
    "benfica": {"name": "SL Benfica", "league": "Liga Portugal"},
    "braga": {"name": "SC Braga", "league": "Liga Portugal"},
    "casa-pia": {"name": "Casa Pia AC", "league": "Liga Portugal"},
    "estoril": {"name": "Estoril Praia", "league": "Liga Portugal"},
    "estrela": {"name": "Estrela Amadora", "league": "Liga Portugal"},
    "famalicao": {"name": "FC Famalicão", "league": "Liga Portugal"},
    "gil-vicente": {"name": "Gil Vicente", "league": "Liga Portugal"},
    "guimaraes": {"name": "Vitória de Guimarães", "league": "Liga Portugal"},
    "moreirense": {"name": "Moreirense FC", "league": "Liga Portugal"},
    "nacional": {"name": "CD Nacional", "league": "Liga Portugal"},
    "porto": {"name": "FC Porto", "league": "Liga Portugal"},
    "rio-ave": {"name": "Rio Ave FC", "league": "Liga Portugal"},
    "santa-clara": {"name": "Santa Clara", "league": "Liga Portugal"},
    "sporting-cp": {"name": "Sporting CP", "league": "Liga Portugal"},
    # ── Eredivisie ──
    "ajax": {"name": "Ajax Amsterdam", "league": "Eredivisie"},
    "az-alkmaar": {"name": "AZ Alkmaar", "league": "Eredivisie"},
    "fc-twente": {"name": "FC Twente", "league": "Eredivisie"},
    "fc-utrecht": {"name": "FC Utrecht", "league": "Eredivisie"},
    "feyenoord": {"name": "Feyenoord Rotterdam", "league": "Eredivisie"},
    "fortuna-sittard": {"name": "Fortuna Sittard", "league": "Eredivisie"},
    "go-ahead-eagles": {"name": "Go Ahead Eagles", "league": "Eredivisie"},
    "groningen": {"name": "FC Groningen", "league": "Eredivisie"},
    "heerenveen": {"name": "SC Heerenveen", "league": "Eredivisie"},
    "heracles": {"name": "Heracles Almelo", "league": "Eredivisie"},
    "nac-breda": {"name": "NAC Breda", "league": "Eredivisie"},
    "nec": {"name": "NEC Nijmegen", "league": "Eredivisie"},
    "psv": {"name": "PSV Eindhoven", "league": "Eredivisie"},
    "sparta-rotterdam": {"name": "Sparta Rotterdam", "league": "Eredivisie"},
    # ── Liga Colombia ──
    "america-cali": {"name": "América de Cali", "league": "Liga BetPlay"},
    "atletico-nacional": {"name": "Atlético Nacional", "league": "Liga BetPlay"},
    "bucaramanga": {"name": "Atlético Bucaramanga", "league": "Liga BetPlay"},
    "deportes-tolima": {"name": "Deportes Tolima", "league": "Liga BetPlay"},
    "deportivo-cali": {"name": "Deportivo Cali", "league": "Liga BetPlay"},
    "deportivo-pasto": {"name": "Deportivo Pasto", "league": "Liga BetPlay"},
    "deportivo-pereira": {"name": "Deportivo Pereira", "league": "Liga BetPlay"},
    "independiente-medellin": {"name": "Independiente Medellín", "league": "Liga BetPlay"},
    "independiente-santa-fe": {"name": "Independiente Santa Fe", "league": "Liga BetPlay"},
    "junior-barranquilla": {"name": "Junior de Barranquilla", "league": "Liga BetPlay"},
    "millonarios": {"name": "Millonarios FC", "league": "Liga BetPlay"},
    "once-caldas": {"name": "Once Caldas", "league": "Liga BetPlay"},
    # ── Liga Argentina ──
    "argentinos-juniors": {"name": "Argentinos Juniors", "league": "Liga Argentina"},
    "banfield": {"name": "Banfield", "league": "Liga Argentina"},
    "belgrano": {"name": "Belgrano", "league": "Liga Argentina"},
    "boca-juniors": {"name": "Boca Juniors", "league": "Liga Argentina"},
    "defensa-y-justicia": {"name": "Defensa y Justicia", "league": "Liga Argentina"},
    "estudiantes": {"name": "Estudiantes de La Plata", "league": "Liga Argentina"},
    "gimnasia-lp": {"name": "Gimnasia La Plata", "league": "Liga Argentina"},
    "huracan": {"name": "Huracán", "league": "Liga Argentina"},
    "independiente-arg": {"name": "Independiente", "league": "Liga Argentina"},
    "lanus": {"name": "Lanús", "league": "Liga Argentina"},
    "newells": {"name": "Newell's Old Boys", "league": "Liga Argentina"},
    "platense": {"name": "Platense", "league": "Liga Argentina"},
    "racing-club": {"name": "Racing Club", "league": "Liga Argentina"},
    "river-plate": {"name": "River Plate", "league": "Liga Argentina"},
    "rosario-central": {"name": "Rosario Central", "league": "Liga Argentina"},
    "san-lorenzo": {"name": "San Lorenzo", "league": "Liga Argentina"},
    "talleres": {"name": "Talleres de Córdoba", "league": "Liga Argentina"},
    "tigre": {"name": "Tigre", "league": "Liga Argentina"},
    "velez-sarsfield": {"name": "Vélez Sarsfield", "league": "Liga Argentina"},
    # ── LigaPro Ecuador ──
    "aucas": {"name": "SD Aucas", "league": "LigaPro Ecuador"},
    "barcelona-sc": {"name": "Barcelona SC", "league": "LigaPro Ecuador"},
    "delfin": {"name": "Delfín SC", "league": "LigaPro Ecuador"},
    "deportivo-cuenca": {"name": "Deportivo Cuenca", "league": "LigaPro Ecuador"},
    "emelec": {"name": "Emelec", "league": "LigaPro Ecuador"},
    "independiente-del-valle": {"name": "Independiente del Valle", "league": "LigaPro Ecuador"},
    "ldu-quito": {"name": "LDU Quito", "league": "LigaPro Ecuador"},
    "mushuc-runa": {"name": "Mushuc Runa", "league": "LigaPro Ecuador"},
    "orense": {"name": "Orense SC", "league": "LigaPro Ecuador"},
    "tecnico-universitario": {"name": "Técnico Universitario", "league": "LigaPro Ecuador"},
    # ── Primera Chile ──
    "audax-italiano": {"name": "Audax Italiano", "league": "Primera Chile"},
    "cobreloa": {"name": "Cobreloa", "league": "Primera Chile"},
    "cobresal": {"name": "Cobresal", "league": "Primera Chile"},
    "colo-colo": {"name": "Colo-Colo", "league": "Primera Chile"},
    "everton-chile": {"name": "Everton de Viña del Mar", "league": "Primera Chile"},
    "huachipato": {"name": "Huachipato", "league": "Primera Chile"},
    "ohiggins": {"name": "O'Higgins", "league": "Primera Chile"},
    "palestino": {"name": "Palestino", "league": "Primera Chile"},
    "universidad-catolica": {"name": "Universidad Católica", "league": "Primera Chile"},
    "universidad-chile": {"name": "Universidad de Chile", "league": "Primera Chile"},
    # ── Liga 1 Perú ──
    "alianza-lima": {"name": "Alianza Lima", "league": "Liga 1 Perú"},
    "cienciano": {"name": "Cienciano", "league": "Liga 1 Perú"},
    "cusco-fc": {"name": "Cusco FC", "league": "Liga 1 Perú"},
    "melgar": {"name": "FBC Melgar", "league": "Liga 1 Perú"},
    "sport-boys": {"name": "Sport Boys", "league": "Liga 1 Perú"},
    "sport-huancayo": {"name": "Sport Huancayo", "league": "Liga 1 Perú"},
    "sporting-cristal": {"name": "Sporting Cristal", "league": "Liga 1 Perú"},
    "universitario": {"name": "Universitario", "league": "Liga 1 Perú"},
}


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
    "mets": "mets",
    "padres": "padres",
    "angels": "angels",
    "angelinos": "angels",
    "athletics": "athletics",
    "atleticos": "athletics",
    "blue jays": "blue jays",
    "azulejos": "blue jays",
    "braves": "braves",
    "brewers": "brewers",
    "cerveceros": "brewers",
    "cardinals": "cardinals",
    "cardenales": "cardinals",
    "cubs": "cubs",
    "diamondbacks": "diamondbacks",
    "giants": "giants",
    "gigantes": "giants",
    "guardians": "guardians",
    "guardianes": "guardians",
    "mariners": "mariners",
    "marineros": "mariners",
    "marlins": "marlins",
    "nationals": "nationals",
    "nacionales": "nationals",
    "orioles": "orioles",
    "phillies": "phillies",
    "filis": "phillies",
    "pirates": "pirates",
    "piratas": "pirates",
    "rangers": "rangers",
    "rays": "rays",
    "reds": "reds",
    "rojos": "reds",
    "rockies": "rockies",
    "royals": "royals",
    "reales": "royals",
    "tigers": "tigers",
    "tigres detroit": "tigers",
    "twins": "twins",
    "gemelos": "twins",
    "white sox": "white sox",
    "medias blancas": "white sox",
    "astros": "astros",
    "red sox": "red sox",
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
