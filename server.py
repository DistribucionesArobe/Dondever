"""
DondeVer.app — Main FastAPI server
Where to watch sports in Mexico & USA
"""

import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.twiml.messaging_response import MessagingResponse

from config import AFFILIATES, STREAMING_AFFILIATES, LEAGUES, ALL_LEAGUES, APP_URL, TZ_MX, TZ_ET, TEAM_ALIASES, TEAM_SHOP, MELI_AFF_PARAM, TEAM_SHOP_MELI, POPULAR_TEAMS
from db import init_db, persist_games, get_team_history, get_team_upcoming, get_team_channels
import sports_api as _sports_api_mod
from sports_api import (
    get_todays_games, search_games, get_team_stats, get_league_standings,
    fetch_odds, match_odds_to_game, match_full_odds_to_game,
    get_recent_league_results, get_upcoming_league_games, fetch_team_news,
    fetch_meli_product_image, fetch_espn_event_summary,
    generate_nfl_power_rankings, generate_nfl_picks, get_nfl_team_advanced_stats,
    fetch_sportsdb_team_info, compute_sportsdb_standings,
    fetch_league_leaders, fetch_team_form,
    DEFAULT_LEAGUE_CHANNELS,
)
from whatsapp_bot import handle_whatsapp_message
import meta_whatsapp
import email_subscribers
import user_favs
from tiktok_auth import (
    get_tiktok_auth_url, exchange_code_for_token, get_user_info,
    upload_video_to_tiktok, check_publish_status, is_authenticated,
    get_token_info,
)

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dondever")

# ── App ──────────────────────────────────────────────────
app = FastAPI(
    title="DondeVer.app",
    description="Donde ver juegos deportivos en Mexico y USA",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Red de seguridad: ninguna excepción suelta debe ser un 500 ──────────────
#
# Search Console reporta 49 páginas con error de servidor, subiendo desde
# principios de agosto. Los ejemplos están repartidos por TODOS los tipos de
# ruta (/canal/, /equipo/, /juego/, /partido/, /resultado/, /evento/), y al
# recargarlas a mano funcionan. O sea: son intermitentes, no un bug de una
# ruta concreta — algo de arriba (un timeout, un campo que un día no viene)
# revienta y como no había manejador global, salía un 500 pelado.
#
# Un 500 repetido hace que Google termine sacando la página del índice. Un 503
# con Retry-After le dice "esto es temporal, vuelve" y conserva el puesto.
# Además el 503 es la verdad: la página existe, hoy no se pudo armar.
#
# OJO: esto trata el síntoma. La causa hay que buscarla en los logs de Render,
# y por eso el handler registra ruta y tipo de excepción con traza completa —
# la idea es poder arreglar el origen, no taparlo.
@app.exception_handler(Exception)
async def _error_no_previsto(request: Request, exc: Exception):
    logger.exception(
        "500 no previsto en %s %s — %s: %s",
        request.method, request.url.path, type(exc).__name__, exc,
    )
    acepta_html = "text/html" in (request.headers.get("accept") or "")
    if not acepta_html:
        return JSONResponse({"error": "temporalmente no disponible"}, status_code=503,
                            headers={"Retry-After": "120"})
    try:
        r = templates.TemplateResponse(
            request, "404.html", status_code=503,
            context={"message": "Estamos teniendo un problema para armar esta página. "
                                "Vuelve a intentar en un momento."},
        )
    except Exception:  # si hasta la plantilla de error falla, algo muy raro pasa
        r = HTMLResponse("<h1>Vuelve en un momento</h1>", status_code=503)
    r.headers["Retry-After"] = "120"
    return r


# ── Semantic game slugs ─────────────────────────────────
def _slugify(text: str) -> str:
    """Unicode-safe slugify: 'Club América' → 'club-america'."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text)


def _make_game_slug(game: dict) -> str:
    """Build a semantic slug matching title order.
    Soccer/boxing/MMA: home-vs-away.  Other sports: away-vs-home."""
    home = _slugify(game.get("home", {}).get("name", "home"))
    away = _slugify(game.get("away", {}).get("name", "away"))
    sport = game.get("sport", "")
    home_first = sport in ("soccer", "boxing", "mma")
    first = home if home_first else away
    second = away if home_first else home
    # Parse date from ISO string (e.g. '2026-08-11T22:00Z')
    raw_date = game.get("date", "")
    try:
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        date_str = dt.astimezone(TZ_MX).strftime("%Y-%m-%d")
    except Exception:
        date_str = datetime.now(TZ_MX).strftime("%Y-%m-%d")
    return f"{first}-vs-{second}-{date_str}"


def _game_url(game: dict) -> str:
    """Full path for a game: /partido/america-vs-cruz-azul-2026-08-11.
    UFC y F1 tienen página de evento propia (cartelera/sesiones): /evento/{slug}."""
    if game.get("event_slug") and game.get("sport") in ("mma", "racing"):
        return f"/evento/{game['event_slug']}"
    return f"/partido/{_make_game_slug(game)}"


# Register Jinja2 filters
templates.env.filters["game_url"] = _game_url
templates.env.globals["game_url"] = _game_url

# ── hreflang tags for LATAM + Spain geo-targeting ──────────────
HREFLANG_LOCALES = ["es-MX", "es-US", "es-AR", "es-CO", "es-CL", "es-PE", "es-EC", "es-VE", "es-PA", "es-DO", "es-ES"]


def _hreflang_tags(request_url: str) -> str:
    """Generate hreflang link tags for all target countries.

    All tags point to the same URL because DondeVer is a single-language
    site that covers channels for MX, USA, LATAM and Spain.  This tells
    Google the page is relevant in every listed market.
    """
    from markupsafe import Markup
    # Build canonical URL (strip query params, force https)
    from urllib.parse import urlparse
    parsed = urlparse(str(request_url))
    canonical = f"{APP_URL}{parsed.path}"
    if canonical.endswith("/") and canonical != f"{APP_URL}/":
        canonical = canonical.rstrip("/")

    tags = []
    for locale in HREFLANG_LOCALES:
        tags.append(f'<link rel="alternate" hreflang="{locale}" href="{canonical}">')
    tags.append(f'<link rel="alternate" hreflang="es" href="{canonical}">')
    tags.append(f'<link rel="alternate" hreflang="x-default" href="{canonical}">')
    return Markup("\n    ".join(tags))


templates.env.globals["hreflang_tags"] = _hreflang_tags


# ── Free channels & Interest scoring ─────────────────────
# ── Free channels by country ──────────────────────────────────────────
# MX: TV abierta en México (Canal 5, Azteca 7, etc.)
# US: broadcast networks + Spanish-language OTA in the US
FREE_CHANNELS_MX = {
    "canal 5", "azteca 7", "las estrellas", "azteca uno", "azteca deportes",
    "tv azteca", "nu9ve", "canal 5 nu9ve", "canal once", "canal 22",
    "pluto tv",
}
# Prefixes for MX — match "ViX (free)" or "YouTube (official)" etc.
FREE_PREFIXES_MX = {"vix"}

FREE_CHANNELS_US = {
    "nbc", "cbs", "abc", "fox", "the cw",
    "univision", "unimás", "telemundo",
    "pluto tv",
}
FREE_PREFIXES_US = {"vix"}

FREE_CHANNELS_VE = {
    "televen", "rctv", "pluto tv",
    # Señal abierta en Venezuela, y las cuatro transmiten LVBP. Sin esto, el
    # filtro "Gratis hoy" no encontraba un solo juego de béisbol venezolano.
    "venevisión", "venevision", "tves", "canal i", "meridiano tv", "meridiano",
}
FREE_PREFIXES_VE = set()

FREE_CHANNELS_DO = {
    "teleantillas", "coral 39", "digital 15", "vtv canal 32",
    "teleuniverso", "telenord", "cdn deportes",
}
FREE_PREFIXES_DO = set()

FREE_CHANNELS_PA = {
    "tvn panamá", "tvn", "tvmax", "rpc", "pluto tv",
}
FREE_PREFIXES_PA = set()

# España. Lo verificado el 22/09/2026: la ACB emite UN partido por jornada en
# abierto por Teledeporte, y las autonomicas (TV3, TVG, Aragon TV, EITB) pasan
# otro los domingos por la manana, con acuerdo hasta 2027-28. El futbol de
# LaLiga NO se emite en abierto, asi que Movistar, Orange, DAZN y Vodafone se
# quedan fuera de esta lista a proposito.
FREE_CHANNELS_ES = {
    "teledeporte", "tdp", "rtve", "la 1", "tve", "tve 1",
    "tv3", "tvg", "aragón tv", "aragon tv", "eitb", "etb", "etb 1", "etb 2",
}
FREE_PREFIXES_ES = set()

_LEAGUE_TIER = {
    "liga-mx": 30, "nfl": 30, "champions": 30, "copa-del-mundo": 30,
    "premier-league": 25, "la-liga": 25, "nba": 25, "serie-a": 25,
    "bundesliga": 20, "ligue-1": 20, "mlb": 20, "mls": 20,
    "liga-argentina": 20, "copa-libertadores": 20,
    "copa-mx": 15, "liga-expansion": 15, "eredivisie": 15,
    "liga-portuguesa": 15, "fa-cup": 15, "copa-del-rey": 15,
    "leagues-cup": 15, "euro": 30, "concacaf-champions": 15,
}

DERBIES = [
    ({"america", "chivas"}, 25),
    ({"america", "cruz azul"}, 25),
    ({"pumas", "america"}, 20),
    ({"monterrey", "tigres"}, 25),
    ({"real madrid", "barcelona"}, 25),
    ({"real madrid", "atletico"}, 20),
    ({"liverpool", "manchester"}, 20),
    ({"arsenal", "tottenham"}, 20),
    ({"boca", "river"}, 25),
    ({"yankees", "red sox"}, 20),
    ({"dodgers", "giants"}, 15),
    ({"cowboys", "eagles"}, 20),
    ({"packers", "bears"}, 15),
    ({"lakers", "celtics"}, 20),
    ({"inter miami", "la galaxy"}, 15),
    ({"santos", "leon"}, 15),
    ({"atlas", "chivas"}, 20),
    ({"milan", "inter"}, 20),
    ({"man city", "manchester united"}, 20),
]


def _is_free_broadcast(channel_name: str, country: str = "MX") -> bool:
    """Check if a channel is free OTA in the given country."""
    c = channel_name.lower().strip()
    if not c:
        return False
    _FREE_MAP = {
        "US": (FREE_CHANNELS_US, FREE_PREFIXES_US),
        "VE": (FREE_CHANNELS_VE, FREE_PREFIXES_VE),
        "DO": (FREE_CHANNELS_DO, FREE_PREFIXES_DO),
        "PA": (FREE_CHANNELS_PA, FREE_PREFIXES_PA),
        "ES": (FREE_CHANNELS_ES, FREE_PREFIXES_ES),
    }
    channels, prefixes = _FREE_MAP.get(country, (FREE_CHANNELS_MX, FREE_PREFIXES_MX))
    # Exact match
    if c in channels:
        return True
    # Prefix match (e.g. "vix" matches "vix free", "vix premium" should NOT)
    for pfx in prefixes:
        if c.startswith(pfx) and ("premium" not in c and "plus" not in c and "+" not in c):
            return True
    return False


def score_game_interest(game: dict) -> int:
    """Score a game 0-100 for the 'must watch' section."""
    score = 0
    league_slug = game.get("league_slug", "")
    home_name = game.get("home", {}).get("name", "").lower()
    away_name = game.get("away", {}).get("name", "").lower()

    # 1. League tier (0-30)
    score += _LEAGUE_TIER.get(league_slug, 10)

    # 2. Rivalry detection (0-25)
    for teams, bonus in DERBIES:
        team_list = list(teams)
        if ((team_list[0] in home_name or team_list[0] in away_name) and
                (team_list[1] in home_name or team_list[1] in away_name)):
            score += bonus
            break

    # 3. Free TV bonus (0-15)
    broadcasts = game.get("broadcasts", [])
    if any(_is_free_broadcast(b.get("channel", "")) for b in broadcasts):
        score += 15

    # 4. Has odds (0-5)
    if game.get("odds"):
        score += 5

    # 5. Prime time MX (0-10)
    raw_date = game.get("date", "")
    try:
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(TZ_MX)
        hour = dt.hour
        if 18 <= hour <= 22:
            score += 10
        elif 12 <= hour < 18:
            score += 5
    except Exception:
        pass

    # 6. Close game bonus (0-10)
    odds = game.get("odds")
    if odds:
        try:
            home_ml = float(odds.get("home", 0))
            away_ml = float(odds.get("away", 0))
            if home_ml and away_ml:
                # Convert American odds to implied probability
                def _implied(ml):
                    ml = float(ml)
                    if ml > 0:
                        return 100 / (ml + 100)
                    else:
                        return abs(ml) / (abs(ml) + 100)
                p_home = _implied(home_ml)
                p_away = _implied(away_ml)
                fav_prob = max(p_home, p_away)
                if fav_prob < 0.60:
                    score += 10
                elif fav_prob < 0.65:
                    score += 5
        except (ValueError, TypeError):
            pass

    # 7. Live game bonus (0-5)
    if game.get("status", {}).get("state") == "in":
        score += 5

    return min(score, 100)


# ── Google Analytics middleware ──────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware


class GAInjectMiddleware(BaseHTTPMiddleware):
    """Inject Google Analytics 4 and Microsoft Clarity snippets into every HTML response.
    Activated per-tool when env vars are set:
      - GA_MEASUREMENT_ID (format: G-XXXXXXXXXX) for GA4
      - CLARITY_PROJECT_ID (format: lowercase alphanumeric) for Microsoft Clarity
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ga_id = os.getenv("GA_MEASUREMENT_ID", "").strip()
        gads_id = os.getenv("GOOGLE_ADS_ID", "").strip()  # format: AW-XXXXXXXXXXX
        clarity_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
        gtm_id = os.getenv("GTM_CONTAINER_ID", "").strip()
        adsense_id = os.getenv("ADSENSE_PUB_ID", "").strip()  # format: ca-pub-XXXXXXXXXXXXXXXX
        onesignal_id = os.getenv("ONESIGNAL_APP_ID", "").strip()
        monetag_zone = os.getenv("MONETAG_ZONE_ID", "").strip()
        if not ga_id and not clarity_id and not gtm_id and not gads_id and not adsense_id and not onesignal_id and not monetag_zone:
            return response

        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype:
            return response

        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            snippet = ""
            if gtm_id:
                snippet += (
                    f'<!-- Google Tag Manager -->\n'
                    f'<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{"gtm.start":\n'
                    f'new Date().getTime(),event:"gtm.js"}});var f=d.getElementsByTagName(s)[0],\n'
                    f'j=d.createElement(s),dl=l!="dataLayer"?"&l="+l:"";j.async=true;j.src=\n'
                    f'"https://www.googletagmanager.com/gtm.js?id="+i+dl;f.parentNode.insertBefore(j,f);\n'
                    f'}})(window,document,"script","dataLayer","{gtm_id}");</script>\n'
                    f'<!-- End Google Tag Manager -->\n'
                )
            if ga_id:
                snippet += (
                    f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>\n'
                    f'<script>\n'
                    f'  window.dataLayer = window.dataLayer || [];\n'
                    f'  function gtag(){{dataLayer.push(arguments);}}\n'
                    f'  gtag("js", new Date());\n'
                    f'  gtag("config", "{ga_id}", {{ anonymize_ip: true }});\n'
                    + (f'  gtag("config", "{gads_id}");\n' if gads_id else '')
                    + f'</script>\n'
                )
            if clarity_id:
                snippet += (
                    f'<script>\n'
                    f'  (function(c,l,a,r,i,t,y){{\n'
                    f'    c[a]=c[a]||function(){{(c[a].q=c[a].q||[]).push(arguments)}};\n'
                    f'    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;\n'
                    f'    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);\n'
                    f'  }})(window, document, "clarity", "script", "{clarity_id}");\n'
                    f'</script>\n'
                )
            if adsense_id:
                snippet += (
                    f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={adsense_id}"\n'
                    f'     crossorigin="anonymous"></script>\n'
                )
            if onesignal_id:
                snippet += (
                    f'<script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>\n'
                    f'<script>\n'
                    f'  window.OneSignalDeferred = window.OneSignalDeferred || [];\n'
                    f'  OneSignalDeferred.push(async function(OneSignal) {{\n'
                    f'    await OneSignal.init({{ appId: "{onesignal_id}" }});\n'
                    f'  }});\n'
                    f'</script>\n'
                )
            # Monetag SUSPENDED — degradaba UX y afectaba visitas
            # if monetag_zone:
            #     snippet += (
            #         f'<meta name="monetag" content="ce90bfedb944d0ac4754ee93708ed15d">\n'
            #         f'<script src="https://5gvci.com/act/files/tag.min.js?z=11479109" data-cfasync="false" async></script>\n'
            #         f'<script>(function(s){{s.dataset.zone="11481358",s.src="https://nap5k.com/tag.min.js"}})([document.documentElement, document.body].filter(Boolean).pop().appendChild(document.createElement("script")))</script>\n'
            #     )
            snippet = snippet.encode("utf-8")

            if b"</head>" in body:
                body = body.replace(b"</head>", snippet + b"</head>", 1)

            # Logo fijo arriba al hacer scroll en TODAS las páginas interiores (la portada ya lo tiene)
            _p = request.url.path
            _is_en = _p.startswith("/en/")
            if _p != "/" and not _p.startswith("/widget") and b"</body>" in body and b'id="sticky-brand"' not in body:
                sticky_css = (
                    '<style>.dv-sticky{position:fixed;top:0;left:0;right:0;z-index:150;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);'
                    'border-bottom:1px solid rgba(255,255,255,.08);padding:.4rem 1rem;display:flex;align-items:center;justify-content:space-between;'
                    'transform:translateY(-100%);transition:transform .3s ease;box-shadow:0 2px 12px rgba(0,0,0,.3)}'
                    '.dv-sticky.visible{transform:translateY(0)}.dv-sticky img{height:30px;width:auto;display:block}'
                    '.dv-sticky a.h{color:#10b981;font-weight:800;font-size:.8rem;text-decoration:none}</style>'
                ).encode("utf-8")
                if b"</head>" in body:
                    body = body.replace(b"</head>", sticky_css + b"</head>", 1)
                sticky_html = (
                    '<div class="dv-sticky" id="dv-sticky"><a href="/" aria-label="DondeVer inicio"><img src="/static/logo-dondever-sm.png" alt="DondeVer.app"></a>'
                    '<span style="display:flex;align-items:center;gap:0.7rem;"><a class="h dv-sticky-app" href="/app" style="display:none;">&#128241; App</a>'
                    f'<a class="h" href="/">{"Home" if _is_en else "Inicio"} &rarr;</a></span></div>'
                    '<script>(function(){try{var m=/Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);var st=window.matchMedia("(display-mode: standalone)").matches||navigator.standalone===true;'
                    'if(m&&!st){var a=document.querySelector(".dv-sticky-app");if(a)a.style.display="inline";}}catch(e){}})();</script>'
                    '<script>(function(){var b=document.getElementById("dv-sticky");if(!b)return;var h=document.querySelector(".header")||document.querySelector("header");'
                    'var t=h?(h.offsetTop+h.offsetHeight):80;var on=false;function f(){var y=window.scrollY||window.pageYOffset;'
                    'if(y>t&&!on){b.classList.add("visible");on=true}else if(y<=t&&on){b.classList.remove("visible");on=false}}'
                    'window.addEventListener("scroll",f,{passive:true});f();})();</script>'
                ).encode("utf-8")
                body = body.replace(b"</body>", sticky_html + b"</body>", 1)

            # Botón de contacto en el pie de TODAS las páginas (publicidad, ideas, opiniones).
            # En las páginas en inglés (/en/) va en inglés: si el usuario de EE.UU. ve texto en
            # español se va, que es justo el problema que estas páginas vienen a resolver.
            if b"</footer>" in body and not request.url.path.startswith(("/widget", "/contacto")):
                _label = ('&#128172; Contact us &middot; advertising, ideas and feedback' if _is_en
                          else '&#128172; Cont&aacute;ctanos &middot; publicidad, ideas y opiniones')
                contact_btn = (
                    '<p style="margin:0.6rem 0 0.2rem;"><a href="/contacto" style="display:inline-block;padding:0.45rem 0.95rem;'
                    'background:#10b981;color:#fff;border-radius:999px;font-weight:800;font-size:0.78rem;text-decoration:none;">'
                    f'{_label}</a></p>'
                ).encode("utf-8")
                body = body.replace(b"</footer>", contact_btn + b"</footer>", 1)

            # GTM also needs a <noscript> iframe right after <body>
            if gtm_id:
                gtm_noscript = (
                    f'\n<!-- Google Tag Manager (noscript) -->\n'
                    f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}"\n'
                    f'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
                    f'<!-- End Google Tag Manager (noscript) -->\n'
                ).encode("utf-8")
                # Match both <body> and <body ...> variants
                import re as _re
                body = _re.sub(
                    rb"(<body\b[^>]*>)",
                    lambda m: m.group(1) + gtm_noscript,
                    body, count=1, flags=_re.IGNORECASE,
                )

            from starlette.responses import Response
            # Strip content-length so Starlette recalculates
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=ctype,
            )
        except Exception as e:
            logger = logging.getLogger("dondever")
            logger.warning(f"GA inject failed: {e}")
            return response


app.add_middleware(GAInjectMiddleware)


# ── Server-side HTML cache ────────────────────────────────
# GSC crawl stats: 4,020 ms avg response to Googlebot. Team/league/game pages
# do 8-12 upstream calls each. Cache the final rendered HTML (post-GA-inject)
# for a few minutes. Stored zlib-compressed (~15 KB/page → ~12 MB for 800 pages).
import zlib as _zlib
from cachetools import TTLCache as _TTLCache

from time import monotonic as _mono

_HTML_CACHE = _TTLCache(maxsize=800, ttl=300)

# La portada es la página más pesada (arma todas las ligas del día + momios) y
# era la única que no pasaba por el caché: /equipo/, /liga/, /partido/ y los hubs
# sí, pero "/" no estaba en ninguna de las dos listas, así que se renderizaba de
# cero en cada visita. Va en su propio caché porque necesita un TTL mucho más
# corto que los 300 s del resto: muestra marcadores y "hoy", y a los 5 minutos
# ya mentiría. Con 60 s el HTML puede quedar un minuto viejo, pero los
# marcadores en vivo se refrescan aparte desde /api/live-scores.
_HOME_CACHE = _TTLCache(maxsize=64, ttl=60)
_HTML_CACHE_PREFIXES = ("/equipo/", "/liga/", "/partido/", "/evento/", "/canal/", "/donde-ver/",
                        "/guia/", "/resultado/", "/donde-ver-en-", "/equipos", "/widget/")
_HTML_CACHE_HUBS = {"/playoffs-mlb", "/gratis-hoy", "/pronosticos-hoy", "/futbol-hoy",
                    "/futbol-americano-hoy", "/basquetbol-hoy", "/beisbol-hoy", "/hockey-hoy",
                    "/streaming", "/casinos"}


class HTMLCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        is_home = path == "/"
        cacheable = (
            request.method == "GET"
            and (is_home or path.startswith(_HTML_CACHE_PREFIXES) or path in _HTML_CACHE_HUBS
                 or path.startswith("/sitemap"))
            and "nocache" not in request.query_params
            and "token" not in request.query_params
        )
        if not cacheable:
            return await call_next(request)

        store = _HOME_CACHE if is_home else _HTML_CACHE
        key = path + ("?" + str(request.query_params) if request.query_params else "")
        hit = store.get(key)
        if hit is not None and len(hit) == 3 and hit[2] is not None and _mono() > hit[2]:
            # Venció antes que su TTL normal: es una ficha de un partido en vivo.
            # Se reporto que la portada iba en la alta de la 9a y la ficha en la
            # baja de la 8a. La portada se cachea 60 s y la ficha 300 s, asi que
            # la ficha podia ir cuatro minutos atras. En deportes eso destruye la
            # confianza en todo el sitio, no solo en esa pagina.
            hit = None
        if hit is not None:
            body, ctype = hit[0], hit[1]
            # La portada conserva su propio Cache-Control (90 s) para no
            # contradecir lo que ya manda la ruta.
            _cc = ("public, max-age=90, s-maxage=90" if is_home
                   else "public, max-age=120, s-maxage=300")
            if len(hit) == 3 and hit[2] is not None:
                _cc = "public, max-age=15, s-maxage=15"
            _h = {"X-Cache": "HIT", "Cache-Control": _cc, "Vary": "Accept-Encoding"}
            if path.startswith("/widget/"):
                _h["Content-Security-Policy"] = "frame-ancestors *"  # embebible en otros sitios
            return Response(content=_zlib.decompress(body), status_code=200, media_type=ctype, headers=_h)

        response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        if response.status_code != 200 or not ("text/html" in ctype or "xml" in ctype):
            return response
        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            # La ruta puede pedir un vencimiento más corto que el TTL del caché
            # (cabecera x-dv-ttl). Lo usan las fichas de partidos en vivo o a
            # punto de empezar: ahí 300 s de HTML viejo es una mentira.
            _ttl_corto = None
            try:
                _v = int(response.headers.get("x-dv-ttl") or 0)
                if _v > 0:
                    _ttl_corto = _mono() + _v
            except Exception:
                pass
            store[key] = (_zlib.compress(body, 6), ctype, _ttl_corto)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            headers.pop("x-dv-ttl", None)
            headers["X-Cache"] = "MISS"
            if _ttl_corto is not None:
                headers["cache-control"] = "public, max-age=15, s-maxage=15"
            # dict(response.headers) trae las claves en minúscula, así que un
            # setdefault("Cache-Control", …) no encontraba el valor que ya había
            # puesto la ruta y añadía un SEGUNDO Cache-Control. La portada salía
            # con "max-age=90, s-maxage=90, public, max-age=120, s-maxage=300".
            headers.setdefault("cache-control", "public, max-age=120, s-maxage=300")
            return Response(content=body, status_code=200, headers=headers, media_type=ctype)
        except Exception as e:
            logging.getLogger("dondever").warning(f"HTML cache failed: {e}")
            return response


app.add_middleware(HTMLCacheMiddleware)  # outermost: caches the final (GA-injected) HTML


# ── Template helpers ─────────────────────────────────────
def format_mx_time(iso_date: str) -> str:
    """Convert ISO date to Mexico City time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


_DAYS_ES_FMT = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_MONTHS_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
              "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


_DAYS_ES_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_MONTHS_ES_SHORT = ["ene", "feb", "mar", "abr", "may", "jun",
                    "jul", "ago", "sep", "oct", "nov", "dic"]


def format_date_es(dt) -> str:
    """'Lunes 10 de Agosto, 2026' — fully Spanish date display."""
    return f"{_DAYS_ES_FMT[dt.weekday()]} {dt.day} de {_MONTHS_ES[dt.month]}, {dt.year}"


def format_mx_day_time(iso_date: str) -> str:
    """'Domingo 26 · 7:20 PM' in Mexico City time (Spanish, DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return f"{_DAYS_ES_FMT[mx.weekday()]} {mx.day} · {mx.strftime('%I:%M %p').lstrip('0')}"
    except Exception:
        return ""


def format_mx_date_short(iso_date: str) -> str:
    """'vie 18 sep' en hora de México.

    Las plantillas imprimían `date[:10]` sobre el ISO en UTC, así que un partido
    del 18 a las 20:00 (México) salía como '2026-09-19'. Todo lo que muestre una
    fecha al usuario tiene que pasar por aquí, igual en todas las ligas.
    """
    try:
        dt = datetime.fromisoformat(str(iso_date).replace("Z", "+00:00")).astimezone(TZ_MX)
        return f"{_DAYS_ES_SHORT[dt.weekday()]} {dt.day} {_MONTHS_ES_SHORT[dt.month - 1]}"
    except Exception:
        return str(iso_date)[:10] if iso_date else ""


def mx_when_label(iso_date: str) -> str:
    """'hoy', 'mañana', 'ayer' o 'el vie 18 sep' — según la fecha en México.

    Evita el título 'Dónde ver hoy' en partidos que no son hoy.
    """
    try:
        dt = datetime.fromisoformat(str(iso_date).replace("Z", "+00:00")).astimezone(TZ_MX)
        delta = (dt.date() - datetime.now(TZ_MX).date()).days
        if delta == 0:
            return "hoy"
        if delta == 1:
            return "mañana"
        if delta == -1:
            return "ayer"
        return f"el {format_mx_date_short(iso_date)}"
    except Exception:
        return ""


def format_us_time(iso_date: str) -> str:
    """Convert ISO date to US Eastern time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        et = dt.astimezone(TZ_ET)
        return et.strftime("%I:%M %p ET")
    except Exception:
        return ""


templates.env.globals["format_mx_time"] = format_mx_time
templates.env.globals["format_mx_day_time"] = format_mx_day_time
templates.env.globals["format_mx_date_short"] = format_mx_date_short
templates.env.globals["mx_when_label"] = mx_when_label
templates.env.globals["format_us_time"] = format_us_time
# Hora en cualquier zona, para renderizar el bloque "Horario por país" en el
# servidor. Antes México y EE.UU. salían del servidor pero Argentina y Colombia
# eran un "—" que sólo rellenaba el JavaScript: Googlebot y cualquiera con el
# JS lento veían un guion donde debía ir la hora.
templates.env.globals["format_tz_time"] = lambda iso, tz: _fmt_local(iso, tz)

# Etiquetas y orden de los países para el bloque "Dónde verlo en tu país".
# El orden es el del tráfico real (Search Console, 28 días), no alfabético:
# México, Venezuela y Panamá son los tres primeros por volumen de clics.
COUNTRY_LABELS = {
    "MX": ("México", "🇲🇽"), "VE": ("Venezuela", "🇻🇪"), "PA": ("Panamá", "🇵🇦"),
    "US": ("Estados Unidos", "🇺🇸"), "DO": ("Dominicana", "🇩🇴"),
    "CO": ("Colombia", "🇨🇴"), "ES": ("España", "🇪🇸"), "PE": ("Perú", "🇵🇪"),
    "EC": ("Ecuador", "🇪🇨"), "PR": ("Puerto Rico", "🇵🇷"),
    "AR": ("Argentina", "🇦🇷"), "CL": ("Chile", "🇨🇱"),
    "*": ("Internacional", "🌎"),
}
COUNTRY_ORDER = ["MX", "VE", "PA", "US", "DO", "CO", "ES", "PE", "EC", "PR", "AR", "CL", "*"]
templates.env.globals["country_labels"] = COUNTRY_LABELS
templates.env.globals["country_order"] = COUNTRY_ORDER
templates.env.globals["affiliates"] = AFFILIATES
templates.env.globals["streaming_aff"] = STREAMING_AFFILIATES
templates.env.globals["app_url"] = APP_URL
templates.env.globals["now"] = lambda: datetime.now(TZ_MX)
templates.env.globals["team_shop"] = TEAM_SHOP
templates.env.globals["meli_aff"] = MELI_AFF_PARAM
templates.env.globals["team_shop_meli"] = TEAM_SHOP_MELI
templates.env.globals["popular_teams"] = POPULAR_TEAMS
# JSON list for onboarding JS: [{slug, name, league}, ...]
templates.env.globals["popular_teams_json"] = json.dumps(
    [{"slug": s, "name": i["name"], "league": i["league"]} for s, i in POPULAR_TEAMS.items()],
    ensure_ascii=False
)


# ── Free / OTA channels ────────────────────────────────
# FREE_CHANNELS and _is_free_broadcast defined above (near line 116)


def _team_name_to_slug(team_name: str) -> str | None:
    """Reverse lookup: ESPN team display name → DondeVer slug."""
    if not team_name:
        return None
    name_lower = team_name.lower()
    # Exact match first
    for slug, info in POPULAR_TEAMS.items():
        if info["name"].lower() == name_lower:
            return slug
    # Slug appears in team name (e.g. "yankees" in "new york yankees")
    for slug, info in POPULAR_TEAMS.items():
        slug_clean = slug.replace("-", " ")
        if slug_clean in name_lower:
            return slug
    return None


# ── Service Workers (must be at root scope) ──────────────
from pathlib import Path as _Path

@app.get("/OneSignalSDKWorker.js")
async def onesignal_service_worker():
    sw_path = _Path(__file__).parent / "OneSignalSDKWorker.js"
    return Response(
        content=sw_path.read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )

# Monetag service worker SUSPENDED
# @app.get("/sw.js")
# async def monetag_service_worker():
#     sw_path = _Path(__file__).parent / "sw.js"
#     return Response(
#         content=sw_path.read_text(),
#         media_type="application/javascript",
#         headers={"Service-Worker-Allowed": "/"},
#     )

# ── PWA ──────────────────────────────────────────────────────
@app.get("/manifest.json")
async def pwa_manifest():
    manifest_path = _Path(__file__).parent / "static" / "manifest.json"
    return Response(
        content=manifest_path.read_text(),
        media_type="application/manifest+json",
    )

@app.get("/pwa-sw.js")
async def pwa_service_worker():
    sw_path = _Path(__file__).parent / "pwa-sw.js"
    return Response(
        content=sw_path.read_text(),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


# ── Web Routes ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    date: Optional[str] = Query(None, description="Date YYYYMMDD"),
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
):
    """Main page — today's games."""
    # Auto-trigger WhatsApp broadcast if server woke after 9 AM MX
    asyncio.ensure_future(_maybe_catchup_broadcast())

    games = await get_todays_games(
        date_str=date, sport_filter=sport, league_filter=league
    )

    # Group games by sport
    sports_grouped = {}
    for game in games:
        sport_key = game["league_slug"].split("-")[0] if "-" in game["league_slug"] else game["league_slug"]
        # Use league_name for grouping
        league_name = game["league_name"]
        if league_name not in sports_grouped:
            sports_grouped[league_name] = {
                "emoji": game["emoji"],
                "slug": game["league_slug"],
                "games": [],
            }
        sports_grouped[league_name]["games"].append(game)

    # ── Fetch odds for homepage games ────────────────────
    # Collect unique league slugs from upcoming AND live games (live betting)
    odds_leagues = set()
    for g in games:
        if g["status"]["state"] in ("pre", "in"):
            odds_leagues.add(g.get("league_slug", ""))
    # Fetch priority leagues first (MLB, NFL, NBA, etc.), then secondary
    from sports_api import ODDS_PRIORITY_LEAGUES
    sorted_leagues = sorted(
        odds_leagues,
        key=lambda ls: ODDS_PRIORITY_LEAGUES.index(ls) if ls in ODDS_PRIORITY_LEAGUES else 99
    )
    odds_by_league: dict[str, list] = {}
    # Fetch all odds in parallel instead of sequentially
    async def _fetch_one_odds(ls):
        try:
            ol = await fetch_odds(ls)
            return (ls, ol) if ol else None
        except Exception:
            return None
    _odds_results = await asyncio.gather(*[_fetch_one_odds(ls) for ls in sorted_leagues])
    for r in _odds_results:
        if r:
            odds_by_league[r[0]] = r[1]
    # Attach odds to each game (pre + live) + quick prediction from odds
    for g in games:
        ls = g.get("league_slug", "")
        if g["status"]["state"] in ("pre", "in") and ls in odds_by_league:
            g["odds"] = match_odds_to_game(g, odds_by_league[ls])
        else:
            g["odds"] = None
        g["prediction"] = _quick_prediction_from_odds(g) if g.get("odds") else None
        g["preview"] = generate_match_preview(g)

    # ── Interest scoring for all games ─────────────────
    for g in games:
        g["interest_score"] = score_game_interest(g)

    # ── Must-watch "Lo imperdible" — send 15, JS picks best 5 per user ──
    # Se eligen por interés, pero se MUESTRAN por hora: una lista que va 1:30 PM,
    # 6:05 PM y luego vuelve a 11:00 AM se lee como un error. Y 15 partidos no son
    # una curaduría: 8 sí.
    must_watch = sorted(
        [g for g in games if g["status"]["state"] == "pre" and g["interest_score"] >= 20],
        key=lambda g: g["interest_score"],
        reverse=True,
    )[:8]
    must_watch.sort(key=lambda g: g.get("date", ""))

    # ── Free games (TV abierta / streaming gratis) ─────
    free_games = [
        g for g in games
        if g["status"]["state"] == "pre"
        and any(_is_free_broadcast(b.get("channel", "")) for b in g.get("broadcasts", []))
    ]
    for g in free_games:
        g["free_channels"] = [
            b["channel"] for b in g.get("broadcasts", [])
            if _is_free_broadcast(b.get("channel", ""))
        ]

    # ── Live games section ──────────────────────────────
    live_games = [g for g in games if g["status"]["state"] == "in"]

    # ── IDs already shown in must-watch / free / live ──
    _priority_ids = set()
    for g in must_watch:
        _priority_ids.add(g["id"])
    for g in free_games:
        _priority_ids.add(g["id"])
    for g in live_games:
        _priority_ids.add(g["id"])

    # Pick del dia — best single game for the card
    pick_game = must_watch[0] if must_watch else (live_games[0] if live_games else None)

    # ── Dedup: collect IDs already shown in priority sections ──
    shown_ids = set(_priority_ids)

    # Available sports for filter
    sport_types = sorted(set(v[0] for v in LEAGUES.values()))

    today = datetime.now(TZ_MX)

    # Date navigation
    if date:
        try:
            viewing_date = datetime.strptime(date, "%Y%m%d").replace(tzinfo=TZ_MX)
        except ValueError:
            viewing_date = today
    else:
        viewing_date = today

    prev_date = (viewing_date - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (viewing_date + timedelta(days=1)).strftime("%Y%m%d")

    # ── Sport counts for hero cards ─────────────────────
    sport_counts: dict[str, int] = {"all": len(games)}
    for g in games:
        ls = g.get("league_slug", "")
        s = LEAGUES.get(ls, ("other",))[0] if ls in LEAGUES else "other"
        sport_counts[s] = sport_counts.get(s, 0) + 1

    # ── Compact standings for home page ────────────────
    home_standings: dict = {}
    _STANDINGS_LEAGUES = [
        ("liga-mx", "soccer", "mex.1", "Liga MX", "⚽"),
        ("premier-league", "soccer", "eng.1", "Premier League", "⚽"),
        ("mlb", "baseball", "mlb", "MLB", "⚾"),
        ("nba", "basketball", "nba", "NBA", "🏀"),
    ]
    active_sports = set(sport_counts.keys()) - {"all"}
    standings_tasks = []
    standings_keys = []
    for ls, sp, lid, nm, em in _STANDINGS_LEAGUES:
        if sp in active_sports or "soccer" in active_sports:
            standings_tasks.append(get_league_standings(sp, lid, limit=5))
            standings_keys.append((ls, sp, nm, em))
    if standings_tasks:
        standings_results = await asyncio.gather(*standings_tasks, return_exceptions=True)
        for (ls, sp, nm, em), result in zip(standings_keys, standings_results):
            if isinstance(result, Exception) or not result:
                continue
            home_standings[ls] = {"name": nm, "emoji": em, "sport": sp, "entries": result}

    # Is this a non-today date page? (noindex for historical pages)
    is_historical = bool(date) and date != today.strftime("%Y%m%d")

    # El strip de eventos grandes (UFC PPV / GP / boxeo) se quitó de la portada para poner un anuncio;
    # los eventos siguen en /liga/ufc, /liga/boxeo, /liga/f1 y el buscador. Se deja vacío para no costar llamadas.
    big_events: list = []

    response = templates.TemplateResponse(
        request,
        "index.html",
        context={
            "games": games,
            "sports_grouped": sports_grouped,
            "sport_types": sport_types,
            "leagues": LEAGUES,
            "current_sport": sport,
            "current_league": league,
            "current_date": date or today.strftime("%Y%m%d"),
            "today_display": format_date_es(viewing_date),
            "prev_date": prev_date,
            "next_date": next_date,
            "total_games": len(games),
            "pick_game": pick_game,
            "live_games": live_games,
            "must_watch": must_watch,
            "big_events": big_events,
            "fmt_event_when": lambda iso: _fmt_local(iso, "America/Mexico_City", True),
            "free_games": free_games,
            "sport_counts": sport_counts,
            "home_standings": home_standings,
            "is_historical": is_historical,
            "shown_ids": shown_ids,
            "default_league_channels": DEFAULT_LEAGUE_CHANNELS,
        },
    )
    # Short cache to prevent stale dates — 90s browser, 90s CDN
    response.headers["Cache-Control"] = "public, max-age=90, s-maxage=90"
    response.headers["Vary"] = "Accept-Encoding"
    return response


# ── Eventos: UFC / F1 / Boxeo (/evento/{slug}) ────────────
_EVENT_META = {
    "ufc": {"org": "UFC", "league_slug": "ufc", "sport": "Mixed Martial Arts", "kicker": "UFC"},
    "f1": {"org": "Fórmula 1", "league_slug": "f1", "sport": "Motorsport", "kicker": "Fórmula 1"},
    "boxing": {"org": "Boxeo", "league_slug": "boxeo", "sport": "Boxing", "kicker": "Boxeo"},
    "motogp": {"org": "MotoGP", "league_slug": "motogp", "sport": "Motorcycle Racing", "kicker": "MotoGP"},
    "nascar": {"org": "NASCAR", "league_slug": "nascar", "sport": "Motorsport", "kicker": "NASCAR Cup Series"},
    "indycar": {"org": "IndyCar", "league_slug": "indycar", "sport": "Motorsport", "kicker": "IndyCar"},
    # Tenis y golf son TORNEOS, no enfrentamientos: ESPN devuelve "US Open" con
    # la lista de competidores vacía, y tratarlos como partido generaba páginas
    # /partido/tbd-vs-tbd-…. Van por /evento/ como UFC, F1 y boxeo.
    "atp": {"org": "ATP", "league_slug": "atp", "sport": "Tennis", "kicker": "ATP"},
    "wta": {"org": "WTA", "league_slug": "wta", "sport": "Tennis", "kicker": "WTA"},
    "pga": {"org": "PGA Tour", "league_slug": "pga", "sport": "Golf", "kicker": "PGA Tour"},
}
_RACE_KINDS = ("f1", "motogp", "nascar", "indycar")
_TOURNAMENT_KINDS = ("atp", "wta", "pga")
from events_api import EVENT_CHANNELS as EVENT_CHANNELS_BY_KIND
# Texto de "dónde ver gratis" por categoría de motor (FAQ)
_RACE_FREE_FAQ = {
    "f1": ("¿Se puede ver la F1 gratis en México?",
           "Canal 5 transmite en TV abierta carreras selectas los domingos. El resto de sesiones van por Fox Sports MX y F1 TV Pro."),
    "motogp": ("¿Dónde ver MotoGP en México?",
               "MotoGP se transmite en México y Latinoamérica por ESPN y Disney+ (todas las sesiones: prácticas, clasificación, sprint y carrera). En España por DAZN."),
    "nascar": ("¿Dónde ver NASCAR en México?",
               "La NASCAR Cup Series se ve en México y Latinoamérica por Fox Sports. En Estados Unidos la temporada se reparte entre FOX, Prime Video, TNT/HBO Max y NBC/USA Network."),
    "indycar": ("¿Dónde ver IndyCar y a Pato O'Ward en México?",
                "IndyCar se transmite en México y Latinoamérica por ESPN y Disney+. En Estados Unidos por FOX."),
}
_EVENT_COUNTRIES = [
    ("MX", "México", "🇲🇽", "America/Mexico_City"),
    ("US", "EE.UU. (Este)", "🇺🇸", "America/New_York"),
    ("VE", "Venezuela", "🇻🇪", "America/Caracas"),
    ("CO", "Colombia", "🇨🇴", "America/Bogota"),
    ("AR", "Argentina", "🇦🇷", "America/Argentina/Buenos_Aires"),
    ("ES", "España", "🇪🇸", "Europe/Madrid"),
]
_DAYS_ES_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_MONTHS_ES_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_local(iso: str, tz_name: str, with_day: bool = False) -> str:
    from zoneinfo import ZoneInfo as _ZI
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_ZI(tz_name))
        t = dt.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
        return f"{_DAYS_ES_SHORT[dt.weekday()]} {dt.day} {_MONTHS_ES_SHORT[dt.month - 1]} · {t}" if with_day else t
    except Exception:
        return ""


def _event_seo(ev: dict) -> dict:
    """Title/H1/answer that resolve the query: dónde ver + a qué hora."""
    meta = _EVENT_META[ev["kind"]]
    t_mx = _fmt_local(ev["date"], "America/Mexico_City")
    day_mx = _fmt_local(ev["date"], "America/Mexico_City", with_day=True).split(" · ")[0]
    ch = ev["channels"].get("MX") or []
    ch1 = ch[0].replace(" (por confirmar)", "") if ch else ""
    ch_txt = " y ".join(ch[:2]) if ch else "canal por confirmar"
    approx = "" if ev.get("time_confirmed", True) else " (aprox.)"
    if ev["kind"] in _RACE_KINDS:
        race = next((s for s in ev["sessions"] if s["key"].lower() == "race"), None)
        qual = next((s for s in ev["sessions"] if s["key"].lower().startswith("qual") or s["key"] == "Q2"), None)
        sprint = next((s for s in ev["sessions"] if s["key"].lower() == "sprint"), None)
        r_t = _fmt_local(race["date"], "America/Mexico_City", True) if race else _fmt_local(ev["date"], "America/Mexico_City", True)
        q_t = _fmt_local(qual["date"], "America/Mexico_City", True) if qual else ""
        s_t = _fmt_local(sprint["date"], "America/Mexico_City", True) if sprint else ""
        if ev["kind"] == "f1":
            title = f"{ev['short_name']}: hora de carrera y qualy en México, dónde ver | DondeVer"
            h1 = f"Dónde ver el {ev['name']}: horarios en México y canal"
        elif ev["kind"] == "motogp":
            title = f"{ev['short_name']}: hora de carrera y sprint en México, dónde ver | DondeVer"
            h1 = f"Dónde ver el {ev['name']}: horarios en México y canal"
        elif ev["kind"] == "nascar":
            title = f"{ev['short_name']} {day_mx}: hora en México y dónde ver{' (Playoffs)' if ev.get('playoffs') else ''} | DondeVer"
            h1 = f"Dónde ver {ev['name']}: hora en México y canal"
        else:
            title = f"{ev['short_name']} {day_mx}: hora en México y dónde ver IndyCar | DondeVer"
            h1 = f"Dónde ver {ev['name']}: hora en México y canal"
        if ev["status"] == "post":
            title = f"{ev['short_name']}: resultados y próxima carrera | DondeVer"
            answer = (f"El <b>{ev['name']}</b> se corrió el <b>{r_t.split(' · ')[0]}</b> en {ev['venue'] or ev['city']}. "
                      f"Abajo están las próximas carreras de {_EVENT_META[ev['kind']]['org']}.")
        else:
            extra = ""
            if s_t:
                extra += f", la <b>sprint</b> el <b>{s_t}</b>"
            if q_t:
                extra += f" y la <b>clasificación</b> el <b>{q_t}</b>"
            answer = (f"La <b>carrera</b> del {ev['short_name']} es el <b>{r_t} hora de México</b>{extra}. "
                      f"En México se ve por <b>{ch_txt}</b>.")
        desc = (f"{ev['name']}: carrera {r_t} MX{(', sprint ' + s_t + ' MX') if s_t else ''}{(', clasificación ' + q_t + ' MX') if q_t else ''}. "
                f"Dónde ver en México ({ch_txt}), Venezuela, Colombia, Argentina y España. "
                f"{'Horarios de todas las sesiones en ' + (ev['venue'] or ev['city']) + '.' if len(ev['sessions']) > 1 else ''}").strip()
    else:
        main = next((f for f in ev["fights"] if f["is_main"]), None)
        fighters = " vs ".join(x["name"] for x in main["fighters"]) if main else ev["name"]
        pre = ""
        if ev["kind"] == "ufc" and len(ev["segments"]) > 1:
            pre = f" Las preliminares empiezan a las <b>{_fmt_local(ev['segments'][0]['date'], 'America/Mexico_City')}</b>."
        title = f"Dónde ver {ev['short_name']}: {day_mx} {t_mx}{approx} MX en {ch1} | Cartelera" if ev["status"] != "post" \
            else f"{ev['short_name']}: resultados y cartelera completa | DondeVer"
        h1 = f"Dónde ver {ev['name']}: hora en México, canal y cartelera"
        if ev["status"] == "post":
            winner = next((x["name"] for x in (main["fighters"] if main else []) if x.get("winner")), "")
            answer = (f"<b>{fighters}</b> se realizó el <b>{day_mx}</b> en {ev['venue'] or ev['city']}."
                      f"{(' Ganó <b>' + winner + '</b>.') if winner else ''} Cartelera y resultados abajo.")
        else:
            answer = (f"La <b>pelea estelar {fighters}</b> es el <b>{day_mx}</b> a las <b>{t_mx}{approx} hora de México</b>."
                      f"{pre} En México se ve por <b>{ch_txt}</b>.")
        desc = (f"{ev['name']} — {day_mx} {t_mx} hora de México por {ch_txt}. Cartelera completa, hora por país "
                f"(Venezuela, Colombia, Argentina, España) y dónde ver en vivo.")
    return {"title": title, "h1": h1, "answer": answer, "desc": desc}


def _event_faq(ev: dict) -> list:
    meta = _EVENT_META[ev["kind"]]
    ch = ev["channels"].get("MX") or []
    t_mx = _fmt_local(ev["date"], "America/Mexico_City", True)
    t_ve = _fmt_local(ev["date"], "America/Caracas", True)
    t_es = _fmt_local(ev["date"], "Europe/Madrid", True)
    faq = [(f"¿A qué hora es {ev['short_name']} en México?",
            f"{'La carrera' if ev['kind'] in _RACE_KINDS else 'La pelea estelar'} es el {t_mx} hora del centro de México."
            + ("" if ev.get("time_confirmed", True) else " El horario es estimado y se confirma la semana del evento.")),
           (f"¿En qué canal pasan {ev['short_name']} en México?",
            f"En México se transmite por {', '.join(ch) if ch else 'canal por confirmar'}."),
           (f"¿A qué hora es en Venezuela y España?",
            f"En Venezuela: {t_ve}. En España: {t_es}.")]
    if ev["kind"] == "ufc":
        faq.append(("¿Dónde ver las preliminares de UFC?",
                    "Las preliminares y la cartelera estelar se transmiten completas por Paramount+ en México y Latinoamérica."))
    if ev["kind"] in _RACE_FREE_FAQ:
        faq.append(_RACE_FREE_FAQ[ev["kind"]])
    if ev["kind"] == "boxing":
        faq.append(("¿La pelea es gratis en TV abierta?",
                    "Depende del evento: TV Azteca y Canal 5 transmiten peleas selectas de boxeadores mexicanos. Si no está confirmado, la opción segura es DAZN."))
    return faq


@app.get("/evento/{slug}", response_class=HTMLResponse)
async def evento_page(request: Request, slug: str):
    """Página de evento: UFC (cartelera), F1 (sesiones del GP), Boxeo (curado)."""
    from events_api import get_event_by_slug, fetch_events
    ev = await get_event_by_slug(slug)
    if not ev:
        return templates.TemplateResponse(request, "404.html", status_code=404,
                                          context={"message": "Evento no encontrado."})
    meta = _EVENT_META[ev["kind"]]
    seo = _event_seo(ev)
    tz_rows = [{"code": c, "name": n, "flag": f, "time": _fmt_local(ev["date"], tz),
                "day": _fmt_local(ev["date"], tz, True).split(" · ")[0]} for c, n, f, tz in _EVENT_COUNTRIES]
    countries = [{"code": c, "name": n, "flag": f, "time": _fmt_local(ev["date"], tz, True),
                  "channels": ev["channels"].get(c) or ev["channels"].get("MX", [])}
                 for c, n, f, tz in _EVENT_COUNTRIES]
    related = [e for e in await fetch_events(ev["kind"], days_back=0, days_ahead=90)
               if e["slug"] != ev["slug"] and not e.get("is_minor")][:6]
    competitor_names = []
    for f in ev["fights"][:2]:
        competitor_names += [x["name"] for x in f["fighters"] if x.get("name")]
    return templates.TemplateResponse(request, "evento.html", {
        "ev": ev, "seo_title": seo["title"], "seo_h1": seo["h1"], "seo_desc": seo["desc"], "answer": seo["answer"],
        "org_name": meta["org"], "league_slug": meta["league_slug"], "sport_name": meta["sport"], "kicker": meta["kicker"],
        "date_long_mx": _fmt_local(ev["date"], "America/Mexico_City", True),
        "tz_rows": tz_rows, "countries": countries, "faq": _event_faq(ev), "related": related,
        "competitor_names": competitor_names, "is_race": ev["kind"] in _RACE_KINDS,
        "motor_links": [(k, _EVENT_META[k]["org"], _EVENT_META[k]["league_slug"]) for k in _RACE_KINDS if k != ev["kind"]],
        "fmt_day": lambda iso: _fmt_local(iso, "America/Mexico_City", True).split(" · ")[0],
        "fmt_time": lambda iso: _fmt_local(iso, "America/Mexico_City"),
        "year": datetime.now(TZ_MX).year,
    })


# ── Widget embebible (backlinks): /widget/equipo/{slug}, /widget/hoy, generador /widget ──
def _widget_row(g: dict) -> dict:
    sport = g.get("sport", "")
    home_left = sport in ("soccer", "boxing", "mma")
    first = g["home"] if home_left else g["away"]
    second = g["away"] if home_left else g["home"]
    state = g["status"]["state"]
    if state == "in":
        when, sub, live = f"{first.get('score', '')}-{second.get('score', '')}", "EN VIVO", True
    elif state == "post":
        when, sub, live = f"{first.get('score', '')}-{second.get('score', '')}", "Final", False
    else:
        when, sub, live = format_mx_time(g["date"]).lstrip("0"), format_mx_day_time(g["date"]).split(" · ")[0], False
    chans = [b.get("channel", "") for b in (g.get("broadcasts") or [])[:2]]
    return {"title": f"{first['name']} vs {second['name']}", "league": g.get("league_name", ""),
            "channels": ", ".join(c for c in chans if c), "when": when, "sub": sub, "live": live,
            "logo": first.get("logo", ""), "url": f"/partido/{_make_game_slug(g)}"}


@app.get("/widget/equipo/{team_slug}", response_class=HTMLResponse)
async def widget_team(request: Request, team_slug: str):
    """Widget para incrustar en blogs/foros: próximos partidos del equipo con hora MX y canal."""
    info = POPULAR_TEAMS.get(team_slug)
    if not info:
        return HTMLResponse("<p style='font-family:sans-serif;font-size:13px'>Equipo no encontrado.</p>", status_code=404)
    data = await api_mis_equipos(teams=team_slug)
    payload = json.loads(data.body) if hasattr(data, "body") else data
    rows = []
    for g in payload.get("today", []):
        soccer_like = g.get("emoji") in ("⚽", "🥊")
        first, second = (g["home_name"], g["away_name"]) if soccer_like else (g["away_name"], g["home_name"])
        sh, sa = g.get("score_home", ""), g.get("score_away", "")
        score = f"{sh}-{sa}" if soccer_like else f"{sa}-{sh}"
        rows.append({"title": f"{first} vs {second}", "league": g.get("league_name", ""), "channels": g.get("channels", ""),
                     "when": score if g["state"] != "pre" else g.get("time_mx", "").lstrip("0"),
                     "sub": {"in": "EN VIVO", "post": "Final"}.get(g["state"], "Hoy"), "live": g["state"] == "in",
                     "logo": g.get("home_logo", ""), "url": g.get("url") or f"/equipo/{team_slug}"})
    for g in payload.get("upcoming", []):
        if len(rows) >= 4:
            break
        rows.append({"title": f"{g['home_name']} vs {g['away_name']}", "league": g.get("league_name", ""),
                     "channels": g.get("channels", ""), "when": format_mx_time(g["date"]).lstrip("0") if g.get("date") else "",
                     "sub": format_mx_day_time(g["date"]).split(" · ")[0] if g.get("date") else "", "live": False,
                     "logo": g.get("home_logo", ""), "url": f"/equipo/{team_slug}"})
    resp = templates.TemplateResponse(request, "widget.html", {
        "title": f"Dónde ver {info['name']}", "rows": rows[:4], "more_url": f"/equipo/{team_slug}",
    })
    resp.headers["Cache-Control"] = "public, max-age=300"
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    return resp


@app.get("/widget/hoy", response_class=HTMLResponse)
async def widget_hoy(request: Request, liga: str = ""):
    """Widget 'Dónde ver hoy' (o de una liga): 5 partidos con hora MX y canal."""
    if liga and liga not in ALL_LEAGUES:
        liga = ""
    games = await get_todays_games(league_filter=liga or None)
    games = [g for g in games if g["home"]["name"] != "TBD"]
    for g in games:
        g["interest_score"] = score_game_interest(g)
    live = [g for g in games if g["status"]["state"] == "in"]
    pre = sorted([g for g in games if g["status"]["state"] == "pre"], key=lambda g: -g["interest_score"])
    rows = [_widget_row(g) for g in (live[:2] + pre)[:5]]
    lname = ALL_LEAGUES[liga][2] if liga else ""
    resp = templates.TemplateResponse(request, "widget.html", {
        "title": f"{lname} hoy" if lname else "Dónde ver hoy", "rows": rows,
        "more_url": f"/liga/{liga}" if lname else "/",
    })
    resp.headers["Cache-Control"] = "public, max-age=120"
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    return resp


@app.get("/widget", response_class=HTMLResponse)
async def widget_generator(request: Request):
    """Página para blogs/foros/peñas: elige equipo o liga y copia el código del widget."""
    teams = sorted(({"slug": s, "name": i["name"], "league": i.get("league", "")} for s, i in POPULAR_TEAMS.items()),
                   key=lambda t: (t["league"], t["name"]))
    leagues = [(slug, v[2]) for slug, v in LEAGUES.items()]
    return templates.TemplateResponse(request, "widget_generator.html", {
        "teams": teams, "leagues": leagues, "year": datetime.now(TZ_MX).year,
    })


@app.get("/playoffs-mlb", response_class=HTMLResponse)
async def playoffs_mlb(request: Request):
    """Hub estacional: 'dónde ver playoffs MLB' — octubre es el pico de Dodgers/Yankees (GSC)."""
    now = datetime.now(TZ_MX)
    year = now.year
    all_games = await get_todays_games()
    games = [g for g in all_games if g.get("league_slug") == "mlb"
             and g.get("home", {}).get("name") != "TBD" and g.get("away", {}).get("name") != "TBD"]
    is_postseason = any(g.get("season_type") == 3 or g.get("series_note") for g in games)
    games.sort(key=lambda g: ({"in": 0, "pre": 1, "post": 2}.get(g["status"]["state"], 3), g.get("date", "")))

    upcoming = []
    try:
        upcoming = await get_upcoming_league_games("baseball", "mlb", days=7, limit=16)
    except Exception:
        pass

    mlb_ch = LEAGUE_CHANNELS_BY_COUNTRY.get("MLB", {})
    countries = []
    for cs in ("mexico", "venezuela", "panama", "republica-dominicana", "estados-unidos", "colombia"):
        meta = TEAM_COUNTRY_SEO.get(cs, {})
        chs = mlb_ch.get(cs) or _DEFAULT_LATAM_CHANNELS
        countries.append({"slug": cs, "name": meta.get("name", cs), "flag": meta.get("flag", ""), "channels": chs})

    live = [g for g in games if g["status"]["state"] == "in"]
    pre = [g for g in games if g["status"]["state"] == "pre"]
    _sn = lambda n: _short_team_name(n, "MLB")
    if is_postseason and live:
        g = live[0]
        seo_title = f"Playoffs MLB {year} EN VIVO: {_sn(g['away']['name'])} vs {_sn(g['home']['name'])} | Dónde ver"
    elif is_postseason and pre:
        g = pre[0]
        t = format_mx_time(g.get("date", "")).lstrip("0")
        ch = (g.get("broadcasts") or [{}])[0].get("channel", "")
        seo_title = f"Playoffs MLB {year} hoy: {_sn(g['away']['name'])} vs {_sn(g['home']['name'])} {t} MX{(' en ' + ch) if ch else ''}"
    else:
        seo_title = f"Dónde ver los playoffs MLB {year}: canales, horarios y calendario | DondeVer"
    seo_h1 = f"Dónde ver los playoffs de MLB {year} en vivo"
    seo_desc = (f"Postemporada MLB {year}: dónde ver cada juego en México (ESPN MX, Disney+, Fox Sports MX), "
                f"Venezuela, Panamá y República Dominicana. Serie de Comodines, Series Divisionales, "
                f"Series de Campeonato y Serie Mundial con horarios locales.")
    hero_text = (f"Todos los juegos de la postemporada {year} con canal y horario de México. "
                 f"Serie de Comodines, Divisionales, Series de Campeonato y Serie Mundial.")
    top_teams = [("dodgers", "Dodgers"), ("yankees", "Yankees"), ("phillies", "Phillies"), ("padres", "Padres"),
                 ("mets", "Mets"), ("red-sox", "Red Sox"), ("braves", "Braves"), ("astros", "Astros"),
                 ("cubs", "Cubs"), ("orioles", "Orioles"), ("blue-jays", "Blue Jays"), ("brewers", "Brewers")]
    faq = [
        (f"¿Cuándo empiezan los playoffs de MLB {year}?",
         "La postemporada arranca a inicios de octubre con la Serie de Comodines y termina con la Serie Mundial a finales de octubre. Aquí verás cada juego con su horario de México el mismo día."),
        ("¿En qué canal pasan los playoffs de MLB en México?",
         "ESPN MX y Disney+ transmiten la postemporada en México; Fox Sports MX tiene juegos selectos. MLB.TV ofrece todos los juegos en streaming."),
        ("¿Dónde ver la Serie Mundial en Venezuela, Panamá o República Dominicana?",
         "Por ESPN Latinoamérica y Disney+. En Panamá también TVMax cubre béisbol y en República Dominicana CDN Deportes."),
        ("¿A qué hora juegan los Dodgers y los Yankees en playoffs?",
         "Consulta la página de cada equipo: el título muestra la hora de México y el canal del juego de hoy, y puedes elegir tu país para ver la hora local."),
    ]

    return templates.TemplateResponse(request, "playoffs_mlb.html", {
        "seo_title": seo_title, "seo_h1": seo_h1, "seo_desc": seo_desc, "hero_text": hero_text,
        "year": year, "games": games, "upcoming": upcoming, "is_postseason": is_postseason,
        "countries": countries, "top_teams": top_teams, "faq": faq,
        "today_display": format_date_es(now),
        "format_mx_time": format_mx_time, "format_mx_day_time": format_mx_day_time,
    })


@app.get("/gratis-hoy", response_class=HTMLResponse)
async def free_today(request: Request):
    """Landing page: free sports events today — TV abierta & free streaming."""
    games = await get_todays_games()

    # Attach odds to upcoming/live games
    odds_leagues = set()
    for g in games:
        if g["status"]["state"] in ("pre", "in"):
            odds_leagues.add(g.get("league_slug", ""))
    from sports_api import ODDS_PRIORITY_LEAGUES
    sorted_leagues = sorted(
        odds_leagues,
        key=lambda ls: ODDS_PRIORITY_LEAGUES.index(ls) if ls in ODDS_PRIORITY_LEAGUES else 99,
    )
    odds_by_league: dict[str, list] = {}

    async def _fetch_one_odds_gratis(ls):
        try:
            ol = await fetch_odds(ls)
            return (ls, ol) if ol else None
        except Exception:
            return None

    _odds_results = await asyncio.gather(
        *[_fetch_one_odds_gratis(ls) for ls in sorted_leagues]
    )
    for r in _odds_results:
        if r:
            odds_by_league[r[0]] = r[1]
    for g in games:
        ls = g.get("league_slug", "")
        if g["status"]["state"] in ("pre", "in") and ls in odds_by_league:
            g["odds"] = match_odds_to_game(g, odds_by_league[ls])
        else:
            g["odds"] = None
        g["prediction"] = _quick_prediction_from_odds(g) if g.get("odds") else None

    # Filter free games
    free_games = [
        g for g in games
        if g["status"]["state"] in ("pre", "in")
        and any(_is_free_broadcast(b.get("channel", "")) for b in g.get("broadcasts", []))
    ]
    for g in free_games:
        g["free_channels"] = [
            b["channel"] for b in g.get("broadcasts", [])
            if _is_free_broadcast(b.get("channel", ""))
        ]
        g["interest_score"] = score_game_interest(g)

    # Group by sport
    free_by_sport: dict[str, list] = {}
    for g in free_games:
        s = g.get("sport", "other")
        free_by_sport.setdefault(s, []).append(g)

    today = datetime.now(TZ_MX)

    return templates.TemplateResponse(
        request,
        "gratis_hoy.html",
        context={
            "free_games": free_games,
            "free_by_sport": free_by_sport,
            "total_free": len(free_games),
            "today_display": format_date_es(today),
        },
    )


@app.get("/canales-tv", response_class=HTMLResponse)
async def canales_ads_page(request: Request):
    """Clean landing page for Google Ads — no betting/affiliate content."""
    games = await get_todays_games()

    sports_grouped = {}
    for game in games:
        league_name = game["league_name"]
        if league_name not in sports_grouped:
            sports_grouped[league_name] = {
                "emoji": game["emoji"],
                "games": [],
            }
        sports_grouped[league_name]["games"].append(game)

    return templates.TemplateResponse(
        request,
        "canales.html",
        context={
            "games": games,
            "sports_grouped": sports_grouped,
            "total_games": len(games),
        },
    )


def _slugify_channel(name: str) -> str:
    """Turn a channel name into a URL-safe slug: 'ESPN MX' -> 'espn-mx', 'Disney+' -> 'disney', 'MLB.TV' -> 'mlbtv'.
    ÚNICA regla de slug de canal: la usan /canales, /canal/ y el filtro Jinja `channel_slug` de las cards."""
    import re, unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.replace("+", " plus")            # ESPN+ → espn-plus (distinto de ESPN); Disney+ → disney-plus
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


_CHANNEL_SLUG_LEGACY = {
    "disney": "disney-plus", "paramount": "paramount-plus", "appletv": "apple-tv-plus", "apple-tv": "apple-tv-plus",
    "mlb.tv": "mlbtv", "mlb-tv": "mlbtv", "nba.tv": "nbatv", "nba-tv": "nbatv", "nfl.tv": "nfltv", "nhl.tv": "nhltv",
    "espnplus": "espn-plus", "star-plus": "disney-plus", "star": "disney-plus",
}


def _canon_channel_slug(slug: str) -> str:
    """Normaliza variantes viejas de URL a la canónica de _slugify_channel."""
    s = (slug or "").lower().strip().strip("-")
    return _CHANNEL_SLUG_LEGACY.get(s, s.replace(".", ""))


templates.env.filters["channel_slug"] = _slugify_channel


@app.get("/canales", response_class=HTMLResponse)
async def canales_index(request: Request):
    """Channel index — list all channels broadcasting today with game counts."""
    games = await get_todays_games()

    # Build channel -> games mapping
    channels: dict[str, dict] = {}
    for g in games:
        for b in g.get("broadcasts", []):
            ch = b["channel"]
            slug = _slugify_channel(ch)
            if slug not in channels:
                saff = STREAMING_AFFILIATES.get(ch)
                channels[slug] = {
                    "name": ch,
                    "slug": slug,
                    "games": [],
                    "is_streaming": saff is not None,
                    "bg": saff["bg"] if saff else "#6b7280",
                    "color": saff["color"] if saff else "white",
                }
            channels[slug]["games"].append(g)

    # Sort: most games first
    sorted_channels = sorted(channels.values(), key=lambda c: len(c["games"]), reverse=True)

    return templates.TemplateResponse(
        request,
        "canales_index.html",
        context={
            "channels": sorted_channels,
            "total_channels": len(sorted_channels),
            "total_games": len(games),
        },
    )


# Ruta de streaming oficial por canal, solo donde está verificada (ver /streaming, sept 2026).
# No inventamos números de cable ni listas de operadores: eso cambia por región y proveedor.
CHANNEL_STREAM_ROUTE = {
    "tudn": ("ViX Premium", "La programación de TUDN se ve en ViX Premium con suscripción."),
    "espn": ("Disney+", "En México, los canales ESPN se ven dentro de Disney+ (planes con ESPN)."),
    "espn2": ("Disney+", "En México, los canales ESPN se ven dentro de Disney+ (planes con ESPN)."),
    "espn-mx": ("Disney+", "En México, los canales ESPN se ven dentro de Disney+ (planes con ESPN)."),
    "espn-deportes": ("Disney+", "En México, los canales ESPN se ven dentro de Disney+ (planes con ESPN)."),
    "tnt-sports": ("HBO Max", "TNT Sports comparte derechos con HBO Max en México."),
    "canal-5": ("ViX", "Canal 5 es TV abierta; su señal también está en ViX."),
    "fox-sports-mx": ("Fox One", "Fox One es la app de streaming de Fox Sports México."),
}

_CHANNEL_TYPE_HOWTO = {
    "broadcast": "Es televisión abierta: se ve gratis con antena o en la señal digital de tu zona, sin suscripción.",
    "cable": "Es un canal de televisión de paga: necesitas un plan de cable o satélite que lo incluya, o la app oficial del canal iniciando sesión con tu proveedor.",
    "streaming": "Es una plataforma de streaming: se ve por internet con una suscripción, sin necesidad de cable.",
}


async def _channel_upcoming(channel_slug: str, days: int = 3, limit: int = 10) -> list:
    """Próximos partidos en este canal (siguientes días). Da contenido a la página
    los días sin transmisión, que antes quedaba vacía y en noindex."""
    out = []
    base = datetime.now(TZ_MX)
    for d in range(1, days + 1):
        if len(out) >= limit:
            break
        ds = (base + timedelta(days=d)).strftime("%Y%m%d")
        try:
            for g in await get_todays_games(date_str=ds):
                if any(_slugify_channel(b.get("channel", "")) == channel_slug for b in g.get("broadcasts", [])):
                    out.append(g)
                    if len(out) >= limit:
                        break
        except Exception:
            continue
    return out


@app.get("/canal/{channel_slug}", response_class=HTMLResponse)
async def canal_page(request: Request, channel_slug: str, date: Optional[str] = Query(None)):
    """Per-channel page — all games airing on a specific channel today."""
    # Una sola URL por canal: variantes viejas (disney-plus, mlb.tv, paramount-plus…) → 301 a la canónica
    _canon = _canon_channel_slug(channel_slug)
    if _canon != channel_slug:
        return RedirectResponse(url=f"/canal/{_canon}" + (f"?date={date}" if date else ""), status_code=301)
    games = await get_todays_games(date_str=date)

    today = datetime.now(TZ_MX)
    if date:
        try:
            viewing_date = datetime.strptime(date, "%Y%m%d").replace(tzinfo=TZ_MX)
        except ValueError:
            viewing_date = today
    else:
        viewing_date = today
    prev_date = (viewing_date - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (viewing_date + timedelta(days=1)).strftime("%Y%m%d")

    # Find channel name from slug, and filter games
    channel_name = None
    channel_games = []
    for g in games:
        for b in g.get("broadcasts", []):
            if _slugify_channel(b["channel"]) == channel_slug:
                if not channel_name:
                    channel_name = b["channel"]
                if g not in channel_games:
                    channel_games.append(g)

    # ── Soft-404 guard (GSC: kfmb-8.1-(cbs), kmsp-tv, kunp-16, wxix-fox19, fanduel-sn-west…)
    # Regional US affiliates / RSNs are useless for our LATAM audience → real 404.
    # Unknown channel with no games today (not curated, no affiliate) → 404 too.
    _regional_ok = {"win-sports", "willow-tv", "wwe-network", "wapa-deportes", "mlb.tv", "nba.tv", "nfl.tv", "nhl.tv"}
    _regional_us = channel_slug not in _regional_ok and (
        re.match(r"^[kw][a-z]{2,3}(-|\d|$)", channel_slug) or re.search(
            r"(fanduel-sn|bally|nbc-sports-(?!mx)|root-sports|marquee|yes-network|^sny$|^nesn$|^masn|"
            r"^sportsnet|^[a-z]+\.tv$|fox\d{1,2}$|cbs\)|\(cbs|\(nbc|\(abc|\(fox)", channel_slug))
    _curated_page = CHANNEL_PAGES.get(channel_slug) or next((v for k, v in CHANNEL_PAGES.items() if _canon_channel_slug(k) == channel_slug), None)
    _is_curated = bool(_curated_page) or (channel_name and channel_name in STREAMING_AFFILIATES) \
        or any(_slugify_channel(n) == channel_slug for n in STREAMING_AFFILIATES)
    if _regional_us and not _is_curated:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Canal regional no disponible en Latinoamérica."}
        )
    if not channel_name and not _is_curated:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Canal no encontrado."}
        )
    if not channel_name:
        channel_name = (_curated_page or {}).get("name") or next((n for n in STREAMING_AFFILIATES if _slugify_channel(n) == channel_slug), None) \
            or channel_slug.replace("-", " ").title()

    # Check if this is a streaming platform with affiliate
    saff = STREAMING_AFFILIATES.get(channel_name)

    # Group by league
    sports_grouped = {}
    for g in channel_games:
        ln = g["league_name"]
        if ln not in sports_grouped:
            sports_grouped[ln] = {"emoji": g["emoji"], "games": []}
        sports_grouped[ln]["games"].append(g)

    # Live / upcoming counts
    live_count = sum(1 for g in channel_games if g["status"]["state"] == "in")
    upcoming_count = sum(1 for g in channel_games if g["status"]["state"] == "pre")

    # ── Contenido permanente: cómo verlo, qué transmite y próximos días ──
    from config import CHANNEL_ALIASES as _CH_ALIAS
    _cinfo = _curated_page or _CH_ALIAS.get(channel_name) or {}
    ch_type = _cinfo.get("type", "")
    ch_country = _cinfo.get("country", "")
    ch_desc = _cinfo.get("desc", "")
    howto = _CHANNEL_TYPE_HOWTO.get(ch_type, "")
    stream_route = CHANNEL_STREAM_ROUTE.get(channel_slug)
    next_games = [] if date else await _channel_upcoming(channel_slug)
    # Ligas que realmente se transmiten en este canal (de nuestros propios datos, no inventadas)
    leagues_here = []
    for g in channel_games + next_games:
        ln = g.get("league_name", "")
        if ln and ln not in leagues_here:
            leagues_here.append(ln)

    return templates.TemplateResponse(
        request,
        "canal.html",
        context={
            "channel_name": channel_name,
            "channel_slug": channel_slug,
            "channel_games": channel_games,
            "sports_grouped": sports_grouped,
            "total_games": len(channel_games),
            "live_count": live_count,
            "upcoming_count": upcoming_count,
            "saff": saff,
            "ch_type": ch_type,
            "ch_country": ch_country,
            "ch_desc": ch_desc,
            "howto": howto,
            "stream_route": stream_route,
            "next_games": next_games,
            "leagues_here": leagues_here[:8],
            "current_date": date or today.strftime("%Y%m%d"),
            "today_display": format_date_es(viewing_date),
            "prev_date": prev_date,
            "next_date": next_date,
            # ?date= sigue siendo duplicado delgado → noindex. La página sin partidos HOY ya no:
            # ahora lleva cómo verlo, qué transmite y los próximos días, así que es indexable.
            "noindex": bool(date) or (len(channel_games) == 0 and len(next_games) == 0),
        },
    )


@app.get("/partido/{slug}", response_class=HTMLResponse)
async def game_semantic(request: Request, slug: str):
    """Semantic game URL: /partido/america-vs-cruz-azul-2026-08-11."""
    # Parse slug: everything before last 10 chars is teams, last 10 is date
    match = re.match(r"^(.+)-vs-(.+)-(\d{4}-\d{2}-\d{2})$", slug)
    if not match:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "URL de partido no válida."}
        )

    slug_part_1 = match.group(1)
    slug_part_2 = match.group(2)
    date_str = match.group(3).replace("-", "")  # YYYYMMDD for API

    # Search across the target date and adjacent days
    game = None
    for delta in [0, 1, -1]:
        try:
            dt = datetime.strptime(date_str, "%Y%m%d") + timedelta(days=delta)
            try_date = dt.strftime("%Y%m%d")
        except ValueError:
            continue
        all_games = await get_todays_games(date_str=try_date)
        for g in all_games:
            g_slug = _make_game_slug(g)
            if g_slug == slug:
                game = g
                break
            # Fallback: match team slugs in either order (handles old URLs + tz edge cases)
            g_home = _slugify(g.get("home", {}).get("name", ""))
            g_away = _slugify(g.get("away", {}).get("name", ""))
            if (g_home == slug_part_1 and g_away == slug_part_2) or \
               (g_away == slug_part_1 and g_home == slug_part_2):
                game = g
                break
        if game:
            break

    # Try DB as last resort
    if not game:
        try:
            from db import get_team_history, get_team_upcoming
            from sqlalchemy import text as sa_text
            from db import async_session
            async with async_session() as session:
                result = await session.execute(
                    sa_text("""
                        SELECT id FROM games
                        WHERE game_date = :gd
                        ORDER BY date_utc DESC LIMIT 20
                    """),
                    {"gd": date_str}
                )
                for row in result.mappings().all():
                    # We found a game_id, redirect to /juego/ which can handle it
                    return RedirectResponse(url=f"/juego/{row['id']}", status_code=302)
        except Exception:
            pass

    if not game:
        return templates.TemplateResponse(
            request, "404.html", status_code=410,
            context={"message": "Este partido ya terminó. Ve los juegos de hoy en la home."}
        )

    # Canonical: ensure the URL matches the expected slug
    expected_slug = _make_game_slug(game)
    if slug != expected_slug:
        return RedirectResponse(url=f"/partido/{expected_slug}", status_code=301)

    # ── Reuse game_detail logic ──
    event_id = game["id"]

    # ── Parallel fetch: odds, stats, summary, recent, upcoming ──
    home_slug = _team_name_to_slug(game["home"]["name"])
    away_slug = _team_name_to_slug(game["away"]["name"])
    sport = game.get("sport", "")
    league_slug = game.get("league_slug", "")

    from config import ALL_LEAGUES
    league_info = ALL_LEAGUES.get(league_slug)
    espn_league = league_info[1] if isinstance(league_info, tuple) else league_slug

    async def _fetch_game_odds():
        if game["status"]["state"] not in ("pre", "in"):
            return None
        try:
            odds_list = await fetch_odds(league_slug)
            return match_full_odds_to_game(game, odds_list)
        except Exception as e:
            logger.warning(f"Odds fetch failed for game {event_id}: {e}")
            return None

    async def _fetch_home_stats():
        try:
            return await get_team_stats(home_slug) if home_slug else {}
        except Exception:
            return {}

    async def _fetch_away_stats():
        try:
            return await get_team_stats(away_slug) if away_slug else {}
        except Exception:
            return {}

    async def _fetch_summary():
        try:
            if sport and espn_league:
                return await fetch_espn_event_summary(sport, espn_league, event_id)
        except Exception as e:
            logger.warning(f"Summary fetch failed for game {event_id}: {e}")
        return {}

    async def _fetch_recent():
        try:
            if sport and espn_league:
                return await get_recent_league_results(sport, espn_league, days=10, limit=40)
        except Exception:
            pass
        return []

    async def _fetch_upcoming_games():
        try:
            if sport and espn_league:
                return await get_upcoming_league_games(sport, espn_league, days=10, limit=40)
        except Exception:
            pass
        return []

    odds, home_stats, away_stats, summary, all_recent, all_upcoming_games = await asyncio.gather(
        _fetch_game_odds(),
        _fetch_home_stats(),
        _fetch_away_stats(),
        _fetch_summary(),
        _fetch_recent(),
        _fetch_upcoming_games(),
    )

    # ── Forma reciente + próximos partidos de ambos equipos ──
    home_recent = []
    away_recent = []
    home_upcoming = []
    away_upcoming = []
    home_form = []
    away_form = []

    if sport and espn_league:
        try:

            home_name_lower = game["home"]["name"].lower()
            away_name_lower = game["away"]["name"].lower()

            for r in all_recent:
                if len(home_recent) < 5 and (home_name_lower in r["home"].lower() or home_name_lower in r["away"].lower()):
                    home_recent.append(r)
                if len(away_recent) < 5 and (away_name_lower in r["home"].lower() or away_name_lower in r["away"].lower()):
                    away_recent.append(r)

            for u in all_upcoming_games:
                if len(home_upcoming) < 3 and (home_name_lower in u["home"].lower() or home_name_lower in u["away"].lower()):
                    home_upcoming.append(u)
                if len(away_upcoming) < 3 and (away_name_lower in u["home"].lower() or away_name_lower in u["away"].lower()):
                    away_upcoming.append(u)

            # Build form guide (W/D/L) for each team
            for r in home_recent[:5]:
                h_s = int(r.get("home_score", 0) or 0)
                a_s = int(r.get("away_score", 0) or 0)
                is_home = home_name_lower in r.get("home", "").lower()
                ts, os_ = (h_s, a_s) if is_home else (a_s, h_s)
                home_form.append("W" if ts > os_ else ("L" if ts < os_ else "D"))

            for r in away_recent[:5]:
                h_s = int(r.get("home_score", 0) or 0)
                a_s = int(r.get("away_score", 0) or 0)
                is_home = away_name_lower in r.get("home", "").lower()
                ts, os_ = (h_s, a_s) if is_home else (a_s, h_s)
                away_form.append("W" if ts > os_ else ("L" if ts < os_ else "D"))

            # La racha de arriba sale de una ventana de 10 días del marcador, y
            # en ligas de un partido por semana da uno o dos juegos: el
            # 17/09/2026 el Toluca aparecía con "últimos 2 partidos" siendo
            # líder con 8 jugados. El calendario del equipo trae la temporada
            # completa en UNA petición, así que si responde, manda.
            try:
                _ids = [(home_stats or {}).get("team_id", ""), (away_stats or {}).get("team_id", "")]
                if any(_ids):
                    _f_home, _f_away = await asyncio.gather(
                        fetch_team_form(sport, espn_league, _ids[0]),
                        fetch_team_form(sport, espn_league, _ids[1]),
                        return_exceptions=True,
                    )
                    if isinstance(_f_home, list) and _f_home:
                        home_form = [x["result"] for x in _f_home]
                    if isinstance(_f_away, list) and _f_away:
                        away_form = [x["result"] for x in _f_away]
            except Exception as e:
                logger.warning(f"Racha por calendario falló para {event_id}: {e}")
        except Exception as e:
            logger.warning(f"Form/upcoming fetch failed for game {event_id}: {e}")

    # ── Canales por país (GatoTV) ──────────────────────────────────────────
    # El 38% de los clics viene de fuera de México (Venezuela 17%, Panamá 8%,
    # Dominicana 4%, Colombia 4%, Perú y Ecuador ~2%) y hasta ahora les
    # respondíamos con canales mexicanos. GatoTV publica la parrilla por país;
    # aquí buscamos ESTE partido en la de cada uno.
    #
    # Se hace en la ficha del partido y no en get_todays_games a propósito: la
    # portada arma decenas de juegos y no vale la pena, mientras que quien abre
    # la ficha es justo el que quiere saber su canal. Las parrillas se cachean
    # 6 h, así que son ~35 peticiones al día en total, no por visita.
    try:
        import gatotv as _gatotv
        _g_start = datetime.fromisoformat(str(game.get("date", "")).replace("Z", "+00:00"))
        # La parrilla es por día y en hora local de cada país; gatotv.py decide
        # qué día (o días) pedir a partir del horario del partido.
        _g_date = _gatotv.grid_date_for(_g_start)
        # Con la caché fría esto pide hasta 70 parrillas y medimos 12 s en la
        # ficha de un juego de MLB, que además no saca ni un canal de GatoTV.
        # Nadie espera 12 s. Damos 2.5 s: si llega, se muestra; si no, la página
        # sale sin el bloque y la tarea SIGUE VIVA llenando la caché, así que la
        # siguiente visita —incluida la de Google— ya lo trae.
        import epgshare as _epgshare
        _g_task = asyncio.ensure_future(_gatotv.channels_by_country_for_game(
            _g_date, game["home"]["name"], game["away"]["name"], _g_start,
            sport=game.get("sport"),
        ))
        # Segunda fuente, complementaria. epgshare01 publica XMLTV ya armado
        # (un archivo por país al día) y trae dos cosas que GatoTV no: béisbol
        # —MLB fuera de México estaba en blanco— y las ligas locales completas.
        # Van juntas y no en lugar de: GatoTV es el único que cubre Venezuela,
        # que es nuestro primer país de LATAM.
        _e_task = asyncio.ensure_future(_epgshare.channels_by_country_for_game(
            game["home"]["name"], game["away"]["name"], _g_start,
        ))
        _tareas = {_g_task, _e_task}
        _done, _ = await asyncio.wait(_tareas, timeout=2.5)
        _by_country = {}
        for _t in _tareas:
            if _t in _done:
                try:
                    _res = _t.result()
                except Exception:
                    _res = {}
                for _cc, _chs in (_res or {}).items():
                    _b = _by_country.setdefault(_cc, [])
                    for _ch in _chs:
                        if _ch not in _b:
                            _b.append(_ch)
            else:
                # Sin esto, si la tarea termina en excepción, asyncio la reporta
                # como "never retrieved" y ensucia los logs. La dejamos viva:
                # sigue llenando la caché para la siguiente visita.
                _t.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        if _by_country:
            _merged = dict(game.get("channels_by_country") or {})
            for _cc, _chs in _by_country.items():
                _bucket = list(_merged.get(_cc) or [])
                for _ch in _chs:
                    if _ch not in _bucket:
                        _bucket.append(_ch)
                _merged[_cc] = _bucket
            game["channels_by_country"] = _merged
    except Exception as e:
        logger.warning(f"GatoTV por país falló para {event_id}: {e}")

    # ── Implied probability from odds ──
    implied_probs = {}
    if odds:
        try:
            def _american_to_prob(odds_val):
                if odds_val is None:
                    return None
                o = float(str(odds_val).replace("+", ""))
                if o > 0:
                    return 100 / (o + 100) * 100
                elif o < 0:
                    return abs(o) / (abs(o) + 100) * 100
                return None

            hp = _american_to_prob(odds.get("home_odds"))
            ap = _american_to_prob(odds.get("away_odds"))
            dp = _american_to_prob(odds.get("draw_odds"))
            # Normalize to 100%
            total = (hp or 0) + (ap or 0) + (dp or 0)
            if total > 0:
                implied_probs = {
                    "home": round(hp / total * 100, 1) if hp else None,
                    "away": round(ap / total * 100, 1) if ap else None,
                    "draw": round(dp / total * 100, 1) if dp else None,
                }
        except Exception:
            pass

    # ── Generate editorial preview ──
    game["prediction"] = _quick_prediction_from_odds(game) if odds else None
    game["odds"] = odds
    match_preview = generate_match_preview(game)

    # Build richer preview using form data if available
    if match_preview and (home_form or away_form):
        extra = []
        if home_form:
            hw = home_form.count("W")
            hl = home_form.count("L")
            n = len(home_form)
            extra.append(f"{game['home']['name']} llega con {hw}G-{n-hw-hl}E-{hl}P en {'su último partido' if n == 1 else f'sus últimos {n} partidos'}")
        if away_form:
            aw = away_form.count("W")
            al_ = away_form.count("L")
            extra.append(f"{game['away']['name']} tiene marca de {aw}G-{len(away_form)-aw-al_}E-{al_}P")
        if extra:
            match_preview["full"] = match_preview["full"] + " " + ". ".join(extra) + "."

    # Mark TBD games as noindex — no real content for search engines
    noindex = (
        game.get("home", {}).get("name", "") == "TBD"
        or game.get("away", {}).get("name", "") == "TBD"
    )

    # ── Cuánto puede envejecer este HTML ──────────────────────────────────────
    # Se reportó que la portada mostraba Padres-Rockies en la alta de la novena
    # y esta página en la baja de la octava. La portada se cachea 60 s y esta
    # 300 s: podía ir cuatro minutos atrás. En un sitio de deportes, un marcador
    # viejo no es un detalle — el usuario deja de creerle al resto del sitio.
    #
    # En vivo: 15 s. A punto de empezar: 30 s, para que el cambio de "programado"
    # a "en vivo" no se quede atorado en una copia vieja. Lo demás no cambia.
    _estado = (game.get("status") or {}).get("state", "")
    _ttl_corto = 0
    if _estado == "in":
        _ttl_corto = 15
    elif _estado == "pre":
        try:
            _faltan = (datetime.fromisoformat(str(game.get("date", "")).replace("Z", "+00:00"))
                       - datetime.now(timezone.utc)).total_seconds()
            if -3600 < _faltan < 1800:
                _ttl_corto = 30
        except Exception:
            pass

    _resp = templates.TemplateResponse(
        request, "game.html", context={
            "game": game, "odds": odds,
            "home_slug": home_slug, "away_slug": away_slug,
            "home_stats": home_stats, "away_stats": away_stats,
            "summary": summary,
            "home_recent": home_recent, "away_recent": away_recent,
            "home_upcoming": home_upcoming, "away_upcoming": away_upcoming,
            "home_form": home_form, "away_form": away_form,
            "implied_probs": implied_probs,
            "match_preview": match_preview,
            "faq_items": _build_match_faq(game),
            "noindex": noindex,
        }
    )
    if _ttl_corto:
        _resp.headers["x-dv-ttl"] = str(_ttl_corto)
    return _resp


@app.get("/juego/{event_id}", response_class=HTMLResponse)
async def game_detail(request: Request, event_id: str, date: Optional[str] = Query(None)):
    """Redirect to semantic URL or show game if slug can't be built."""
    # IDs basura (no numéricos de ESPN) → 410 sin gastar 5 fetches
    if not re.fullmatch(r"\d{6,14}", event_id):
        return templates.TemplateResponse(request, "404.html", status_code=410,
                                          context={"message": "Este juego ya no existe. Ve los juegos de hoy en la home."})

    # Partidos viejos (2,056 URLs /juego/ legacy en GSC): si está en la BD y ya terminó,
    # 301 a la página del equipo (conserva el valor del enlace) en vez de 410.
    try:
        from sqlalchemy import text as sa_text
        from db import async_session
        async with async_session() as session:
            row = (await session.execute(
                sa_text("SELECT home_name, away_name, league_slug, game_date, state FROM games WHERE id = :id"),
                {"id": event_id})).mappings().first()
        if row and (row["state"] == "post" or (row["game_date"] or "") < (datetime.now(TZ_MX) - timedelta(days=1)).strftime("%Y%m%d")):
            tslug = _team_name_to_slug(row["home_name"]) or _team_name_to_slug(row["away_name"])
            if tslug:
                return RedirectResponse(url=f"/equipo/{tslug}", status_code=301)
            if row["league_slug"] in ALL_LEAGUES:
                return RedirectResponse(url=f"/liga/{row['league_slug']}", status_code=301)
    except Exception:
        pass

    # Try the requested date first, then today, then nearby dates
    all_games = await get_todays_games(date_str=date)
    game = next((g for g in all_games if g["id"] == event_id), None)

    # If not found with the given date, try today (in case date param is stale)
    if not game and date:
        all_games = await get_todays_games()
        game = next((g for g in all_games if g["id"] == event_id), None)

    # Still not found — try yesterday and tomorrow as fallback
    if not game:
        now = datetime.now(TZ_MX)
        for delta in [1, -1, 2, -2]:
            try_date = (now + timedelta(days=delta)).strftime("%Y%m%d")
            fallback_games = await get_todays_games(date_str=try_date)
            game = next((g for g in fallback_games if g["id"] == event_id), None)
            if game:
                break

    if not game:
        # 410 Gone: le dice a Google que la URL existio pero ya no.
        # Google desindexa mas rapido con 410 que con 404.
        return templates.TemplateResponse(
            request, "404.html", status_code=410,
            context={"message": "Este juego ya termino. Ve los juegos de hoy en la home."}
        )

    # 301 redirect to the semantic URL
    return RedirectResponse(url=f"/partido/{_make_game_slug(game)}", status_code=301)


# ── Dynamic OG images ────────────────────────────────────
from og_image import generate_game_og, generate_team_og, _og_cache


@app.get("/og/partido/{slug}.png")
async def og_game_image(slug: str):
    """Generate a dynamic OG image for a game page."""
    cache_key = f"og:partido:{slug}"
    cached = _og_cache.get(cache_key)
    if cached:
        return Response(content=cached, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

    # Parse slug to find the game
    match = re.match(r"^(.+)-vs-(.+)-(\d{4}-\d{2}-\d{2})$", slug)
    if not match:
        return Response(status_code=404)

    slug_part_1 = match.group(1)
    slug_part_2 = match.group(2)
    date_str = match.group(3).replace("-", "")

    # Find the game
    game = None
    for delta in [0, 1, -1]:
        try:
            dt = datetime.strptime(date_str, "%Y%m%d") + timedelta(days=delta)
            try_date = dt.strftime("%Y%m%d")
        except ValueError:
            continue
        all_games = await get_todays_games(date_str=try_date)
        for g in all_games:
            g_home = _slugify(g.get("home", {}).get("name", ""))
            g_away = _slugify(g.get("away", {}).get("name", ""))
            if (g_home == slug_part_1 and g_away == slug_part_2) or \
               (g_away == slug_part_1 and g_home == slug_part_2):
                game = g
                break
        if game:
            break

    if not game:
        # Fallback: generate with slug parts only
        home_name = slug_part_1.replace("-", " ").title()
        away_name = slug_part_2.replace("-", " ").title()
        date_display = match.group(3)
        png = await generate_game_og(home_name, away_name, date_str=date_display)
    else:
        # Format date in Spanish
        try:
            dt = datetime.fromisoformat(game.get("date", "").replace("Z", "+00:00"))
            dt_mx = dt.astimezone(TZ_MX)
            MONTHS_ES_SHORT = ["ene", "feb", "mar", "abr", "may", "jun",
                               "jul", "ago", "sep", "oct", "nov", "dic"]
            date_display = f"{dt_mx.day} {MONTHS_ES_SHORT[dt_mx.month - 1]} {dt_mx.year} · {dt_mx.strftime('%H:%M')} hrs MX"
        except Exception:
            date_display = match.group(3)

        png = await generate_game_og(
            home_name=game["home"]["name"],
            away_name=game["away"]["name"],
            home_logo_url=game["home"].get("logo", ""),
            away_logo_url=game["away"].get("logo", ""),
            league_name=game.get("league_name", ""),
            date_str=date_display,
        )

    _og_cache[cache_key] = png
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/og/equipo/{team_slug}.png")
async def og_team_image(team_slug: str):
    """Generate a dynamic OG image for a team page."""
    cache_key = f"og:equipo:{team_slug}"
    cached = _og_cache.get(cache_key)
    if cached:
        return Response(content=cached, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

    team_info = POPULAR_TEAMS.get(team_slug)
    if team_info:
        team_name = team_info["name"]
        team_league = team_info.get("league", "")
    else:
        team_name = team_slug.replace("-", " ").title()
        team_league = ""

    # Find logo from current games or stats
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    team_logo_url = ""
    try:
        games = await search_games(search_term)
        for game in games:
            if search_term.lower() in game["home"]["name"].lower():
                team_logo_url = game["home"].get("logo", "")
                if not team_league:
                    team_league = game.get("league_name", "")
                break
            elif search_term.lower() in game["away"]["name"].lower():
                team_logo_url = game["away"].get("logo", "")
                if not team_league:
                    team_league = game.get("league_name", "")
                break
    except Exception:
        pass

    if not team_logo_url:
        try:
            stats = await get_team_stats(team_slug)
            team_logo_url = stats.get("team_logo", "")
        except Exception:
            pass

    png = await generate_team_og(
        team_name=team_name,
        team_logo_url=team_logo_url,
        league_name=team_league,
    )
    _og_cache[cache_key] = png
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


# ── Affiliate click tracking ──────────────────────────────
import json as _json
from pathlib import Path as _Path
from datetime import date as _date

_CLICKS_FILE = os.getenv("CLICKS_FILE", os.path.join(
    os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "affiliate_clicks.json"
))


def _track_click(affiliate: str, source: str, sport: str = "", league: str = "",
                 country: str = "", match: str = "", market: str = ""):
    """Cuenta clics por día, afiliado y origen.

    Antes la clave era solo "afiliado:origen", que responde "¿convierte más la
    tarjeta o el banner?" pero no "¿qué liga, qué deporte, qué país?". Ahora la
    clave lleva las cinco dimensiones y se sigue guardando la corta, para no
    romper el panel de admin ni perder la serie histórica.
    """
    try:
        _Path(_CLICKS_FILE).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CLICKS_FILE, "r") as f:
                data = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            data = {}
        today = _date.today().isoformat()
        data.setdefault(today, {})
        key = f"{affiliate}:{source}"
        data[today][key] = data[today].get(key, 0) + 1
        if sport or league or country:
            detalle = f"{affiliate}:{source}:{sport or '-'}:{league or '-'}:{country or '-'}"
            data[today][detalle] = data[today].get(detalle, 0) + 1
        # Clave por partido y mercado: responde "que encuentro y que mercado
        # convierte", que es distinto de "que posicion de la pagina convierte".
        if match or market:
            fino = f"{affiliate}:{source}:{match or '-'}:{market or '-'}"
            data[today][fino] = data[today].get(fino, 0) + 1
        with open(_CLICKS_FILE, "w") as f:
            _json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Click tracking failed: {e}")


def get_click_stats(days: int = 7) -> dict:
    """Get click stats for the last N days."""
    try:
        with open(_CLICKS_FILE, "r") as f:
            data = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        data = {}
    from datetime import timedelta
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    result = {}
    for day, clicks in data.items():
        if day >= cutoff:
            for k, v in clicks.items():
                # Ignore junk written before hardening (sqlmap payloads, /go/styles.css…)
                if not re.fullmatch(r"[a-z0-9_-]{1,32}:[a-z0-9_-]{1,40}", k):
                    continue
                result[k] = result.get(k, 0) + v
    return result


def _purge_click_junk() -> int:
    """One-time cleanup of affiliate_clicks.json: drop keys that aren't slug:slug."""
    try:
        with open(_CLICKS_FILE, "r") as f:
            data = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return 0
    removed = 0
    for day, clicks in list(data.items()):
        for k in list(clicks.keys()):
            if not re.fullmatch(r"[a-z0-9_-]{1,32}:[a-z0-9_-]{1,40}", k):
                del clicks[k]
                removed += 1
    if removed:
        with open(_CLICKS_FILE, "w") as f:
            _json.dump(data, f, indent=2)
    return removed


# Branded affiliate redirect — "dondever.app/go/betsson" en vez de links largos
@app.get("/go/{key}")
async def affiliate_redirect(key: str, s: str = "web", sport: str = "",
                             league: str = "", match: str = "", market: str = "",
                             request: Request = None):
    """
    Redirige a la URL del afiliado con tracking de source.
    Uso: /go/betsson?s=twitter  →  link afiliado real + sub1=twitter

    /go/bet = link geo-inteligente: decide el casino según el país
    del visitante (Cloudflare / Vercel header / Accept-Language).
    MX → Jubilee/Vivento, LATAM/resto → 1xBet.
    EE.UU. y tráfico en inglés: sin CTA de apuestas desde que Betsson
    terminó el programa (24-sep-2026). Ninguno de nuestros socios tiene
    licencia allá, y mandar a alguien a apostar sin licencia en su estado
    sería peor que no mandarlo a ningún lado.
    """
    from fastapi.responses import RedirectResponse
    from config import get_affiliate_url, AFFILIATES, STREAMING_AFFILIATES
    import random as _random
    # ── Hardening (admin dashboard showed sqlmap payloads in `s` and bots hitting
    #    /go/styles.css, /go/flag-global.svg). Whitelist both params, skip bots. ──
    key = (key or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", key):
        return Response(status_code=404)
    _known = key in ("bet", "strendus") or key in AFFILIATES or any(v.get("key") == key for v in STREAMING_AFFILIATES.values())
    if not _known:
        return Response(status_code=404)
    s = (s or "web").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", s):
        s = "other"
    sport = sport if re.fullmatch(r"[a-z0-9_-]{0,30}", sport or "") else ""
    # Hasta ahora solo se guardaba afiliado + origen, asi que no habia forma de
    # saber QUE LIGA convierte. Se valida igual que el resto: esto viene de la
    # URL y el panel de admin ya vio intentos de inyeccion por estos parametros.
    league = league if re.fullmatch(r"[a-z0-9_-]{0,40}", league or "") else ""
    # Partido y mercado: sin esto solo sabiamos "alguien toco los momios", no
    # QUE partido ni si fue el ganador, el handicap o el total.
    match = match if re.fullmatch(r"[a-z0-9_-]{0,80}", match or "") else ""
    market = market if re.fullmatch(r"[a-z0-9_-]{0,20}", market or "") else ""
    _ua = (request.headers.get("user-agent", "") if request is not None else "").lower()
    _is_bot = any(b in _ua for b in ("bot", "crawl", "spider", "whatsapp", "facebookexternalhit",
                                     "preview", "curl", "python-requests", "sqlmap", "scanner", "headless"))
    # Betsson terminó el programa de afiliados el 24 de septiembre de 2026.
    # Todo enlace suyo ya no paga nada, así que mandarle tráfico es regalar
    # clics. Los enlaces viejos (incluido strendus, que ya redirigía aquí) van
    # al comparador: el usuario llegó buscando dónde apostar y ahí lo
    # encuentra, en vez de caer en una casa que ya no es socia.
    if key in ("betsson", "strendus"):
        return RedirectResponse(url="/casinos", status_code=302)
    # Smart geo link: /go/bet
    if key == "bet":
        country = ""
        al = ""
        if request is not None:
            country = (request.headers.get("cf-ipcountry")
                       or request.headers.get("x-vercel-ip-country") or "").upper()
            al = (request.headers.get("accept-language") or "").lower()
        LATAM_COUNTRIES = {"VE","PA","DO","CO","NI","CL","AR","PE","CR","EC",
                           "GT","HN","SV","CU","BO","PY","UY","BR","ES","PR"}
        # Estados Unidos y el tráfico en inglés los cubría Betsson. Ahora no
        # hay con qué cubrirlos, y la salida fácil —mandarlos a 1xBet— sería
        # peor que no hacer nada: en EE.UU. las apuestas se licencian estado
        # por estado y 1xBet no es un operador licenciado ahí. Mandar a un
        # gringo a apostar en un sitio sin licencia en su estado es un
        # problema para él y para nosotros, con AdSense mirando.
        #
        # Así que ese tráfico no ve CTA de apuestas hasta que haya un socio
        # que sí opere legalmente allá. Se pierde el clic; se conserva el
        # sitio. `None` hace que la ruta mande a la home sin registrar nada.
        if country:
            if country == "MX":
                key = _random.choice(["jubilee", "vivento"])
            elif country == "US":
                return RedirectResponse(url="/", status_code=302)
            elif country in LATAM_COUNTRIES:
                key = "1xbet"
            else:
                key = "1xbet"  # default resto del mundo → 1xBet
        else:
            # Heurística por idioma
            if "es-mx" in al:
                key = _random.choice(["jubilee", "vivento"])
            elif "en-us" in al or "en-gb" in al or al.startswith("en"):
                return RedirectResponse(url="/", status_code=302)
            else:
                key = "1xbet"  # español genérico u otro → 1xBet
    if not _is_bot:
        # El pais sale del mismo header que ya usamos para elegir casa.
        _pais = ((request.headers.get("cf-ipcountry")
                  or request.headers.get("x-vercel-ip-country") or "").upper()
                 if request is not None else "")
        _track_click(key, s, sport=sport, league=league, country=_pais,
                     match=match, market=market)
    target = get_affiliate_url(key, source=s, sport=sport)
    if target == "#":
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=target, status_code=302)


# Legacy URLs que Google sigue rastreando de versiones viejas del sitio
@app.get("/game/{old_id}")
async def legacy_game_redirect(request: Request, old_id: str):
    """Handle old /game/ URLs. ESPN numeric IDs → 301 to /juego/.
    Non-ESPN IDs (e.g. la_liga_apifb_*) → 410 Gone directly."""
    # ESPN IDs are purely numeric (e.g. "401654321")
    if old_id.isdigit():
        return RedirectResponse(url=f"/juego/{old_id}", status_code=301)
    # Non-numeric IDs are from old third-party APIs — they'll never resolve.
    # Return 410 Gone directly to speed up deindexing (skip the redirect chain).
    return templates.TemplateResponse(
        request, "404.html", status_code=410,
        context={"message": "Este juego ya no existe. Ve los juegos de hoy en la home."}
    )


# ── League-specific SEO extra content ─────────────────────
LEAGUE_SEO_EXTRA = {
    "lmp": {
        "title": "Donde ver LMP hoy en vivo — Liga Mexicana del Pacifico | DondeVer",
        "meta_desc": "Donde ver la LMP (Liga Mexicana del Pacifico) en vivo hoy: horarios, canales y resultados. Tomateros, Naranjeros, Yaquis, Aguilas y todos los equipos. TUDN, ESPN y Canal 5.",
        "h2": "Donde ver la Liga Mexicana del Pacifico (LMP) en vivo",
        "paragraphs": [
            "La Liga Mexicana del Pacifico (LMP) es la liga de beisbol invernal de Mexico, con temporada de octubre a enero. Cuenta con 10 equipos de los estados del noroeste: Tomateros de Culiacan, Naranjeros de Hermosillo, Yaquis de Obregon, Aguilas de Mexicali, Venados de Mazatlan, Caneros de Los Mochis, Mayos de Navojoa, Algodoneros de Guasave, Charros de Jalisco y Sultanes de Monterrey.",
            "Los partidos de la LMP se transmiten por TUDN, ESPN Mexico y Canal 5. El campeon de la LMP representa a Mexico en la Serie del Caribe. En DondeVer.app te mostramos donde ver cada juego de la LMP con horarios de Mexico.",
        ],
        "links": [],
    },
    "lmb": {
        "title": "Donde ver LMB hoy en vivo — Liga Mexicana de Beisbol | DondeVer",
        "meta_desc": "Donde ver la LMB (Liga Mexicana de Beisbol) en vivo hoy: horarios, canales y resultados. Diablos Rojos, Tigres, Leones, Sultanes y mas. ESPN y TUDN.",
        "h2": "Donde ver la Liga Mexicana de Beisbol (LMB) en vivo",
        "paragraphs": [
            "La Liga Mexicana de Beisbol (LMB) es la liga de beisbol de verano en Mexico, con temporada de abril a agosto. Cuenta con 16 equipos divididos en Zona Norte y Zona Sur, incluyendo los historicos Diablos Rojos del Mexico, Tigres de Quintana Roo, Leones de Yucatan y Sultanes de Monterrey.",
            "Los juegos de la LMB se transmiten por ESPN Mexico y TUDN. La LMB es la liga Triple-A de MLB en Mexico y varios jugadores han dado el salto a las Grandes Ligas. En DondeVer.app te decimos en que canal pasan cada partido de la LMB.",
        ],
        "links": [],
    },
    "liga-mx-femenil": {
        "title": "Donde ver Liga MX Femenil hoy en vivo — Futbol femenil Mexico | DondeVer",
        "meta_desc": "Donde ver la Liga MX Femenil en vivo hoy: horarios, canales y resultados. Tigres, America, Chivas, Monterrey y todos los equipos. TUDN, Canal 5 y Vix.",
        "h2": "Donde ver la Liga MX Femenil en vivo hoy",
        "paragraphs": [
            "La Liga MX Femenil es la primera division del futbol femenil profesional en Mexico. Cuenta con 18 equipos que compiten en torneos Apertura y Clausura. Tigres UANL Femenil, America Femenil y Monterrey Femenil (Rayadas) son los equipos mas ganadores del torneo.",
            "Los partidos de la Liga MX Femenil se transmiten por TUDN, Canal 5, Vix y ESPN Mexico. El futbol femenil mexicano ha crecido enormemente en popularidad y varias jugadoras han dado el salto a ligas europeas. En DondeVer.app te mostramos donde ver cada partido de la Liga MX Femenil con horarios de Mexico.",
        ],
        "links": [],
    },
    "lvbp": {
        "title": "Donde ver LVBP hoy en vivo: beisbol venezolano | DondeVer",
        "meta_desc": "LVBP en vivo hoy: horarios y canales del beisbol venezolano. Magallanes, Caracas, Aragua, Lara y Zulia. Gratis por Meridiano, Televen, Venevision y TVES.",
        "h2": "Donde ver la LVBP (beisbol venezolano) en vivo",
        "paragraphs": [
            "La LVBP (Liga Venezolana de Beisbol Profesional) es la liga de beisbol invernal de Venezuela, una de las mas importantes del Caribe. Cuenta con 8 equipos: Navegantes del Magallanes, Leones del Caracas, Tigres de Aragua, Cardenales de Lara, Aguilas del Zulia, Tiburones de La Guaira, Caribes de Anzoategui y Bravos de Margarita.",
            "Ocho canales transmiten la LVBP en Venezuela. En senal abierta y gratis: Meridiano TV, Televen, Venevision, TVES y Canal i. Por cable: IVC, ByM Sport y 1 Baseball. Tambien esta BeisbolPlay, que es la plataforma de streaming con todos los juegos de la temporada. Ojo con una confusion habitual: Inter y Simple TV son las operadoras de cable, no canales con derechos; quien las tiene ve la LVBP por Meridiano, IVC o ByM Sport. Si estas fuera de Venezuela, BeisbolPlay es la unica opcion: transmite todos los juegos a nivel nacional e internacional. La temporada 2026-2027 arranca el lunes 12 de octubre, la ronda regular termina el 27 de diciembre, el Round Robin empieza el 2 de enero y la Serie Final el 23 de enero. El campeon representa a Venezuela en la Serie del Caribe.",
        ],
        "links": [],
    },
    # Tenis y golf: son TORNEOS, no jornadas. El texto responde a lo que la gente
    # escribe ("donde ver el US Open", "a que hora juega Alcaraz") y no a "partidos
    # de hoy", que en estos deportes casi nunca existe como pregunta.
    "atp": {
        "title": "Donde ver tenis ATP: torneos, horarios y canales | DondeVer",
        "meta_desc": "Tenis ATP en vivo: calendario de torneos con hora de Mexico, canales y streaming. US Open, Roland Garros, Wimbledon, Abierto de Australia y Masters 1000.",
        "h2": "Donde ver el tenis ATP en vivo: proximos torneos",
        "paragraphs": [
            "El circuito ATP agrupa el tenis masculino profesional: los cuatro Grand Slams (Abierto de Australia, Roland Garros, Wimbledon y US Open), los nueve Masters 1000, los ATP 500 y 250, y las ATP Finals de noviembre. A diferencia del futbol, el tenis se organiza por torneos de una o dos semanas, no por jornadas: por eso aqui veras el calendario de torneos con su fecha de inicio y de cierre, y no un listado de partidos sueltos.",
            "En Mexico y Latinoamerica el tenis ATP se transmite habitualmente por ESPN y Disney+. En Espana va por Movistar Plus+ y en Estados Unidos se reparte entre ESPN y Tennis Channel. Los derechos de los Grand Slams cambian de manos con frecuencia, asi que en cada torneo confirmamos el canal contra la parrilla real de tu pais antes de afirmarlo.",
        ],
        "links": [],
    },
    "wta": {
        "title": "Donde ver tenis WTA: torneos, horarios y canales | DondeVer",
        "meta_desc": "Tenis WTA en vivo: calendario de torneos con hora de Mexico, canales y streaming. Guadalajara Open, US Open, Roland Garros y WTA Finals.",
        "h2": "Donde ver el tenis WTA en vivo: proximos torneos",
        "paragraphs": [
            "El circuito WTA es el tenis femenino profesional. Incluye los cuatro Grand Slams, los WTA 1000, los WTA 500 y 250, y las WTA Finals. Para Mexico tiene un torneo propio: el Guadalajara Open, un WTA 500 que se juega en septiembre en el Panamericano de Zapopan y que suele ser el evento de tenis mas visto del ano en el pais.",
            "En Mexico y Latinoamerica la WTA se transmite habitualmente por ESPN y Disney+. En Espana por Movistar Plus+ y en Estados Unidos entre ESPN y Tennis Channel. Como los torneos duran varios dias, en cada uno mostramos cuando empieza y cuando termina, con hora del centro de Mexico.",
        ],
        "links": [],
    },
    "pga": {
        "title": "Donde ver golf PGA Tour: torneos y horarios | DondeVer",
        "meta_desc": "PGA Tour en vivo: calendario de torneos con hora de Mexico, canales y streaming. El Masters de Augusta, PGA Championship, US Open y The Open.",
        "h2": "Donde ver el PGA Tour en vivo: proximos torneos",
        "paragraphs": [
            "El PGA Tour es el circuito principal del golf profesional masculino. Su temporada se arma alrededor de cuatro majors: el Masters de Augusta en abril, el PGA Championship en mayo, el US Open en junio y The Open Championship (el British Open) en julio. Cada torneo se juega de jueves a domingo, asi que la pregunta util no es que partido hay hoy, sino que torneo esta en curso y cuando son las rondas.",
            "En Mexico y Latinoamerica el golf del PGA Tour se transmite habitualmente por ESPN y Disney+. En Estados Unidos se reparte entre Golf Channel, CBS y NBC segun la ronda y el torneo, y en Espana va por Movistar Plus+. Los majors se negocian aparte del resto del calendario, asi que conviene confirmar el canal torneo por torneo.",
        ],
        "links": [],
    },
    "lidom": {
        "title": "Donde ver LIDOM hoy en vivo — Liga Dominicana de Beisbol | DondeVer",
        "meta_desc": "Donde ver la LIDOM en vivo hoy: horarios, canales y resultados del beisbol dominicano. Licey, Aguilas, Escogido, Estrellas, Gigantes y Toros. CDN Deportes, Teleantillas.",
        "h2": "Donde ver la LIDOM (beisbol dominicano) en vivo",
        "paragraphs": [
            "La LIDOM (Liga de Beisbol Profesional de la Republica Dominicana) es la liga invernal mas prestigiosa del Caribe. Cuenta con 6 equipos: Tigres del Licey, Aguilas Cibaenas, Leones del Escogido, Estrellas Orientales, Gigantes del Cibao y Toros del Este.",
            "Los juegos de LIDOM se transmiten en Republica Dominicana por CDN Deportes (cable), Teleantillas Canal 10, Coral 39, Digital 15 y VTV Canal 32 (TV abierta). La temporada va de octubre a enero, seguida del round robin y la final. El campeon va a la Serie del Caribe. En DondeVer.app te mostramos los canales actualizados.",
        ],
        "links": [],
    },
    "lnbp": {
        "title": "Donde ver LNBP hoy en vivo — Liga Nacional de Baloncesto Profesional | DondeVer",
        "meta_desc": "Donde ver la LNBP (Liga Nacional de Baloncesto Profesional) en vivo hoy: horarios, canales y resultados. Fuerza Regia, Capitanes, Soles y mas. ESPN MX y Claro Sports.",
        "h2": "Donde ver la LNBP (basquetbol mexicano) en vivo",
        "paragraphs": [
            "La LNBP (Liga Nacional de Baloncesto Profesional) es la maxima liga de basquetbol en Mexico. Cuenta con equipos como Fuerza Regia de Monterrey, Capitanes de la Ciudad de Mexico (afiliados a la NBA G League), Soles de Mexicali, Astros de Jalisco y Abejas de Leon.",
            "Los juegos de la LNBP se transmiten por ESPN Mexico, Claro Sports y TUDN. La temporada regular va de febrero a junio, seguida de playoffs. En DondeVer.app te decimos en que canal pasan cada partido de la LNBP con horario de Mexico.",
        ],
        "links": [],
    },
    "f1": {
        "title": "Donde ver F1 hoy en vivo — Formula 1 en Mexico | DondeVer",
        "meta_desc": "Donde ver la Formula 1 (F1) en vivo hoy en Mexico: horarios, canales y resultados de cada Gran Premio. Checo Perez, Verstappen, Hamilton. Fox Sports, Canal 5, F1 TV.",
        "h2": "Donde ver Formula 1 en vivo hoy",
        "paragraphs": [
            "La Formula 1 (F1) es la maxima categoria del automovilismo mundial. La temporada 2026 cuenta con 24 Grandes Premios incluyendo el Gran Premio de Mexico en el Autodromo Hermanos Rodriguez (noviembre). Sergio 'Checo' Perez es el piloto mexicano que compite en la grilla junto a Max Verstappen, Lewis Hamilton y Carlos Sainz.",
            "En Mexico, la F1 se transmite por Fox Sports MX (cable), Canal 5 (gratis en TV abierta los domingos de carrera), TUDN y F1 TV (streaming oficial). Cada fin de semana de carrera incluye practicas libres (viernes), clasificacion (sabado) y la carrera (domingo). En DondeVer.app te mostramos donde ver cada sesion del Gran Premio con horarios de Mexico.",
        ],
        "links": [],
    },
    "motogp": {
        "title": "Dónde ver MotoGP hoy: horarios en México, calendario y canales | DondeVer",
        "meta_desc": "Calendario MotoGP 2026 con hora de México: prácticas, clasificación, sprint y carrera de cada Gran Premio. Dónde ver en ESPN y Disney+ en México y Latinoamérica.",
        "h2": "Dónde ver MotoGP en vivo: próximas carreras",
        "paragraphs": [
            "MotoGP es el campeonato mundial de motociclismo. Cada fin de semana de Gran Premio tiene prácticas (viernes), clasificación y carrera sprint (sábado) y la carrera principal (domingo). Marc Márquez, Pecco Bagnaia, Jorge Martín y Pedro Acosta encabezan la parrilla.",
            "En México y Latinoamérica MotoGP se transmite por ESPN y Disney+; en España por DAZN. En DondeVer.app publicamos cada Gran Premio con la hora de México de todas las sesiones, el canal por país y la próxima carrera del calendario.",
        ],
        "links": [],
    },
    "nascar": {
        "title": "Dónde ver NASCAR hoy: próxima carrera, hora en México y canal | DondeVer",
        "meta_desc": "Calendario NASCAR Cup Series 2026 con hora de México: próxima carrera, playoffs y canales (Fox Sports MX; FOX, Prime Video, TNT y NBC en EE.UU.). Daniel Suárez y más.",
        "h2": "Dónde ver la NASCAR Cup Series en vivo",
        "paragraphs": [
            "La NASCAR Cup Series es la máxima categoría del automovilismo de stock cars en Estados Unidos, con 36 carreras entre febrero y noviembre y playoffs de 10 carreras que definen al campeón en Phoenix. El regiomontano Daniel Suárez es el piloto mexicano de la categoría.",
            "En México y Latinoamérica las carreras se ven por Fox Sports. En Estados Unidos la temporada se reparte entre FOX y FS1, Prime Video, TNT/HBO Max y NBC/USA Network. Aquí encuentras cada carrera con su hora de México y el canal confirmado.",
        ],
        "links": [],
    },
    "indycar": {
        "title": "Dónde ver IndyCar hoy: Pato O'Ward, hora en México y canal | DondeVer",
        "meta_desc": "Calendario IndyCar 2026 con hora de México: próxima carrera, Indy 500 y canales (ESPN y Disney+ en México; FOX en EE.UU.). Sigue a Pato O'Ward en cada carrera.",
        "h2": "Dónde ver IndyCar en vivo: próximas carreras",
        "paragraphs": [
            "IndyCar es la principal categoría de monoplazas de Estados Unidos y la casa de las 500 Millas de Indianápolis. El mexicano Pato O'Ward (Arrow McLaren) pelea el campeonato cada temporada, que corre de marzo a septiembre.",
            "En México y Latinoamérica IndyCar se transmite por ESPN y Disney+; en Estados Unidos por FOX. En DondeVer.app publicamos cada carrera con su hora de México y canal, incluyendo la Indy 500 en mayo.",
        ],
        "links": [],
    },
    "boxeo": {
        "title": "Dónde ver boxeo hoy: próximas peleas, Canelo, horarios y canales | DondeVer",
        "meta_desc": "Calendario de boxeo 2026: próximas peleas con hora de México y canal (DAZN, TV Azteca, Netflix, Paramount+). Canelo Álvarez, Pitbull Cruz, Mayweather vs Pacquiao y más. Cartelera y horarios por país.",
        "h2": "Dónde ver boxeo en vivo: próximas peleas",
        "paragraphs": [
            "El boxeo en México se vive en TV abierta y streaming. TV Azteca y Canal 5 transmiten peleas selectas de boxeadores mexicanos, mientras que DAZN concentra la mayoría de las carteleras internacionales y Paramount+ transmite los eventos de Zuffa Boxing. Netflix ha entrado con eventos especiales como Mayweather vs Pacquiao.",
            "En DondeVer.app publicamos cada pelea importante con su hora en México, Venezuela, Colombia, Argentina y España, el canal por país y la cartelera completa. Toca un evento para ver los detalles. Las horas se confirman la semana de la pelea.",
        ],
        "links": [],
    },
    "ufc": {
        "title": "Donde ver UFC hoy en vivo — Peleas MMA en Mexico | DondeVer",
        "meta_desc": "Donde ver la UFC en vivo hoy en Mexico: horarios, cartelera y canales. Peleas de MMA, UFC Fight Night y PPV. Paramount+, Fox Sports MX.",
        "h2": "Donde ver UFC en vivo hoy",
        "paragraphs": [
            "La UFC (Ultimate Fighting Championship) es la organizacion de MMA (artes marciales mixtas) mas importante del mundo. Realiza eventos casi cada semana entre UFC Fight Night (carteleras regulares) y eventos numerados PPV (UFC 324, 325, etc.) con peleas de campeonato. Peleadores mexicanos como Brandon Moreno y Alexa Grasso compiten regularmente.",
            "Desde 2026, la UFC se transmite en Mexico y Latinoamerica por Paramount+ (streaming). Fox Sports MX tambien transmite eventos selectos. Las carteleras suelen iniciar los sabados por la tarde (horario Mexico) con las preliminares, seguidas de la cartelera estelar por la noche. En DondeVer.app te mostramos donde ver cada evento de la UFC con horarios de Mexico.",
        ],
        "links": [],
    },
    "nfl": {
        "h2": "Donde ver NFL en Mexico 2026: Canales y Streaming",
        "paragraphs": [
            "La temporada 2026 de la NFL arranca el 9 de septiembre con el kickoff game. La temporada regular consta de 18 semanas (17 juegos por equipo), seguida de los playoffs en enero y el Super Bowl LXI en febrero de 2027.",
            "Los canales para ver NFL en Mexico incluyen Canal 5 (gratis, senal abierta), FOX y FOX One, ESPN a traves de Disney+, DAZN con NFL Game Pass, y Netflix con juegos selectos. La pretemporada se puede ver gratis en DAZN sin suscripcion.",
            "Los equipos mas populares de la NFL en Mexico son los Dallas Cowboys, Kansas City Chiefs, Las Vegas Raiders, Seattle Seahawks y Miami Dolphins. DondeVer.app te muestra en que canal pasan cada partido de tu equipo favorito.",
        ],
        "links": [
            {"text": "Guia: NFL en Mexico", "url": "/guia/donde-ver-nfl-en-mexico"},
            {"text": "Pronosticos NFL", "url": "/pronosticos-hoy"},
        ],
    },
    "champions": {
        "title": "Donde ver Champions League hoy en vivo — Canales en Mexico | DondeVer",
        "meta_desc": "Donde ver la Champions League en vivo hoy en Mexico: horarios, canales y resultados. ViX, TUDN, Amazon Prime, Fox Sports y Canal 5. Partidos de hoy.",
        "h2": "Donde ver la Champions League en Mexico",
        "paragraphs": [
            "La UEFA Champions League es el torneo de clubes mas prestigioso del mundo. En Mexico se transmite por ViX Premium y TUDN (mayoria de partidos), Amazon Prime Video (partidos selectos de martes), Fox Sports MX (partidos selectos) y Canal 5 (1 partido gratis por jornada en TV abierta).",
            "Los partidos se juegan martes y miercoles a las 12:45 PM y 3:00 PM hora del centro de Mexico. Real Madrid, Barcelona, Manchester City, Bayern Munich y los clubes mas grandes de Europa compiten por el titulo. En DondeVer.app te mostramos el canal exacto para cada partido con horario de Mexico.",
        ],
        "links": [
            {"text": "Guia completa: Champions en Mexico", "url": "/guia/donde-ver-champions-en-mexico"},
        ],
    },
    "copa-libertadores": {
        "title": "Donde ver Copa Libertadores hoy en vivo — Canales en Mexico | DondeVer",
        "meta_desc": "Donde ver la Copa Libertadores en vivo hoy en Mexico: horarios, canales y resultados. ESPN MX, Disney+, Fox Sports. Boca, River, Flamengo y mas.",
        "h2": "Donde ver la Copa Libertadores en Mexico",
        "paragraphs": [
            "La Copa CONMEBOL Libertadores es el torneo de clubes mas importante de Sudamerica. En Mexico se transmite por ESPN MX (cable), Disney+ (streaming) y Fox Sports MX (partidos selectos). Boca Juniors, River Plate, Flamengo, Palmeiras y los mejores equipos del continente compiten por el titulo.",
            "Los partidos se juegan martes, miercoles y jueves entre las 5:00 PM y 8:00 PM hora de Mexico. El campeon clasifica al Mundial de Clubes de la FIFA. En DondeVer.app te mostramos donde ver cada partido de Libertadores con horario de Mexico.",
        ],
        "links": [
            {"text": "Guia completa: Libertadores en Mexico", "url": "/guia/donde-ver-copa-libertadores-en-mexico"},
        ],
    },
}

# ── NFL team-specific SEO content for popular teams in Mexico ──
NFL_TEAM_EXTRA = {
    "cowboys": {
        "division": "NFC Este",
        "conference": "NFC",
        "stadium": "AT&T Stadium, Arlington, TX",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Dallas Cowboys en Mexico 2026",
        "seo_p": "Los Dallas Cowboys son el equipo mas popular de la NFL en Mexico. Sus juegos se transmiten frecuentemente por Canal 5 en senal abierta, FOX Sports y ESPN. Dallas compite en la Division Este de la NFC y juega de local en el AT&T Stadium en Arlington, Texas. En DondeVer.app te mostramos el canal exacto para cada partido de los Cowboys con horario de Mexico.",
        "fun_fact": "Los Cowboys son conocidos como 'America's Team' y tienen una de las aficiones mas grandes en Mexico, donde se les llama carinosamente 'Los Vaqueros'.",
    },
    "chiefs": {
        "division": "AFC Oeste",
        "conference": "AFC",
        "stadium": "GEHA Field at Arrowhead Stadium, Kansas City, MO",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Kansas City Chiefs en Mexico 2026",
        "seo_p": "Los Kansas City Chiefs, liderados por Patrick Mahomes, son uno de los equipos mas seguidos en Mexico tras ganar multiples Super Bowls recientes. Sus partidos se transmiten por Canal 5, FOX Sports y ESPN. Kansas City juega en la Division Oeste de la AFC. En DondeVer.app encuentras el canal confirmado para cada juego de los Chiefs.",
        "fun_fact": "Los Chiefs han jugado partidos de temporada regular en el Estadio Azteca de la Ciudad de Mexico como parte del programa NFL Mexico.",
    },
    "raiders": {
        "division": "AFC Oeste",
        "conference": "AFC",
        "stadium": "Allegiant Stadium, Las Vegas, NV",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Las Vegas Raiders en Mexico 2026",
        "seo_p": "Los Las Vegas Raiders tienen una de las aficiones mas fieles en Mexico, herencia de sus anos como Oakland Raiders. Juegan en la Division Oeste de la AFC en el moderno Allegiant Stadium de Las Vegas. Sus partidos se transmiten por Canal 5, FOX y ESPN. DondeVer.app te dice en que canal pasan cada juego de los Raiders.",
        "fun_fact": "Los Raiders fueron el primer equipo de la NFL en jugar un partido de temporada regular en Mexico (2005) y mantienen una enorme base de fans en el pais.",
    },
    "seahawks": {
        "division": "NFC Oeste",
        "conference": "NFC",
        "stadium": "Lumen Field, Seattle, WA",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Seattle Seahawks en Mexico 2026",
        "seo_p": "Los Seattle Seahawks son muy populares en Mexico gracias a su estilo de juego agresivo. Juegan en la Division Oeste de la NFC en el Lumen Field de Seattle. Sus juegos se transmiten por FOX Sports, ESPN y ocasionalmente Canal 5. Consulta DondeVer.app para saber en que canal pasan cada partido de los Seahawks.",
        "fun_fact": "El Lumen Field es famoso por el ruido de sus fans — los '12s' — que han provocado temblores registrados por sismografos cercanos.",
    },
    "dolphins": {
        "division": "AFC Este",
        "conference": "AFC",
        "stadium": "Hard Rock Stadium, Miami Gardens, FL",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Miami Dolphins en Mexico 2026",
        "seo_p": "Los Miami Dolphins, conocidos como 'Los Delfines', cuentan con una gran aficion en Mexico. Juegan en la Division Este de la AFC en el Hard Rock Stadium de Miami. Sus partidos se transmiten por Canal 5, FOX, CBS y ESPN. En DondeVer.app te decimos donde ver cada juego de los Dolphins con horario de Mexico.",
        "fun_fact": "Los Dolphins son el unico equipo en la historia de la NFL en completar una temporada perfecta (1972), incluyendo el Super Bowl.",
    },
    "patriots": {
        "division": "AFC Este",
        "conference": "AFC",
        "stadium": "Gillette Stadium, Foxborough, MA",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los New England Patriots en Mexico 2026",
        "seo_p": "Los New England Patriots, con su legado de 6 Super Bowls en la era Brady-Belichick, mantienen una fuerte presencia en Mexico. Juegan en la Division Este de la AFC en el Gillette Stadium. Sus partidos se ven por Canal 5, FOX y ESPN. DondeVer.app te muestra el canal de cada juego de los Patriots.",
        "fun_fact": "Los Patriots ganaron 6 Super Bowls entre 2001 y 2018, convirtiendose en una de las dinastias mas grandes del deporte profesional.",
    },
    "49ers": {
        "division": "NFC Oeste",
        "conference": "NFC",
        "stadium": "Levi's Stadium, Santa Clara, CA",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los San Francisco 49ers en Mexico 2026",
        "seo_p": "Los San Francisco 49ers son uno de los equipos con mas tradicion en la NFL, con 5 titulos de Super Bowl. Juegan en la Division Oeste de la NFC en el Levi's Stadium. Sus partidos se transmiten por FOX, Canal 5 y ESPN. En DondeVer.app encuentras el canal confirmado para cada juego de los 49ers.",
        "fun_fact": "Los 49ers dominaron la NFL en los anos 80 y 90 con Joe Montana y Steve Young, y siguen siendo uno de los equipos mas populares en la costa oeste de Mexico.",
    },
    "eagles": {
        "division": "NFC Este",
        "conference": "NFC",
        "stadium": "Lincoln Financial Field, Philadelphia, PA",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Philadelphia Eagles en Mexico 2026",
        "seo_p": "Los Philadelphia Eagles, conocidos como 'Las Aguilas', han ganado popularidad en Mexico tras sus recientes apariciones en el Super Bowl. Juegan en la Division Este de la NFC en el Lincoln Financial Field. Sus juegos se ven por FOX, Canal 5 y ESPN. DondeVer.app te dice donde ver cada partido de los Eagles.",
        "fun_fact": "La aficion de los Eagles es considerada una de las mas apasionadas y exigentes de toda la NFL.",
    },
    "steelers": {
        "division": "AFC Norte",
        "conference": "AFC",
        "stadium": "Acrisure Stadium, Pittsburgh, PA",
        "channels_mx": "Canal 5 (abierta), CBS, FOX Sports, ESPN (Disney+), DAZN",
        "seo_h2": "Donde ver a los Pittsburgh Steelers en Mexico 2026",
        "seo_p": "Los Pittsburgh Steelers, con sus 6 titulos de Super Bowl, son un equipo historico de la NFL con seguidores en todo Mexico. Juegan en la Division Norte de la AFC. Sus partidos se transmiten por CBS, FOX y ESPN. En DondeVer.app te decimos donde ver cada juego de los Steelers.",
        "fun_fact": "Los Steelers poseen el record de mas Super Bowls ganados (6) empatados con los Patriots.",
    },
    "packers": {
        "division": "NFC Norte",
        "conference": "NFC",
        "stadium": "Lambeau Field, Green Bay, WI",
        "channels_mx": "Canal 5 (abierta), FOX Sports, ESPN (Disney+), DAZN (NFL Game Pass)",
        "seo_h2": "Donde ver a los Green Bay Packers en Mexico 2026",
        "seo_p": "Los Green Bay Packers son el unico equipo de propiedad comunitaria en la NFL y juegan en el legendario Lambeau Field. Compiten en la Division Norte de la NFC. Sus partidos se transmiten por FOX, Canal 5 y ESPN. Consulta DondeVer.app para ver en que canal pasan cada juego de los Packers.",
        "fun_fact": "El Lambeau Field, conocido como 'The Frozen Tundra', es uno de los estadios mas iconicos del deporte mundial.",
    },
}


@app.get("/liga/{league_slug}", response_class=HTMLResponse)
async def league_page(request: Request, league_slug: str):
    """
    Permanent league landing page — always has content for Google to index.
    e.g. /liga/liga-mx, /liga/nfl, /liga/nba
    """
    if league_slug in ("boxing",):
        return RedirectResponse(url="/liga/boxeo", status_code=301)
    if league_slug not in ALL_LEAGUES:
        return templates.TemplateResponse(
            request, "404.html", status_code=404
        )

    sport, league_id, display_name, emoji = ALL_LEAGUES[league_slug]

    # UFC / F1 / Boxeo: eventos con página propia (/evento/{slug})
    upcoming_events = []
    recent_events = []
    event_faq = []
    _EVENT_KIND = {"ufc": "ufc", "f1": "f1", "boxeo": "boxing", "motogp": "motogp",
                   "nascar": "nascar", "indycar": "indycar",
                   "atp": "atp", "wta": "wta", "pga": "pga"}
    _ev_kind = _EVENT_KIND.get(league_slug)
    if _ev_kind:
        try:
            from events_api import fetch_events
            _is_race = _ev_kind in _RACE_KINDS
            # Carreras: calendario completo de lo que queda de temporada ("calendario motogp 2026");
            # combate: próximos 90 días. Recientes: últimos 30 días (resultados / "quién ganó").
            _all = [e for e in await fetch_events(_ev_kind, days_back=30, days_ahead=200 if _is_race else 90)
                    if not e.get("is_minor")]
            upcoming_events = [e for e in _all if e["status"] != "post"][: 30 if _is_race else 10]
            recent_events = [e for e in _all if e["status"] == "post"][-5:][::-1]
        except Exception as _e:
            logger.warning(f"events for {league_slug} failed: {_e}")

    # Title/H1 dinámicos con el próximo evento ("UFC 331 (19 sep): dónde ver…")
    event_seo = None
    nxt = next((e for e in upcoming_events if e["status"] != "post"), None)
    if nxt:
        _t = _fmt_local(nxt["date"], "America/Mexico_City", True)
        _ch = (nxt["channels"].get("MX") or [""])[0].replace(" (por confirmar)", "")
        _today = _fmt_local(nxt["date"], "America/Mexico_City", True).split(" · ")[0] == \
            _fmt_local(datetime.now(timezone.utc).isoformat(), "America/Mexico_City", True).split(" · ")[0]
        if league_slug == "f1":
            title = (f"F1 hoy: {nxt['short_name']} {_t.split(' · ')[-1]} MX en {_ch} — dónde ver la carrera" if _today
                     else f"Próxima carrera de F1: {nxt['short_name']} {_t} MX — horarios y dónde ver")
            h1 = f"Fórmula 1: {nxt['short_name']} — dónde ver y horarios en México"
            desc = (f"Próxima carrera de F1: {nxt['name']}, carrera {_t} hora de México por {_ch}. "
                    f"Calendario completo con prácticas, clasificación y carrera, canales en México y Latinoamérica.")
        elif league_slug == "motogp":
            _gp = nxt['short_name'].replace("MotoGP ", "GP de ")
            title = (f"MotoGP hoy: {_gp} {_t.split(' · ')[-1]} MX en {_ch} — dónde ver la carrera" if _today
                     else f"Próxima carrera de MotoGP: {_gp} {_t} MX — horarios y dónde ver")
            h1 = f"MotoGP: {_gp} — dónde ver y horarios en México"
            desc = (f"Próxima carrera de MotoGP: {nxt['name']}, carrera {_t} hora de México por {_ch}. "
                    f"Calendario 2026 con prácticas, clasificación, sprint y carrera; canales en México y Latinoamérica.")
        elif league_slug == "nascar":
            title = (f"NASCAR hoy: {nxt['short_name']} {_t.split(' · ')[-1]} MX en {_ch} — dónde ver" if _today
                     else f"Próxima carrera de NASCAR: {nxt['short_name']} {_t} MX — dónde ver y calendario")
            h1 = f"NASCAR Cup Series: {nxt['short_name']} — dónde ver y hora en México"
            desc = (f"Próxima carrera de la NASCAR Cup Series: {nxt['name']}, {_t} hora de México por {_ch}. "
                    f"Calendario, playoffs y canales en México, Latinoamérica y Estados Unidos.")
        elif league_slug == "indycar":
            title = (f"IndyCar hoy: {nxt['short_name']} {_t.split(' · ')[-1]} MX en {_ch} — dónde ver" if _today
                     else f"Próxima carrera de IndyCar: {nxt['short_name']} {_t} MX — dónde ver a Pato O'Ward")
            h1 = f"IndyCar: {nxt['short_name']} — dónde ver y hora en México"
            desc = (f"Próxima carrera de IndyCar: {nxt['name']}, {_t} hora de México por {_ch}. "
                    f"Calendario completo, Pato O'Ward y canales en México y Latinoamérica.")
        elif league_slug in _TOURNAMENT_KINDS:
            # Un torneo dura días: la pregunta es "¿ya empezó?" y "¿hasta cuándo?",
            # no "¿a qué hora es el partido?". Por eso el title lleva el rango y
            # no una hora suelta, y dice "en curso" cuando ya arrancó.
            _org = {"atp": "Tenis ATP", "wta": "Tenis WTA", "pga": "Golf PGA Tour"}[league_slug]
            _ini = _fmt_local(nxt.get("start_date") or nxt["date"], "America/Mexico_City", True).split(" · ")[0]
            _fin = _fmt_local(nxt.get("end_date") or nxt["date"], "America/Mexico_City", True).split(" · ")[0]
            _rango = _ini if _ini == _fin else f"{_ini} al {_fin}"
            _curso = nxt.get("status") == "in"
            title = (f"{_org}: {nxt['name']} en vivo — dónde ver y horarios en México" if _curso
                     else f"{_org}: {nxt['name']} ({_rango}) — dónde ver y horarios en México")
            h1 = f"{nxt['name']}: dónde ver{' en vivo' if _curso else ''} y horarios en México"
            desc = (f"{nxt['name']}{' está en curso' if _curso else f': del {_rango}'}. "
                    f"Dónde verlo en México por {_ch}, horarios por país y calendario de los próximos torneos.")
        elif league_slug == "ufc":
            title = f"UFC {'hoy' if _today else 'próximo evento'}: {nxt['short_name']} {_t} MX en {_ch} — cartelera y dónde ver"
            h1 = f"UFC: {nxt['name']} — dónde ver, hora en México y cartelera"
            desc = (f"{nxt['name']}: {_t} hora de México por {_ch}. Cartelera completa, preliminares y estelar, "
                    f"horarios por país y próximos eventos de UFC.")
        else:
            title = f"Boxeo{' hoy' if _today else ''}: {nxt['short_name']} {_t} MX en {_ch} — próximas peleas y dónde ver"
            h1 = f"Boxeo: {nxt['name']} — dónde ver, hora en México y próximas peleas"
            desc = (f"Próxima pelea: {nxt['name']}, {_t} hora de México por {_ch}. Calendario de boxeo con Canelo, "
                    f"Pitbull Cruz y más: horarios por país, canales y cartelera.")
        event_seo = {"title": title, "h1": h1, "desc": desc}

    # FAQ (visible + JSON-LD) para ligas de eventos: responde "a qué hora / en qué canal / próxima"
    if _ev_kind:
        _org = _EVENT_META[_ev_kind]["org"]
        _mx_ch = ", ".join(EVENT_CHANNELS_BY_KIND.get(_ev_kind, {}).get("MX", [])) or "canal por confirmar"
        _what = "carrera" if _ev_kind in _RACE_KINDS else ("pelea" if _ev_kind == "boxing" else "evento")
        if nxt:
            event_faq.append((f"¿Cuándo es la próxima {_what} de {_org}?",
                              f"{nxt['name']}: {_fmt_local(nxt['date'], 'America/Mexico_City', True)} hora del centro de México"
                              f"{' (hora estimada)' if not nxt.get('time_confirmed', True) else ''}."))
            event_faq.append((f"¿En qué canal pasan {nxt['short_name']} en México?",
                              f"En México se transmite por {', '.join(nxt['channels'].get('MX') or []) or _mx_ch}. "
                              f"En Venezuela, Colombia y Argentina por {', '.join(nxt['channels'].get('CO') or []) or 'ESPN Latinoamérica'}; en España por {', '.join(nxt['channels'].get('ES') or []) or 'DAZN'}."))
        event_faq.append((f"¿Dónde ver {_org} en México?",
                          f"{_org} se ve en México por {_mx_ch}. En DondeVer.app publicamos cada {_what} con hora de México, canal por país y "
                          f"{'todas las sesiones del fin de semana' if _ev_kind in _RACE_KINDS else 'la cartelera completa'}."))
        if _ev_kind in _RACE_KINDS and len(upcoming_events) > 1:
            _rest = "; ".join(f"{e['short_name']} ({_fmt_local(e['date'], 'America/Mexico_City', True).split(' · ')[0]})" for e in upcoming_events[:8])
            event_faq.append((f"¿Cuál es el calendario de {_org} {datetime.now(TZ_MX).year}?",
                              f"Próximas carreras: {_rest}{'…' if len(upcoming_events) > 8 else ''}. Fechas en hora de México."))
        if recent_events:
            _last = recent_events[0]
            event_faq.append((f"¿Cuál fue la última {_what} de {_org}?",
                              f"{_last['name']}, el {_fmt_local(_last['date'], 'America/Mexico_City', True).split(' · ')[0]} en {_last['venue'] or _last['city'] or 'sede por confirmar'}."))

    # Parallel fetch: games, standings, recent results, upcoming, leaders — all independent
    games_task = get_todays_games(league_filter=league_slug)
    standings_task = get_league_standings(sport, league_id, limit=50)
    results_task = get_recent_league_results(sport, league_id, days=10, limit=10)
    upcoming_task = get_upcoming_league_games(sport, league_id, days=14, limit=10)
    leaders_task = fetch_league_leaders(sport, league_id, top_n=5)

    games, standings, recent_results, upcoming_games, league_leaders = await asyncio.gather(
        games_task, standings_task, results_task, upcoming_task, leaders_task,
        return_exceptions=True,
    )
    # Graceful fallback on errors
    if isinstance(games, Exception):
        games = []
    if isinstance(standings, Exception):
        standings = []
    if isinstance(recent_results, Exception):
        recent_results = []
    if isinstance(upcoming_games, Exception):
        upcoming_games = []
    if isinstance(league_leaders, Exception):
        league_leaders = []

    # Tenis y golf: ESPN devuelve TORNEOS, no enfrentamientos. Su scoreboard trae
    # el evento ("Guadalajara Open") con competitions vacío, así que el parser
    # produce "TBD vs TBD" y /liga/wta terminaba enlazando a tres
    # /partido/tbd-vs-tbd-…, que no le sirven a nadie y se comen presupuesto de
    # rastreo. Se ocultan hasta que tenis y golf se traten como eventos
    # (/evento/), igual que ya se hace con UFC, F1 y boxeo.
    def _sin_tbd(lista):
        return [g for g in lista
                if (g.get("home") or {}).get("name") != "TBD"
                and (g.get("away") or {}).get("name") != "TBD"]
    games = _sin_tbd(games)
    recent_results = [r for r in recent_results
                      if r.get("home") != "TBD" and r.get("away") != "TBD"]
    upcoming_games = [u for u in upcoming_games
                      if u.get("home") != "TBD" and u.get("away") != "TBD"]

    # Related teams from POPULAR_TEAMS that play in this league
    league_teams = {
        slug: info for slug, info in POPULAR_TEAMS.items()
        if info.get("league") == league_slug
    }

    # Collect actual channels from today's games for this league
    league_channels_today = []
    for g in games:
        for b in g.get("broadcasts", []):
            ch = b.get("channel", "")
            if ch and ch not in league_channels_today:
                league_channels_today.append(ch)
    # Fallback to default channels if no games today
    from sports_api import DEFAULT_LEAGUE_CHANNELS
    default_channels = DEFAULT_LEAGUE_CHANNELS.get(league_slug, [])

    # Derive league stats summary from standings
    league_stats = {}
    if standings:
        leader = standings[0]
        league_stats["leader"] = leader.get("team_name", "")
        league_stats["leader_logo"] = leader.get("team_logo", "")
        league_stats["total_teams"] = len(standings)
        if sport == "soccer":
            league_stats["leader_pts"] = leader.get("points", "")
            league_stats["leader_record"] = f'{leader.get("wins","0")}G {leader.get("ties","0")}E {leader.get("losses","0")}P'
        else:
            league_stats["leader_record"] = f'{leader.get("wins","0")}-{leader.get("losses","0")}'
        # Best streak
        best_streak = ""
        best_streak_team = ""
        for t in standings:
            s = str(t.get("streak", ""))
            if s.startswith("W"):
                try:
                    n = int(s[1:])
                    if not best_streak or n > int(best_streak[1:]):
                        best_streak = s
                        best_streak_team = t.get("team_short", t.get("team_name", ""))
                except ValueError:
                    pass
        if best_streak:
            league_stats["best_streak"] = best_streak
            league_stats["best_streak_team"] = best_streak_team

    # NFL-specific: Power Rankings & Picks
    power_rankings = []
    nfl_picks = []
    if league_slug == "nfl":
        try:
            pr_task = generate_nfl_power_rankings()
            pk_task = generate_nfl_picks(upcoming_games, standings)
            pr_res, pk_res = await asyncio.gather(pr_task, pk_task, return_exceptions=True)
            if not isinstance(pr_res, Exception):
                power_rankings = pr_res
            if not isinstance(pk_res, Exception):
                nfl_picks = pk_res
        except Exception:
            pass

    return templates.TemplateResponse(
        request, "league.html", context={
            "league_slug": league_slug,
            "league_name": display_name,
            "emoji": emoji,
            "sport": sport,
            "games": games,
            "total_games": len(games),
            "standings": standings,
            "league_stats": league_stats,
            "league_teams": league_teams,
            "recent_results": recent_results,
            "upcoming_games": upcoming_games,
            "league_channels_today": league_channels_today[:8],
            "default_channels": default_channels,
            "league_leaders": league_leaders,
            "league_seo_extra": LEAGUE_SEO_EXTRA.get(league_slug),
            "power_rankings": power_rankings,
            "nfl_picks": nfl_picks,
            "upcoming_events": upcoming_events,
            "recent_events": recent_events,
            "event_faq": event_faq,
            "event_kind": _ev_kind,
            "is_race": bool(_ev_kind) and _ev_kind in _RACE_KINDS,
            "motor_links": [(k, _EVENT_META[k]["org"], _EVENT_META[k]["league_slug"]) for k in _RACE_KINDS if k != _ev_kind],
            "event_seo": event_seo,
        }
    )


# ── Sport-Today Pages (SEO) ────────────────────────────────

# Config: slug → (sport_key, display_name, emoji, seo_channels_mx, seo_channels_us, seo_streaming)
SPORT_TODAY_PAGES = {
    "futbol-hoy": (
        "soccer", "Futbol", "⚽",
        "Los canales de futbol en Mexico cambian segun la liga y el partido. Consulta arriba las opciones confirmadas para cada juego de hoy.",
        "En Estados Unidos los derechos de futbol estan repartidos entre varias cadenas y plataformas. Revisa cada partido para ver el canal confirmado.",
        "DondeVer actualiza los canales de cada partido en tiempo real. Arriba puedes ver las opciones de streaming confirmadas para los juegos de hoy.",
    ),
    "futbol-americano-hoy": (
        "football", "Futbol Americano", "🏈",
        "Los canales de NFL en Mexico varian por juego y semana. Consulta arriba las transmisiones confirmadas para cada partido de hoy.",
        "En Estados Unidos la NFL reparte sus juegos entre varias cadenas y plataformas de streaming. Revisa cada partido para ver donde se transmite.",
        "DondeVer muestra el canal confirmado de cada juego. Arriba puedes ver las opciones de streaming disponibles para los partidos de hoy.",
    ),
    "basquetbol-hoy": (
        "basketball", "Basquetbol", "🏀",
        "Los canales de NBA en Mexico varian por partido. Consulta arriba las transmisiones confirmadas para cada juego de hoy.",
        "En Estados Unidos los derechos de NBA estan repartidos entre varias cadenas. Revisa cada partido para ver el canal confirmado.",
        "DondeVer muestra el canal confirmado de cada juego en tiempo real. Arriba puedes ver las opciones de streaming para los partidos de hoy.",
    ),
    "beisbol-hoy": (
        "baseball", "Beisbol", "⚾",
        "Los canales de MLB en Mexico varian segun el partido. Consulta arriba las transmisiones confirmadas para cada juego de hoy.",
        "En Estados Unidos la MLB reparte sus juegos entre cadenas nacionales y canales regionales por equipo. Revisa cada partido para ver donde se transmite.",
        "DondeVer muestra el canal confirmado de cada juego. Arriba puedes ver las opciones de streaming disponibles para los partidos de hoy.",
    ),
    "hockey-hoy": (
        "hockey", "Hockey", "🏒",
        "La NHL tiene cobertura limitada en Mexico. Consulta arriba los canales confirmados para cada juego de hoy.",
        "En Estados Unidos los juegos de NHL se transmiten por diferentes cadenas segun el partido. Revisa cada juego para ver el canal confirmado.",
        "DondeVer muestra el canal de cada juego en tiempo real. Arriba puedes ver las opciones de streaming para los partidos de hoy.",
    ),
}


async def _render_sport_today(request: Request, sport_slug: str):
    """
    Sport-specific landing page — /futbol-hoy, /beisbol-hoy, etc.
    Always has content for Google to index (SEO text even with no games).
    """
    sport_key, sport_display, sport_emoji, seo_mx, seo_us, seo_stream = SPORT_TODAY_PAGES[sport_slug]

    # Get today's games filtered by sport
    games = await get_todays_games(sport_filter=sport_key)

    # Group games by league for organized display
    games_by_league = {}
    for g in games:
        ls = g["league_slug"]
        if ls not in games_by_league:
            league_info = ALL_LEAGUES.get(ls, (sport_key, ls, ls, ""))
            games_by_league[ls] = {
                "name": league_info[2],
                "emoji": league_info[3],
                "games": [],
            }
        games_by_league[ls]["games"].append(g)

    # Related teams for this sport (from POPULAR_TEAMS)
    related_teams = {
        slug: info for slug, info in POPULAR_TEAMS.items()
        if info.get("sport") == sport_key
    }

    # Leagues for this sport
    sport_leagues = {
        slug: {"name": info[2], "emoji": info[3]}
        for slug, info in ALL_LEAGUES.items()
        if info[0] == sport_key
    }

    total_games = len(games)

    return templates.TemplateResponse(
        request, "sport_today.html", context={
            "page_slug": sport_slug,
            "page_title": f"Donde ver {sport_display} en vivo hoy - Canales Mexico y USA",
            "meta_description": f"{total_games} juegos de {sport_display} en vivo hoy. Horarios y canales de TV para Mexico y Estados Unidos. TUDN, ESPN, Fox Sports y mas.",
            "hero_text": f"Todos los juegos de {sport_display} de hoy con horarios, canales de TV y opciones de streaming para Mexico y Estados Unidos.",
            "sport_key": sport_key,
            "sport_display": sport_display,
            "sport_emoji": sport_emoji,
            "games": games,
            "games_by_league": games_by_league,
            "total_games": total_games,
            "related_teams": related_teams,
            "sport_leagues": sport_leagues,
            "seo_channels_mx": seo_mx,
            "seo_channels_us": seo_us,
            "seo_streaming": seo_stream,
        }
    )


# Explicit routes for each sport page (avoids catch-all /{slug} conflicts)
@app.get("/futbol-hoy", response_class=HTMLResponse)
async def futbol_hoy(request: Request):
    return await _render_sport_today(request, "futbol-hoy")

@app.get("/futbol-americano-hoy", response_class=HTMLResponse)
async def futbol_americano_hoy(request: Request):
    return await _render_sport_today(request, "futbol-americano-hoy")

@app.get("/basquetbol-hoy", response_class=HTMLResponse)
async def basquetbol_hoy(request: Request):
    return await _render_sport_today(request, "basquetbol-hoy")

@app.get("/beisbol-hoy", response_class=HTMLResponse)
async def beisbol_hoy(request: Request):
    return await _render_sport_today(request, "beisbol-hoy")

@app.get("/hockey-hoy", response_class=HTMLResponse)
async def hockey_hoy(request: Request):
    return await _render_sport_today(request, "hockey-hoy")


# ── Pronósticos / Apuestas Landing ────────────────────────

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_es_larga(d) -> str:
    """'martes 15 de septiembre de 2026' (sin depender del locale del servidor)."""
    return f"{_DIAS_ES[d.weekday()]} {d.day} de {_MESES_ES[d.month - 1]} de {d.year}"


@app.get("/pronosticos-hoy", response_class=HTMLResponse)
async def pronosticos_hoy(request: Request):
    """SEO landing page for betting queries: pronósticos, momios, picks del día."""
    today = datetime.now(TZ_MX)
    today_str = today.strftime("%Y%m%d")
    all_games = await get_todays_games()

    games_with_odds = []
    for game in all_games:
        if game["status"]["state"] not in ("pre", "in"):
            continue
        try:
            league_slug = game.get("league_slug", "")
            odds_list = await fetch_odds(league_slug)
            odds = match_odds_to_game(game, odds_list)
            if odds:
                game["prediction"] = _quick_prediction_from_odds(game)
                game["odds"] = odds
                game["preview"] = generate_match_preview(game)
                games_with_odds.append(game)
        except Exception:
            pass

    upcoming = sum(1 for g in all_games if g.get("status", {}).get("state") in ("pre", "in"))
    return templates.TemplateResponse(request, "pronosticos.html", context={
        "games": games_with_odds,
        "today": today,
        "today_es": _fecha_es_larga(today),
        "total_games": len(all_games),
        "upcoming_games": upcoming,
        "odds_configured": bool(_sports_api_mod.ODDS_API_KEY),
    })


@app.get("/api/internal/odds-diag")
async def odds_diag(request: Request):
    """Diagnóstico del feed de cuotas (the-odds-api): key, cuota restante, último error."""
    token = request.query_params.get("token", "")
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    all_games = await get_todays_games()
    leagues_today = sorted({g.get("league_slug", "") for g in all_games if g.get("status", {}).get("state") in ("pre", "in")})
    mapped = [l for l in leagues_today if l in _sports_api_mod.ODDS_SPORT_MAP]
    sample = {}
    for slug in mapped[:6]:
        odds_list = await fetch_odds(slug)
        matched = 0
        for g in all_games:
            if g.get("league_slug") == slug and g.get("status", {}).get("state") in ("pre", "in"):
                if match_odds_to_game(g, odds_list):
                    matched += 1
        sample[slug] = {"events_from_api": len(odds_list), "matched_games": matched}
    return {"diag": _sports_api_mod.ODDS_DIAG, "leagues_today": leagues_today, "leagues_with_odds_map": mapped,
            "sample": sample, "cache_keys": [k for k in _sports_api_mod._odds_cache.keys()]}


@app.get("/momios-hoy", response_class=HTMLResponse)
async def momios_hoy_redirect(request: Request):
    """Redirect /momios-hoy → /pronosticos-hoy for SEO consolidation."""
    return RedirectResponse(url="/pronosticos-hoy", status_code=301)


@app.get("/apuestas-deportivas-hoy", response_class=HTMLResponse)
async def apuestas_hoy_redirect(request: Request):
    """Redirect /apuestas-deportivas-hoy → /pronosticos-hoy."""
    return RedirectResponse(url="/pronosticos-hoy", status_code=301)


@app.get("/nfl-hoy")
async def nfl_hoy_redirect():
    """Redirect /nfl-hoy → /liga/nfl for SEO consolidation."""
    return RedirectResponse(url="/liga/nfl", status_code=301)


@app.get("/nfl-en-vivo")
async def nfl_en_vivo_redirect():
    """Redirect /nfl-en-vivo → /liga/nfl for SEO consolidation."""
    return RedirectResponse(url="/liga/nfl", status_code=301)


# ── LMP SEO redirects ──
@app.get("/lmp-hoy")
async def lmp_hoy_redirect():
    return RedirectResponse(url="/liga/lmp", status_code=301)

@app.get("/lmp-en-vivo")
async def lmp_en_vivo_redirect():
    return RedirectResponse(url="/liga/lmp", status_code=301)

@app.get("/donde-ver-lmp")
async def donde_ver_lmp_redirect():
    return RedirectResponse(url="/liga/lmp", status_code=301)

@app.get("/liga-mexicana-del-pacifico")
async def liga_pacifico_redirect():
    return RedirectResponse(url="/liga/lmp", status_code=301)


# ── LMB SEO redirects ──
@app.get("/lmb-hoy")
async def lmb_hoy_redirect():
    return RedirectResponse(url="/liga/lmb", status_code=301)

@app.get("/lmb-en-vivo")
async def lmb_en_vivo_redirect():
    return RedirectResponse(url="/liga/lmb", status_code=301)

@app.get("/donde-ver-lmb")
async def donde_ver_lmb_redirect():
    return RedirectResponse(url="/liga/lmb", status_code=301)

@app.get("/liga-mexicana-de-beisbol")
async def liga_beisbol_redirect():
    return RedirectResponse(url="/liga/lmb", status_code=301)

# ── Liga MX Femenil SEO redirects ──
@app.get("/liga-mx-femenil-hoy")
async def femenil_hoy_redirect():
    return RedirectResponse(url="/liga/liga-mx-femenil", status_code=301)

@app.get("/liga-mx-femenil-en-vivo")
async def femenil_en_vivo_redirect():
    return RedirectResponse(url="/liga/liga-mx-femenil", status_code=301)

@app.get("/donde-ver-liga-mx-femenil")
async def donde_ver_femenil_redirect():
    return RedirectResponse(url="/liga/liga-mx-femenil", status_code=301)

@app.get("/futbol-femenil-mexico")
async def futbol_femenil_redirect():
    return RedirectResponse(url="/liga/liga-mx-femenil", status_code=301)

# ── LNBP SEO redirects ──
@app.get("/lnbp-hoy")
async def lnbp_hoy_redirect():
    return RedirectResponse(url="/liga/lnbp", status_code=301)

@app.get("/lnbp-en-vivo")
async def lnbp_en_vivo_redirect():
    return RedirectResponse(url="/liga/lnbp", status_code=301)

@app.get("/donde-ver-lnbp")
async def donde_ver_lnbp_redirect():
    return RedirectResponse(url="/liga/lnbp", status_code=301)

@app.get("/basquetbol-mexico")
async def basquetbol_mexico_redirect():
    return RedirectResponse(url="/liga/lnbp", status_code=301)

# ── LVBP SEO redirects ──
@app.get("/lvbp-hoy")
async def lvbp_hoy_redirect():
    return RedirectResponse(url="/liga/lvbp", status_code=301)

@app.get("/lvbp-en-vivo")
async def lvbp_en_vivo_redirect():
    return RedirectResponse(url="/liga/lvbp", status_code=301)

@app.get("/donde-ver-lvbp")
async def donde_ver_lvbp_redirect():
    return RedirectResponse(url="/liga/lvbp", status_code=301)

@app.get("/beisbol-venezuela")
async def beisbol_venezuela_redirect():
    return RedirectResponse(url="/liga/lvbp", status_code=301)

# ── LIDOM SEO redirects ──
@app.get("/lidom-hoy")
async def lidom_hoy_redirect():
    return RedirectResponse(url="/liga/lidom", status_code=301)

@app.get("/lidom-en-vivo")
async def lidom_en_vivo_redirect():
    return RedirectResponse(url="/liga/lidom", status_code=301)

@app.get("/donde-ver-lidom")
async def donde_ver_lidom_redirect():
    return RedirectResponse(url="/liga/lidom", status_code=301)

@app.get("/beisbol-dominicano")
async def beisbol_dominicano_redirect():
    return RedirectResponse(url="/liga/lidom", status_code=301)

# ── F1 SEO redirects ──
@app.get("/f1-hoy")
async def f1_hoy_redirect():
    return RedirectResponse(url="/liga/f1", status_code=301)

@app.get("/f1-en-vivo")
async def f1_en_vivo_redirect():
    return RedirectResponse(url="/liga/f1", status_code=301)

@app.get("/donde-ver-f1")
async def donde_ver_f1_redirect():
    return RedirectResponse(url="/liga/f1", status_code=301)

@app.get("/formula-1-hoy")
async def formula_1_hoy_redirect():
    return RedirectResponse(url="/liga/f1", status_code=301)

@app.get("/donde-ver-formula-1")
async def donde_ver_formula_1_redirect():
    return RedirectResponse(url="/liga/f1", status_code=301)

# ── MotoGP / NASCAR / IndyCar SEO redirects ──
@app.get("/motogp-hoy")
async def motogp_hoy_redirect():
    return RedirectResponse(url="/liga/motogp", status_code=301)

@app.get("/donde-ver-motogp")
async def donde_ver_motogp_redirect():
    return RedirectResponse(url="/liga/motogp", status_code=301)

@app.get("/nascar-hoy")
async def nascar_hoy_redirect():
    return RedirectResponse(url="/liga/nascar", status_code=301)

@app.get("/donde-ver-nascar")
async def donde_ver_nascar_redirect():
    return RedirectResponse(url="/liga/nascar", status_code=301)

@app.get("/indycar-hoy")
async def indycar_hoy_redirect():
    return RedirectResponse(url="/liga/indycar", status_code=301)

@app.get("/donde-ver-indycar")
async def donde_ver_indycar_redirect():
    return RedirectResponse(url="/liga/indycar", status_code=301)

@app.get("/indy-500")
async def indy_500_redirect():
    """'Indy 500' sin año → la edición vigente (o /liga/indycar fuera de temporada)."""
    return await _next_event_redirect("indycar", "/liga/indycar", match="indy-500")

@app.get("/daytona-500")
async def daytona_500_redirect():
    return await _next_event_redirect("nascar", "/liga/nascar", match="daytona")

# ── UFC SEO redirects ──
# Tenis y golf: las formas en que la gente busca estos deportes en español. Van
# como 301 a la página de liga, igual que /f1-hoy o /ufc-hoy, para no partir la
# autoridad entre varias URLs con el mismo contenido.
#
# Se registran una por una a propósito. Una ruta comodín /{slug} habría tapado
# cualquier ruta de un solo segmento declarada más abajo, porque FastAPI resuelve
# por orden de registro: habría roto media web para ahorrar diez líneas.
def _hub_301(path: str, destino: str) -> None:
    async def _redir():
        return RedirectResponse(url=destino, status_code=301)
    app.add_api_route(f"/{path}", _redir, methods=["GET"], include_in_schema=False)


for _p, _d in (
    ("tenis-hoy", "/liga/atp"), ("tenis-en-vivo", "/liga/atp"),
    ("donde-ver-tenis", "/liga/atp"), ("atp-hoy", "/liga/atp"),
    ("wta-hoy", "/liga/wta"), ("tenis-femenil-hoy", "/liga/wta"),
    ("guadalajara-open", "/liga/wta"),
    ("golf-hoy", "/liga/pga"), ("golf-en-vivo", "/liga/pga"),
    ("donde-ver-golf", "/liga/pga"), ("pga-hoy", "/liga/pga"),
):
    _hub_301(_p, _d)


@app.get("/ufc-hoy")
async def ufc_hoy_redirect():
    return RedirectResponse(url="/liga/ufc", status_code=301)

@app.get("/ufc-en-vivo")
async def ufc_en_vivo_redirect():
    return RedirectResponse(url="/liga/ufc", status_code=301)

@app.get("/donde-ver-ufc")
async def donde_ver_ufc_redirect():
    return RedirectResponse(url="/liga/ufc", status_code=301)

@app.get("/pelea-ufc-hoy")
async def pelea_ufc_hoy_redirect():
    return RedirectResponse(url="/liga/ufc", status_code=301)

# ── Boxeo SEO redirects ──
@app.get("/boxeo-hoy")
async def boxeo_hoy_redirect():
    return RedirectResponse(url="/liga/boxeo", status_code=301)

@app.get("/donde-ver-boxeo")
async def donde_ver_boxeo_redirect():
    return RedirectResponse(url="/liga/boxeo", status_code=301)

async def _next_event_redirect(kind: str, fallback: str, match: str = ""):
    """Redirige a la página del próximo evento (evergreen: /f1/proxima-carrera, /ufc/proximo-evento…)."""
    from events_api import fetch_events
    try:
        for e in await fetch_events(kind, days_back=0, days_ahead=200):
            if e["status"] == "post" or e.get("is_minor"):
                continue
            if match and match not in e["slug"]:
                continue
            return RedirectResponse(url=f"/evento/{e['slug']}", status_code=302)
    except Exception:
        pass
    return RedirectResponse(url=fallback, status_code=302)


@app.get("/f1/proxima-carrera")
async def f1_proxima_carrera():
    return await _next_event_redirect("f1", "/liga/f1")

@app.get("/gp-de-mexico")
async def gp_de_mexico():
    """'GP de México' sin año → la edición vigente (o /liga/f1 fuera de temporada)."""
    return await _next_event_redirect("f1", "/liga/f1", match="gp-de-mexico")

@app.get("/motogp/proxima-carrera")
async def motogp_proxima_carrera():
    return await _next_event_redirect("motogp", "/liga/motogp")

@app.get("/nascar/proxima-carrera")
async def nascar_proxima_carrera():
    return await _next_event_redirect("nascar", "/liga/nascar")

@app.get("/indycar/proxima-carrera")
async def indycar_proxima_carrera():
    return await _next_event_redirect("indycar", "/liga/indycar")

@app.get("/ufc/proximo-evento")
async def ufc_proximo_evento():
    return await _next_event_redirect("ufc", "/liga/ufc")

@app.get("/boxeo/proxima-pelea")
async def boxeo_proxima_pelea():
    return await _next_event_redirect("boxing", "/liga/boxeo")

@app.get("/pelea-de-canelo")
async def pelea_canelo_redirect():
    """Canelo es la búsqueda #1 de box en México → su próxima pelea."""
    from events_api import load_boxing_events
    for e in load_boxing_events(days_back=1, days_ahead=200):
        if "canelo" in e["slug"]:
            return RedirectResponse(url=f"/evento/{e['slug']}", status_code=302)
    return RedirectResponse(url="/liga/boxeo", status_code=302)

# ── Copa America SEO redirects ──
@app.get("/copa-america-hoy")
async def copa_america_hoy_redirect():
    return RedirectResponse(url="/guia/donde-ver-copa-america-en-mexico", status_code=301)

@app.get("/donde-ver-copa-america")
async def donde_ver_copa_america_redirect():
    return RedirectResponse(url="/guia/donde-ver-copa-america-en-mexico", status_code=301)

# ── Copa Libertadores SEO redirects ──
@app.get("/libertadores-hoy")
async def libertadores_hoy_redirect():
    return RedirectResponse(url="/guia/donde-ver-copa-libertadores-en-mexico", status_code=301)

@app.get("/donde-ver-libertadores")
async def donde_ver_libertadores_redirect():
    return RedirectResponse(url="/guia/donde-ver-copa-libertadores-en-mexico", status_code=301)

# ── Mundial SEO redirects ──
@app.get("/mundial-2026")
async def mundial_2026_redirect():
    return RedirectResponse(url="/guia/donde-ver-mundial-2026-gratis", status_code=301)

@app.get("/donde-ver-mundial")
async def donde_ver_mundial_redirect():
    return RedirectResponse(url="/guia/donde-ver-mundial-2026-gratis", status_code=301)


# ── API Routes ───────────────────────────────────────────

@app.get("/api/games")
async def api_games(
    date: Optional[str] = None,
    sport: Optional[str] = None,
    league: Optional[str] = None,
    q: Optional[str] = None,
):
    """JSON API for games."""
    if q:
        games = await search_games(q, date_str=date)
    else:
        games = await get_todays_games(
            date_str=date, sport_filter=sport, league_filter=league
        )
    return JSONResponse({"games": games, "count": len(games)})


@app.get("/api/live-scores")
async def api_live_scores():
    """Lightweight endpoint for live score polling — only returns games in progress or recently finished."""
    games = await get_todays_games()
    live = []
    for g in games:
        state = g["status"]["state"]
        if state in ("in", "post"):
            lv = g.get("live") or {}
            live.append({
                "id": g["id"],
                "home_score": g["home"]["score"],
                "away_score": g["away"]["score"],
                "state": state,
                "clock": g["status"].get("clock", ""),
                "period": g["status"].get("period", 0),
                "detail": g["status"].get("detail", ""),
                "live": {k: lv.get(k) for k in ("situation_text", "possession", "period_label", "last_play") if lv.get(k) is not None},
            })
    return JSONResponse({"games": live, "ts": datetime.now(TZ_MX).isoformat()},
                        headers={"Cache-Control": "public, max-age=15"})


@app.get("/api/live/{game_id}")
async def api_live_game(game_id: str):
    """Situación completa de un partido (campo NFL, diamante MLB, goles, línea por periodo) para /partido/."""
    games = await get_todays_games()
    g = next((x for x in games if str(x.get("id")) == str(game_id)), None)
    if not g:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({
        "id": g["id"], "state": g["status"]["state"], "detail": g["status"].get("detail", ""),
        "home": {"name": g["home"]["name"], "short": g["home"].get("short", ""), "score": g["home"]["score"]},
        "away": {"name": g["away"]["name"], "short": g["away"].get("short", ""), "score": g["away"]["score"]},
        "live": g.get("live") or {}, "ts": datetime.now(TZ_MX).isoformat(),
    }, headers={"Cache-Control": "public, max-age=15"})


@app.get("/api/instagram-image")
async def api_instagram_image(date: Optional[str] = None):
    """Generate Instagram daily image and return public URL.
    The image is saved to /static/instagram/ so it's publicly accessible
    (required by Meta Content Publishing API).
    """
    from generate_instagram import (
        get_todays_games as ig_get_games,
        pick_best_games, generate_image,
    )

    now = datetime.now(TZ_MX)
    if date:
        date_str = date.replace("-", "")
    else:
        date_str = now.strftime("%Y%m%d")

    date_nice = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # Fetch real games
    games = await ig_get_games(date_str)
    if not games:
        return JSONResponse({"error": "No games found", "date": date_nice}, status_code=404)

    selected = pick_best_games(games, 7)

    # Generate image with Playwright (HTML → PNG)
    filename = f"juegos_{date_nice}.png"
    static_path = os.path.join("static", "instagram", filename)
    os.makedirs(os.path.dirname(static_path), exist_ok=True)
    await generate_image(selected, date_str, False, static_path)

    public_url = f"{APP_URL}/static/instagram/{filename}"

    # Build caption
    lines = [f"⚽🏀⚾ Juegos de hoy — {date_nice}\n"]
    for g in selected:
        emoji = g.get("emoji", "⚽")
        ch = g.get("channel", "")
        lines.append(f"{g['time']} {g['away']['name']} vs {g['home']['name']} — {ch}")
    lines.append("\n📺 Todos los horarios y canales en DondeVer.app")
    lines.append("\n#DondeVer #DeportesEnVivo #LigaMX #MLB #NBA #NFL #FutbolMexicano #DeportesHoy")
    caption = "\n".join(lines)

    return JSONResponse({
        "image_url": public_url,
        "filename": filename,
        "date": date_nice,
        "games_count": len(selected),
        "caption": caption,
        "games": [
            {"time": g["time"], "league": g["league"],
             "away": g["away"]["name"], "home": g["home"]["name"],
             "channel": g.get("channel", "")}
            for g in selected
        ],
    })


@app.get("/api/leagues")
async def api_leagues():
    """List available leagues."""
    return JSONResponse({
        "leagues": [
            {"slug": slug, "sport": sport, "league": league, "name": name, "emoji": emoji}
            for slug, (sport, league, name, emoji) in LEAGUES.items()
        ]
    })


@app.get("/api/team/{team_slug}")
async def api_team_quick(team_slug: str):
    """
    Rich team info for the smart search panel — mini team page.
    Returns: stats, next/live games, upcoming list, last result, shop products.
    """
    from config import HOME_LEFT_SPORTS, TEAM_SHOP_MELI, MELI_AFF_PARAM
    from sports_api import TEAM_LEAGUE_MAP

    team_info = POPULAR_TEAMS.get(team_slug)
    if not team_info:
        return JSONResponse({"found": False})

    team_name = team_info["name"]
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))

    # Fetch games and stats in parallel
    games_task = search_games(search_term)
    stats_task = get_team_stats(team_slug)

    games, stats = await asyncio.gather(games_task, stats_task, return_exceptions=True)
    if isinstance(games, Exception): games = []
    if isinstance(stats, Exception): stats = {}

    # Find team logo
    team_logo = ""
    team_league = team_info.get("league", "")
    for g in games:
        if search_term.lower() in g["home"]["name"].lower():
            team_logo = g["home"].get("logo", "")
            if not team_league: team_league = g.get("league_name", "")
            break
        elif search_term.lower() in g["away"]["name"].lower():
            team_logo = g["away"].get("logo", "")
            if not team_league: team_league = g.get("league_name", "")
            break

    # ── Stats block (mirrors team page) ──
    stats_block = {}
    if stats:
        sport_type = stats.get("sport_type", "")
        w = stats.get("wins", "")
        l = stats.get("losses", "")
        t = stats.get("ties", "")
        stats_block = {
            "rank": stats.get("rank", ""),
            "wins": w, "losses": l, "ties": t,
            "points": stats.get("points", ""),
            "goals_for": stats.get("goals_for", ""),
            "goals_against": stats.get("goals_against", ""),
            "goal_diff": stats.get("goal_diff", ""),
            "games_played": stats.get("games_played", ""),
            "win_pct": stats.get("win_pct", ""),
            "streak": stats.get("streak", ""),
            "sport_type": sport_type,
        }
        # Build G-E-P or W-L record string
        if sport_type == "soccer" and w and l:
            stats_block["record_display"] = f"{w}-{t}-{l}" if t else f"{w}-{l}"
        elif w and l:
            stats_block["record_display"] = f"{w}-{l}"
        else:
            stats_block["record_display"] = ""

    # ── Games: upcoming, live, completed ──
    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    live_list = [g for g in games if g["status"]["state"] == "in"]
    completed = [g for g in games if g["status"]["state"] == "post"]

    def _format_game(g, include_odds=False):
        sport = g.get("sport", "")
        home_left = sport in HOME_LEFT_SPORTS
        first = g["home"]["name"] if home_left else g["away"]["name"]
        second = g["away"]["name"] if home_left else g["home"]["name"]
        channels = [b["channel"] for b in g.get("broadcasts", [])[:4]]
        result = {
            "id": g["id"],
            "first": first, "second": second,
            "first_logo": g["home"].get("logo", "") if home_left else g["away"].get("logo", ""),
            "second_logo": g["away"].get("logo", "") if home_left else g["home"].get("logo", ""),
            "date": g["date"],
            "time_mx": format_mx_time(g["date"]),
            "league": g.get("league_name", ""),
            "channels": channels,
            "state": g["status"]["state"],
            "status_display": g["status"].get("display", ""),
        }
        # Scores for live/completed
        if g["status"]["state"] in ("in", "post"):
            result["score_first"] = g["home"].get("score", "") if home_left else g["away"].get("score", "")
            result["score_second"] = g["away"].get("score", "") if home_left else g["home"].get("score", "")
        return result

    # Next game with odds
    next_game = None
    if upcoming:
        next_game = _format_game(upcoming[0])
        try:
            ls = upcoming[0].get("league_slug", "")
            odds_list = await fetch_odds(ls)
            next_game["odds"] = match_odds_to_game(upcoming[0], odds_list)
        except Exception:
            next_game["odds"] = None

    # More upcoming (up to 3 total)
    upcoming_list = [_format_game(g) for g in upcoming[1:3]]

    # Live game
    live_game = _format_game(live_list[0]) if live_list else None

    # Last result
    last_result = _format_game(completed[0]) if completed else None

    # ── Shop products ──
    shop = TEAM_SHOP.get(team_slug, {})
    amz_tag = AFFILIATES.get("amazon", "dondever0f-20")
    products = []
    if shop.get("jersey"):
        j = shop["jersey"]
        products.append({
            "type": "jersey", "name": j.get("name", "Jersey"),
            "brand": j.get("brand", ""),
            "img": j.get("img", ""),
            "url": f"https://www.amazon.com/dp/{j['asin']}?tag={amz_tag}",
            "store": "Amazon",
        })
    if shop.get("gorra"):
        g = shop["gorra"]
        products.append({
            "type": "gorra", "name": g.get("name", "Gorra"),
            "brand": g.get("brand", ""),
            "img": g.get("img", ""),
            "url": f"https://www.amazon.com/dp/{g['asin']}?tag={amz_tag}",
            "store": "Amazon",
        })
    # MercadoLibre curated link
    meli_curated = TEAM_SHOP_MELI.get(team_slug)
    if meli_curated:
        products.append({
            "type": "meli", "name": f"Jerseys en MercadoLibre",
            "brand": "", "img": "",
            "url": f"{meli_curated}{MELI_AFF_PARAM}",
            "store": "MercadoLibre",
        })

    return JSONResponse({
        "found": True,
        "slug": team_slug,
        "name": team_name,
        "logo": team_logo,
        "league": team_league,
        "stats": stats_block,
        "next_game": next_game,
        "upcoming": upcoming_list,
        "live_game": live_game,
        "last_result": last_result,
        "products": products,
        "has_shop": bool(shop),
    })


# ── Mis Equipos API ─────────────────────────────────────

@app.get("/api/mis-equipos")
async def api_mis_equipos(teams: str = Query("", description="Comma-separated team slugs")):
    """
    Return today's games + upcoming 7-day games for a list of followed teams.
    Used by the "Mis equipos" section on the homepage and /mis-equipos page.
    """
    from sports_api import TEAM_LEAGUE_MAP, get_upcoming_league_games
    slugs = [s.strip() for s in teams.split(",") if s.strip()]
    if not slugs:
        return JSONResponse({"ok": False, "error": "no_teams"}, status_code=400)
    if len(slugs) > 30:
        slugs = slugs[:30]

    # Resolve team info and search terms
    team_map = {}  # slug -> {name, search_term, sport, league_key}
    for slug in slugs:
        info = POPULAR_TEAMS.get(slug)
        if not info:
            continue
        search_term = TEAM_ALIASES.get(slug.replace("-", " "), slug.replace("-", " "))
        league_info = TEAM_LEAGUE_MAP.get(slug)
        team_map[slug] = {
            "name": info["name"],
            "search_term": search_term.lower(),
            "sport": league_info[0] if league_info else "",
            "league_key": league_info[1] if league_info else "",
            "league_name": info.get("league", ""),
            "logo": info.get("logo", ""),
        }

    if not team_map:
        return JSONResponse({"ok": True, "teams": [], "today": [], "upcoming": []})

    # 1) Get today's games (one fetch, shared for all teams)
    all_today = await get_todays_games()
    today_games = []
    seen_today = set()
    for g in all_today:
        searchable = f"{g['home']['name']} {g['away']['name']}".lower()
        for slug, tm in team_map.items():
            if tm["search_term"] in searchable and g["id"] not in seen_today:
                seen_today.add(g["id"])
                sport = g.get("sport", "")
                home_left = sport in ("soccer", "boxing", "mma")
                first = g["home"] if home_left else g["away"]
                second = g["away"] if home_left else g["home"]
                channels = [b["channel"] for b in g.get("broadcasts", [])[:4]]
                channels_str = ", ".join(channels) if channels else ""
                today_games.append({
                    "id": g["id"],
                    "team_slug": slug,
                    "home_name": g["home"]["name"],
                    "away_name": g["away"]["name"],
                    "home_logo": g["home"].get("logo", ""),
                    "away_logo": g["away"].get("logo", ""),
                    "date": g["date"],
                    "time_mx": format_mx_time(g["date"]),
                    "state": g["status"]["state"],
                    "status_display": g["status"].get("display", ""),
                    "status_detail": g["status"].get("detail", ""),
                    "score_home": g["home"].get("score", "") if g["status"]["state"] != "pre" else "",
                    "score_away": g["away"].get("score", "") if g["status"]["state"] != "pre" else "",
                    "league_name": g.get("league_name", ""),
                    "league_slug": g.get("league_slug", ""),
                    "emoji": g.get("emoji", ""),
                    "channels": channels_str,
                    "url": f"/partido/{_make_game_slug(g)}",
                })
                break

    # 2) Get upcoming games (deduplicate by sport/league, then filter)
    unique_leagues = {}
    for slug, tm in team_map.items():
        if tm["sport"] and tm["league_key"]:
            key = f"{tm['sport']}:{tm['league_key']}"
            if key not in unique_leagues:
                unique_leagues[key] = {"sport": tm["sport"], "league": tm["league_key"], "slugs": []}
            unique_leagues[key]["slugs"].append(slug)

    upcoming_games = []
    if unique_leagues:
        async def _fetch_upcoming(sport, league):
            try:
                return await get_upcoming_league_games(sport, league, days=7, limit=50)
            except Exception:
                return []

        league_results = await asyncio.gather(*[
            _fetch_upcoming(v["sport"], v["league"])
            for v in unique_leagues.values()
        ])

        seen_upcoming = set()
        for (key, league_data), games_list in zip(unique_leagues.items(), league_results):
            for g in games_list:
                g_id = g.get("id", f"{g['home']}-{g['away']}-{g['date']}")
                if g_id in seen_upcoming:
                    continue
                home_lower = g["home"].lower()
                away_lower = g["away"].lower()
                matched_slug = None
                for slug in league_data["slugs"]:
                    st = team_map[slug]["search_term"]
                    if st in home_lower or st in away_lower:
                        matched_slug = slug
                        break
                if not matched_slug:
                    continue
                seen_upcoming.add(g_id)
                # Parse channels
                channels = []
                if g.get("channels"):
                    for ch in g["channels"]:
                        if isinstance(ch, dict):
                            channels.append(ch.get("name", ""))
                        else:
                            channels.append(str(ch))
                upcoming_games.append({
                    "id": g_id,
                    "team_slug": matched_slug,
                    "home_name": g["home"],
                    "away_name": g["away"],
                    "home_logo": g.get("home_logo", ""),
                    "away_logo": g.get("away_logo", ""),
                    "date": g.get("date", ""),
                    "state": "pre",
                    "channels": ", ".join(channels[:4]) if channels else "",
                    "league_name": team_map[matched_slug]["league_name"],
                    "url": "",
                })

    # Sort upcoming by date
    upcoming_games.sort(key=lambda x: x.get("date", ""))

    # Build team summary
    teams_info = []
    for slug, tm in team_map.items():
        teams_info.append({
            "slug": slug,
            "name": tm["name"],
            "league": tm["league_name"],
            "logo": tm.get("logo", ""),
        })

    return JSONResponse({
        "ok": True,
        "teams": teams_info,
        "today": today_games,
        "upcoming": upcoming_games[:50],
    })


# ── WhatsApp Webhook ─────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    Body: str = Form(""),
    From: str = Form(""),
):
    """Twilio WhatsApp webhook — receives messages, responds with game info."""
    logger.info(f"WhatsApp from {From}: {Body!r}")

    try:
        response_text = await handle_whatsapp_message(Body, From)
        if not response_text:
            logger.warning(f"WhatsApp handler returned empty response for body={Body!r}")
            response_text = (
                "Hmm, no entendi. Escribe *ayuda* para ver comandos, "
                "*hoy* para juegos, o *picks* para el pick del dia."
            )
    except Exception as e:
        logger.exception(f"WhatsApp handler crashed on body={Body!r}: {e}")
        response_text = (
            "Tuvimos un problema procesando tu mensaje. Intenta de nuevo o escribe *ayuda*."
        )

    logger.info(f"WhatsApp reply to {From}: {response_text[:100]}...")

    if response_text == "__SENT_DIRECTLY__":
        # Already sent via Meta API — return empty TwiML
        twiml = MessagingResponse()
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    twiml = MessagingResponse()
    twiml.message(response_text)
    return HTMLResponse(content=str(twiml), media_type="application/xml")


@app.get("/webhook/whatsapp")
async def whatsapp_verify():
    """Health check for Twilio webhook verification."""
    return {"status": "ok", "service": "dondever-whatsapp"}


# ── Meta WhatsApp Cloud API Webhook ────────────────────────

WELCOME_BUTTONS = [
    {"id": "btn_hoy", "title": "📺 Juegos de hoy"},
    {"id": "btn_picks", "title": "🎯 Pick del día"},
    {"id": "btn_suscribir", "title": "🔔 Resumen diario"},
]

WELCOME_BODY = (
    "Hola! Soy *DondeVer* - tu guia de deportes en vivo.\n\n"
    "Elige una opcion o escribe el nombre de un equipo:"
)


@app.get("/webhook/meta-whatsapp")
async def meta_whatsapp_verify(request: Request):
    """Meta webhook verification — responds to hub.challenge."""
    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == verify_token and verify_token:
        logger.info("Meta WhatsApp webhook verified successfully")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning(f"Meta webhook verification failed: mode={mode}, token_match={token == verify_token}")
    return PlainTextResponse(content="Forbidden", status_code=403)


_seen_wamids = _TTLCache(maxsize=4000, ttl=900)  # dedupe de webhooks entrantes de Meta


@app.post("/webhook/meta-whatsapp")
async def meta_whatsapp_webhook(request: Request):
    """Meta WhatsApp Cloud API webhook — receives messages and replies.

    CRITICAL: Meta has a ~20s webhook timeout. If we don't respond in time,
    Meta retries (causing duplicates) and may deactivate the webhook entirely.
    Solution: acknowledge immediately, process + reply in background.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok"}

    # Estados de entrega (sent/delivered/read/failed + código de error) → DELIVERY_LOG
    try:
        for st in meta_whatsapp.parse_status_webhook(payload):
            if st.get("status") == "failed" or st.get("errors"):
                logger.warning(f"Meta WA status {st.get('status')} → {st.get('to')}: {st.get('errors')}")
    except Exception:
        pass

    messages = meta_whatsapp.parse_inbound_webhook(payload)
    if not messages:
        return {"status": "ok"}

    async def _process_message(msg: dict):
        """Process a single inbound WhatsApp message in background."""
        from_number = msg["from"]
        body = msg["body"]
        message_id = msg.get("message_id")

        logger.info(f"Meta WA from {from_number}: {body!r}")
        meta_whatsapp.record_inbound(from_number, float(msg.get("timestamp") or 0) or None)

        # Mark as read (blue check)
        if message_id:
            meta_whatsapp.mark_as_read(message_id)

        try:
            response_text = await handle_whatsapp_message(body, from_number)
        except Exception as e:
            logger.exception(f"WhatsApp handler error: {e}")
            response_text = "Tuvimos un problema. Intenta de nuevo o escribe *ayuda*."

        if not response_text:
            response_text = (
                "No entendi. Escribe *ayuda* para ver comandos, "
                "*hoy* para juegos, o *picks* para el pick del dia."
            )

        # Special markers — already handled or need special treatment
        if response_text == "__SENT_DIRECTLY__":
            logger.info(f"Meta WA: picks being sent in background to {from_number}")
            return

        # Special marker: send interactive buttons instead of plain text
        if response_text == "__BUTTONS_WELCOME__":
            result = meta_whatsapp.send_interactive_buttons(
                to=from_number,
                body=WELCOME_BODY,
                buttons=WELCOME_BUTTONS,
                header="DondeVer.app",
                footer="dondever.app — Todo GRATIS",
            )
            logger.info(f"Meta WA buttons to {from_number}: {result.get('ok')}")
        else:
            # Toda respuesta lleva botones (Juegos de hoy / Pick del día / Mis equipos):
            # el usuario ve qué hace el bot sin escribir y cada toque mantiene la ventana de 24 h.
            from whatsapp_bot import buttons_for as _wa_buttons_for
            result = meta_whatsapp.send_text_with_buttons(from_number, response_text, buttons=_wa_buttons_for(body))
            logger.info(f"Meta WA reply to {from_number}: ok={result.get('ok')}")

    # Fire-and-forget: process all messages in background, respond to Meta immediately.
    # Dedupe: Meta entrega el mismo webhook 2 veces (reintento o doble suscripción del WABA)
    # → el usuario recibía la respuesta duplicada. Ignoramos wamid ya vistos (15 min).
    for msg in messages:
        mid = msg.get("message_id") or f"{msg.get('from')}:{msg.get('timestamp')}:{msg.get('body')}"
        if mid in _seen_wamids:
            logger.info(f"Meta WA duplicado ignorado: {mid[:40]}")
            continue
        _seen_wamids[mid] = True
        task = asyncio.create_task(_process_message(msg))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {"status": "ok"}


# ── Email Newsletter Endpoints ──────────────────────────────


@app.post("/api/email-subscribe")
async def api_email_subscribe(request: Request):
    """Alta al correo diario.

    Acepta JSON (el camino normal, por JS) y TAMBIEN un formulario clasico.
    Se reporto que el formulario no tenia respaldo: si el JS fallaba, el campo
    no llevaba atributo name y el navegador mandaba un GET vacio a la portada,
    asi que el visitante creia haberse suscrito y no pasaba nada. Ahora degrada:
    sin JS el navegador hace POST normal y responde una pagina de gracias.
    """
    email_addr = ""
    origen = ""
    es_formulario = False
    try:
        data = await request.json()
        email_addr = (data.get("email") or "").strip().lower()
        origen = (data.get("source") or "").strip()[:40]
    except Exception:
        try:
            form = await request.form()
            email_addr = (form.get("email") or "").strip().lower()
            origen = (form.get("source") or "").strip()[:40]
            es_formulario = True
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    if not email_addr:
        if es_formulario:
            return RedirectResponse(url="/?email=falta#email-subscribe", status_code=303)
        return JSONResponse({"ok": False, "error": "email_required"}, status_code=400)

    result = email_subscribers.subscribe(email_addr)
    if origen:
        logger.info(f"Alta de correo desde: {origen}")
    if not result["success"]:
        if es_formulario:
            return RedirectResponse(url="/?email=error#email-subscribe", status_code=303)
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    if es_formulario:
        return RedirectResponse(
            url=f"/?email={'listo' if result['is_new'] else 'ya'}#email-subscribe",
            status_code=303)
    return {"ok": True, "is_new": result["is_new"]}


@app.get("/email-unsubscribe")
async def email_unsubscribe(request: Request, token: str = ""):
    """Unsubscribe from email newsletter via token link."""
    if not token:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
            "<h2>Link invalido</h2><p>No se pudo cancelar la suscripcion.</p>"
            "</body></html>",
            status_code=400,
        )

    success = email_subscribers.unsubscribe(token=token)
    if success:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
            "<h2>Listo!</h2><p>Te desuscribiste del newsletter de DondeVer.</p>"
            "<p>Si cambias de opinion, suscribete de nuevo en "
            "<a href='https://dondever.app'>dondever.app</a></p>"
            "</body></html>"
        )
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>"
        "<h2>No encontramos tu suscripcion</h2>"
        "<p>Es posible que ya estuvieras desuscrito.</p>"
        "</body></html>"
    )


@app.get("/api/email-subscribers/count")
async def api_email_subscriber_count():
    """Get email subscriber count (for admin)."""
    return {"count": email_subscribers.get_subscriber_count()}


@app.get("/api/internal/whatsapp-subscribers")
async def internal_whatsapp_subscribers(key: str = ""):
    """Internal endpoint for cron jobs to fetch WhatsApp subscriber list."""
    internal_key = os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    from subscribers import get_active_subscribers
    return {"subscribers": get_active_subscribers()}


@app.get("/api/internal/email-subscribers")
async def internal_email_subscribers(key: str = ""):
    """Internal endpoint for cron jobs to fetch email subscriber list."""
    internal_key = os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    return {"subscribers": email_subscribers.get_active_subscribers()}


@app.get("/whatsapp/debug")
async def whatsapp_debug():
    """Diagnostico unificado: Meta Cloud API + Twilio + subscribers."""
    import os as _os
    from subscribers import get_active_subscribers, get_subscriber_count
    from meta_whatsapp import is_configured as meta_ok

    # Meta Cloud API (current system for broadcasts + incoming)
    meta_token = _os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    meta_phone_id = _os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    meta_verify = _os.getenv("WHATSAPP_VERIFY_TOKEN", "")

    # Twilio (legacy — may still handle some incoming)
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")

    info = {
        "meta_cloud_api": {
            "configured": meta_ok(),
            "access_token_set": bool(meta_token),
            "phone_number_id": meta_phone_id[:6] + "..." if meta_phone_id else None,
            "verify_token_set": bool(meta_verify),
            "webhook_url": "https://dondever.app/webhook/meta-whatsapp (GET=verify, POST=messages)",
            "note": "Check Meta Developer Console → WhatsApp → Configuration to verify webhook URL is set",
        },
        "twilio_legacy": {
            "sid_set": bool(sid),
            "sid_prefix": sid[:6] + "..." if sid else None,
            "token_set": bool(twilio_token),
            "whatsapp_number": wa_num,
            "webhook_url": "https://dondever.app/webhook/whatsapp (POST)",
        },
        "subscribers": {"total": 0, "active": 0},
        "last_broadcast": _last_broadcast,
    }
    try:
        info["subscribers"]["total"] = get_subscriber_count()
        info["subscribers"]["active"] = len(get_active_subscribers())
    except Exception as e:
        info["subscribers"]["error"] = str(e)
    return info


@app.get("/api/internal/events-diag")
async def events_diag(token: str = ""):
    """Diagnóstico de fuentes de eventos (UFC/F1/MotoGP/NASCAR/IndyCar). Protegido por ADMIN_TOKEN."""
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    import events_api as _E
    from config import SPORTSDB_KEY as _SK, SPORTSDB_BASE as _SB
    out = {"sportsdb_key_len": len(_SK or ""), "sportsdb_key_prefix": (_SK or "")[:2], "kinds": {}}
    for k in _E.ALL_KINDS:
        try:
            evs = await _E.fetch_events(k, days_back=30, days_ahead=200)
            out["kinds"][k] = {"n": len(evs), "next": [e["slug"] for e in evs if e["status"] != "post"][:3]}
        except Exception as e:
            out["kinds"][k] = {"error": str(e)}
    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=25) as client:
            r = await client.get(f"{_SB}/eventsseason.php", params={"id": _E.SPORTSDB_MOTOGP_ID, "s": str(datetime.now(TZ_MX).year)})
            try:
                body = r.json()
            except Exception:
                body = {}
            out["motogp_raw"] = {"status": r.status_code, "ctype": r.headers.get("content-type", ""),
                                 "n": len((body or {}).get("events") or []), "text_head": r.text[:200],
                                 "sample": [(e.get("strEvent"), e.get("dateEvent")) for e in ((body or {}).get("events") or [])[-3:]]}
    except Exception as e:
        out["motogp_raw"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        _E._events_cache.pop(f"sportsdb:{_E.SPORTSDB_MOTOGP_ID}:{datetime.now(TZ_MX).year}", None)
        raw = await _E._fetch_sportsdb_season(_E.SPORTSDB_MOTOGP_ID, str(datetime.now(TZ_MX).year))
        out["motogp_fetch_fn"] = {"n": len(raw), "grouped": len(_E._group_motogp(raw, datetime.now(timezone.utc)))}
    except Exception as e:
        out["motogp_fetch_fn"] = {"error": f"{type(e).__name__}: {e}"}
    return out


@app.get("/api/internal/whatsapp-diag")
async def whatsapp_diagnostics(key: str = ""):
    """Diagnostic endpoint: subscribe app to WABA + check templates."""
    import os as _os
    internal_key = _os.getenv("INTERNAL_API_KEY", "")
    if not internal_key or key != internal_key:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    token = _os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    waba_id = "1224835083125902"
    results = {}

    async with httpx.AsyncClient(timeout=15) as client:
        # 1. Subscribe app to WABA
        try:
            r = await client.post(
                f"https://graph.facebook.com/v25.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {token}"},
            )
            results["subscribe_app"] = r.json()
        except Exception as e:
            results["subscribe_app_error"] = str(e)

        # 2. Get message templates
        try:
            r = await client.get(
                f"https://graph.facebook.com/v25.0/{waba_id}/message_templates",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name,language,status,category", "limit": 50},
            )
            results["templates"] = r.json()
        except Exception as e:
            results["templates_error"] = str(e)

        # 3. Check WABA details
        try:
            r = await client.get(
                f"https://graph.facebook.com/v25.0/{waba_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name,account_review_status,messaging_limit_tier,on_behalf_of_business_info"},
            )
            results["waba_info"] = r.json()
        except Exception as e:
            results["waba_info_error"] = str(e)

        # 4. Check phone number quality
        phone_id = _os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        if phone_id:
            try:
                r = await client.get(
                    f"https://graph.facebook.com/v25.0/{phone_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "verified_name,quality_rating,display_phone_number,status,messaging_limit_tier"},
                )
                results["phone_info"] = r.json()
            except Exception as e:
                results["phone_info_error"] = str(e)

    return results


@app.post("/api/whatsapp/subscribe")
async def api_whatsapp_subscribe(request: Request):
    """Public endpoint for WhatsApp opt-in from the website."""
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        if not phone:
            return {"ok": False, "error": "Ingresa tu numero de telefono."}
        # Normalize: ensure + prefix, remove spaces
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        # Basic validation: must be 10-15 digits after +
        digits = phone[1:]
        if not digits.isdigit() or len(digits) < 10 or len(digits) > 15:
            return {"ok": False, "error": "Numero invalido. Usa formato +521XXXXXXXXXX"}
        from subscribers import subscribe
        is_new = subscribe(phone)
        logger.info(f"WhatsApp subscribe from web: {phone} (new={is_new})")
        return {"ok": True, "new": is_new}
    except Exception as e:
        logger.error(f"WhatsApp subscribe error: {e}")
        return {"ok": False, "error": "Error interno. Intenta de nuevo."}


# ── User Favorites (by phone) ──────────────────────────────


@app.post("/api/favs/save")
async def api_favs_save(request: Request):
    """Save user favorites linked to WhatsApp phone number."""
    try:
        data = await request.json()
        phone = data.get("phone", "").strip()
        favs = data.get("favs", [])
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)

    if not phone:
        return JSONResponse({"ok": False, "error": "phone_required"}, status_code=400)

    result = user_favs.save_favs(phone, favs)
    if not result["success"]:
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    return {"ok": True, "count": result["count"]}


@app.get("/api/favs/load")
async def api_favs_load(phone: str = ""):
    """Load user favorites by phone number."""
    if not phone:
        return JSONResponse({"ok": False, "error": "phone_required"}, status_code=400)

    result = user_favs.load_favs(phone)
    if not result["success"]:
        return JSONResponse({"ok": False, "error": result.get("error", "failed")}, status_code=400)

    return {"ok": True, "favs": result["favs"], "found": result.get("found", False)}


@app.get("/admin/subscribers")
async def admin_subscribers(token: str = ""):
    """
    Lista detallada de suscriptores. Protegido por ADMIN_TOKEN.
    Uso: https://dondever.app/admin/subscribers?token=TU_TOKEN
    """
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    from subscribers import _load
    data = _load()
    subs = data.get("subscribers", {})
    active = [(p, info) for p, info in subs.items() if info.get("active", True)]
    inactive = [(p, info) for p, info in subs.items() if not info.get("active", True)]

    def mask(phone: str) -> str:
        # Muestra +52155***1234 para privacidad en logs
        if len(phone) > 6:
            return phone[:5] + "***" + phone[-4:]
        return phone

    return {
        "ok": True,
        "total": len(subs),
        "active_count": len(active),
        "inactive_count": len(inactive),
        "active": [
            {
                "phone": mask(p),
                "subscribed_at": info.get("subscribed_at"),
                "last_active": info.get("last_active"),
            }
            for p, info in active
        ],
        "inactive": [
            {"phone": mask(p), "subscribed_at": info.get("subscribed_at")}
            for p, info in inactive
        ],
    }


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, token: str = ""):
    """Dashboard admin con métricas clave de DondeVer."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return HTMLResponse("<h1>Token inválido</h1><p>Usa ?token=TU_ADMIN_TOKEN</p>", status_code=403)

    from subscribers import _load, get_subscriber_count, get_active_subscribers
    from twitter_bot import _tweet_timestamps, _posted_games, MAX_TWEETS_PER_DAY

    # Subscribers
    subs_data = _load()
    all_subs = subs_data.get("subscribers", {})
    active_subs = [(p, i) for p, i in all_subs.items() if i.get("active", True)]
    inactive_subs = [(p, i) for p, i in all_subs.items() if not i.get("active", True)]

    # Today's tweets
    now_ts = __import__("time").time()
    tweets_today = len(_tweet_timestamps)
    tweets_last_hour = sum(1 for t in _tweet_timestamps if t > now_ts - 3600)

    # Games posted today
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games_posted = len(_posted_games.get(today_key, set()))

    # Affiliate clicks
    clicks_7d = get_click_stats(7)
    clicks_today = get_click_stats(1)

    # Group clicks by affiliate and source
    def group_clicks(raw: dict) -> dict:
        by_aff = {}
        by_src = {}
        total = 0
        for k, v in raw.items():
            aff, src = k.split(":", 1) if ":" in k else (k, "unknown")
            by_aff[aff] = by_aff.get(aff, 0) + v
            by_src[src] = by_src.get(src, 0) + v
            total += v
        return {"by_affiliate": by_aff, "by_source": by_src, "total": total}

    clicks_7d_grouped = group_clicks(clicks_7d)
    clicks_today_grouped = group_clicks(clicks_today)

    # Today's games count
    try:
        games = await get_todays_games()
        total_games = len(games)
        live_games = sum(1 for g in games if g["status"]["state"] == "in")
    except Exception:
        total_games = 0
        live_games = 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "active_count": len(active_subs),
        "inactive_count": len(inactive_subs),
        "active_subs": active_subs,
        "tweets_today": tweets_today,
        "tweets_max": MAX_TWEETS_PER_DAY,
        "tweets_last_hour": tweets_last_hour,
        "games_posted": games_posted,
        "total_games": total_games,
        "live_games": live_games,
        "clicks_today": clicks_today_grouped,
        "clicks_7d": clicks_7d_grouped,
        "token": token,
    })


@app.post("/whatsapp/test-send")
async def whatsapp_test_send(to: str):
    """Enviar un mensaje de prueba a un numero via Twilio. ej: /whatsapp/test-send?to=+521XXXXXXXXXX"""
    import os as _os
    from twilio.rest import Client as TwilioClient
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")
    if not sid or not token:
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN no configurados"}
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    try:
        client = TwilioClient(sid, token)
        msg = client.messages.create(
            body="Test de DondeVer.app — si recibes este mensaje, el webhook de salida funciona. Responde *suscribir* para probar el flujo de entrada.",
            from_=wa_num,
            to=to,
        )
        return {"ok": True, "message_sid": msg.sid, "status": msg.status, "to": to, "from": wa_num}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


# (duplicate /whatsapp/debug removed — unified endpoint is above)


# Store last broadcast result for diagnostics + deduplication
_last_broadcast = {"ran_at": None, "result": None, "error": None, "date": None}
_PROCESS_STARTED = datetime.now(TZ_MX).isoformat(timespec="seconds")  # para detectar reinicios entre envíos
# Keep strong reference to background tasks so GC doesn't collect them mid-execution
_background_tasks: set = set()
_catchup_checked = {"date": None}


def _broadcast_already_ran_today() -> bool:
    """Check if broadcast already ran today (MX time) to prevent duplicates."""
    today = datetime.now(TZ_MX).strftime("%Y-%m-%d")
    return _last_broadcast.get("date") == today and _last_broadcast.get("result") is not None


async def _maybe_catchup_broadcast():
    """Auto-trigger broadcast if server woke up after 9 AM MX and hasn't sent today.
    Called once per day on first request after 9 AM MX (15:00 UTC)."""
    now = datetime.now(TZ_MX)
    today = now.strftime("%Y-%m-%d")
    if _catchup_checked.get("date") == today:
        return  # Already checked today
    _catchup_checked["date"] = today
    if now.hour < 9:
        return  # Too early
    if _broadcast_already_ran_today():
        return  # Already ran
    logger.info("Catch-up broadcast: server woke after 9 AM MX, triggering broadcast")
    try:
        from send_whatsapp_daily import send_daily_broadcast
        result = await send_daily_broadcast()
        _last_broadcast["ran_at"] = now.isoformat()
        _last_broadcast["date"] = today
        _last_broadcast["result"] = result
        _last_broadcast["error"] = None
        _last_broadcast["source"] = "catchup"
        logger.info(f"Catch-up broadcast completed: {result}")
    except Exception as e:
        _last_broadcast["ran_at"] = now.isoformat()
        _last_broadcast["result"] = None
        _last_broadcast["error"] = str(e)
        _last_broadcast["source"] = "catchup"
        logger.error(f"Catch-up broadcast FAILED: {e}")


@app.api_route("/whatsapp/broadcast-now", methods=["GET", "POST"])
async def whatsapp_broadcast_now(
    request: Request, token: str = "", force: str = "", sync: str = ""
):
    """Disparar el broadcast diario ahora mismo a todos los suscriptores.
    Acepta GET y POST para compatibilidad con cron externos (cron-job.org, etc.).
    Usa Meta Cloud API (send_whatsapp_daily.py).
    Añade &force=1 para ignorar la deduplicación diaria.
    Por defecto corre en background (no timeout). Añade &sync=1 para esperar."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    # Deduplication: skip if already ran today (unless force=1)
    if _broadcast_already_ran_today() and force != "1":
        return {
            "ok": True,
            "skipped": True,
            "reason": f"Broadcast already ran today at {_last_broadcast['ran_at']}",
            "result": _last_broadcast["result"],
        }

    async def _run_broadcast():
        try:
            from send_whatsapp_daily import send_daily_broadcast
            result = await send_daily_broadcast()
            now = datetime.now(TZ_MX)
            _last_broadcast["ran_at"] = now.isoformat()
            _last_broadcast["date"] = now.strftime("%Y-%m-%d")
            _last_broadcast["result"] = result
            _last_broadcast["error"] = None
            _last_broadcast["source"] = "endpoint"
            logger.info(f"Broadcast endpoint completed: {result}")
        except Exception as e:
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = None
            _last_broadcast["error"] = str(e)
            logger.error(f"Broadcast endpoint FAILED: {e}")

    if sync == "1":
        # Synchronous — wait for result (may timeout on slow hosts)
        await _run_broadcast()
        return {"ok": True, "result": _last_broadcast["result"], "error": _last_broadcast["error"]}
    else:
        # Fire-and-forget — respond immediately, run in background
        # Keep strong reference so GC doesn't collect the task mid-execution
        task = asyncio.create_task(_run_broadcast())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {
            "ok": True,
            "queued": True,
            "message": "Broadcast started in background. Check /whatsapp/broadcast-status for results.",
        }


@app.get("/whatsapp/test-send")
async def whatsapp_test_send(token: str = "", to: str = "", mode: str = "template",
                              tpl: str = "dondever_picks_diarios", lang: str = "en"):
    """Enviar UN mensaje de prueba a un número específico.
    mode=template (default): usa template (configurable via tpl= y lang=)
    mode=hello: usa hello_world template (no params, en_US)
    mode=freeform: usa mensaje de texto libre (solo funciona en ventana 24h)
    mode=both: prueba template + freeform
    mode=all: prueba picks_diarios + hello_world + freeform"""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    if not to:
        return {"ok": False, "error": "Falta parametro 'to' con numero (ej: 528118001161)"}
    try:
        from meta_whatsapp import send_template, send_text, _normalize_to
        normalized = _normalize_to(to)
        results = {}

        if mode in ("template", "both", "all"):
            results["template"] = send_template(
                to,
                template_name=tpl,
                language=lang,
                components=[{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "⚾ Test mensaje de prueba. Responde VER. https://dondever.app/"},
                    ],
                }],
            )

        if mode in ("hello", "all"):
            results["hello_world"] = send_template(
                to,
                template_name="hello_world",
                language="en_US",
            )

        if mode in ("freeform", "both", "all"):
            results["freeform"] = send_text(
                to,
                "🏆 *Test DondeVer* — Este es un mensaje de prueba.\n\n📱 dondever.app",
            )

        return {
            "ok": True,
            "original_number": to,
            "normalized": normalized,
            "mode": mode,
            "tpl": tpl,
            "lang": lang,
            "results": results,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/whatsapp/debug-waba")
async def whatsapp_debug_waba(token: str = ""):
    """Debug: show phone number info, its WABA, and templates on that WABA."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    import httpx as _httpx
    wa_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    business_id = "879031608444232"
    results = {}
    try:
        with _httpx.Client(timeout=15) as c:
            # 1. Phone number details
            r = c.get(f"https://graph.facebook.com/v25.0/{phone_id}",
                      params={"fields": "id,display_phone_number,verified_name,quality_rating,name_status",
                              "access_token": wa_token})
            results["phone_number"] = {"phone_id": phone_id, "data": r.json()}

            # 2. List WABAs owned by business
            r = c.get(f"https://graph.facebook.com/v25.0/{business_id}/owned_whatsapp_business_accounts",
                      params={"fields": "id,name,currency,message_template_namespace",
                              "access_token": wa_token})
            wabas = r.json()
            results["business_wabas"] = wabas

            # 3. For each WABA, list phone numbers
            for waba in (wabas.get("data") or []):
                waba_id = waba["id"]
                r = c.get(f"https://graph.facebook.com/v25.0/{waba_id}/phone_numbers",
                          params={"fields": "id,display_phone_number,verified_name,quality_rating",
                                  "access_token": wa_token})
                waba[f"phone_numbers"] = r.json()
                # Also list templates on this WABA
                r = c.get(f"https://graph.facebook.com/v25.0/{waba_id}/message_templates",
                          params={"fields": "name,language,status", "limit": "20",
                                  "access_token": wa_token})
                waba["templates"] = r.json()

            return {"ok": True, "results": results}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc(), "partial": results}


@app.get("/whatsapp/list-templates")
async def whatsapp_list_templates(token: str = ""):
    """Debug: list all WABA templates with their language codes."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    import httpx as _httpx
    wa_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    waba_id = "1224835083125902"  # Distribuciones Arobe (real WABA)
    url = f"https://graph.facebook.com/v25.0/{waba_id}/message_templates"
    params = {"fields": "name,language,status,category,components", "limit": "50", "access_token": wa_token}
    try:
        with _httpx.Client(timeout=15) as c:
            resp = c.get(url, params=params)
            return {"ok": True, "status": resp.status_code, "data": resp.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.api_route("/whatsapp/broadcast-preview", methods=["GET"])
async def whatsapp_broadcast_preview():
    """Preview del mensaje diario sin enviarlo."""
    try:
        from send_whatsapp_daily import compose_daily_message
        msg = await compose_daily_message()
        if msg:
            return {"ok": True, "message": msg, "chars": len(msg)}
        return {"ok": True, "message": None, "note": "No games today"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/whatsapp/broadcast-status")
async def whatsapp_broadcast_status():
    """Ver el resultado del ultimo broadcast (sin auth)."""
    from subscribers import get_active_subscribers
    from meta_whatsapp import is_configured as wa_configured
    active = get_active_subscribers()
    try:
        from send_whatsapp_daily import sent_today as _wa_sent_today, _LEDGER_FILE as _wa_ledger
        ledger = {"sent_today": len(_wa_sent_today()), "file": str(_wa_ledger)}
    except Exception as e:
        ledger = {"error": str(e)}
    return {
        "last_broadcast": _last_broadcast,
        "ledger": ledger,
        "process_started": _PROCESS_STARTED,
        "active_subscribers": len(active),
        "meta_whatsapp_configured": wa_configured(),
        "hint": "Usa Meta Cloud API. Set WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID en env vars."
    }


@app.get("/whatsapp/create-utility-template")
async def whatsapp_create_utility_template(token: str = ""):
    """Crea 'dondever_resumen_diario' (UTILITY) en el WABA. Ver estado en /whatsapp/list-templates."""
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    return meta_whatsapp.create_daily_utility_template()


@app.get("/whatsapp/window-status")
async def whatsapp_window_status(token: str = ""):
    """Qué suscriptores están dentro de la ventana de 24 h (freeform) y qué plantilla se usaría."""
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    from subscribers import get_active_subscribers
    subs = [s.get("phone", s) if isinstance(s, dict) else s for s in get_active_subscribers()]
    name, lang, cat = meta_whatsapp.pick_daily_template()
    return {"template": {"name": name, "lang": lang, "category": cat},
            "in_window": [p for p in subs if meta_whatsapp.in_24h_window(str(p))],
            "outside_window": [p for p in subs if not meta_whatsapp.in_24h_window(str(p))],
            "templates": [{k: t.get(k) for k in ("name", "language", "status", "category")} for t in meta_whatsapp.list_templates()]}


@app.get("/whatsapp/delivery-log")
async def whatsapp_delivery_log(token: str = "", to: str = ""):
    """Estados de entrega que Meta reporta por webhook (sent → delivered → read, o failed + error).
    Úsalo justo después de /whatsapp/test-send para ver por qué no llega una plantilla."""
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    rows = list(meta_whatsapp.DELIVERY_LOG)
    if to:
        rows = [r for r in rows if (r.get("to") or "").endswith(to[-10:])]
    # Agrupar por message id: último estado + errores
    by_id: dict = {}
    for r in rows:
        cur = by_id.setdefault(r["id"], {"id": r["id"], "to": r["to"], "states": [], "errors": [], "category": r.get("category")})
        cur["states"].append(f"{r['status']}@{r['ts']}")
        cur["errors"] += r.get("errors") or []
    return {"ok": True, "n": len(rows), "messages": list(by_id.values())[:50],
            "hint": "Si la plantilla queda en 'sent' sin 'delivered' o sale 'failed', el código de error dice la causa "
                    "(130472 = Meta retiene marketing a ese usuario por experimento; 131049 = límite de marketing por usuario; "
                    "131026 = no entregable; 131047 = fuera de ventana 24h sin plantilla)."}


@app.get("/whatsapp/check-delivery")
async def whatsapp_check_delivery():
    """Verifica el estado de entrega de los ultimos mensajes enviados por Twilio."""
    from whatsapp_broadcast import get_twilio_client
    client = get_twilio_client()
    if not client:
        return {"ok": False, "error": "Twilio no configurado"}
    try:
        # Get last 10 outbound messages
        messages = client.messages.list(
            from_=TWILIO_WA_NUMBER if 'TWILIO_WA_NUMBER' in dir() else None,
            limit=10
        )
        results = []
        for m in messages:
            results.append({
                "sid": m.sid[:12] + "...",
                "to": m.to,
                "status": m.status,  # queued, sent, delivered, read, failed, undelivered
                "error_code": m.error_code,
                "error_message": m.error_message,
                "date_sent": str(m.date_sent) if m.date_sent else None,
                "date_created": str(m.date_created),
                "direction": m.direction,
            })
        return {"ok": True, "messages": results}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.post("/whatsapp/broadcast-to")
async def whatsapp_broadcast_to(to: str):
    """Mandar el broadcast diario a un solo numero. ej: /whatsapp/broadcast-to?to=+521XXXXXXXXXX"""
    try:
        from whatsapp_broadcast import compose_daily_broadcast, get_twilio_client, CONTENT_SID
        from config import TWILIO_WA_NUMBER
        msg = await compose_daily_broadcast()
        if not msg:
            return {"ok": False, "error": "No hay juegos hoy"}
        client = get_twilio_client()
        if not client:
            return {"ok": False, "error": "Twilio no configurado"}
        from whatsapp_broadcast import _ensure_wa_number
        to_num = _ensure_wa_number(to)

        if CONTENT_SID:
            import json as _json
            m = client.messages.create(content_sid=CONTENT_SID, content_variables=_json.dumps({"1": msg}), from_=TWILIO_WA_NUMBER, to=to_num)
        else:
            m = client.messages.create(body=msg, from_=TWILIO_WA_NUMBER, to=to_num)

        return {
            "ok": True, "sid": m.sid, "status": m.status, "to": to_num,
            "used_template": bool(CONTENT_SID),
            "preview": msg[:200],
        }
    except Exception as e:
        error_msg = str(e)
        hint = ""
        if "63016" in error_msg or "63032" in error_msg or "outside" in error_msg.lower():
            hint = "El usuario esta fuera de la ventana de 24h. Necesitas un Content Template aprobado en Twilio."
        elif "21408" in error_msg:
            hint = "El numero no tiene sesion activa de WhatsApp. El usuario debe mandar un mensaje primero."
        elif "credentials" in error_msg.lower() or "auth" in error_msg.lower():
            hint = "Credenciales de Twilio invalidas. Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN."
        return {"ok": False, "error": error_msg, "type": type(e).__name__, "hint": hint}


# ── Twitter Bot Scheduler ────────────────────────────────

import os
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from twitter_bot import setup_twitter_scheduler
    from facebook_bot import setup_facebook_scheduler
    from send_whatsapp_daily import send_daily_broadcast as _meta_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from push_notifications import check_and_send_pregame_pushes, send_daily_push_summary
    from apscheduler.triggers.interval import IntervalTrigger

    async def _tracked_broadcast():
        """Wrapper that logs broadcast results — now uses Meta Cloud API.
        Includes deduplication: skips if broadcast already ran today."""
        if _broadcast_already_ran_today():
            logger.info("Scheduled broadcast SKIPPED — already ran today")
            return
        try:
            result = await _meta_broadcast()
            now = datetime.now(TZ_MX)
            _last_broadcast["ran_at"] = now.isoformat()
            _last_broadcast["date"] = now.strftime("%Y-%m-%d")
            _last_broadcast["result"] = result
            _last_broadcast["error"] = None
            _last_broadcast["source"] = "scheduler"
            logger.info(f"Scheduled broadcast completed: {result}")
        except Exception as e:
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = None
            _last_broadcast["error"] = str(e)
            _last_broadcast["source"] = "scheduler"
            logger.error(f"Scheduled broadcast FAILED: {e}")

    async def _keep_alive_ping():
        """Ping self every 13 min to prevent Render free-tier sleep."""
        import urllib.request
        try:
            ping_url = os.getenv("RENDER_EXTERNAL_URL", "https://dondever.app")
            urllib.request.urlopen(f"{ping_url}/health", timeout=10)
        except Exception:
            pass

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():
        # ── Vigilante de memoria ──
        # Va primero a propósito: si algo de lo que sigue falla, esto ya quedó
        # corriendo. Render mató la instancia cuatro veces el 24/09/2026 por
        # pasarse de 512 MB, y cada muerte son 503 para todos más medio minuto
        # de arranque en frío. Vaciar cachés cuesta lentitud unos minutos;
        # que te maten el contenedor cuesta la visita entera.
        try:
            import memoria as _mem
            scheduler.add_job(_mem.vigilar, "interval", minutes=1,
                              id="vigilante_memoria", max_instances=1,
                              coalesce=True, replace_existing=True)
            logger.info("Vigilante de memoria activo (umbral %.0f MB, RSS actual %.0f MB)",
                        _mem.UMBRAL_MB, _mem.rss_mb())
        except Exception as e:
            logger.warning(f"No se pudo activar el vigilante de memoria: {e}")

        # ── Init database tables ──
        try:
            await init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.warning(f"DB init failed (non-fatal): {e}")

        # One-time cleanup of junk in affiliate clicks (sqlmap payloads, bot keys)
        try:
            _n = _purge_click_junk()
            if _n:
                logger.info(f"Purged {_n} junk affiliate click keys")
        except Exception as e:
            logger.warning(f"Click junk purge failed: {e}")

        # Seed admin subscriber (ensure owner always gets broadcasts)
        try:
            from subscribers import subscribe
            admin_phone = os.getenv("ADMIN_WHATSAPP", "528118001161")
            subscribe(admin_phone)
            logger.info(f"Admin subscriber seeded: {admin_phone}")
        except Exception as e:
            logger.warning(f"Failed to seed admin subscriber: {e}")

        # Twitter bot (only if credentials set)
        if os.getenv("TWITTER_API_KEY"):
            setup_twitter_scheduler(scheduler)

        # Facebook bot (only if credentials set)
        if os.getenv("FB_PAGE_ACCESS_TOKEN"):
            setup_facebook_scheduler(scheduler)

        # WhatsApp daily broadcast at 9:00 AM MX (15:00 UTC)
        scheduler.add_job(
            _tracked_broadcast,
            CronTrigger(hour=15, minute=0),
            id="whatsapp_daily_broadcast",
            name="Daily WhatsApp picks broadcast",
            replace_existing=True,
        )
        logger.info("WhatsApp broadcast scheduled at 9:00 AM MX")

        # WhatsApp pre-game alerts every 5 minutes
        scheduler.add_job(
            send_pregame_alerts,
            IntervalTrigger(minutes=5),
            id="whatsapp_pregame_alerts",
            name="Pre-game WhatsApp alerts",
            replace_existing=True,
        )
        logger.info("Pre-game alerts scheduled every 5 min")

        # Web Push notifications (only if OneSignal configured)
        if os.getenv("ONESIGNAL_APP_ID") and os.getenv("ONESIGNAL_API_KEY"):
            async def _push_pregame_check():
                """Check for games starting soon and send push notifications (solo a seguidores)."""
                try:
                    games = await get_todays_games()
                    await check_and_send_pregame_pushes(games, _push_targets_for, _push_url_for)
                except Exception as e:
                    logger.error(f"Push pre-game check failed: {e}")

            # Alertas en vivo (inicio / anotación / final) cada 60 s
            scheduler.add_job(
                _push_live_check,
                IntervalTrigger(seconds=60),
                id="push_live_alerts",
                name="Live push notifications",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

            async def _push_daily_summary():
                """Send daily summary push at 8:00 AM MX."""
                try:
                    games = await get_todays_games()
                    await send_daily_push_summary(games)
                except Exception as e:
                    logger.error(f"Push daily summary failed: {e}")

            # Pre-game push alerts every 5 minutes
            scheduler.add_job(
                _push_pregame_check,
                IntervalTrigger(minutes=5),
                id="push_pregame_alerts",
                name="Pre-game push notifications",
                replace_existing=True,
            )
            # Daily push summary at 8:00 AM MX (14:00 UTC)
            scheduler.add_job(
                _push_daily_summary,
                CronTrigger(hour=14, minute=0),
                id="push_daily_summary",
                name="Daily push summary",
                replace_existing=True,
            )
            logger.info("Push notifications scheduled (pre-game every 5 min + daily at 8AM MX)")

        # TikTok/Reels daily video + images at 7:30 AM MX (13:30 UTC)
        scheduler.add_job(
            generate_daily_video,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_video",
            name="Daily TikTok video generation",
            replace_existing=True,
        )
        scheduler.add_job(
            generate_daily_images,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_images",
            name="Daily TikTok images generation",
            replace_existing=True,
        )
        logger.info("TikTok video generation scheduled at 7:30 AM MX")

        # Keep-alive ping every 13 min (prevents Render free-tier sleep at 15 min)
        scheduler.add_job(
            _keep_alive_ping,
            IntervalTrigger(minutes=13),
            id="keep_alive_ping",
            name="Keep-alive self-ping",
            replace_existing=True,
        )
        logger.info("Keep-alive ping scheduled every 13 min")

        scheduler.start()
        logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def stop_scheduler():
        scheduler.shutdown()

except ImportError:
    logger.warning("APScheduler not installed, scheduled jobs disabled")


# ── SEO: Sitemap & Robots ───────────────────────────────

@app.get("/tiktokVCdYT0dv6jrqTL4pncMRP6dXaRB54Aka.txt", response_class=PlainTextResponse)
async def tiktok_verification_old():
    """TikTok domain verification file (legacy — sandbox)."""
    return "tiktok-developers-site-verification=VCdYT0dv6jrqTL4pncMRP6dXaRB54Aka"


@app.get("/tiktokaCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg.txt", response_class=PlainTextResponse)
async def tiktok_verification_prod():
    """TikTok domain verification file (production)."""
    return "tiktok-developers-site-verification=aCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg"


@app.get("/ads.txt", response_class=PlainTextResponse)
async def ads_txt():
    """Serve ads.txt for Google AdSense verification."""
    return "google.com, pub-2576227882415709, DIRECT, f08c47fec0942fa0\n"


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt for search engine crawlers."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /partido/\n"
        "Allow: /pronosticos-hoy\n"
        "Allow: /nfl-hoy\n"
        "Allow: /lmp-hoy\n"
        "Allow: /lmb-hoy\n"
        "Allow: /liga-mx-femenil-hoy\n"
        "Allow: /lnbp-hoy\n"
        "Allow: /lvbp-hoy\n"
        "Allow: /lidom-hoy\n"
        "Allow: /liga/\n"
        "Disallow: /api/\n"
        "Disallow: /webhook/\n"
        f"\nSitemap: {APP_URL}/sitemap.xml\n"
    )


@app.get("/tiktok/hoy")
async def tiktok_today():
    """Show today's TikTok video and images for easy download."""
    from pathlib import Path
    from datetime import datetime
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"/static/tiktok/dondever_picks_{date_tag}.mp4"
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = []
    if images_dir.exists():
        images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")])
    return {
        "date": date_tag,
        "video": video_path,
        "images": images,
        "instructions": "Descarga el video y subelo a TikTok/Reels/Shorts. Las imagenes sirven para carrusel de Instagram.",
    }


@app.get("/twitter/debug")
async def twitter_debug():
    """Diagnostico del bot de Twitter — para saber por que no tweetea."""
    from twitter_bot import (
        twitter_credentials_valid, get_twitter_client,
        _tweet_timestamps, MAX_TWEETS_PER_HOUR, MAX_TWEETS_PER_DAY,
        MIN_SECONDS_BETWEEN_TWEETS, _can_post_now,
    )
    from sports_api import get_todays_games
    from datetime import datetime, timezone
    import time as _time

    info = {
        "credentials_set": twitter_credentials_valid(),
        "tweets_posted_last_24h": len(_tweet_timestamps),
        "limits": {
            "per_hour": MAX_TWEETS_PER_HOUR,
            "per_day": MAX_TWEETS_PER_DAY,
            "min_seconds_between": MIN_SECONDS_BETWEEN_TWEETS,
        },
        "can_post_now": None,
        "rate_limit_reason": None,
        "auth_check": None,
        "upcoming_games_20min": [],
        "games_today": 0,
    }

    allowed, reason = _can_post_now()
    info["can_post_now"] = allowed
    info["rate_limit_reason"] = reason or None

    # Verifica que los tokens funcionen (sin postear)
    try:
        client = get_twitter_client()
        if client:
            me = client.get_me()
            info["auth_check"] = {"ok": True, "username": me.data.username if me.data else None}
        else:
            info["auth_check"] = {"ok": False, "error": "no client (credentials missing)"}
    except Exception as e:
        info["auth_check"] = {"ok": False, "error": str(e)}

    # Juegos próximos en los siguientes 20 min
    try:
        games = await get_todays_games()
        info["games_today"] = len(games)
        now = datetime.now(timezone.utc)
        for g in games:
            if g["status"]["state"] != "pre":
                continue
            try:
                gt = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                diff_min = (gt - now).total_seconds() / 60
                if 0 < diff_min <= 20:
                    info["upcoming_games_20min"].append({
                        "name": g["name"], "in_minutes": round(diff_min, 1),
                    })
            except Exception:
                pass
    except Exception as e:
        info["games_error"] = str(e)

    return info


@app.post("/twitter/test-tweet")
async def twitter_test_tweet():
    """Postea un tweet de prueba MANUALMENTE. Solo usar para verificar que funciona."""
    from twitter_bot import post_tweet
    from datetime import datetime
    text = f"Test de DondeVer.app — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC. Juegos de hoy en https://dondever.app"
    result = post_tweet(text)
    return result


@app.post("/twitter/trigger/{job_type}")
async def twitter_trigger_job(job_type: str):
    """Disparar manualmente un job de Twitter: summary, poll, pick, games."""
    from twitter_bot import (
        compose_daily_summary_tweet, post_tweet, post_daily_poll,
        post_pick_del_dia, post_game_tweets,
    )
    from sports_api import get_todays_games

    if job_type == "summary":
        games = await get_todays_games()
        if not games:
            return {"status": "no_games", "message": "No hay juegos hoy"}
        tweet = compose_daily_summary_tweet(games)
        result = post_tweet(tweet)
        return {"status": "ok", "type": "summary", "tweet": tweet, "result": result}
    elif job_type == "poll":
        result = await post_daily_poll()
        return {"status": "ok", "type": "poll", "result": result}
    elif job_type == "pick":
        result = await post_pick_del_dia()
        return {"status": "ok", "type": "pick", "result": result}
    elif job_type == "games":
        result = await post_game_tweets(minutes_before=240, max_tweets=2)
        return {"status": "ok", "type": "games", "result": result}
    else:
        return {"status": "error", "message": f"Tipo '{job_type}' no valido. Usa: summary, poll, pick, games"}


@app.get("/tiktok/generar")
async def tiktok_generate_now(images: bool = False):
    """Manually trigger TikTok video generation. Pass ?images=true para generar carrusel tambien."""
    try:
        from tiktok_generator import generate_daily_video, generate_daily_images
        from sports_api import get_todays_games

        # Verificar primero que si hay juegos (para distinguir no_games vs error ffmpeg)
        games_check = await get_todays_games()
        if not games_check:
            return JSONResponse({"status": "no_games", "message": "No hay juegos hoy"})

        logger.info(f"[generar] {len(games_check)} juegos encontrados, generando video...")
        video = await generate_daily_video()

        if not video:
            # Hay juegos pero el video no se genero → error en ffmpeg/PIL
            return JSONResponse(
                {"status": "error", "error": "Video generation returned empty (ffmpeg/PIL error)", "games_found": len(games_check)},
                status_code=500,
            )

        img_count = 0
        if images:
            img_list = await generate_daily_images()
            img_count = len(img_list) if img_list else 0

        return JSONResponse({
            "video": video,
            "images_count": img_count,
            "games_used": len(games_check),
            "status": "ok",
        })
    except Exception as e:
        logger.exception("tiktok_generate_now failed")
        return JSONResponse(
            {"status": "error", "error": str(e), "type": type(e).__name__},
            status_code=500,
        )


def _sitemap_hreflang(loc: str) -> str:
    """Generate xhtml:link hreflang entries for a sitemap <url>."""
    lines = []
    for locale in HREFLANG_LOCALES:
        lines.append(f'    <xhtml:link rel="alternate" hreflang="{locale}" href="{loc}"/>')
    lines.append(f'    <xhtml:link rel="alternate" hreflang="es" href="{loc}"/>')
    lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{loc}"/>')
    return "\n".join(lines)


def _sm_url(loc: str, lastmod: str, freq: str, priority: str, hreflang: bool = False) -> str:
    hl = f'\n{_sitemap_hreflang(loc)}' if hreflang else ""
    return (f'  <url>\n    <loc>{loc}</loc>{hl}\n'
            f'    <lastmod>{lastmod}</lastmod>\n'
            f'    <changefreq>{freq}</changefreq>\n'
            f'    <priority>{priority}</priority>\n  </url>')


def _sm_wrap(urls: list) -> Response:
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
           ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
           + "\n".join(urls) + '\n</urlset>')
    return Response(content=xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=1800"})


# Honest lastmod helpers — Google ignores sitemaps where every lastmod == today.
def _sm_dates():
    today = datetime.now(TZ_MX)
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    month_start = today.strftime("%Y-%m-01")
    return today.strftime("%Y-%m-%d"), week_start, month_start


_SM_STATIC_LASTMOD = "2026-09-12"  # bump when legal/guide copy changes


@app.get("/sitemap.xml")
async def sitemap_index():
    """Sitemap index → 4 sub-sitemaps (core / partidos / equipos / equipos-paises)."""
    today_str, week_start, month_start = _sm_dates()
    parts = [
        ("sitemap-core.xml", today_str),
        ("sitemap-partidos.xml", today_str),
        ("sitemap-equipos.xml", today_str),
        ("sitemap-equipos-paises.xml", month_start),
        ("sitemap-eventos.xml", today_str),
    ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(f'  <sitemap>\n    <loc>{APP_URL}/{p}</loc>\n    <lastmod>{lm}</lastmod>\n  </sitemap>'
                       for p, lm in parts)
           + '\n</sitemapindex>')
    return Response(content=xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=1800"})


@app.get("/sitemap-partidos.xml")
async def sitemap_partidos():
    """Today's games, matchups and recaps — changes hourly."""
    today_str, _, _ = _sm_dates()
    games = await get_todays_games()
    urls = []
    for game in games:
        if game.get("home", {}).get("name", "") == "TBD" or game.get("away", {}).get("name", "") == "TBD":
            continue
        urls.append(_sm_url(f'{APP_URL}{_game_url(game)}', today_str, "hourly", "0.8", hreflang=True))
        home_slug = _slugify_team(game["home"]["name"])
        away_slug = _slugify_team(game["away"]["name"])
        if home_slug and away_slug:
            urls.append(_sm_url(f'{APP_URL}/donde-ver/{away_slug}-vs-{home_slug}', today_str, "daily", "0.7"))
        if game.get("status", {}).get("state") == "post":
            urls.append(_sm_url(f'{APP_URL}/resultado/{_make_game_slug(game)}', today_str, "never", "0.5"))
    return _sm_wrap(urls)


@app.get("/sitemap-equipos.xml")
async def sitemap_equipos():
    """Team pages. lastmod = today only if the team plays today, else start of week."""
    today_str, week_start, _ = _sm_dates()
    games = await get_todays_games()
    playing_today = set()
    for game in games:
        for side in ("home", "away"):
            playing_today.add(_slugify_team(game[side]["name"]))
    playing_today.discard("")
    urls = [_sm_url(f'{APP_URL}/equipos', today_str, "daily", "0.8")]
    from config import POPULAR_TEAMS as _CFG_TEAMS
    _athletes = {k for k, v in _CFG_TEAMS.items() if v.get("league") in _ATHLETE_LEAGUES}
    all_team_slugs = set(POPULAR_TEAMS.keys()) | playing_today | _athletes
    for team_slug in sorted(all_team_slugs):
        plays = team_slug in playing_today
        t_priority = "0.9" if plays else ("0.8" if team_slug in NFL_TEAM_EXTRA else "0.7")
        urls.append(_sm_url(f'{APP_URL}/equipo/{team_slug}', today_str if plays else week_start,
                            "daily", t_priority))
    # Versiones en inglés para equipos de EE.UU. (hreflang recíproco con /equipo/{slug})
    for team_slug in _en_team_list():
        urls.append(_sm_url(f'{APP_URL}/en/{team_slug}', today_str, "daily", "0.7"))
    # /equipo/{slug}/calendario se quitó del sitemap (GSC 28 d: 5,013 impresiones, 0 clics,
    # posición ~44). Duplica los próximos partidos que ya trae /equipo/{slug} y se lleva
    # presupuesto de rastreo. Las páginas siguen accesibles para el usuario, pero con noindex.
    return _sm_wrap(urls)


@app.get("/sitemap-eventos.xml")
async def sitemap_eventos():
    """UFC / F1 / Boxeo event pages (próximos 120 días + recientes)."""
    today_str, _, _ = _sm_dates()
    from events_api import fetch_all_events
    urls = []
    try:
        for e in await fetch_all_events(days_ahead=120):
            if e.get("is_minor"):
                continue
            lm = today_str if e["status"] != "post" else mx_date_str(e["date"])
            urls.append(_sm_url(f'{APP_URL}/evento/{e["slug"]}', lm, "daily", "0.8", hreflang=True))
    except Exception as e:
        logger.warning(f"sitemap-eventos failed: {e}")
    return _sm_wrap(urls)


def mx_date_str(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ_MX).strftime("%Y-%m-%d")
    except Exception:
        return (iso or "")[:10]


@app.get("/sitemap-equipos-paises.xml")
async def sitemap_equipos_paises():
    """Team × country pages — evergreen, monthly lastmod."""
    _, _, month_start = _sm_dates()
    urls = []
    for team_slug in sorted(POPULAR_TEAMS.keys()):
        for c_slug in TEAM_COUNTRY_SEO:
            urls.append(_sm_url(f'{APP_URL}/equipo/{team_slug}/en/{c_slug}', month_start, "weekly", "0.6"))
    return _sm_wrap(urls)


@app.get("/sitemap-core.xml")
async def sitemap_core():
    """Home, leagues, sport hubs, channels, countries, guides, legal."""
    today_str, week_start, month_start = _sm_dates()

    urls = [_sm_url(APP_URL, today_str, "hourly", "1.0", hreflang=True)]

    # Static pages (legal + guides)
    static_pages = [
        ("sobre-nosotros", "monthly", "0.5"),
        ("app", "monthly", "0.6"),
        ("privacidad", "monthly", "0.3"),
        ("terminos", "monthly", "0.3"),
        ("guia/donde-ver-liga-mx", "weekly", "0.8"),
        ("guia/donde-ver-nfl-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-nba-en-mexico", "weekly", "0.8"),
        ("guia/mejores-streaming-deportes-mexico", "weekly", "0.8"),
        # donde-ver-champions-league redirects 301 → donde-ver-champions-en-mexico
        ("guia/como-ver-tudn-en-usa", "weekly", "0.8"),
        ("pronosticos-hoy", "daily", "0.9"),
        # *-hoy hubs de ligas ahora son 301 → /liga/{slug} (ya listadas abajo); no van en sitemap
        ("gratis-hoy", "daily", "0.9"),
        ("playoffs-mlb", "daily", "0.9"),
        ("guia/mejores-casas-apuestas-liga-mx", "weekly", "0.9"),
        ("guia/donde-ver-champions-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-copa-america-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-copa-libertadores-en-mexico", "weekly", "0.8"),
        # New "Como ver" guides
        ("guia/como-ver-premier-league-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-la-liga-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-serie-a-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-bundesliga-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-ligue-1-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-mlb-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-nhl-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-mls-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-ufc-en-mexico", "weekly", "0.8"),
        ("guia/como-ver-liga-mx-femenil", "weekly", "0.8"),
        ("guia/como-ver-europa-league-en-mexico", "weekly", "0.8"),
        # Caribbean / LatAm MLB guides
        ("guia/donde-ver-mlb-en-venezuela", "weekly", "0.8"),
        ("guia/donde-ver-mlb-en-republica-dominicana", "weekly", "0.8"),
        ("guia/donde-ver-mlb-en-panama", "weekly", "0.8"),
        # Evergreen high-volume guides (Jul 2026)
        ("guia/donde-ver-mundial-2026-gratis", "daily", "0.9"),
        ("guia/mejores-apps-ver-futbol-en-vivo", "weekly", "0.8"),
        ("guia/como-ver-fox-sports-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-futbol-gratis-por-internet", "weekly", "0.8"),
        ("guia/guia-canales-deportivos-mexico", "weekly", "0.8"),
    ]
    for page, freq, priority in static_pages:
        # "-hoy" hubs change daily; guides/legal only when edited
        lm = today_str if page.endswith("-hoy") else _SM_STATIC_LASTMOD
        urls.append(_sm_url(f'{APP_URL}/{page}', lm, freq, priority, hreflang=True))

    # Permanent league landing pages (daily content)
    for slug in LEAGUES:
        urls.append(_sm_url(f'{APP_URL}/liga/{slug}', today_str, "daily", "0.9", hreflang=True))
    urls.append(_sm_url(f'{APP_URL}/widget', _SM_STATIC_LASTMOD, "monthly", "0.5"))
    urls.append(_sm_url(f'{APP_URL}/contacto', _SM_STATIC_LASTMOD, "monthly", "0.4"))
    urls.append(_sm_url(f'{APP_URL}/whatsapp', _SM_STATIC_LASTMOD, "monthly", "0.6"))
    # Ligas de LEAGUES_INDIVIDUAL: no salen en la portada (son deportes sin
    # "equipo local vs visitante") pero tienen página propia y hay que indexarla.
    for slug in ("nascar", "indycar", "atp", "wta", "pga"):
        urls.append(_sm_url(f'{APP_URL}/liga/{slug}', today_str, "daily", "0.8", hreflang=True))

    # Sport-today hubs
    for sport_slug in SPORT_TODAY_PAGES:
        urls.append(_sm_url(f'{APP_URL}/{sport_slug}', today_str, "daily", "0.9"))

    # Curated channel pages ("qué pasan hoy en ESPN")
    for ch_slug in sorted({_canon_channel_slug(k) for k in CHANNEL_PAGES}):
        urls.append(_sm_url(f'{APP_URL}/canal/{ch_slug}', today_str, "daily", "0.8"))

    # Country pages (evergreen)
    for c_slug in COUNTRY_PAGES:
        urls.append(_sm_url(f'{APP_URL}/donde-ver-en-{c_slug}', month_start, "monthly", "0.8"))

    urls.append(_sm_url(f'{APP_URL}/streaming', month_start, "monthly", "0.8"))
    urls.append(_sm_url(f'{APP_URL}/casinos', week_start, "weekly", "0.9"))

    return _sm_wrap(urls)


# ── Static Pages (Legal + Guides for AdSense) ───────────

@app.get("/sobre-nosotros", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html")


# ── Contacto: publicidad, ideas, opiniones, correcciones ──
_CONTACT_FILE = os.path.join(os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "contact_messages.json")
_contact_rate: dict = {}   # ip → [timestamps]
_contact_last_email: dict = {}   # diagnóstico del último intento de envío por Resend


def _contact_ctx(**kw) -> dict:
    base = {"year": datetime.now(TZ_MX).year, "ts": str(int(datetime.now(timezone.utc).timestamp())),
            "sent": False, "error": "", "motivo": "", "nombre": "", "email": "", "mensaje": ""}
    base.update(kw)
    return base


@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_guide(request: Request):
    """Instrucciones del bot de WhatsApp: cómo empezar, botones, comandos, por qué hay que responder."""
    return templates.TemplateResponse(request, "whatsapp_guide.html", {"year": datetime.now(TZ_MX).year})


@app.get("/app", response_class=HTMLResponse)
async def app_install_page(request: Request):
    """Cómo instalar la PWA (iPhone Safari / Android / escritorio). Se enlaza desde el header móvil y el bot."""
    return templates.TemplateResponse(request, "app_install.html", {"year": datetime.now(TZ_MX).year})


@app.get("/contacto", response_class=HTMLResponse)
async def contacto_page(request: Request, motivo: str = ""):
    return templates.TemplateResponse(request, "contacto.html", _contact_ctx(motivo=motivo))


@app.post("/contacto", response_class=HTMLResponse)
async def contacto_submit(request: Request, motivo: str = Form("otro"), nombre: str = Form(""), email: str = Form(""),
                          mensaje: str = Form(""), website: str = Form(""), ts: str = Form("")):
    """Guarda el mensaje en contact_messages.json y lo manda por email (Resend) a CONTACT_EMAIL."""
    ip = (request.headers.get("x-forwarded-for", "") or (request.client.host if request.client else "")).split(",")[0].strip()
    now = datetime.now(timezone.utc).timestamp()
    ctx = dict(motivo=motivo, nombre=nombre.strip()[:80], email=email.strip()[:120], mensaje=mensaje.strip()[:3000])
    # Anti-spam: honeypot, formulario llenado en <3 s, más de 3 envíos por hora por IP
    if website:
        return templates.TemplateResponse(request, "contacto.html", _contact_ctx(sent=True, email=ctx["email"]))
    try:
        if now - float(ts or 0) < 3:
            return templates.TemplateResponse(request, "contacto.html", _contact_ctx(error="Espera un momento y vuelve a enviar.", **ctx))
    except ValueError:
        pass
    hits = [t for t in _contact_rate.get(ip, []) if now - t < 3600]
    if len(hits) >= 3:
        return templates.TemplateResponse(request, "contacto.html", _contact_ctx(error="Ya recibimos varios mensajes desde tu conexión. Inténtalo más tarde o escribe a contacto@dondever.app.", **ctx))
    if not ctx["nombre"] or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", ctx["email"]) or len(ctx["mensaje"]) < 10:
        return templates.TemplateResponse(request, "contacto.html", _contact_ctx(error="Revisa nombre, email y mensaje (mínimo 10 caracteres).", **ctx))
    _contact_rate[ip] = hits + [now]

    rec = {"ts": datetime.now(TZ_MX).isoformat(timespec="seconds"), "ip": ip, "ua": request.headers.get("user-agent", "")[:160], **ctx}
    try:
        data = []
        if os.path.exists(_CONTACT_FILE):
            with open(_CONTACT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data.append(rec)
        with open(_CONTACT_FILE, "w", encoding="utf-8") as f:
            json.dump(data[-500:], f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.warning(f"contact save failed: {e}")
    try:
        from send_email_daily import send_email
        import html as _html
        to = os.getenv("CONTACT_EMAIL", "contacto@arobegroup.com")
        labels = {"publicidad": "📣 Publicidad", "idea": "💡 Idea", "opinion": "💬 Opinión", "correccion": "🛠️ Corrección",
                  "widget": "🔗 Widget/colaboración", "otro": "Otro"}
        body = (f"<p><b>{labels.get(motivo, motivo)}</b> — {_html.escape(ctx['nombre'])} &lt;{_html.escape(ctx['email'])}&gt;</p>"
                f"<p style='white-space:pre-wrap'>{_html.escape(ctx['mensaje'])}</p><hr><p style='color:#888;font-size:12px'>{rec['ts']} · {ip}</p>")
        r = send_email(to, f"[DondeVer contacto] {labels.get(motivo, motivo)}: {ctx['nombre']}", body)
        if not r.get("ok"):
            logger.warning(f"contact email not sent: {r}")
        _contact_last_email.update({"ts": rec["ts"], "to": to, "result": r})
    except Exception as e:
        logger.warning(f"contact email failed: {e}")
        _contact_last_email.update({"ts": rec["ts"], "error": str(e)})
    return templates.TemplateResponse(request, "contacto.html", _contact_ctx(sent=True, email=ctx["email"]))


@app.get("/api/internal/contact-messages")
async def contact_messages(token: str = ""):
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    diag = {"resend_key_configured": bool(os.getenv("RESEND_API_KEY")), "from": os.getenv("RESEND_FROM_EMAIL", "DondeVer Picks <picks@dondever.app>"),
            "to": os.getenv("CONTACT_EMAIL", "contacto@arobegroup.com"), "last_email": _contact_last_email}
    try:
        with open(_CONTACT_FILE, "r", encoding="utf-8") as f:
            return {"ok": True, "email": diag, "messages": list(reversed(json.load(f)))[:100]}
    except Exception:
        return {"ok": True, "email": diag, "messages": []}

@app.get("/privacidad", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")

@app.get("/terminos", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html")

@app.get("/casinos", response_class=HTMLResponse)
async def casinos_page(request: Request):
    """Casino comparison landing — for SEO + affiliate conversion."""
    from config import get_affiliate_url
    return templates.TemplateResponse(request, "casinos.html", {
        "jubilee_url": get_affiliate_url("jubilee", source="casinos"),
        "vivento_url": get_affiliate_url("vivento", source="casinos"),
    })

GUIDE_REDIRECTS = {
    # Consolidate duplicate Champions League guides — avoid keyword cannibalization
    "donde-ver-champions-league": "donde-ver-champions-en-mexico",
    # Copa America / Libertadores variants
    "donde-ver-copa-america": "donde-ver-copa-america-en-mexico",
    "copa-america-en-vivo": "donde-ver-copa-america-en-mexico",
    "donde-ver-libertadores": "donde-ver-copa-libertadores-en-mexico",
    "copa-libertadores-en-vivo": "donde-ver-copa-libertadores-en-mexico",
}

@app.get("/guia/{guide_slug}", response_class=HTMLResponse)
async def guide_page(request: Request, guide_slug: str):
    """Original content guides for SEO + AdSense."""
    if guide_slug in GUIDE_REDIRECTS:
        return RedirectResponse(
            url=f"/guia/{GUIDE_REDIRECTS[guide_slug]}",
            status_code=301,
        )
    template_name = f"guides/{guide_slug}.html"
    try:
        return templates.TemplateResponse(request, template_name)
    except Exception:
        return templates.TemplateResponse(request, "404.html", status_code=404)


# ── Channel Pages (SEO: "que pasan hoy en ESPN") ────────

CHANNEL_PAGES = {
    # Mexico
    "tudn":          {"name": "TUDN",          "country": "MX", "type": "cable",     "desc": "TUDN es el canal deportivo mas importante de Mexico. Transmite Liga MX, Champions League, Liga de Naciones y mas."},
    "canal-5":       {"name": "Canal 5",       "country": "MX", "type": "broadcast", "desc": "Canal 5 de Televisa transmite partidos selectos de Liga MX en television abierta, gratis para toda Mexico."},
    "azteca-7":      {"name": "Azteca 7",      "country": "MX", "type": "broadcast", "desc": "Azteca 7 transmite partidos de futbol mexicano y eventos de la Seleccion Mexicana en TV abierta."},
    "fox-sports-mx": {"name": "Fox Sports MX", "country": "MX", "type": "cable",     "desc": "Fox Sports Mexico cubre Liga MX, MLB, NFL y UFC. Disponible en los principales sistemas de cable."},
    "vix":           {"name": "ViX",           "country": "MX", "type": "streaming", "desc": "ViX es la plataforma de streaming de TelevisaUnivision. Ofrece partidos de Liga MX, MLS, Champions y mas."},
    "espn-mx":       {"name": "ESPN MX",       "country": "MX", "type": "cable",     "desc": "ESPN Mexico transmite Premier League, La Liga, Serie A, NBA, NFL y mas deportes internacionales."},
    "claro-sports":  {"name": "Claro Sports",  "country": "MX", "type": "cable",     "desc": "Claro Sports cubre eventos deportivos selectos incluyendo Juegos Olimpicos y liga mexicana."},
    # USA
    "espn":          {"name": "ESPN",          "country": "US", "type": "cable",     "desc": "ESPN es el canal deportivo numero uno en Estados Unidos. Transmite NFL, NBA, MLB, MLS y mas."},
    "espn-plus":     {"name": "ESPN+",         "country": "US", "type": "streaming", "desc": "ESPN+ es el servicio de streaming deportivo de Disney. Incluye La Liga, Bundesliga, UFC y mas."},
    "fox":           {"name": "FOX",           "country": "US", "type": "broadcast", "desc": "FOX transmite NFL, MLB World Series, NASCAR y eventos deportivos premium en TV abierta en EE.UU."},
    "fs1":           {"name": "FS1",           "country": "US", "type": "cable",     "desc": "Fox Sports 1 cubre MLB, NASCAR, USFL y eventos de UFC en cable."},
    "nbc":           {"name": "NBC",           "country": "US", "type": "broadcast", "desc": "NBC transmite Sunday Night Football de la NFL, Premier League y Juegos Olimpicos."},
    "peacock":       {"name": "Peacock",       "country": "US", "type": "streaming", "desc": "Peacock de NBCUniversal ofrece Premier League, Sunday Night Football y eventos en vivo."},
    "paramount-plus":{"name": "Paramount+",    "country": "US", "type": "streaming", "desc": "Paramount+ transmite Champions League, Europa League, Serie A, NWSL y CBS Sports."},
    "tnt":           {"name": "TNT",           "country": "US", "type": "cable",     "desc": "TNT transmite NBA, NHL y AEW Wrestling en Estados Unidos."},
    "abc":           {"name": "ABC",           "country": "US", "type": "broadcast", "desc": "ABC transmite NBA Finals, College Football Playoff y Saturday Night Football."},
    "prime-video":   {"name": "Prime Video",   "country": "US", "type": "streaming", "desc": "Amazon Prime Video tiene Thursday Night Football de la NFL y partidos selectos de MLS."},
    "apple-tv":      {"name": "Apple TV+",     "country": "US", "type": "streaming", "desc": "Apple TV+ tiene MLS Season Pass con todos los partidos de la MLS y Friday Night Baseball de MLB."},
    "univision":     {"name": "Univision",     "country": "US", "type": "broadcast", "desc": "Univision transmite Liga MX, Concacaf y la Seleccion Mexicana para la audiencia hispana en EE.UU."},
    "telemundo":     {"name": "Telemundo",     "country": "US", "type": "broadcast", "desc": "Telemundo cubre Premier League, Copa del Mundo y eventos deportivos en espanol en EE.UU."},
    # Streaming con afiliado / muy buscados (no deben dar 404 aunque hoy no tengan partidos)
    "disney-plus":   {"name": "Disney+",       "country": "MX", "type": "streaming", "desc": "Disney+ (antes Star+) incluye ESPN en Mexico y Latinoamerica: Premier League, La Liga, Champions, NBA, MLB, F1 y UFC segun el pais."},
    "mlbtv":         {"name": "MLB.TV",        "country": "US", "type": "streaming", "desc": "MLB.TV transmite todos los juegos de la temporada regular de las Grandes Ligas en streaming (con bloqueos locales en EE.UU.; en Mexico sin bloqueos)."},
    "tnt-sports":    {"name": "TNT Sports",    "country": "MX", "type": "cable",     "desc": "TNT Sports Mexico transmite Champions League, Europa League y Conference League por cable y en HBO Max."},
    "hbo-max":       {"name": "HBO Max",       "country": "MX", "type": "streaming", "desc": "HBO Max transmite en Mexico la Champions League y competencias UEFA de TNT Sports."},
    "nba-league-pass": {"name": "NBA League Pass", "country": "MX", "type": "streaming", "desc": "NBA League Pass ofrece todos los partidos de la NBA en vivo y bajo demanda en Mexico y Latinoamerica."},
}


@app.get("/canal/{channel_slug}", response_class=HTMLResponse)
async def channel_page(request: Request, channel_slug: str):
    """Channel page — what's on today for a specific channel. SEO goldmine."""
    channel = CHANNEL_PAGES.get(channel_slug)
    if not channel:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Canal no encontrado."}
        )

    all_games = await get_todays_games()

    # Filter games that broadcast on this channel
    channel_name = channel["name"]
    channel_games = []
    for g in all_games:
        if g.get("broadcasts"):
            for b in g["broadcasts"]:
                # Match by normalized name or raw name
                raw = b.get("channel", "")
                info = b.get("info", {})
                norm = info.get("name", raw) if info else raw
                if norm == channel_name or raw == channel_name:
                    channel_games.append(g)
                    break

    today = datetime.now(TZ_MX)
    return templates.TemplateResponse(
        request, "canal.html",
        context={
            "channel": channel,
            "channel_slug": channel_slug,
            "games": channel_games,
            "total_games": len(channel_games),
            "today_display": format_date_es(today),
        },
    )


# ── Country Pages (SEO: "donde ver deportes en venezuela") ──

COUNTRY_PAGES = {
    "mexico": {
        "name": "México",
        "flag": "🇲🇽",
        "desc": "Guía completa de dónde ver deportes en vivo en México. Canales de TV abierta, cable y streaming disponibles.",
        "channels": [
            {"name": "Canal 5 / Las Estrellas", "type": "TV Abierta (gratis)", "sports": "Liga MX selectos, Selección Mexicana"},
            {"name": "Azteca 7", "type": "TV Abierta (gratis)", "sports": "Liga MX, Selección Mexicana"},
            {"name": "TUDN", "type": "Cable", "sports": "Liga MX, Champions, Selección, Liga de Naciones"},
            {"name": "ESPN MX", "type": "Cable", "sports": "Premier League, La Liga, Serie A, NBA, NFL"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "Liga MX, MLB, NFL, UFC"},
            {"name": "Claro Sports", "type": "Cable", "sports": "Eventos selectos, Olímpicos"},
            {"name": "ViX Premium", "type": "Streaming", "sports": "Liga MX, MLS, Champions, Selección"},
            {"name": "ESPN+ (con VPN)", "type": "Streaming", "sports": "La Liga, Bundesliga, UFC, MLB"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "NFL Game Pass", "type": "Streaming", "sports": "Todos los juegos de NFL"},
        ],
        "tip": "La mayoría de partidos de Liga MX se transmiten por TUDN (cable) y ViX (streaming). Los partidos de la Selección Mexicana se pasan por TV abierta en Canal 5 o Azteca 7.",
    },
    "venezuela": {
        "name": "Venezuela",
        "flag": "🇻🇪",
        "desc": "Dónde ver deportes en vivo desde Venezuela. Canales disponibles, streaming y opciones gratuitas para béisbol LVBP, MLB, fútbol y más.",
        "channels": [
            {"name": "Televen", "type": "TV Abierta (gratis)", "sports": "LVBP selectos, eventos deportivos"},
            {"name": "IVC (Inter)", "type": "Cable", "sports": "Béisbol LVBP en vivo"},
            {"name": "ByM Sport", "type": "Cable", "sports": "LVBP, béisbol venezolano"},
            {"name": "SimpleTV", "type": "Cable/Satélite", "sports": "Deportes, LVBP, eventos internacionales"},
            {"name": "ESPN Caribe/Latam", "type": "Cable", "sports": "MLB, NBA, NFL, Premier League, La Liga"},
            {"name": "DirecTV Sports / DSports", "type": "Cable/Satélite", "sports": "Liga MX, Premier League, Champions"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Disney+ / Star+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "DirecTV GO", "type": "Streaming", "sports": "Deportes en vivo, canales de cable"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "La LVBP (béisbol venezolano, oct-feb) se transmite por IVC, ByM Sport y Televen. Para MLB, la opción más completa es MLB.TV. Para fútbol europeo, Disney+ tiene la mayoría de ligas.",
    },
    "republica-dominicana": {
        "name": "República Dominicana",
        "flag": "🇩🇴",
        "desc": "Guía de dónde ver deportes en vivo en República Dominicana. Canales de TV y streaming para béisbol LIDOM, MLB, fútbol y más.",
        "channels": [
            {"name": "CDN Deportes", "type": "Cable/TV", "sports": "LIDOM (béisbol dominicano) en vivo"},
            {"name": "Teleantillas", "type": "TV Abierta", "sports": "Deportes selectos, eventos locales"},
            {"name": "Coral 39", "type": "TV Abierta", "sports": "Deportes dominicanos, LIDOM selectos"},
            {"name": "Digital 15", "type": "Cable", "sports": "Deportes, LIDOM"},
            {"name": "VTV Canal 32", "type": "Cable", "sports": "Deportes y entretenimiento"},
            {"name": "ESPN Caribe", "type": "Cable", "sports": "MLB, NBA, NFL, fútbol europeo"},
            {"name": "Sky / Claro TV", "type": "Cable/Satélite", "sports": "Liga MX, Champions, NBA"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Disney+ / Star+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "La LIDOM (béisbol dominicano, oct-ene) se transmite por CDN Deportes, Coral 39 y canales locales. Para MLB, la mejor opción es MLB.TV. Para fútbol europeo y NBA, Disney+ tiene la cobertura más amplia.",
    },
    "panama": {
        "name": "Panamá",
        "flag": "🇵🇦",
        "desc": "Dónde ver deportes en vivo en Panamá. TV abierta, cable y streaming disponibles para béisbol, fútbol y más deportes.",
        "channels": [
            {"name": "TVN", "type": "TV Abierta (gratis)", "sports": "Selección de Panamá, eventos deportivos"},
            {"name": "RPC", "type": "TV Abierta (gratis)", "sports": "Deportes selectos, eventos locales"},
            {"name": "TVMax", "type": "Cable", "sports": "Deportes panameños, Probeis"},
            {"name": "Tigo Sports", "type": "Cable", "sports": "Fútbol, béisbol, deportes internacionales"},
            {"name": "ESPN Centroamérica", "type": "Cable", "sports": "MLB, NBA, NFL, fútbol europeo"},
            {"name": "Cable & Wireless", "type": "Cable", "sports": "Liga MX, Champions, NBA"},
            {"name": "MedcomGO", "type": "Streaming", "sports": "TVN y RPC en vivo, deportes locales"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "Disney+ / Star+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
        ],
        "tip": "El béisbol profesional panameño (Probeis) se transmite por TVMax y canales locales. Para MLB y deportes internacionales, ESPN Centroamérica y MLB.TV son las mejores opciones. MedcomGO tiene la señal de TVN y RPC en streaming.",
    },
    "estados-unidos": {
        "name": "Estados Unidos",
        "flag": "🇺🇸",
        "desc": "Guía completa de dónde ver deportes en vivo en Estados Unidos en español. Canales hispanos, streaming y opciones para ver Liga MX, MLB y más.",
        "channels": [
            {"name": "Univision / UniMás", "type": "TV Abierta (gratis)", "sports": "Liga MX, Selección Mexicana, MLS"},
            {"name": "Telemundo", "type": "TV Abierta (gratis)", "sports": "Premier League, Copa del Mundo"},
            {"name": "TUDN", "type": "Cable", "sports": "Liga MX, Champions, Liga de Naciones"},
            {"name": "ESPN / ESPN2", "type": "Cable", "sports": "NFL, NBA, MLB, MLS, fútbol europeo"},
            {"name": "FOX / FS1", "type": "Cable/TV", "sports": "MLB, NFL, NASCAR, UFC"},
            {"name": "NBC / Peacock", "type": "Cable/Streaming", "sports": "Premier League, SNF, NHL"},
            {"name": "ESPN+", "type": "Streaming", "sports": "La Liga, Bundesliga, UFC, MLB extra"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions, Europa League, NWSL"},
            {"name": "Prime Video", "type": "Streaming", "sports": "Thursday Night Football (NFL)"},
            {"name": "Apple TV+ / MLS Season Pass", "type": "Streaming", "sports": "MLS (todos los partidos)"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
            {"name": "NFL+", "type": "Streaming", "sports": "Juegos de NFL en vivo (móvil)"},
        ],
        "tip": "Para ver Liga MX en EE.UU., las opciones principales son TUDN (cable), Univision (TV abierta gratis) y ViX (streaming). Para NFL, la mayoría de juegos están en FOX, CBS, NBC y ESPN, con Thursday Night Football exclusivo en Amazon Prime Video.",
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "desc": "Dónde ver deportes en vivo en Colombia. Canales de TV, cable y streaming para fútbol, béisbol y más deportes.",
        "channels": [
            {"name": "Win Sports", "type": "Streaming/Cable", "sports": "Liga BetPlay (fútbol colombiano)"},
            {"name": "ESPN Latam", "type": "Cable", "sports": "Premier League, La Liga, NBA, NFL"},
            {"name": "DirecTV Sports", "type": "Cable/Satélite", "sports": "Liga MX, Champions, fútbol sudamericano"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
        ],
        "tip": "El fútbol colombiano (Liga BetPlay) se ve exclusivamente en Win Sports. Para fútbol europeo y deportes americanos, Disney+ (ESPN) tiene la cobertura más amplia.",
    },
    "argentina": {
        "name": "Argentina",
        "flag": "🇦🇷",
        "desc": "Guía de dónde ver deportes en vivo en Argentina. Canales, streaming y opciones para fútbol, NBA, NFL y más.",
        "channels": [
            {"name": "ESPN Latam", "type": "Cable", "sports": "Premier League, La Liga, NBA, NFL, fútbol argentino"},
            {"name": "TNT Sports Argentina", "type": "Cable", "sports": "Liga Profesional, Copa Argentina"},
            {"name": "TV Pública", "type": "TV Abierta (gratis)", "sports": "Selección Argentina, eventos selectos"},
            {"name": "Star+/Disney+", "type": "Streaming", "sports": "ESPN content, La Liga, Serie A, fútbol argentino"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League, Europa League"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
        ],
        "tip": "El fútbol argentino (Liga Profesional) se transmite por TNT Sports y Disney+ (Star+). Para fútbol europeo, Disney+ tiene la mayoría de ligas. La Selección Argentina se pasa por TV Pública cuando juega de local.",
    },
    # Derechos temporada 2026/27 verificados en sep 2026 (LaLiga.com, RTVE,
    # DAZN España, Movistar, ACB). Revisar cada verano: los lotes cambian.
    "espana": {
        "name": "España",
        "flag": "🇪🇸",
        "desc": "Guía de dónde ver deportes en vivo en España: LaLiga, Champions, Liga Endesa, NBA, NFL, F1 y MotoGP. Canales de pago y qué se puede ver gratis en abierto.",
        "channels": [
            {"name": "Movistar Plus+", "type": "Cable/Streaming", "sports": "LaLiga (5 partidos por jornada), Champions League, Copa del Rey"},
            {"name": "DAZN España", "type": "Streaming", "sports": "LaLiga (5 partidos por jornada), NFL, NBA, Liga Endesa"},
            {"name": "Orange TV", "type": "Cable/Streaming", "sports": "Champions League, LaLiga"},
            {"name": "La 1 / RTVE Play", "type": "TV Abierta (gratis)", "sports": "Selección española, Copa del Rey, final de Champions"},
            {"name": "Teledeporte (TDP)", "type": "TV Abierta (gratis)", "sports": "Liga Endesa (un partido por jornada), Copa del Rey, atletismo, ciclismo"},
            {"name": "DAZN F1", "type": "Streaming (dial 69)", "sports": "Fórmula 1: libres, clasificación, sprint y carrera"},
            {"name": "DAZN MotoGP", "type": "Streaming (dial 70)", "sports": "MotoGP, Moto2 y Moto3"},
            {"name": "Prime Video", "type": "Streaming", "sports": "NBA"},
            {"name": "TV3, TVG, Aragón TV, EITB", "type": "TV Abierta (gratis)", "sports": "Liga Endesa y deporte autonómico"},
            {"name": "Vodafone TV", "type": "Cable/Streaming", "sports": "LaLiga (revende los canales de Movistar y DAZN)"},
        ],
        "tip": "Para ver LaLiga completa necesitas Movistar Plus+ y DAZN: cada uno tiene 5 partidos por jornada. Gratis y sin suscripción puedes ver un partido de LaLiga por jornada en DAZN (solo con registro, y normalmente sin Real Madrid, Barça ni Atlético), un partido de Liga Endesa por jornada en Teledeporte, los partidos de la selección y la final de Champions en La 1. La Champions es exclusiva de Movistar y Orange: ni DAZN ni Vodafone la tienen.",
    },
}


# Per-country sport priorities + internal SEO links.
# Games from priority leagues are shown FIRST on the country page.
COUNTRY_SPORT_PRIORITY = {
    "mexico": {
        "leagues": ["liga-mx", "liga-expansion", "mls", "mlb", "nfl", "nba", "champions"],
        "links": [
            {"label": "⚽ Dónde ver Liga MX", "url": "/guia/donde-ver-liga-mx"},
            {"label": "⚾ Dónde ver MLB", "url": "/guia/como-ver-mlb-en-mexico"},
            {"label": "🏈 Dónde ver NFL", "url": "/guia/donde-ver-nfl-en-mexico"},
            {"label": "🏀 Dónde ver NBA", "url": "/guia/donde-ver-nba-en-mexico"},
            {"label": "🏆 Dónde ver Champions League", "url": "/guia/donde-ver-champions-en-mexico"},
            {"label": "🏆 Copa América", "url": "/guia/donde-ver-copa-america-en-mexico"},
            {"label": "🏆 Copa Libertadores", "url": "/guia/donde-ver-copa-libertadores-en-mexico"},
            {"label": "🏎️ Dónde ver F1", "url": "/liga/f1"},
            {"label": "🏆 Mundial 2026 gratis", "url": "/guia/donde-ver-mundial-2026-gratis"},
            {"label": "📱 Apps para ver futbol", "url": "/guia/mejores-apps-ver-futbol-en-vivo"},
            {"label": "📺 Canales deportivos", "url": "/guia/guia-canales-deportivos-mexico"},
        ],
    },
    "venezuela": {
        "leagues": ["lvbp", "mlb", "champions", "la-liga", "premier-league", "serie-a", "europa-league"],
        "links": [
            {"label": "⚾ Dónde ver LVBP hoy", "url": "/lvbp-hoy"},
            {"label": "⚾ Dónde ver MLB en Venezuela", "url": "/guia/donde-ver-mlb-en-venezuela"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Premier League", "url": "/liga/premier-league"},
        ],
    },
    "republica-dominicana": {
        "leagues": ["lidom", "mlb", "nba", "champions"],
        "links": [
            {"label": "⚾ Dónde ver LIDOM hoy", "url": "/lidom-hoy"},
            {"label": "⚾ Dónde ver MLB en República Dominicana", "url": "/guia/donde-ver-mlb-en-republica-dominicana"},
            {"label": "🏀 Dónde ver NBA", "url": "/liga/nba"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
        ],
    },
    "panama": {
        "leagues": ["mlb", "nba", "champions", "premier-league"],
        "links": [
            {"label": "⚾ Dónde ver MLB en Panamá", "url": "/guia/donde-ver-mlb-en-panama"},
            {"label": "🏀 Dónde ver NBA", "url": "/liga/nba"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver Premier League", "url": "/liga/premier-league"},
        ],
    },
    "estados-unidos": {
        "leagues": ["liga-mx", "nfl", "mlb", "nba", "mls", "champions"],
        "links": [
            {"label": "⚽ Cómo ver TUDN en USA", "url": "/guia/como-ver-tudn-en-usa"},
            {"label": "⚽ Dónde ver Liga MX", "url": "/guia/donde-ver-liga-mx"},
            {"label": "🏈 NFL hoy", "url": "/liga/nfl"},
            {"label": "⚾ MLB hoy", "url": "/liga/mlb"},
        ],
    },
    "colombia": {
        "leagues": ["champions", "la-liga", "premier-league", "mlb", "nba"],
        "links": [
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Premier League", "url": "/liga/premier-league"},
        ],
    },
    "argentina": {
        "leagues": ["champions", "la-liga", "premier-league", "serie-a", "nba"],
        "links": [
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "⚽ Dónde ver La Liga", "url": "/liga/la-liga"},
            {"label": "⚽ Dónde ver Serie A", "url": "/liga/serie-a"},
        ],
    },
    "espana": {
        "leagues": ["la-liga", "champions", "europa-league", "copa-del-rey",
                    "liga-endesa", "premier-league", "f1", "motogp", "nba", "nfl"],
        "links": [
            {"label": "⚽ Dónde ver LaLiga", "url": "/liga/la-liga"},
            {"label": "🏆 Dónde ver Champions League", "url": "/liga/champions"},
            {"label": "🏀 Dónde ver Liga Endesa", "url": "/liga/liga-endesa"},
            {"label": "🏎️ Dónde ver F1", "url": "/liga/f1"},
            {"label": "🏍️ Dónde ver MotoGP", "url": "/liga/motogp"},
            {"label": "🏀 Dónde ver NBA", "url": "/liga/nba"},
            {"label": "🏈 Dónde ver NFL", "url": "/liga/nfl"},
            {"label": "📺 Deporte gratis hoy", "url": "/gratis-hoy"},
        ],
    },
}

_DAYS_ES_FULL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS_ES_FULL = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                   "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


@app.get("/donde-ver-en-{country_slug}", response_class=HTMLResponse)
async def country_page(request: Request, country_slug: str):
    """Country-specific guide — where to watch sports from each country."""
    country = COUNTRY_PAGES.get(country_slug)
    if not country:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "País no encontrado."}
        )

    all_games = await get_todays_games()
    today = datetime.now(TZ_MX)

    # Prioritize games by the country's sport interests
    prio = COUNTRY_SPORT_PRIORITY.get(country_slug, {})
    prio_leagues = prio.get("leagues", [])

    def _game_sort_key(g):
        slug = g.get("league_slug", "")
        try:
            rank = prio_leagues.index(slug)
        except ValueError:
            rank = len(prio_leagues) + 1
        return (rank, g.get("date", ""))

    sorted_games = sorted(all_games, key=_game_sort_key) if prio_leagues else all_games

    # Spanish date (server locale is English — never use %A/%B directly)
    today_display = f"{_DAYS_ES_FULL[today.weekday()]} {today.day} de {_MONTHS_ES_FULL[today.month]}, {today.year}"

    return templates.TemplateResponse(
        request, "country.html",
        context={
            "country": country,
            "country_slug": country_slug,
            "games": sorted_games,
            "total_games": len(all_games),
            "today_display": today_display,
            "sport_links": prio.get("links", []),
        },
    )


# ── Matchup Pages (SEO: "donde ver america vs chivas") ──

def _slugify_team(name: str) -> str:
    """Convert team name to URL slug."""
    import unicodedata, re
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s.lower().strip())
    return re.sub(r"[-\s]+", "-", s)


# Zonas de los países que nos dan clics, para responder "¿a qué hora?".
_FAQ_TZ = [
    ("MX", "America/Mexico_City"), ("VE", "America/Caracas"),
    ("PA", "America/Panama"), ("CO", "America/Bogota"),
    ("DO", "America/Santo_Domingo"), ("ES", "Europe/Madrid"),
    ("US", "America/New_York"),
]


def _build_match_faq(game: dict) -> list[dict]:
    """FAQ de una ficha de partido, armado SOLO con datos de ese partido.

    Por qué existe: en el SERP real de "donde ver cruz azul hoy" lo que hay
    arriba no son resultados, es un resumen de IA y un bloque "Más preguntas"
    con cosas como "¿Dónde ver el partido Monterrey Cruz Azul?". Esas preguntas
    son de enfrentamiento, que es justo lo que esta página responde — pero la
    página no las tenía marcadas en ningún lado.

    Regla, la de siempre: si el canal no viene del dato de ESTE partido, no se
    nombra. Es preferible decir "por confirmar" que inventar un canal.
    """
    home = (game.get("home") or {}).get("name", "")
    away = (game.get("away") or {}).get("name", "")
    if not home or not away or "TBD" in (home, away):
        return []

    lg = game.get("league_name", "")
    vs = f"{home} vs {away}"
    faq = []

    # ── 1. Dónde ver — la pregunta literal del bloque de Google ──
    confirmado = game.get("channels_confirmed", True)
    canales = []
    if confirmado:
        for b in (game.get("broadcasts") or []):
            ch = b.get("channel") if isinstance(b, dict) else str(b)
            if ch and ch not in canales:
                canales.append(ch)
    if canales:
        faq.append({
            "q": f"¿Dónde ver {vs}?",
            "a": f"{vs} se transmite por {', '.join(canales[:4])}."
                 + (f" Es partido de {lg}." if lg else "")
        })
    else:
        faq.append({
            "q": f"¿Dónde ver {vs}?",
            "a": f"El canal de {vs} está por confirmar. En DondeVer.app lo publicamos "
                 f"en cuanto la transmisión se anuncia."
        })

    # ── 2. A qué hora — en la hora de cada país, no solo México ──
    #
    # Ojo con España: un partido de Liga MX a las 4:50 PM de México cae a las
    # 12:50 AM en Madrid, o sea del DÍA SIGUIENTE. Dar solo "12:50 AM" manda al
    # español a prender la tele el día equivocado. Cuando la fecha local no
    # coincide con la de México, la respuesta lleva el día.
    iso = game.get("date", "")
    if iso:
        from zoneinfo import ZoneInfo as _ZI
        try:
            _utc = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            _dia_mx = _utc.astimezone(_ZI("America/Mexico_City")).date()
        except Exception:
            _utc, _dia_mx = None, None
        horas = []
        for cc, tz in _FAQ_TZ:
            distinto = False
            if _utc is not None:
                try:
                    distinto = _utc.astimezone(_ZI(tz)).date() != _dia_mx
                except Exception:
                    pass
            t = _fmt_local(iso, tz, distinto)
            if t:
                horas.append(f"{COUNTRY_LABELS[cc][0]}, {t}")
        if horas:
            faq.append({
                "q": f"¿A qué hora juega {home} contra {away}?",
                "a": f"{vs} empieza a las {'; '.join(horas)}."
            })

    # ── 3. Gratis — solo si de verdad hay un canal abierto ──
    gratis = [c for c in canales if _is_free_broadcast(c, "MX")]
    if gratis:
        faq.append({
            "q": f"¿Se puede ver {vs} gratis?",
            "a": f"Sí: en México se transmite por {', '.join(gratis)}, que es televisión "
                 f"abierta y no requiere suscripción."
        })

    # ── 4. Canal por país — la mitad de nuestros clics no son de México ──
    cbc = game.get("channels_by_country") or {}
    for cc in ("VE", "PA", "DO", "CO", "ES"):
        chs = cbc.get(cc) or []
        if chs:
            faq.append({
                "q": f"¿Qué canal transmite {vs} en {COUNTRY_LABELS[cc][0]}?",
                "a": f"En {COUNTRY_LABELS[cc][0]} se ve por {', '.join(chs[:3])}."
            })

    # ── 5. Dónde se juega ──
    sede = game.get("venue", "")
    if sede:
        faq.append({"q": f"¿En qué estadio se juega {vs}?", "a": f"{vs} se juega en {sede}."})

    return faq[:6]


def _build_team_faq(*, team_name, team_sport, team_league, stats, games,
                    upcoming_games, recent_results, today_date_str) -> list[dict]:
    """Build unique FAQ items from real team data — no boilerplate."""
    faq = []

    # ── 1. Canales — use ACTUAL broadcast data from today's games ──
    channels_today = []
    if games:
        for g in games[:2]:
            for b in g.get("broadcasts", []):
                ch = b.get("channel", "")
                if ch and ch not in channels_today:
                    channels_today.append(ch)
    if channels_today:
        ch_str = ", ".join(channels_today[:5])
        faq.append({
            "q": f"¿En qué canal pasan a {team_name} hoy?",
            "a": f"Hoy {team_name} se transmite por {ch_str}. "
                 f"Los canales pueden variar según el partido — en DondeVer.app actualizamos esta información en tiempo real."
        })
    else:
        faq.append({
            "q": f"¿En qué canal transmiten los partidos de {team_name}?",
            "a": f"Hoy {today_date_str} no hay partidos de {team_name} programados. "
                 f"Los partidos de {team_league} se transmiten en distintos canales según el país — "
                 f"consulta esta página el día del juego para ver el canal exacto."
        })

    # ── 2. Record/posición — use standings data ──
    if stats:
        record = stats.get("record", "")
        pos = stats.get("position", "")
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        sport_type = stats.get("sport_type", "")
        if sport_type == "soccer":
            pts = stats.get("points", "")
            gd = stats.get("goal_diff", "")
            # `pos` viene vacío cuando la tabla no trae posición (pasa seguido
            # en ligas que no son de ESPN). Interpolarlo a ciegas producía
            # "está en la posición # de MLB" — un gato suelto en medio de la
            # frase. Y esto no se queda en la página: el FAQ va también al
            # JSON-LD, así que una IA puede citar "posición #" tal cual.
            answer = (f"{team_name} está en la posición #{pos} de {team_league}"
                      if pos else f"{team_name} juega en {team_league}")
            if record:
                answer += f" con récord {record}"
            if pts:
                answer += f" ({pts} puntos"
                if gd:
                    answer += f", diferencia de goles {gd}"
                answer += ")"
            answer += ". Estos datos se actualizan automáticamente después de cada jornada."
            faq.append({"q": f"¿Cómo va {team_name} en la tabla de {team_league}?", "a": answer})
        elif record:
            # Mismo caso que arriba: sin posición quedaba "(posición # en MLB)".
            # Comprobado hoy en producción en la página de Dodgers.
            answer = (f"{team_name} tiene récord de {record} (posición #{pos} en {team_league})"
                      if pos else f"{team_name} tiene récord de {record} en {team_league}")
            if wins or losses:
                answer += f" — {wins} victorias y {losses} derrotas"
            answer += ". Las estadísticas se actualizan después de cada juego."
            faq.append({"q": f"¿Cuál es el récord de {team_name} esta temporada?", "a": answer})

    # ── 3. Próximo partido — use upcoming_games ──
    if upcoming_games:
        nxt = upcoming_games[0]
        opp = nxt["away"] if team_name.lower() in nxt["home"].lower() else nxt["home"]
        venue = "local" if team_name.lower() in nxt["home"].lower() else "visitante"
        faq.append({
            "q": f"¿Cuándo es el próximo partido de {team_name}?",
            "a": f"El próximo juego de {team_name} es contra {opp} como {venue}. "
                 f"Consulta la sección de próximos partidos arriba para ver la fecha, hora y canal exactos."
        })
    elif games:
        faq.append({
            "q": f"¿A qué hora juega {team_name} hoy?",
            "a": f"{team_name} tiene {len(games)} partido{'s' if len(games) > 1 else ''} hoy. "
                 f"Revisa los horarios arriba — se muestran en tu hora local automáticamente."
        })

    # ── 4. Últimos resultados — use recent_results ──
    if recent_results:
        last = recent_results[0]
        score = f"{last['home']} {last.get('home_score', '?')} - {last.get('away_score', '?')} {last['away']}"
        streak_w = sum(1 for r in recent_results if _team_won(r, team_name))
        streak_l = sum(1 for r in recent_results if _team_lost(r, team_name))
        form_str = f"{streak_w}G {len(recent_results) - streak_w - streak_l}E {streak_l}P"
        faq.append({
            "q": f"¿Cómo le fue a {team_name} en su último partido?",
            "a": f"El resultado más reciente fue {score}. "
                 f"{'En su último partido' if len(recent_results) == 1 else f'En sus últimos {len(recent_results)} partidos'}: {form_str}."
        })

    # ── 5. Streaming gratis — sport-specific with real platforms ──
    if team_sport == "futbol":
        if "Liga MX" in team_league:
            answer = (f"Algunos partidos de {team_name} se transmiten en TV abierta por Canal 5 o Azteca 7, "
                      f"dependiendo de los derechos del equipo local. ViX ofrece partidos selectos en su versión gratuita. "
                      f"En USA, Univision transmite algunos juegos sin costo.")
        elif "Premier" in team_league or "Champions" in team_league:
            answer = (f"La {team_league} no suele tener transmisiones gratuitas en México. "
                      f"Puedes verla por ESPN (con suscripción de TV de paga) o Paramount+. "
                      f"Algunos partidos se transmiten en abierto en temporadas especiales.")
        elif any(x in team_league for x in ["Argentina", "Colombia", "Ecuador", "Chile", "Perú"]):
            answer = (f"Los partidos de {team_league} se transmiten localmente por TV de paga. "
                      f"En streaming puedes verlos por ESPN, TNT Sports o las plataformas locales de cada país.")
        else:
            answer = (f"La disponibilidad gratuita de {team_league} varía por país. "
                      f"Consulta esta página el día del partido para ver las opciones de transmisión disponibles.")
    elif team_sport == "beisbol":
        answer = (f"MLB.TV y Apple TV+ ocasionalmente ofrecen juegos gratuitos de {team_name}. "
                  f"En TV de paga, ESPN transmite juegos selectos. El 'Free Game of the Day' de MLB.TV "
                  f"rota entre diferentes equipos.")
    elif team_sport == "basketball":
        answer = (f"Juegos selectos de {team_name} se transmiten por ABC en USA (gratis con antena). "
                  f"NBA League Pass ofrece prueba gratuita al inicio de temporada. "
                  f"En México, ESPN transmite partidos selectos con suscripción de TV de paga.")
    elif team_sport == "futbol americano":
        answer = (f"En México, Canal 5 y Azteca 7 transmiten juegos de NFL cada semana en abierto. "
                  f"En USA, CBS, Fox y NBC transmiten juegos gratis con antena. "
                  f"Amazon Prime tiene Thursday Night Football (requiere suscripción).")
    else:
        answer = f"Consulta DondeVer.app el día del partido para ver las opciones de transmisión de {team_name}."
    faq.append({"q": f"¿Cómo ver a {team_name} en vivo gratis?", "a": answer})

    # ── 6. Liga-specific bonus question ──
    if team_sport == "futbol" and "Liga MX" in team_league:
        faq.append({
            "q": f"¿Qué necesita {team_name} para calificar a liguilla?",
            "a": f"La calificación a liguilla depende de la posición en la tabla general. "
                 f"Los primeros 4 clasifican directo a cuartos de final, del 5° al 12° van a Play-In. "
                 f"Consulta la posición actual de {team_name} en la tabla arriba."
        })
    elif team_sport == "beisbol":
        faq.append({
            "q": f"¿Qué posición tiene {team_name} en su división?",
            "a": f"En MLB, cada división tiene 5 equipos y los ganadores de cada una clasifican a postemporada. "
                 f"También hay 3 Wild Cards por liga. Consulta las estadísticas de {team_name} arriba para ver su posición actual."
        })
    elif team_sport == "basketball":
        faq.append({
            "q": f"¿{team_name} va a Playoffs?",
            "a": f"En la NBA, los primeros 6 de cada conferencia clasifican directo a Playoffs, "
                 f"y del 7° al 10° van al Play-In Tournament. Consulta el récord actual de {team_name} arriba."
        })

    return faq


def _team_won(result: dict, team_name: str) -> bool:
    """Check if team won a recent result."""
    tn = team_name.lower()
    hs = result.get("home_score", 0)
    as_ = result.get("away_score", 0)
    try:
        hs, as_ = int(hs), int(as_)
    except (ValueError, TypeError):
        return False
    if tn in result.get("home", "").lower():
        return hs > as_
    elif tn in result.get("away", "").lower():
        return as_ > hs
    return False


def _team_lost(result: dict, team_name: str) -> bool:
    """Check if team lost a recent result."""
    tn = team_name.lower()
    hs = result.get("home_score", 0)
    as_ = result.get("away_score", 0)
    try:
        hs, as_ = int(hs), int(as_)
    except (ValueError, TypeError):
        return False
    if tn in result.get("home", "").lower():
        return hs < as_
    elif tn in result.get("away", "").lower():
        return as_ < hs
    return False


def _quick_prediction_from_odds(game: dict) -> dict | None:
    """
    Fast prediction from odds only — no API calls, no standings.
    Used on homepage cards to show '🤖 Houston 63% favorito'.
    """
    odds = game.get("odds")
    if not odds:
        return None
    h_str = odds.get("home_odds", "")
    a_str = odds.get("away_odds", "")
    d_str = odds.get("draw_odds", "")
    if not h_str or not a_str:
        return None
    try:
        h_num = int(str(h_str).replace("+", ""))
        a_num = int(str(a_str).replace("+", ""))
    except (ValueError, TypeError):
        return None
    # Implied probability from American odds (include draw when present)
    h_prob = abs(h_num) / (abs(h_num) + 100) if h_num < 0 else 100 / (h_num + 100)
    a_prob = abs(a_num) / (abs(a_num) + 100) if a_num < 0 else 100 / (a_num + 100)
    d_prob = 0.0
    if d_str:
        try:
            d_num = int(str(d_str).replace("+", ""))
            d_prob = abs(d_num) / (abs(d_num) + 100) if d_num < 0 else 100 / (d_num + 100)
        except (ValueError, TypeError):
            pass
    total = h_prob + a_prob + d_prob
    if total == 0:
        return None
    h_prob /= total
    a_prob /= total
    if h_prob >= a_prob:
        pick, prob, pick_odds = game["home"]["name"], int(h_prob * 100), h_str
        underdog, dog_odds = game["away"]["name"], a_str
    else:
        pick, prob, pick_odds = game["away"]["name"], int(a_prob * 100), a_str
        underdog, dog_odds = game["home"]["name"], h_str
    # Value detection: underdog with positive odds
    value = None
    try:
        dog_num = int(str(dog_odds).replace("+", ""))
        if dog_num > 0 and dog_num >= 140:
            value = {"team": underdog, "odds": dog_odds}
    except (ValueError, TypeError):
        pass
    return {
        "pick": pick,
        "prob": prob,
        "pick_odds": pick_odds,
        "underdog": underdog,
        "dog_odds": dog_odds,
        "value": value,
    }


def generate_match_preview(game: dict) -> dict | None:
    """
    Generate an editorial-style match preview from available data (odds, prediction).
    No extra API calls — uses only what's already attached to the game dict.
    Returns {"snippet": "...", "full": "...", "tone": "neutral|exciting|rivalry"}
    """
    home = game["home"]["name"]
    away = game["away"]["name"]
    league = game.get("league_name", "")
    prediction = game.get("prediction")
    odds = game.get("odds")
    sport = game.get("sport", "soccer")
    broadcasts = game.get("broadcasts", [])

    if not odds and not prediction:
        return None

    snippet_parts = []
    full_parts = []
    tone = "neutral"

    # Determine favorite and underdog
    fav = prediction["pick"] if prediction else None
    prob = prediction["prob"] if prediction else None
    underdog = prediction["underdog"] if prediction else None
    value = prediction.get("value") if prediction else None

    # ── Snippet (portada) ──
    # Quien llega busca canal y hora, no momios. El snippet de la tarjeta ya no
    # muestra probabilidad implícita ni apuesta de valor; ese análisis vive en
    # /partido/ (full_parts), donde el usuario sí pidió el detalle del partido.
    if fav and prob and prob < 55:
        snippet_parts.append("Partido parejo")
        tone = "exciting"

    snippet = " | ".join(snippet_parts)

    # ── Full preview (for /partido/ page) ──
    # Opening line
    sport_verb = {
        "soccer": "se enfrentan",
        "baseball": "chocan en el diamante",
        "basketball": "se miden en la duela",
        "football": "se enfrentan en el emparrillado",
        "college-football": "se enfrentan en el emparrillado",
        "hockey": "chocan en el hielo",
    }.get(sport, "se enfrentan")

    full_parts.append(
        f"{home} y {away} {sport_verb} en jornada de {league}."
    )

    # Odds analysis paragraph
    if fav and prob:
        if prob >= 70:
            full_parts.append(
                f"Las casas de apuestas dan como claro favorito a {fav} con un "
                f"{prob}% de probabilidad implícita."
            )
        elif prob >= 55:
            full_parts.append(
                f"Según las cuotas, {fav} parte con ligera ventaja ({prob}% de "
                f"probabilidad implícita), aunque {underdog} tiene opciones reales."
            )
        else:
            full_parts.append(
                f"Las cuotas reflejan un encuentro muy parejo — {fav} tiene apenas "
                f"un {prob}% de probabilidad implícita. Cualquiera puede ganar."
            )

    # Value bet angle
    if value:
        full_parts.append(
            f"Apuesta de valor: {value['team']} paga {value['odds']} — una cuota "
            f"atractiva si crees en la sorpresa."
        )

    # Channel info
    if broadcasts:
        ch_names = [b.get("channel", "") for b in broadcasts[:3] if b.get("channel")]
        if ch_names:
            full_parts.append(
                f"El partido se transmite por {', '.join(ch_names)}."
            )

    # Sin análisis real (solo la frase genérica de apertura y/o el canal) no hay
    # nada que justifique un bloque titulado "Análisis": mejor no mostrarlo.
    # full_parts[0] siempre es "X y Y se enfrentan…", así que exigimos algo más.
    if len(full_parts) < 3:
        full_parts = []

    return {
        "snippet": snippet,
        "full": " ".join(full_parts),
        "tone": tone,
    }


def _build_matchup_context(game, home_stats, away_stats, prediction, summary):
    """Build a 1-2 line narrative about why this game matters."""
    home_left = game.get("sport", "") in ("soccer", "boxing", "mma")
    first = game["home"]["name"] if home_left else game["away"]["name"]
    second = game["away"]["name"] if home_left else game["home"]["name"]
    league = game.get("league_name", "")
    parts = []

    # Standings-based context
    for stats, team in [(home_stats, game["home"]["name"]), (away_stats, game["away"]["name"])]:
        record = stats.get("record", "")
        standing = stats.get("standing", "")
        if standing and "1" in str(standing)[:2]:
            parts.append(f"{team} llega como lider")
        elif record:
            parts.append(f"{team} ({record})")

    # Streak context from prediction
    if prediction:
        reason = prediction.get("reason", "")
        if "racha" in reason.lower():
            parts.append(reason.split("|")[0].strip())

    # H2H context
    h2h = summary.get("seasonseries", [])
    if h2h:
        for series in h2h:
            events = series.get("events", [])
            if events:
                parts.append(f"{len(events)} enfrentamientos esta temporada")
                break

    if not parts:
        parts.append(f"Partido de {league}")

    return " — ".join(parts[:2]) + "."


@app.get("/donde-ver/{matchup_slug}", response_class=HTMLResponse)
async def matchup_page(request: Request, matchup_slug: str):
    """
    SEO matchup page: /donde-ver/america-vs-chivas
    Finds today's game matching the team names in the slug and renders it.
    """
    parts = matchup_slug.split("-vs-")
    if len(parts) != 2:
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Formato invalido. Usa /donde-ver/equipo1-vs-equipo2"}
        )

    slug_a, slug_b = parts[0].strip(), parts[1].strip()
    all_games = await get_todays_games()

    # Find the matching game
    matched_game = None
    for g in all_games:
        home_slug = _slugify_team(g["home"]["name"])
        away_slug = _slugify_team(g["away"]["name"])
        # Match in either order
        if (slug_a in home_slug and slug_b in away_slug) or \
           (slug_b in home_slug and slug_a in away_slug) or \
           (slug_a in away_slug and slug_b in home_slug) or \
           (slug_b in away_slug and slug_a in home_slug):
            matched_game = g
            break

    if not matched_game:
        # Try fuzzy: check if any word matches
        for g in all_games:
            h = _slugify_team(g["home"]["name"])
            a = _slugify_team(g["away"]["name"])
            a_in = any(w in h or w in a for w in slug_a.split("-") if len(w) > 3)
            b_in = any(w in h or w in a for w in slug_b.split("-") if len(w) > 3)
            if a_in and b_in:
                matched_game = g
                break

    if not matched_game:
        # Redirect to team page instead of 404 — preserves SEO value
        # Try to find a matching team page for slug_a or slug_b
        for s in (slug_a, slug_b):
            if s in POPULAR_TEAMS:
                return RedirectResponse(url=f"/equipo/{s}", status_code=302)
            # Check TEAM_ALIASES for partial match
            for alias, _search in TEAM_ALIASES.items():
                if s in alias or alias in s:
                    team_slug_match = alias.replace(" ", "-")
                    if team_slug_match in POPULAR_TEAMS:
                        return RedirectResponse(url=f"/equipo/{team_slug_match}", status_code=302)
                    break
        # Fallback: redirect to homepage
        return RedirectResponse(url="/", status_code=302)

    # Fetch odds for the game
    odds = None
    league_slug = matched_game.get("league_slug", "")
    if matched_game["status"]["state"] == "pre":
        try:
            odds_list = await fetch_odds(league_slug)
            odds = match_odds_to_game(matched_game, odds_list)
        except Exception:
            pass

    # ── Enriched data: team stats, ESPN summary, AI prediction ──
    home_slug = _team_name_to_slug(matched_game["home"]["name"])
    away_slug = _team_name_to_slug(matched_game["away"]["name"])

    home_stats = {}
    away_stats = {}
    try:
        if home_slug:
            home_stats = await get_team_stats(home_slug)
        if away_slug:
            away_stats = await get_team_stats(away_slug)
    except Exception:
        pass

    # ESPN event summary (H2H, rosters, standings)
    summary = {}
    try:
        sport = matched_game.get("sport", "")
        league_info = ALL_LEAGUES.get(league_slug)
        espn_league = league_info[1] if isinstance(league_info, tuple) else league_slug
        event_id = matched_game.get("id", "")
        if sport and espn_league and event_id:
            summary = await fetch_espn_event_summary(sport, espn_league, event_id)
    except Exception:
        pass

    # AI prediction using stat tip engine
    prediction = None
    try:
        from send_whatsapp_daily import _generate_stat_tip, _find_team_in_standings
        from send_whatsapp_daily import fetch_standings, LEAGUE_ESPN_MAP
        standings = []
        espn_info = LEAGUE_ESPN_MAP.get(league_slug)
        if espn_info:
            s_sport, s_league = espn_info
            standings = await fetch_standings(s_sport, s_league)
        prediction = await _generate_stat_tip(matched_game, odds, standings)
    except Exception as e:
        logger.warning(f"Prediction failed for matchup {matchup_slug}: {e}")

    # Context narrative — 1-2 lines about why this game matters
    context_narrative = _build_matchup_context(
        matched_game, home_stats, away_stats, prediction, summary
    )

    return templates.TemplateResponse(
        request, "matchup.html",
        context={
            "game": matched_game,
            "odds": odds,
            "matchup_slug": matchup_slug,
            "home_slug": home_slug,
            "away_slug": away_slug,
            "home_stats": home_stats,
            "away_stats": away_stats,
            "summary": summary,
            "prediction": prediction,
            "context_narrative": context_narrative,
        },
    )


# ── Streaming Comparator ────────────────────────────────

@app.get("/streaming", response_class=HTMLResponse)
async def streaming_page(request: Request):
    return templates.TemplateResponse(request, "streaming.html")


# ── Team Pages ──────────────────────────────────────────

# Popular teams for SEO (slug -> display name)
# slug -> {name, sport_label, league, keywords}
# sport_label se usa en SEO: "futbol", "basketball", "futbol americano", "beisbol"
POPULAR_TEAMS = {
    # Liga MX
    "chivas": {"name": "Guadalajara (Chivas)", "sport": "futbol", "league": "Liga MX", "aka": "Chivas, Guadalajara, Rebaño Sagrado"},
    "america": {"name": "Club América", "sport": "futbol", "league": "Liga MX", "aka": "América, Águilas, Club America"},
    "cruz-azul": {"name": "Cruz Azul", "sport": "futbol", "league": "Liga MX", "aka": "Cruz Azul, La Máquina, Cementeros"},
    "pumas": {"name": "Pumas UNAM", "sport": "futbol", "league": "Liga MX", "aka": "Pumas, UNAM, Auriazules"},
    "tigres": {"name": "Tigres UANL", "sport": "futbol", "league": "Liga MX", "aka": "Tigres, UANL, Tigres de Monterrey"},
    "monterrey": {"name": "Monterrey", "sport": "futbol", "league": "Liga MX", "aka": "Rayados, Monterrey, Rayados de Monterrey"},
    "toluca": {"name": "Toluca", "sport": "futbol", "league": "Liga MX", "aka": "Toluca, Diablos Rojos, Choriceros"},
    "santos": {"name": "Santos Laguna", "sport": "futbol", "league": "Liga MX", "aka": "Santos Laguna, Guerreros"},
    "leon": {"name": "León", "sport": "futbol", "league": "Liga MX", "aka": "León, Club León, La Fiera"},
    "pachuca": {"name": "Pachuca", "sport": "futbol", "league": "Liga MX", "aka": "Pachuca, Tuzos"},
    "atlas": {"name": "Atlas", "sport": "futbol", "league": "Liga MX", "aka": "Atlas, Zorros, Rojinegros"},
    "necaxa": {"name": "Necaxa", "sport": "futbol", "league": "Liga MX", "aka": "Necaxa, Rayos"},
    "puebla": {"name": "Puebla", "sport": "futbol", "league": "Liga MX", "aka": "Puebla, La Franja, Camoteros"},
    "queretaro": {"name": "Querétaro", "sport": "futbol", "league": "Liga MX", "aka": "Querétaro, Gallos Blancos"},
    "mazatlan": {"name": "Mazatlán FC", "sport": "futbol", "league": "Liga MX", "aka": "Mazatlán, Cañoneros"},
    "tijuana": {"name": "Club Tijuana", "sport": "futbol", "league": "Liga MX", "aka": "Tijuana, Xolos, Xoloitzcuintles"},
    "juarez": {"name": "FC Juárez", "sport": "futbol", "league": "Liga MX", "aka": "Juárez, Bravos"},
    # Europa
    "real-madrid": {"name": "Real Madrid", "sport": "futbol", "league": "La Liga", "aka": "Real Madrid, Merengues"},
    "barcelona": {"name": "FC Barcelona", "sport": "futbol", "league": "La Liga", "aka": "Barcelona, Barça, Blaugrana"},
    "liverpool": {"name": "Liverpool FC", "sport": "futbol", "league": "Premier League", "aka": "Liverpool, Reds"},
    "manchester-city": {"name": "Manchester City", "sport": "futbol", "league": "Premier League", "aka": "Man City, Citizens"},
    "manchester-united": {"name": "Manchester United", "sport": "futbol", "league": "Premier League", "aka": "Man United, Red Devils"},
    "arsenal": {"name": "Arsenal", "sport": "futbol", "league": "Premier League", "aka": "Arsenal, Gunners"},
    "chelsea": {"name": "Chelsea", "sport": "futbol", "league": "Premier League", "aka": "Chelsea, Blues"},
    "psg": {"name": "Paris Saint-Germain", "sport": "futbol", "league": "Ligue 1", "aka": "PSG, Paris"},
    "bayern": {"name": "Bayern Munich", "sport": "futbol", "league": "Bundesliga", "aka": "Bayern, Bayern München"},
    "juventus": {"name": "Juventus", "sport": "futbol", "league": "Serie A", "aka": "Juventus, Juve, Vecchia Signora"},
    "inter-milan": {"name": "Inter de Milán", "sport": "futbol", "league": "Serie A", "aka": "Inter, Nerazzurri"},
    # MLS
    "lafc": {"name": "Los Angeles FC", "sport": "futbol", "league": "MLS", "aka": "LAFC, Los Angeles FC"},
    "la-galaxy": {"name": "LA Galaxy", "sport": "futbol", "league": "MLS", "aka": "Galaxy, LA Galaxy"},
    "inter-miami": {"name": "Inter Miami CF", "sport": "futbol", "league": "MLS", "aka": "Inter Miami, Miami CF, Messi Miami"},
    "austin-fc": {"name": "Austin FC", "sport": "futbol", "league": "MLS", "aka": "Austin FC, Austin"},
    "houston-dynamo": {"name": "Houston Dynamo FC", "sport": "futbol", "league": "MLS", "aka": "Dynamo, Houston Dynamo"},
    "fc-dallas": {"name": "FC Dallas", "sport": "futbol", "league": "MLS", "aka": "FC Dallas, Dallas FC"},
    # Europa extra
    "atletico-madrid": {"name": "Atlético de Madrid", "sport": "futbol", "league": "La Liga", "aka": "Atlético, Atleti, Colchoneros"},
    "ac-milan": {"name": "AC Milan", "sport": "futbol", "league": "Serie A", "aka": "Milan, AC Milan, Rossoneri"},
    "napoli": {"name": "SSC Napoli", "sport": "futbol", "league": "Serie A", "aka": "Napoli, Nápoles"},
    "borussia-dortmund": {"name": "Borussia Dortmund", "sport": "futbol", "league": "Bundesliga", "aka": "Dortmund, BVB, Borussia"},
    "tottenham": {"name": "Tottenham Hotspur", "sport": "futbol", "league": "Premier League", "aka": "Tottenham, Spurs, Hotspur"},
    "aston-villa": {"name": "Aston Villa", "sport": "futbol", "league": "Premier League", "aka": "Aston Villa, Villa, Villans"},
    # NBA
    "lakers": {"name": "Los Angeles Lakers", "sport": "basketball", "league": "NBA", "aka": "Lakers, LA Lakers"},
    "celtics": {"name": "Boston Celtics", "sport": "basketball", "league": "NBA", "aka": "Celtics, Boston"},
    "warriors": {"name": "Golden State Warriors", "sport": "basketball", "league": "NBA", "aka": "Warriors, Dubs, Golden State"},
    "bulls": {"name": "Chicago Bulls", "sport": "basketball", "league": "NBA", "aka": "Bulls, Chicago"},
    "heat": {"name": "Miami Heat", "sport": "basketball", "league": "NBA", "aka": "Heat, Miami"},
    "knicks": {"name": "New York Knicks", "sport": "basketball", "league": "NBA", "aka": "Knicks, NY Knicks"},
    "nuggets": {"name": "Denver Nuggets", "sport": "basketball", "league": "NBA", "aka": "Nuggets, Denver"},
    "bucks": {"name": "Milwaukee Bucks", "sport": "basketball", "league": "NBA", "aka": "Bucks, Milwaukee"},
    "mavericks": {"name": "Dallas Mavericks", "sport": "basketball", "league": "NBA", "aka": "Mavericks, Mavs, Dallas Mavs"},
    "clippers": {"name": "LA Clippers", "sport": "basketball", "league": "NBA", "aka": "Clippers, LA Clippers"},
    "suns": {"name": "Phoenix Suns", "sport": "basketball", "league": "NBA", "aka": "Suns, Phoenix"},
    "spurs-nba": {"name": "San Antonio Spurs", "sport": "basketball", "league": "NBA", "aka": "Spurs SA, San Antonio, Wemby"},
    "76ers": {"name": "Philadelphia 76ers", "sport": "basketball", "league": "NBA", "aka": "76ers, Sixers, Philadelphia"},
    "thunder": {"name": "Oklahoma City Thunder", "sport": "basketball", "league": "NBA", "aka": "Thunder, OKC"},
    "timberwolves": {"name": "Minnesota Timberwolves", "sport": "basketball", "league": "NBA", "aka": "Timberwolves, Wolves, Minnesota"},
    "cavaliers": {"name": "Cleveland Cavaliers", "sport": "basketball", "league": "NBA", "aka": "Cavaliers, Cavs, Cleveland"},
    # NFL
    "cowboys": {"name": "Dallas Cowboys", "sport": "futbol americano", "league": "NFL", "aka": "Cowboys, Vaqueros, Dallas"},
    "chiefs": {"name": "Kansas City Chiefs", "sport": "futbol americano", "league": "NFL", "aka": "Chiefs, Kansas City"},
    "49ers": {"name": "San Francisco 49ers", "sport": "futbol americano", "league": "NFL", "aka": "49ers, Niners, San Francisco"},
    "eagles": {"name": "Philadelphia Eagles", "sport": "futbol americano", "league": "NFL", "aka": "Eagles, Philadelphia, Águilas"},
    "packers": {"name": "Green Bay Packers", "sport": "futbol americano", "league": "NFL", "aka": "Packers, Green Bay"},
    "steelers": {"name": "Pittsburgh Steelers", "sport": "futbol americano", "league": "NFL", "aka": "Steelers, Pittsburgh, Acereros"},
    "raiders": {"name": "Las Vegas Raiders", "sport": "futbol americano", "league": "NFL", "aka": "Raiders, Las Vegas, Oakland Raiders"},
    "dolphins": {"name": "Miami Dolphins", "sport": "futbol americano", "league": "NFL", "aka": "Dolphins, Delfines, Miami"},
    "patriots": {"name": "New England Patriots", "sport": "futbol americano", "league": "NFL", "aka": "Patriots, Patriotas, New England"},
    "texans": {"name": "Houston Texans", "sport": "futbol americano", "league": "NFL", "aka": "Texans, Houston, Tejanos"},
    "ravens": {"name": "Baltimore Ravens", "sport": "futbol americano", "league": "NFL", "aka": "Ravens, Cuervos, Baltimore"},
    "bears": {"name": "Chicago Bears", "sport": "futbol americano", "league": "NFL", "aka": "Bears, Osos, Chicago Bears"},
    "rams": {"name": "Los Angeles Rams", "sport": "futbol americano", "league": "NFL", "aka": "Rams, LA Rams, Carneros"},
    "chargers": {"name": "Los Angeles Chargers", "sport": "futbol americano", "league": "NFL", "aka": "Chargers, LA Chargers, Cargadores"},
    "broncos": {"name": "Denver Broncos", "sport": "futbol americano", "league": "NFL", "aka": "Broncos, Denver"},
    "bills": {"name": "Buffalo Bills", "sport": "futbol americano", "league": "NFL", "aka": "Bills, Buffalo"},
    "lions": {"name": "Detroit Lions", "sport": "futbol americano", "league": "NFL", "aka": "Lions, Leones, Detroit"},
    "vikings": {"name": "Minnesota Vikings", "sport": "futbol americano", "league": "NFL", "aka": "Vikings, Vikingos, Minnesota"},
    "bengals": {"name": "Cincinnati Bengals", "sport": "futbol americano", "league": "NFL", "aka": "Bengals, Bengalíes, Cincinnati"},
    "giants-nfl": {"name": "New York Giants", "sport": "futbol americano", "league": "NFL", "aka": "Giants NY, Gigantes, NY Giants"},
    "jets": {"name": "New York Jets", "sport": "futbol americano", "league": "NFL", "aka": "Jets, NY Jets"},
    "saints": {"name": "New Orleans Saints", "sport": "futbol americano", "league": "NFL", "aka": "Saints, Santos, New Orleans"},
    "seahawks": {"name": "Seattle Seahawks", "sport": "futbol americano", "league": "NFL", "aka": "Seahawks, Seattle"},
    "commanders": {"name": "Washington Commanders", "sport": "futbol americano", "league": "NFL", "aka": "Commanders, Washington"},
    "cardinals-nfl": {"name": "Arizona Cardinals", "sport": "futbol americano", "league": "NFL", "aka": "Cardinals AZ, Cardenales, Arizona Cardinals"},
    "buccaneers": {"name": "Tampa Bay Buccaneers", "sport": "futbol americano", "league": "NFL", "aka": "Buccaneers, Bucs, Tampa Bay"},
    "falcons": {"name": "Atlanta Falcons", "sport": "futbol americano", "league": "NFL", "aka": "Falcons, Halcones, Atlanta"},
    "panthers-nfl": {"name": "Carolina Panthers", "sport": "futbol americano", "league": "NFL", "aka": "Panthers, Panteras, Carolina"},
    "colts": {"name": "Indianapolis Colts", "sport": "futbol americano", "league": "NFL", "aka": "Colts, Potros, Indianapolis"},
    "jaguars": {"name": "Jacksonville Jaguars", "sport": "futbol americano", "league": "NFL", "aka": "Jaguars, Jaguares, Jacksonville"},
    "titans": {"name": "Tennessee Titans", "sport": "futbol americano", "league": "NFL", "aka": "Titans, Titanes, Tennessee"},
    # MLB
    "dodgers": {"name": "Los Angeles Dodgers", "sport": "beisbol", "league": "MLB", "aka": "Dodgers, LA Dodgers"},
    "yankees": {"name": "New York Yankees", "sport": "beisbol", "league": "MLB", "aka": "Yankees, Yanquis, NY Yankees"},
    "red-sox": {"name": "Boston Red Sox", "sport": "beisbol", "league": "MLB", "aka": "Red Sox, Medias Rojas, Boston"},
    "astros": {"name": "Houston Astros", "sport": "beisbol", "league": "MLB", "aka": "Astros, Houston"},
    "mets": {"name": "New York Mets", "sport": "beisbol", "league": "MLB", "aka": "Mets, NY Mets"},
    "padres": {"name": "San Diego Padres", "sport": "beisbol", "league": "MLB", "aka": "Padres, San Diego"},
    "angels": {"name": "Los Angeles Angels", "sport": "beisbol", "league": "MLB", "aka": "Angels, Angelinos, LA Angels"},
    "athletics": {"name": "Athletics", "sport": "beisbol", "league": "MLB", "aka": "Athletics, A's, Atléticos"},
    "blue-jays": {"name": "Toronto Blue Jays", "sport": "beisbol", "league": "MLB", "aka": "Blue Jays, Azulejos, Toronto"},
    "braves": {"name": "Atlanta Braves", "sport": "beisbol", "league": "MLB", "aka": "Braves, Atlanta"},
    "brewers": {"name": "Milwaukee Brewers", "sport": "beisbol", "league": "MLB", "aka": "Brewers, Cerveceros, Milwaukee"},
    "cardinals": {"name": "St. Louis Cardinals", "sport": "beisbol", "league": "MLB", "aka": "Cardinals, Cardenales, St. Louis"},
    "cubs": {"name": "Chicago Cubs", "sport": "beisbol", "league": "MLB", "aka": "Cubs, Cachorros, Chicago Cubs"},
    "diamondbacks": {"name": "Arizona Diamondbacks", "sport": "beisbol", "league": "MLB", "aka": "Diamondbacks, D-backs, Arizona"},
    "giants": {"name": "San Francisco Giants", "sport": "beisbol", "league": "MLB", "aka": "Giants, Gigantes, San Francisco"},
    "guardians": {"name": "Cleveland Guardians", "sport": "beisbol", "league": "MLB", "aka": "Guardians, Guardianes, Cleveland"},
    "mariners": {"name": "Seattle Mariners", "sport": "beisbol", "league": "MLB", "aka": "Mariners, Marineros, Seattle"},
    "marlins": {"name": "Miami Marlins", "sport": "beisbol", "league": "MLB", "aka": "Marlins, Miami"},
    "nationals": {"name": "Washington Nationals", "sport": "beisbol", "league": "MLB", "aka": "Nationals, Nacionales, Washington"},
    "orioles": {"name": "Baltimore Orioles", "sport": "beisbol", "league": "MLB", "aka": "Orioles, Baltimore"},
    "phillies": {"name": "Philadelphia Phillies", "sport": "beisbol", "league": "MLB", "aka": "Phillies, Filis, Philadelphia"},
    "pirates": {"name": "Pittsburgh Pirates", "sport": "beisbol", "league": "MLB", "aka": "Pirates, Piratas, Pittsburgh"},
    "rangers": {"name": "Texas Rangers", "sport": "beisbol", "league": "MLB", "aka": "Rangers, Texas"},
    "rays": {"name": "Tampa Bay Rays", "sport": "beisbol", "league": "MLB", "aka": "Rays, Rayas, Tampa Bay"},
    "reds": {"name": "Cincinnati Reds", "sport": "beisbol", "league": "MLB", "aka": "Reds, Rojos, Cincinnati"},
    "rockies": {"name": "Colorado Rockies", "sport": "beisbol", "league": "MLB", "aka": "Rockies, Colorado"},
    "royals": {"name": "Kansas City Royals", "sport": "beisbol", "league": "MLB", "aka": "Royals, Reales, Kansas City"},
    "tigers": {"name": "Detroit Tigers", "sport": "beisbol", "league": "MLB", "aka": "Tigers, Tigres de Detroit, Detroit"},
    "twins": {"name": "Minnesota Twins", "sport": "beisbol", "league": "MLB", "aka": "Twins, Gemelos, Minnesota"},
    "white-sox": {"name": "Chicago White Sox", "sport": "beisbol", "league": "MLB", "aka": "White Sox, Medias Blancas, Chicago White Sox"},
    # NHL
    "bruins": {"name": "Boston Bruins", "sport": "hockey", "league": "NHL", "aka": "Bruins, Boston"},
    "golden-knights": {"name": "Vegas Golden Knights", "sport": "hockey", "league": "NHL", "aka": "Golden Knights, Vegas, VGK"},
    "avalanche": {"name": "Colorado Avalanche", "sport": "hockey", "league": "NHL", "aka": "Avalanche, Avs, Colorado"},
    "panthers-nhl": {"name": "Florida Panthers", "sport": "hockey", "league": "NHL", "aka": "Panthers Florida, Florida"},
    "rangers-nhl": {"name": "New York Rangers", "sport": "hockey", "league": "NHL", "aka": "Rangers NY, NY Rangers"},
    "maple-leafs": {"name": "Toronto Maple Leafs", "sport": "hockey", "league": "NHL", "aka": "Maple Leafs, Toronto, Leafs"},
    "oilers": {"name": "Edmonton Oilers", "sport": "hockey", "league": "NHL", "aka": "Oilers, Edmonton"},
    "stars": {"name": "Dallas Stars", "sport": "hockey", "league": "NHL", "aka": "Stars, Dallas Stars, Estrellas"},
    "blackhawks": {"name": "Chicago Blackhawks", "sport": "hockey", "league": "NHL", "aka": "Blackhawks, Hawks, Chicago"},
    "penguins": {"name": "Pittsburgh Penguins", "sport": "hockey", "league": "NHL", "aka": "Penguins, Pinguinos, Pittsburgh"},
    "capitals": {"name": "Washington Capitals", "sport": "hockey", "league": "NHL", "aka": "Capitals, Caps, Washington"},
    # UFC
    "ufc": {"name": "UFC", "sport": "MMA", "league": "UFC", "aka": "UFC, Ultimate Fighting"},
}

_TWO_WORD_NICKS = {"red sox", "white sox", "blue jays", "maple leafs", "trail blazers", "golden knights",
                   "red wings", "blue jackets", "diamondbacks", "rays", "sun", "sky", "fever", "dream",
                   "sparks", "storm", "aces", "liberty", "lynx", "mercury", "mystics", "wings", "valkyries"}
_US_STYLE_LEAGUES = {"MLB", "NBA", "NFL", "NHL", "WNBA", "MLS", "College Football", "NCAA"}


def _short_team_name(full_name: str, league: str = "") -> str:
    """'Los Angeles Dodgers' → 'Dodgers'; 'Boston Red Sox' → 'Red Sox'; 'Tigres UANL' → 'Tigres'.
    Soccer clubs keep their name minus 'Club/FC/CF' noise. That's how people search."""
    if not full_name:
        return ""
    name = full_name.strip()
    low = name.lower()
    # US-style "City Nickname" → nickname
    us_style = league in _US_STYLE_LEAGUES or any(
        low.endswith(" " + n) for n in _TWO_WORD_NICKS) or bool(re.search(
        r"\b(dodgers|yankees|phillies|padres|mets|braves|astros|cubs|orioles|brewers|giants|rangers|"
        r"cardinals|mariners|guardians|tigers|twins|royals|angels|athletics|marlins|nationals|pirates|"
        r"reds|rockies|cowboys|chiefs|eagles|packers|49ers|steelers|patriots|ravens|bills|lions|raiders|"
        r"broncos|rams|chargers|dolphins|jets|bears|vikings|saints|texans|bengals|browns|jaguars|titans|"
        r"colts|falcons|panthers|buccaneers|seahawks|commanders|lakers|celtics|warriors|heat|knicks|"
        r"bulls|nets|bucks|suns|mavericks|nuggets|clippers|rockets|spurs|thunder|kings|grizzlies|"
        r"pelicans|timberwolves|hawks|hornets|magic|pistons|pacers|raptors|wizards|cavaliers|jazz)\b", low))
    if us_style:
        for n in _TWO_WORD_NICKS:
            if low.endswith(" " + n):
                return name[-len(n):]
        parts = name.split()
        return parts[-1] if len(parts) > 1 else name
    # Soccer / LatAm: strip club noise
    name = re.sub(r"^(club|cf|fc|cd|ca|deportivo)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+(uanl|unam|fc|cf|sc|ac|bc|de la uanl)$", "", name, flags=re.I)
    return name.strip() or full_name


def _build_team_seo(team_name: str, team_league: str, search_term: str, games: list,
                    upcoming_games: list, recent_results: list, is_nfl: bool = False) -> dict:
    """Title/description/H1 that answer hora + canal for today's game.

    Priority: live now → today (pre) → today finished → next game → default.
    Titles kept ≤ ~65 chars.
    """
    st = search_term.lower()
    lg = team_league or ("NFL" if is_nfl else "")
    lg_sfx = f" {lg}" if lg else ""
    full_name = team_name
    team_name = _short_team_name(team_name, lg)  # "Los Angeles Dodgers" → "Dodgers" (como buscan)

    def _opp_and_channel(game):
        home = game.get("home", {}) or {}
        away = game.get("away", {}) or {}
        is_home = st in (home.get("name", "") or "").lower()
        opp_full = away.get("name", "") if is_home else home.get("name", "")
        opp = _short_team_name(opp_full, lg)
        # El canal solo se nombra si viene de un dato real de este partido.
        # channels_confirmed es False cuando get_todays_games cayó al default de
        # la liga: en ese caso el título dice la hora y calla el canal, en vez de
        # afirmar uno que nadie verificó.
        if not game.get("channels_confirmed", True):
            return opp, ""
        chans = [b.get("channel") if isinstance(b, dict) else str(b)
                 for b in (game.get("broadcasts") or [])]
        chans = [c for c in chans if c]
        return opp, (chans[0] if chans else "")

    today = [g for g in games if (g.get("status") or {}).get("state") in ("pre", "in", "post")]
    live = [g for g in today if g["status"]["state"] == "in"]
    pre = [g for g in today if g["status"]["state"] == "pre"]
    post = [g for g in today if g["status"]["state"] == "post"]

    # Nota SEO: la consulta real es "donde ver <equipo> hoy" (69 clics de
    # "donde ver cruz azul hoy", 45 de "donde ver yankees hoy"...). Con posición
    # media 10 y CTR 1.1%, lo que más pesa es que el título empiece con la frase
    # exacta que buscaron — Google la resalta en negritas. Por eso "Dónde ver"
    # va al frente y no detrás de un pipe, y la descripción responde hora+canal
    # en la primera línea en vez de listar países.
    if live:
        g = live[0]
        opp, ch = _opp_and_channel(g)
        ch_txt = f" por {ch}" if ch else ""
        return {
            "title": f"Dónde ver {team_name} vs {opp} EN VIVO hoy{ch_txt}",
            "h1": f"{team_name} vs {opp} en vivo hoy{ch_txt}",
            "desc": f"{team_name} vs {opp} se juega ahora{ch_txt}. Marcador en vivo, canal de TV y cómo verlo en streaming{lg_sfx} desde México y Latinoamérica.",
        }
    if pre:
        g = pre[0]
        opp, ch = _opp_and_channel(g)
        t = format_mx_time(g.get("date", "")).lstrip("0")
        ch_txt = f" por {ch}" if ch else ""
        t_txt = f" {t} MX" if t else ""
        return {
            "title": f"Dónde ver {team_name} vs {opp} hoy:{t_txt}{ch_txt}",
            "h1": f"Dónde ver {team_name} hoy vs {opp}:{t_txt}{ch_txt}",
            "desc": f"{team_name} vs {opp} hoy{t_txt} (hora de México){ch_txt}. Horario en tu país, canal de TV, streaming{lg_sfx} y qué hacer si no tienes cable.",
        }
    if post:
        g = post[0]
        opp, _ = _opp_and_channel(g)
        home = g.get("home", {}) or {}
        away = g.get("away", {}) or {}
        hs, as_ = home.get("score", ""), away.get("score", "")
        score = f" {hs}-{as_}" if hs != "" and as_ != "" else ""
        return {
            "title": f"{team_name} hoy{score} vs {opp} | Dónde ver el próximo juego",
            "h1": f"{team_name} hoy: resultado vs {opp} y próximo partido",
            "desc": f"{team_name} vs {opp}{score}: así quedó hoy. Y dónde ver el próximo juego — horario en tu país, canal de TV y streaming{lg_sfx}.",
        }
    if upcoming_games:
        u = upcoming_games[0]
        is_home = st in (u.get("home", "") or "").lower()
        opp = _short_team_name(u.get("away", "") if is_home else u.get("home", ""), lg)
        when = format_mx_day_time(u.get("date_utc", "") or u.get("date", "") or "")
        chs = u.get("channels") or []
        ch = ""
        if chs:
            c0 = chs[0]
            ch = c0.get("channel") if isinstance(c0, dict) else str(c0)
        ch_txt = f" en {ch}" if ch else ""
        when_txt = f" {when}" if when else ""
        when_short = when.replace(" · ", " ")[:3].lower() + when.replace(" · ", " ")[3:] if when else ""
        when_short_txt = f" {when_short}" if when_short else ""
        return {
            # Sin fecha no se escribe " MX" suelto: quedaba "vs Mets: MX y canal".
            "title": (f"Dónde ver {team_name} vs {opp}:{when_short_txt} MX y canal"
                      if when_short_txt else f"Dónde ver {team_name} vs {opp}: horario y canal"),
            "h1": f"Dónde ver {team_name}: próximo partido vs {opp}{when_txt}",
            "desc": f"{team_name} no juega hoy. El próximo es vs {opp}{when_txt}{' (hora de México)' if when_txt else ''}{ch_txt}. Calendario completo, canal de TV y streaming{lg_sfx}.",
        }
    return {
        "title": f"Dónde ver {team_name} hoy: horario, canal y streaming",
        "h1": f"Dónde ver {full_name} hoy en vivo",
        "desc": f"Dónde ver a {team_name} hoy: horario en tu país, canal de TV y opciones de streaming{lg_sfx}. Calendario de los próximos partidos actualizado cada día.",
    }


_ATHLETE_LEAGUES = {"Formula 1": "f1", "UFC": "ufc", "Boxeo": "boxing", "MotoGP": "motogp", "IndyCar": "indycar", "NASCAR": "nascar"}


def _athlete_matches(name: str, ev: dict) -> bool:
    """¿Aparece este peleador en la cartelera? Compara por apellido normalizado."""
    from events_api import slugify as _sl
    key = _sl(name).split("-")
    key = [k for k in key if len(k) > 3 and k not in ("checo", "canelo", "pitbull")] or key
    surname = key[-1]
    for f in ev.get("fights", []):
        for x in f.get("fighters", []):
            if surname in _sl(x.get("name", "")).split("-"):
                return True
    return False


async def _athlete_page(request: Request, slug: str, info: dict):
    """Página de piloto/peleador: su próximo evento y los siguientes. 'próxima pelea de canelo'."""
    from events_api import fetch_events
    kind = _ATHLETE_LEAGUES[info["league"]]
    name = info["name"]
    short = info.get("aka") or _short_team_name(name, "")
    evs = await fetch_events(kind, days_back=60, days_ahead=200)
    if kind in _RACE_KINDS:
        mine = [e for e in evs if not e.get("is_minor")]
    else:
        mine = [e for e in evs if _athlete_matches(name, e)]
    upcoming = [e for e in mine if e["status"] != "post"]
    past = [e for e in mine if e["status"] == "post"][-5:]
    nxt = upcoming[0] if upcoming else None
    meta = _EVENT_META[kind]
    if nxt:
        t = _fmt_local(nxt["date"], "America/Mexico_City", True)
        ch = (nxt["channels"].get("MX") or [""])[0].replace(" (por confirmar)", "")
        if kind in _RACE_KINDS:
            title = f"Próxima carrera de {short}: {nxt['short_name']} {t} MX — dónde ver | DondeVer"
            h1 = f"{name}: próxima carrera, horarios en México y dónde ver"
            answer = f"La próxima carrera de <b>{short}</b> es el <b>{nxt['name']}</b>: <b>{t} hora de México</b> por <b>{ch}</b>."
        else:
            main = next((f for f in nxt["fights"] if _athlete_matches(name, {"fights": [f]})), None)
            opp = ""
            if main:
                others = [x["name"] for x in main["fighters"] if _short_team_name(x["name"], "").split()[-1].lower() not in name.lower()]
                opp = others[0] if others else ""
            title = f"Próxima pelea de {short}: {t} MX{(' vs ' + _short_team_name(opp, '')) if opp else ''} en {ch} | Dónde ver"
            h1 = f"{name}: próxima pelea, hora en México y dónde ver"
            answer = (f"La próxima pelea de <b>{short}</b>{(' contra <b>' + opp + '</b>') if opp else ''} es el "
                      f"<b>{t} hora de México</b> ({nxt['name']}) por <b>{ch}</b>.")
        desc = re.sub("<[^>]+>", "", answer) + f" Cartelera, horarios por país y canales en DondeVer."
    else:
        title = f"{name}: próxima pelea, fecha y dónde ver | DondeVer" if kind not in _RACE_KINDS else f"{name}: próxima carrera y dónde ver {meta['org']} | DondeVer"
        h1 = f"{name}: próximo evento y dónde ver"
        answer = f"<b>{short}</b> no tiene {'carrera' if kind in _RACE_KINDS else 'pelea'} programada por ahora. Abajo están los próximos eventos de {meta['org']}."
        desc = re.sub("<[^>]+>", "", answer)
        upcoming = [e for e in evs if e["status"] != "post" and not e.get("is_minor")][:6]
    return templates.TemplateResponse(request, "atleta.html", {
        "name": name, "short": short, "slug": slug, "kind": kind, "is_race": kind in _RACE_KINDS,
        "org_name": meta["org"], "league_slug": meta["league_slug"],
        "seo_title": title, "seo_h1": h1, "seo_desc": desc, "answer": answer,
        "nxt": nxt, "upcoming": upcoming[:8], "past": past,
        "fmt_day_time": lambda iso: _fmt_local(iso, "America/Mexico_City", True),
        "year": datetime.now(TZ_MX).year,
    })


@app.get("/equipo/{team_slug}", response_class=HTMLResponse)
async def team_page(request: Request, team_slug: str):
    """Dynamic team page with today's games for that team."""
    # Resolve team info from slug
    team_info = POPULAR_TEAMS.get(team_slug)
    # Pilotos / peleadores viven en config.POPULAR_TEAMS (server.POPULAR_TEAMS es solo equipos)
    from config import POPULAR_TEAMS as _CFG_TEAMS
    _ath = _CFG_TEAMS.get(team_slug)
    if _ath and _ath.get("league") in _ATHLETE_LEAGUES:
        return await _athlete_page(request, team_slug, _ath)
    if team_info:
        team_name = team_info["name"]
        team_sport = team_info.get("sport", "")
        team_league_seo = team_info.get("league", "")
        team_aka = team_info.get("aka", team_name)
    else:
        # Fallback for unknown slugs
        clean_slug = team_slug.replace("-", " ")
        resolved = TEAM_ALIASES.get(clean_slug, clean_slug)
        team_name = resolved.title()
        team_sport = ""
        team_league_seo = ""
        team_aka = team_name

    # Search for games
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    games = await search_games(search_term)

    # Get team info from first game found
    team_logo = ""
    team_league = ""
    if games:
        for game in games:
            if search_term.lower() in game["home"]["name"].lower():
                team_logo = game["home"].get("logo", "")
                team_league = game.get("league_name", "")
                break
            elif search_term.lower() in game["away"]["name"].lower():
                team_logo = game["away"].get("logo", "")
                team_league = game.get("league_name", "")
                break

    # Fetch team stats (standings, record, etc.)
    stats = await get_team_stats(team_slug)

    # If we got stats but no logo from games, use logo from stats
    if not team_logo and stats.get("team_logo"):
        team_logo = stats["team_logo"]

    # Today's date in Spanish for dynamic SEO content
    DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now_mx = datetime.now(TZ_MX)
    today_date_str = f"{DAYS_ES[now_mx.weekday()]} {now_mx.day} de {MONTHS_ES[now_mx.month - 1]} de {now_mx.year}"

    # Related teams: other teams from the same league for internal linking
    final_league = team_league or team_league_seo
    related_teams = []
    for slug, info in POPULAR_TEAMS.items():
        if slug != team_slug and info.get("league") == final_league:
            related_teams.append({"slug": slug, "name": info["name"]})

    # Fetch recent results and upcoming games for the team's league
    recent_results = []
    upcoming_games = []
    league_standings = []
    from sports_api import TEAM_LEAGUE_MAP
    league_info_map = TEAM_LEAGUE_MAP.get(team_slug)
    if league_info_map:
        t_sport, t_league = league_info_map
        # Fetch full league standings for the table
        try:
            league_standings = await get_league_standings(t_sport, t_league, limit=50)
        except Exception:
            pass
        # More days when no games today → richer page
        result_days = 7 if games else 14
        try:
            all_recent = await get_recent_league_results(t_sport, t_league, days=result_days, limit=30)
            # Filter for this team
            for r in all_recent:
                if (search_term.lower() in r["home"].lower() or
                    search_term.lower() in r["away"].lower()):
                    recent_results.append(r)
                if len(recent_results) >= (5 if games else 10):
                    break
        except Exception:
            pass
        try:
            all_upcoming = await get_upcoming_league_games(t_sport, t_league, days=10, limit=30)
            for u in all_upcoming:
                if (search_term.lower() in u["home"].lower() or
                    search_term.lower() in u["away"].lower()):
                    upcoming_games.append(u)
                if len(upcoming_games) >= 5:
                    break
        except Exception:
            pass

    # ── DB fallback: if ESPN returned nothing, use historical DB data ──
    top_channels = []
    try:
        if not recent_results:
            db_history = await get_team_history(team_name, limit=5)
            for h in db_history:
                recent_results.append({
                    "home": h["home_name"], "away": h["away_name"],
                    "home_score": h["home_score"], "away_score": h["away_score"],
                    "date": h["game_date"], "league": h.get("league_name", ""),
                })
        if not upcoming_games:
            db_upcoming = await get_team_upcoming(team_name, limit=5)
            for u in db_upcoming:
                import json as _json_mod
                ch_list = _json_mod.loads(u.get("channels_json", "[]")) if u.get("channels_json") else []
                upcoming_games.append({
                    "home": u["home_name"], "away": u["away_name"],
                    "date_utc": str(u.get("date_utc", "")),
                    "league": u.get("league_name", ""),
                    "channels": ch_list,
                })
        # Top channels for this team (always try)
        top_channels = await get_team_channels(team_name)
    except Exception:
        pass  # DB unavailable — degrade gracefully

    # Fetch team news from ESPN
    team_news = []
    if league_info_map:
        t_sport, t_league = league_info_map
        try:
            team_news = await fetch_team_news(t_sport, t_league, team_name, limit=5)
        except Exception:
            pass

    # Fetch MercadoLibre product images for the team (cached 24h each)
    # Fetch all 3 product types in parallel for speed
    meli_jersey, meli_gorra, meli_acc = None, None, None
    try:
        meli_jersey, meli_gorra, meli_acc = await asyncio.gather(
            fetch_meli_product_image(f"jersey {team_name} oficial"),
            fetch_meli_product_image(f"gorra {team_name}"),
            fetch_meli_product_image(f"{team_name} accesorios futbol"),
            return_exceptions=True,
        )
        # If any returned an exception, set to None
        if isinstance(meli_jersey, Exception): meli_jersey = None
        if isinstance(meli_gorra, Exception): meli_gorra = None
        if isinstance(meli_acc, Exception): meli_acc = None
    except Exception:
        pass

    # Fetch TheSportsDB team info for sportsdb-only leagues
    sportsdb_team_info = {}
    if league_info_map and league_info_map[1].startswith("sportsdb:"):
        try:
            # Find team_id from standings (computed from season events)
            league_id = league_info_map[1].split(":")[1]
            sdb_standings = await compute_sportsdb_standings(league_id)
            for entry in sdb_standings:
                if (search_term.lower() in entry["team_name"].lower() or
                        entry["team_name"].lower() in search_term.lower()):
                    sdb_team_id = entry.get("team_id", "")
                    if sdb_team_id:
                        sportsdb_team_info = await fetch_sportsdb_team_info(sdb_team_id)
                        # Use SportsDB badge as logo if we don't have one
                        if not team_logo and sportsdb_team_info.get("badge"):
                            team_logo = sportsdb_team_info["badge"]
                    break
        except Exception:
            pass

    # Build form guide from recent results (W/D/L last 5)
    form_guide = []
    for r in recent_results[:5]:
        h_score = int(r.get("home_score", 0) or 0)
        a_score = int(r.get("away_score", 0) or 0)
        is_home = search_term.lower() in r.get("home", "").lower()
        team_score = h_score if is_home else a_score
        opp_score = a_score if is_home else h_score
        if team_score > opp_score:
            form_guide.append("W")
        elif team_score < opp_score:
            form_guide.append("L")
        else:
            form_guide.append("D")

    # Build dynamic FAQ based on real data (not boilerplate)
    faq_items = _build_team_faq(
        team_name=team_name,
        team_sport=team_sport,
        team_league=team_league or team_league_seo,
        stats=stats,
        games=games,
        upcoming_games=upcoming_games,
        recent_results=recent_results,
        today_date_str=today_date_str,
    )

    # Dynamic SEO title/description answering "¿a qué hora y en qué canal?"
    seo = _build_team_seo(
        team_name=team_name,
        team_league=team_league or team_league_seo,
        search_term=search_term,
        games=games,
        upcoming_games=upcoming_games,
        recent_results=recent_results,
        is_nfl=(team_sport == "futbol americano"),
    )

    # ── ¿Esto es un equipo de verdad? ──────────────────────────────────────
    #
    # Hasta hoy /equipo/cualquier-cosa devolvía 200 con una página completa:
    # /equipo/asdfghjkl salía titulada "Dónde ver Asdfghjkl hoy: horario, canal
    # y streaming". Una fábrica de soft 404 — cada URL inventada que alguien
    # enlace, o que Google adivine, se vuelve una página indexable y vacía.
    # Search Console reporta 31 soft 404 y 731 "descubiertas: sin indexar";
    # esto alimenta las dos, y de paso se come presupuesto de rastreo que
    # hace falta para las páginas buenas.
    #
    # El filtro NO es una lista blanca: los equipos se autodescubren de los
    # juegos del día y una lista fija rompería a los nuevos. La prueba es por
    # datos — si no está en nuestros catálogos Y además no tiene absolutamente
    # nada (ni juego hoy, ni resultados, ni próximos, ni stats, ni escudo),
    # entonces no es un equipo, es ruido.
    from sports_api import TEAM_LEAGUE_MAP as _TLM  # se importa por función en este archivo
    _conocido = (team_slug in POPULAR_TEAMS
                 or team_slug in _TLM
                 or team_slug in EN_TEAMS)
    _hay_datos = any((games, recent_results, upcoming_games, stats,
                      team_logo, sportsdb_team_info))
    if not _conocido and not _hay_datos:
        logger.info("equipo inexistente: %s", team_slug)
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "No encontramos este equipo. "
                                "Busca el tuyo desde la portada."},
        )

    return templates.TemplateResponse(request, "team.html", {
        # Alterna en inglés (solo equipos de EE.UU. con versión traducida)
        "en_alt": f"{APP_URL}/en/{team_slug}" if team_slug in EN_TEAMS else "",
        "seo_title": seo["title"],
        "seo_desc": seo["desc"],
        "seo_h1": seo["h1"],
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league or team_league_seo,
        "team_sport": team_sport,
        "team_aka": team_aka,
        "games": games,
        "stats": stats,
        "format_mx_time": format_mx_time,
        "format_mx_day_time": format_mx_day_time,
        "today_date_str": today_date_str,
        "related_teams": related_teams,
        "recent_results": recent_results,
        "upcoming_games": upcoming_games,
        "team_news": team_news,
        "meli_jersey": meli_jersey,
        "meli_gorra": meli_gorra,
        "meli_acc": meli_acc,
        "league_standings": league_standings,
        "form_guide": form_guide,
        "faq_items": faq_items,
        "top_channels": top_channels,
        "nfl_team_extra": NFL_TEAM_EXTRA.get(team_slug) if team_sport == "futbol americano" else None,
        "nfl_advanced": await get_nfl_team_advanced_stats(team_slug) if team_sport == "futbol americano" else {},
        "sportsdb_team_info": sportsdb_team_info,
    })


# ── Programmatic SEO pages ────────────────────────────────

# Country data for team × country pages
TEAM_COUNTRY_SEO = {
    "mexico": {"name": "México", "flag": "🇲🇽", "code": "MX"},
    "estados-unidos": {"name": "Estados Unidos", "flag": "🇺🇸", "code": "US"},
    "venezuela": {"name": "Venezuela", "flag": "🇻🇪", "code": "VE"},
    "panama": {"name": "Panamá", "flag": "🇵🇦", "code": "PA"},
    "republica-dominicana": {"name": "República Dominicana", "flag": "🇩🇴", "code": "DO"},
    "argentina": {"name": "Argentina", "flag": "🇦🇷", "code": "AR"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "code": "CO"},
    "chile": {"name": "Chile", "flag": "🇨🇱", "code": "CL"},
    "peru": {"name": "Perú", "flag": "🇵🇪", "code": "PE"},
    "ecuador": {"name": "Ecuador", "flag": "🇪🇨", "code": "EC"},
    "espana": {"name": "España", "flag": "🇪🇸", "code": "ES"},
}

# Channels available per country for each league
# Updated Aug 2026 — sources: RÉCORD, Infobae, Mediotiempo
LEAGUE_CHANNELS_BY_COUNTRY = {
    "Liga MX": {
        "mexico": [
            {"name": "TUDN", "type": "Cable", "sports": "Liga MX en vivo"},
            {"name": "Canal 5", "type": "TV Abierta", "sports": "América, Pumas, Monterrey selectos"},
            {"name": "Azteca 7", "type": "TV Abierta", "sports": "Tigres, Puebla, Necaxa selectos"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "León, Pachuca, Tijuana, Querétaro"},
            {"name": "ViX Premium", "type": "Streaming", "sports": "América, Cruz Azul, Pumas, Monterrey, Santos y Atlas"},
            {"name": "Amazon Prime", "type": "Streaming", "sports": "Chivas (exclusivo)"},
            {"name": "ESPN MX / Disney+", "type": "Cable/Streaming", "sports": "Atlético de San Luis"},
        ],
        "estados-unidos": [
            {"name": "TUDN / Univision", "type": "Cable/TV", "sports": "Liga MX en español"},
            {"name": "ViX Premium", "type": "Streaming", "sports": "Equipos de TUDN (no toda la liga)"},
            {"name": "FOX Deportes", "type": "Cable", "sports": "Liga MX selectos"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga MX selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga MX vía ESPN"},
        ],
    },
    "Premier League": {
        "espana": [
            {"name": "DAZN España", "type": "Streaming", "sports": "Premier League íntegra (derechos hasta 2027/28)"},
            {"name": "Movistar Plus+", "type": "Cable/Streaming", "sports": "Un partido por jornada"},
        ],
        "mexico": [
            {"name": "Fox Sports MX", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Max", "type": "Streaming", "sports": "Premier League completa"},
            {"name": "TNT Sports", "type": "Cable", "sports": "Premier League selectos"},
        ],
        "estados-unidos": [
            {"name": "NBC / USA Network", "type": "Cable/TV", "sports": "Premier League selectos"},
            {"name": "Peacock", "type": "Streaming", "sports": "Premier League completa"},
            {"name": "Telemundo", "type": "TV Abierta", "sports": "Premier League en español"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Premier League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Premier League completa"},
        ],
    },
    "La Liga": {
        "espana": [
            {"name": "Movistar Plus+", "type": "Cable/Streaming", "sports": "LaLiga: 5 partidos por jornada (lote D1)"},
            {"name": "DAZN España", "type": "Streaming", "sports": "LaLiga: 5 partidos por jornada (lote D2) + 1 gratis por jornada"},
            {"name": "Orange TV", "type": "Cable/Streaming", "sports": "Un partido por jornada"},
            {"name": "Vodafone TV", "type": "Cable/Streaming", "sports": "Revende los canales de Movistar y DAZN"},
        ],
        "mexico": [
            {"name": "SKY", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Blue To Go", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "estados-unidos": [
            {"name": "ESPN+", "type": "Streaming", "sports": "La Liga completa"},
            {"name": "ESPN Deportes", "type": "Cable", "sports": "La Liga en español"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "La Liga en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "La Liga completa"},
        ],
    },
    "Champions League": {
        "espana": [
            {"name": "Movistar Plus+", "type": "Cable/Streaming", "sports": "Champions League: exclusiva junto a Orange"},
            {"name": "Orange TV", "type": "Cable/Streaming", "sports": "Champions League: exclusiva junto a Movistar"},
            {"name": "La 1 / RTVE Play", "type": "TV Abierta (gratis)", "sports": "Solo la final"},
        ],
        "mexico": [
            {"name": "Fox Sports MX", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Max", "type": "Streaming", "sports": "Champions League completa"},
            {"name": "TNT Sports", "type": "Cable", "sports": "Champions League selectos"},
        ],
        "estados-unidos": [
            {"name": "CBS / CBSSN", "type": "Cable/TV", "sports": "Champions League selectos"},
            {"name": "Paramount+", "type": "Streaming", "sports": "Champions League completa"},
            {"name": "TUDN / Univision", "type": "Cable/TV", "sports": "Champions en español"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Champions League en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Champions League completa"},
        ],
    },
    "NFL": {
        "espana": [
            {"name": "DAZN España", "type": "Streaming", "sports": "NFL en exclusiva: temporada regular, playoffs y Super Bowl"},
            {"name": "NFL Game Pass", "type": "Streaming", "sports": "Todos los partidos"},
        ],
        "mexico": [
            {"name": "ESPN MX / Disney+", "type": "Cable/Streaming", "sports": "MNF, SNF, Playoffs AFC"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "TNF, domingos, Playoffs NFC"},
            {"name": "TUDN / ViX", "type": "Cable/Streaming", "sports": "Juegos en español"},
            {"name": "Netflix", "type": "Streaming", "sports": "Navidad, Thanksgiving selectos"},
            {"name": "Canal 5", "type": "TV Abierta", "sports": "Juegos selectos"},
        ],
        "estados-unidos": [
            {"name": "FOX / CBS / NBC / ABC", "type": "TV Abierta", "sports": "Juegos del domingo y especiales"},
            {"name": "ESPN / ESPN2", "type": "Cable", "sports": "Monday Night Football"},
            {"name": "Amazon Prime", "type": "Streaming", "sports": "Thursday Night Football"},
            {"name": "Netflix", "type": "Streaming", "sports": "Navidad, Thanksgiving selectos"},
            {"name": "NFL+", "type": "Streaming", "sports": "Juegos locales y out-of-market"},
            {"name": "Peacock", "type": "Streaming", "sports": "Sunday Night Football, exclusivos"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NFL en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NFL completa"},
        ],
    },
    "NBA": {
        "espana": [
            {"name": "Prime Video", "type": "Streaming", "sports": "NBA en vivo"},
            {"name": "DAZN España", "type": "Streaming", "sports": "NBA en vivo"},
            {"name": "NBA League Pass", "type": "Streaming", "sports": "Todos los partidos"},
        ],
        "mexico": [
            {"name": "ESPN MX / Disney+", "type": "Cable/Streaming", "sports": "NBA en vivo"},
            {"name": "Amazon Prime", "type": "Streaming", "sports": "NBA selectos"},
            {"name": "NBA League Pass", "type": "Streaming", "sports": "Todos los juegos NBA"},
        ],
        "estados-unidos": [
            {"name": "ESPN / TNT / ABC", "type": "Cable/TV", "sports": "NBA en vivo"},
            {"name": "NBA League Pass", "type": "Streaming", "sports": "Todos los juegos fuera de mercado"},
            {"name": "Max", "type": "Streaming", "sports": "NBA en TNT/TBS"},
            {"name": "Amazon Prime", "type": "Streaming", "sports": "NBA selectos"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "NBA en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "NBA completa"},
        ],
    },
    "MLB": {
        "espana": [
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos de MLB"},
        ],
        "mexico": [
            {"name": "ESPN MX / Disney+", "type": "Cable/Streaming", "sports": "MLB en vivo"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Canal 9 / ViX", "type": "TV Abierta/Streaming", "sports": "Juegos con mexicanos"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos MLB"},
        ],
        "estados-unidos": [
            {"name": "FOX / FS1 / TBS", "type": "Cable/TV", "sports": "MLB selectos"},
            {"name": "ESPN", "type": "Cable", "sports": "Sunday Night Baseball"},
            {"name": "Apple TV+", "type": "Streaming", "sports": "Friday Night Baseball"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos fuera de mercado"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
            {"name": "IVC / Meridiano TV", "type": "Cable", "sports": "Cobertura de peloteros venezolanos"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos MLB"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
            {"name": "TVMax", "type": "Cable", "sports": "Cobertura local de béisbol"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos MLB"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "MLB selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "MLB en vivo"},
            {"name": "CDN Deportes", "type": "Cable", "sports": "Cobertura local de béisbol"},
            {"name": "MLB.TV", "type": "Streaming", "sports": "Todos los juegos MLB"},
        ],
    },
    "MLS": {
        "espana": [
            {"name": "Apple TV MLS Season Pass", "type": "Streaming", "sports": "Todos los partidos de la MLS"},
        ],
        "mexico": [
            {"name": "ViX Premium", "type": "Streaming", "sports": "MLS completa"},
            {"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "Todos los juegos MLS"},
        ],
        "estados-unidos": [
            {"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "Todos los juegos MLS"},
            {"name": "FOX / FS1", "type": "Cable/TV", "sports": "MLS selectos"},
        ],
        "argentina": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "colombia": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "chile": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "peru": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "ecuador": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "venezuela": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "panama": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
        "republica-dominicana": [{"name": "Apple TV+ MLS Season Pass", "type": "Streaming", "sports": "MLS completa"}],
    },
    # ── Ligas domésticas LATAM ──
    "Liga Argentina": {
        "argentina": [
            {"name": "TNT Sports", "type": "Cable", "sports": "Liga Profesional en vivo"},
            {"name": "ESPN Argentina", "type": "Cable", "sports": "Liga Profesional selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Profesional completa"},
            {"name": "AFA Play", "type": "Streaming", "sports": "Todos los partidos (oficial AFA)"},
        ],
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga Argentina selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga Argentina completa"},
        ],
    },
    "Liga BetPlay": {
        "colombia": [
            {"name": "Win Sports+", "type": "Cable/Streaming", "sports": "Liga BetPlay completa"},
            {"name": "RCN", "type": "TV Abierta", "sports": "Partidos selectos"},
        ],
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "Liga BetPlay selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga BetPlay selectos"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga BetPlay selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga BetPlay selectos"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga BetPlay selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga BetPlay selectos"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga BetPlay selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga BetPlay selectos"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga BetPlay selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga BetPlay selectos"},
        ],
    },
    "Primera Chile": {
        "chile": [
            {"name": "TNT Sports", "type": "Cable", "sports": "Primera División completa"},
            {"name": "Estadio TNT", "type": "Streaming", "sports": "Primera División completa"},
        ],
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "Primera Chile selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Primera Chile selectos"},
        ],
        "argentina": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Primera Chile selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Primera Chile selectos"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Primera Chile selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Primera Chile selectos"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Primera Chile selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Primera Chile selectos"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Primera Chile selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Primera Chile selectos"},
        ],
    },
    "Liga 1 Perú": {
        "peru": [
            {"name": "GOLPERU", "type": "Cable", "sports": "Liga 1 en vivo (Movistar)"},
            {"name": "Liga1 Max", "type": "Streaming", "sports": "Liga 1 completa"},
        ],
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "Liga 1 Perú selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga 1 Perú selectos"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga 1 Perú selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga 1 Perú selectos"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga 1 Perú selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga 1 Perú selectos"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Liga 1 Perú selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "Liga 1 Perú selectos"},
        ],
    },
    "LigaPro Ecuador": {
        "ecuador": [
            {"name": "GOLTV Ecuador", "type": "Cable", "sports": "LigaPro completa"},
            {"name": "ESPN Ecuador", "type": "Cable", "sports": "LigaPro selectos"},
            {"name": "Star+", "type": "Streaming", "sports": "LigaPro selectos"},
        ],
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "LigaPro selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "LigaPro selectos"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "LigaPro selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "LigaPro selectos"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "LigaPro selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "LigaPro selectos"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "LigaPro selectos"},
            {"name": "Disney+", "type": "Streaming", "sports": "LigaPro selectos"},
        ],
    },
    "Copa Libertadores": {
        "mexico": [
            {"name": "ESPN MX", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
            {"name": "Fox Sports MX", "type": "Cable", "sports": "Libertadores selectos"},
        ],
        "argentina": [
            {"name": "ESPN Argentina", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "colombia": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "chile": [
            {"name": "ESPN Chile", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "peru": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "ecuador": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "venezuela": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "panama": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
        "republica-dominicana": [
            {"name": "ESPN Latinoamérica", "type": "Cable", "sports": "Libertadores en vivo"},
            {"name": "Disney+", "type": "Streaming", "sports": "Libertadores completa"},
        ],
    },
}

# Timezones per country page (for local kickoff times in geo pages)
_COUNTRY_TZ = {
    "mexico": "America/Mexico_City",
    "estados-unidos": "America/New_York",
    "venezuela": "America/Caracas",
    "panama": "America/Panama",
    "republica-dominicana": "America/Santo_Domingo",
    "argentina": "America/Argentina/Buenos_Aires",
    "colombia": "America/Bogota",
    "chile": "America/Santiago",
    "peru": "America/Lima",
    "ecuador": "America/Guayaquil",
    "espana": "Europe/Madrid",
}
_COUNTRY_TZ_LABEL = {
    "mexico": "hora de México",
    "estados-unidos": "hora del Este (ET)",
    "venezuela": "hora de Venezuela",
    "panama": "hora de Panamá",
    "republica-dominicana": "hora de RD",
    "argentina": "hora de Argentina",
    "colombia": "hora de Colombia",
    "chile": "hora de Chile",
    "peru": "hora de Perú",
    "ecuador": "hora de Ecuador",
    "espana": "hora peninsular española",
}

# Default for leagues not mapped above
_DEFAULT_LATAM_CHANNELS = [
    {"name": "ESPN Latinoamérica / Disney+", "type": "Cable/Streaming", "sports": "Deportes internacionales"},
]

# ESPN no opera en España: el fallback de LATAM sería falso allí. Y como no
# todas las ligas se emiten en España, preferimos "por confirmar" a inventar
# un canal — mismo criterio que usamos con la NFL en México.
_DEFAULT_CHANNELS_BY_COUNTRY = {
    "espana": [
        {"name": "Por confirmar", "type": "—",
         "sports": "Esta competición no tiene emisor confirmado en España. Revisa la parrilla de Movistar Plus+ y DAZN."},
    ],
}


def _default_channels(country_slug: str) -> list:
    return _DEFAULT_CHANNELS_BY_COUNTRY.get(country_slug, _DEFAULT_LATAM_CHANNELS)


@app.get("/equipo/{team_slug}/en/{country_slug}", response_class=HTMLResponse)
async def team_country_page(request: Request, team_slug: str, country_slug: str):
    """Programmatic SEO: 'Dónde ver [equipo] en [país]'."""
    country = TEAM_COUNTRY_SEO.get(country_slug)
    if not country:
        return templates.TemplateResponse(request, "404.html", status_code=404,
                                          context={"message": "País no encontrado."})

    team_info = POPULAR_TEAMS.get(team_slug)
    if team_info:
        team_name = team_info["name"]
        team_league = team_info.get("league", "")
    else:
        team_name = team_slug.replace("-", " ").title()
        team_league = ""

    # Get team logo and games
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    team_logo = ""
    games = await search_games(search_term)
    for game in games:
        if search_term.lower() in game["home"]["name"].lower():
            team_logo = game["home"].get("logo", "")
            if not team_league:
                team_league = game.get("league_name", "")
            break
        elif search_term.lower() in game["away"]["name"].lower():
            team_logo = game["away"].get("logo", "")
            if not team_league:
                team_league = game.get("league_name", "")
            break

    if not team_logo:
        try:
            stats = await get_team_stats(team_slug)
            team_logo = stats.get("team_logo", "")
        except Exception:
            pass

    # Get channels for this league in this country
    league_channels = LEAGUE_CHANNELS_BY_COUNTRY.get(team_league, {})
    country_channels = league_channels.get(country_slug) or _default_channels(country_slug)

    # Country tip from COUNTRY_PAGES if available
    cp = COUNTRY_PAGES.get(country_slug, {})
    country_tip = cp.get("tip", "")

    all_countries = [(cs, cd["name"], cd["flag"]) for cs, cd in TEAM_COUNTRY_SEO.items()]

    # ── Local time for the visitor's country (GSC: geo pages are our best CTR) ──
    from zoneinfo import ZoneInfo as _ZI
    tz_name = _COUNTRY_TZ.get(country_slug, "America/Mexico_City")
    tz_local = _ZI(tz_name)
    tz_label = _COUNTRY_TZ_LABEL.get(country_slug, "hora local")

    def format_local_time(iso_date: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone(tz_local)
            return dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return ""

    # Dynamic SEO: "Dodgers en Venezuela hoy: 8:10 PM en ESPN vs Giants"
    st = search_term.lower()
    first_ch = (country_channels[0].get("name") if country_channels else "") or ""
    first_ch = first_ch.split(" / ")[0]
    cname = country["name"]
    _tshort = _short_team_name(team_name, team_league)
    seo_title = f"Dónde ver {_tshort} en {cname} hoy: canales y horario | DondeVer"
    seo_h1 = f"Dónde ver {team_name} en {cname} {country['flag']}"
    seo_desc = (f"¿Dónde ver a {team_name} en {cname} hoy? Canales de TV, cable y streaming "
                f"({first_ch}) con horario de {cname}. {team_league} en vivo.")
    today_g = next((g for g in games if (g.get("status") or {}).get("state") in ("pre", "in")), None)

    # ── Próximo partido (cuando hoy no juega): mismo dato que /equipo/{slug} ──
    next_game = None
    if not today_g:
        try:
            from sports_api import TEAM_LEAGUE_MAP as _TLM
            _lm = _TLM.get(team_slug)
            if _lm:
                _all_up = await get_upcoming_league_games(_lm[0], _lm[1], days=14, limit=40)
                for u in _all_up:
                    if st in (u.get("home") or "").lower() or st in (u.get("away") or "").lower():
                        next_game = dict(u)
                        break
            if not next_game:
                _db_up = await get_team_upcoming(team_name, limit=1)
                if _db_up:
                    u = _db_up[0]
                    import json as _json_mod
                    next_game = {"home": u["home_name"], "away": u["away_name"], "date": str(u.get("date_utc", "")),
                                 "channels": _json_mod.loads(u.get("channels_json") or "[]"), "league": u.get("league_name", "")}
        except Exception:
            next_game = None
        if next_game:
            ccode = country.get("code", "")
            chs = next_game.get("channels") or []
            if chs and isinstance(chs[0], dict):
                local_chs = [c["name"] for c in chs if c.get("country") == ccode]
                other_chs = [c["name"] for c in chs if c.get("country") != ccode]
            else:
                local_chs, other_chs = [], [str(c) for c in chs]
            next_game["local_channels"] = local_chs
            next_game["other_channels"] = other_chs[:4]
            next_game["time_local"] = format_local_time(next_game.get("date", ""))
            try:
                _dt = datetime.fromisoformat(str(next_game.get("date", "")).replace("Z", "+00:00")).astimezone(tz_local)
                next_game["day_local"] = f"{_DIAS_ES[_dt.weekday()].capitalize()} {_dt.day} de {_MESES_ES[_dt.month - 1]}"
            except Exception:
                next_game["day_local"] = ""
            _nh, _na = next_game.get("home", ""), next_game.get("away", "")
            _opp = _short_team_name(_na if st in _nh.lower() else _nh, team_league)
            _ch_txt = local_chs[0] if local_chs else first_ch
            seo_title = f"{_tshort} vs {_opp} en {cname}: {next_game['day_local']} {next_game['time_local']} en {_ch_txt} | Dónde ver"
            seo_desc = (f"Próximo partido de {team_name} en {cname}: {_tshort} vs {_opp} el {next_game['day_local']} a las "
                        f"{next_game['time_local']} {tz_label}. Canal: {_ch_txt}. Todos los canales para ver {team_league} desde {cname}.")
    if today_g:
        home = today_g.get("home", {}) or {}
        away = today_g.get("away", {}) or {}
        is_home = st in (home.get("name", "") or "").lower()
        opp = _short_team_name(away.get("name", "") if is_home else home.get("name", ""), team_league)
        team_short = _short_team_name(team_name, team_league)
        if today_g["status"]["state"] == "in":
            seo_title = f"{team_short} vs {opp} EN VIVO en {cname}: canal {first_ch} | Dónde ver"
            seo_h1 = f"{team_short} vs {opp} en vivo en {cname} {country['flag']}"
        else:
            t_local = format_local_time(today_g.get("date", ""))
            seo_title = f"{team_short} en {cname} hoy: {t_local} en {first_ch} vs {opp} | Dónde ver"
            seo_h1 = f"Dónde ver {team_short} vs {opp} en {cname}: {t_local} ({tz_label})"
            seo_desc = (f"{team_short} vs {opp} hoy a las {t_local} {tz_label} en {cname}. "
                        f"Canal: {first_ch}. Todos los canales de TV y streaming para ver {team_league} desde {cname}.")

    return templates.TemplateResponse(request, "team_country.html", {
        "seo_title": seo_title,
        "seo_h1": seo_h1,
        "seo_desc": seo_desc,
        "tz_label": tz_label,
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league,
        "country_name": country["name"],
        "country_slug": country_slug,
        "country_flag": country["flag"],
        "country_channels": country_channels,
        "country_tip": country_tip,
        "games": games,
        "next_game": next_game,
        "all_countries": all_countries,
        "format_mx_time": format_mx_time,
        "format_local_time": format_local_time,
    })


# ── Versión en inglés para equipos de EE.UU. ────────────────────────────────
# GSC (28 d, país = Estados Unidos): 47,800 impresiones, 249 clics, CTR 0.5%,
# posición media 11.3. Google ya nos posiciona para consultas en inglés
# ("what channel is the eagles game on", posición 5.9, 0 clics) pero el usuario
# llega a una página en español y se va. URLs separadas + hreflang recíproco:
# NO servimos inglés en la URL española, para no tocar el 91% de tráfico en español.
EN_TEAMS = [
    # Con demanda medida desde EE.UU.
    "dodgers", "yankees", "mets", "red-sox", "braves", "rangers",
    # Mayores generadores de búsquedas "what channel" en EE.UU.
    "astros", "phillies", "cubs", "padres", "giants", "angels",
    "eagles", "chiefs", "cowboys", "49ers", "packers", "bills", "ravens", "lions",
    "lakers", "warriors", "celtics",
]

_EN_LEAGUE_LABEL = {"MLB": "MLB", "NFL": "NFL", "NBA": "NBA", "NHL": "NHL", "WNBA": "WNBA"}


def _en_team_list() -> list:
    """Sólo los slugs que existen en POPULAR_TEAMS y son de liga estadounidense."""
    out = []
    for slug in EN_TEAMS:
        info = POPULAR_TEAMS.get(slug)
        if info and info.get("league") in _EN_LEAGUE_LABEL:
            out.append(slug)
    return out


@app.get("/en/{team_slug}", response_class=HTMLResponse)
async def team_page_en(request: Request, team_slug: str):
    """English TV-schedule page: 'What channel is the {team} game on?'"""
    if team_slug not in _en_team_list():
        return templates.TemplateResponse(
            request, "404.html", status_code=404,
            context={"message": "Page not found."})

    info = POPULAR_TEAMS[team_slug]
    team_name = info["name"]
    league = info.get("league", "")

    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    st = search_term.lower()
    games = await search_games(search_term)

    team_logo = ""
    for g in games:
        for side in ("home", "away"):
            if st in (g.get(side, {}).get("name", "") or "").lower():
                team_logo = g[side].get("logo", "") or team_logo
    if not team_logo:
        try:
            team_logo = (await get_team_stats(team_slug)).get("team_logo", "")
        except Exception:
            pass

    def _us_channels(g):
        out = []
        for b in g.get("broadcasts", []):
            inf = b.get("info") or {}
            if inf.get("country") in ("US", "") or not inf:
                ch = b.get("channel", "")
                if ch and ch not in out:
                    out.append(ch)
        return out[:4]

    today_game = next((g for g in games if (g.get("status") or {}).get("state") in ("pre", "in")), None)

    # Próximos partidos del equipo (mismo dato que la versión en español)
    upcoming = []
    try:
        from sports_api import TEAM_LEAGUE_MAP as _TLM
        lm = _TLM.get(team_slug)
        if lm:
            for u in await get_upcoming_league_games(lm[0], lm[1], days=14, limit=40):
                if st in (u.get("home") or "").lower() or st in (u.get("away") or "").lower():
                    upcoming.append(u)
                if len(upcoming) >= 6:
                    break
    except Exception:
        pass

    def fmt_et(iso_date: str) -> str:
        try:
            dt = datetime.fromisoformat(str(iso_date).replace("Z", "+00:00")).astimezone(TZ_ET)
            return dt.strftime("%a, %b %-d · %-I:%M %p ET")
        except Exception:
            return ""

    ch_now = _us_channels(today_game) if today_game else []
    if today_game:
        opp_raw = (today_game["away"]["name"] if st in (today_game["home"]["name"] or "").lower()
                   else today_game["home"]["name"])
        when = fmt_et(today_game.get("date", ""))
        title = f"What channel is the {team_name} game on today? {(ch_now[0] + ' · ') if ch_now else ''}{when}"
        desc = (f"{team_name} vs {opp_raw} today: {when}"
                f"{' on ' + ', '.join(ch_now) if ch_now else ''}. TV channel, start time and streaming options.")
        h1 = f"What channel is the {team_name} game on today?"
    else:
        title = f"{team_name} TV schedule: what channel is the next game on? | DondeVer"
        desc = (f"{team_name} TV schedule — next game date, start time (ET) and the channel "
                f"carrying it, plus the rest of the upcoming {league} schedule.")
        h1 = f"{team_name} TV schedule: next games and channels"

    return templates.TemplateResponse(request, "team_en.html", {
        "team_name": team_name, "team_slug": team_slug, "team_logo": team_logo,
        "league": league, "today_game": today_game, "ch_now": ch_now,
        "upcoming": upcoming, "fmt_et": fmt_et, "us_channels": _us_channels,
        "seo_title": title, "seo_desc": desc, "seo_h1": h1,
    })


@app.get("/equipo/{team_slug}/calendario", response_class=HTMLResponse)
async def team_calendar_page(request: Request, team_slug: str):
    """Weekly calendar for a team — 7-day view with games."""
    team_info = POPULAR_TEAMS.get(team_slug)
    if team_info:
        team_name = team_info["name"]
        team_league = team_info.get("league", "")
        team_sport = team_info.get("sport", "")
    else:
        team_name = team_slug.replace("-", " ").title()
        team_league = ""
        team_sport = ""

    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    team_logo = ""

    # Build 7-day calendar
    now_mx = datetime.now(TZ_MX)
    calendar_days = []

    for delta in range(7):
        day = now_mx + timedelta(days=delta)
        day_str = day.strftime("%Y%m%d")
        day_label = f"{_DAYS_ES_FULL[day.weekday()].title()} {day.day} de {_MONTHS_ES_FULL[day.month]}"

        try:
            day_games = await get_todays_games(date_str=day_str)
        except Exception:
            day_games = []

        # Filter games for this team
        team_games = []
        for g in day_games:
            if (search_term.lower() in g["home"]["name"].lower() or
                search_term.lower() in g["away"]["name"].lower()):
                team_games.append(g)
                if not team_logo:
                    if search_term.lower() in g["home"]["name"].lower():
                        team_logo = g["home"].get("logo", "")
                    else:
                        team_logo = g["away"].get("logo", "")
                    if not team_league:
                        team_league = g.get("league_name", "")

        calendar_days.append({
            "date": day,
            "label": day_label,
            "is_today": delta == 0,
            "games": team_games,
        })

    if not team_logo:
        try:
            stats = await get_team_stats(team_slug)
            team_logo = stats.get("team_logo", "")
        except Exception:
            pass

    # Get recent results
    recent_results = []
    t_sport = team_sport or "soccer"
    t_league = ""
    if team_info:
        for lslug, ldata in ALL_LEAGUES.items():
            lname = ldata[0] if isinstance(ldata, tuple) else lslug
            if lname == team_league:
                t_league = lslug
                t_sport = ldata[2] if isinstance(ldata, tuple) and len(ldata) > 2 else "soccer"
                break

    if t_league:
        try:
            all_recent = await get_recent_league_results(t_sport, t_league, days=14, limit=30)
            for r in all_recent:
                if (search_term.lower() in r["home"].lower() or
                    search_term.lower() in r["away"].lower()):
                    # Determine result class
                    h_score = int(r.get("home_score", 0) or 0)
                    a_score = int(r.get("away_score", 0) or 0)
                    is_home = search_term.lower() in r["home"].lower()
                    team_score = h_score if is_home else a_score
                    opp_score = a_score if is_home else h_score
                    if team_score > opp_score:
                        r["result_class"] = "result-w"
                    elif team_score < opp_score:
                        r["result_class"] = "result-l"
                    else:
                        r["result_class"] = "result-d"
                    # Format date
                    try:
                        rd = datetime.strptime(r.get("game_date", ""), "%Y%m%d")
                        r["date_display"] = f"{rd.day}/{rd.month}"
                    except Exception:
                        r["date_display"] = r.get("game_date", "")[:6]
                    recent_results.append(r)
                if len(recent_results) >= 5:
                    break
        except Exception:
            pass

    # Week range for display
    end_day = now_mx + timedelta(days=6)
    week_range = f"{now_mx.day} {_MONTHS_ES_FULL[now_mx.month][:3]} – {end_day.day} {_MONTHS_ES_FULL[end_day.month][:3]} {end_day.year}"

    return templates.TemplateResponse(request, "team_calendar.html", {
        # Duplicado delgado de /equipo/{slug}: fuera del índice, pero sigue servible y enlazado
        # para quien la use desde el sitio.
        "noindex": True,
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league,
        "calendar_days": calendar_days,
        "week_range": week_range,
        "recent_results": recent_results,
        "format_mx_time": format_mx_time,
    })


@app.get("/resultado/{slug}", response_class=HTMLResponse)
async def recap_page(request: Request, slug: str):
    """Post-match recap page for finished games."""
    # Reuse the same slug parsing as /partido/
    match = re.match(r"^(.+)-vs-(.+)-(\d{4}-\d{2}-\d{2})$", slug)
    if not match:
        return templates.TemplateResponse(request, "404.html", status_code=404,
                                          context={"message": "URL de resultado no válida."})

    slug_part_1 = match.group(1)
    slug_part_2 = match.group(2)
    date_str = match.group(3).replace("-", "")

    # Find the game
    game = None
    for delta in [0, 1, -1]:
        try:
            dt = datetime.strptime(date_str, "%Y%m%d") + timedelta(days=delta)
            try_date = dt.strftime("%Y%m%d")
        except ValueError:
            continue
        all_games = await get_todays_games(date_str=try_date)
        for g in all_games:
            g_home = _slugify(g.get("home", {}).get("name", ""))
            g_away = _slugify(g.get("away", {}).get("name", ""))
            if (g_home == slug_part_1 and g_away == slug_part_2) or \
               (g_away == slug_part_1 and g_home == slug_part_2):
                game = g
                break
        if game:
            break

    if not game:
        return templates.TemplateResponse(request, "404.html", status_code=410,
                                          context={"message": "Resultado no encontrado."})

    # Only show recap for finished games
    if game["status"]["state"] != "post":
        return RedirectResponse(url=f"/partido/{_make_game_slug(game)}", status_code=302)

    # Fetch ESPN summary
    summary = {}
    try:
        sport = game.get("sport", "")
        league_slug = game.get("league_slug", "")
        league_info = ALL_LEAGUES.get(league_slug)
        espn_league = league_info[1] if isinstance(league_info, tuple) else league_slug
        if sport and espn_league:
            summary = await fetch_espn_event_summary(sport, espn_league, game["id"])
    except Exception as e:
        logger.warning(f"Recap summary fetch failed: {e}")

    # Format date
    try:
        dt = datetime.fromisoformat(game.get("date", "").replace("Z", "+00:00"))
        dt_mx = dt.astimezone(TZ_MX)
        date_display = f"{_DAYS_ES_FULL[dt_mx.weekday()].title()} {dt_mx.day} de {_MONTHS_ES_FULL[dt_mx.month]} {dt_mx.year}"
    except Exception:
        date_display = match.group(3)

    home_slug = _team_name_to_slug(game["home"]["name"])
    away_slug = _team_name_to_slug(game["away"]["name"])

    return templates.TemplateResponse(request, "recap.html", {
        "game": game,
        "slug": slug,
        "summary": summary,
        "date_display": date_display,
        "home_slug": home_slug,
        "away_slug": away_slug,
    })


@app.get("/mis-equipos", response_class=HTMLResponse)
async def mis_equipos_page(request: Request):
    """Client-side page: 7-day agenda for user's followed teams (read from localStorage)."""
    return templates.TemplateResponse("mis_equipos.html", {"request": request})


@app.get("/equipos", response_class=HTMLResponse)
async def teams_list(request: Request):
    """List ALL teams for SEO indexing — auto-generated from POPULAR_TEAMS + today's games."""
    # Build teams_by_league from all POPULAR_TEAMS
    teams_by_league: dict[str, list[dict]] = {}
    for slug, info in POPULAR_TEAMS.items():
        league = info.get("league", "Otros")
        if league not in teams_by_league:
            teams_by_league[league] = []
        teams_by_league[league].append({"slug": slug, "name": info["name"]})

    # Also discover teams from today's games that aren't in POPULAR_TEAMS
    try:
        today_games = await get_todays_games()
        for g in today_games:
            for side in ("home", "away"):
                tname = g[side]["name"]
                tslug = _slugify_team(tname)
                league_name = g.get("league_name", "Otros")
                if tslug not in POPULAR_TEAMS:
                    if league_name not in teams_by_league:
                        teams_by_league[league_name] = []
                    # Avoid duplicates within the league
                    if not any(t["slug"] == tslug for t in teams_by_league[league_name]):
                        teams_by_league[league_name].append({"slug": tslug, "name": tname})
    except Exception:
        pass

    # Sort teams within each league alphabetically
    for league in teams_by_league:
        teams_by_league[league].sort(key=lambda t: t["name"])

    # Order leagues: popular first, then alphabetical
    league_order = ["Liga MX", "Premier League", "La Liga", "Serie A", "Bundesliga",
                    "Ligue 1", "MLS", "NFL", "NBA", "MLB", "NHL", "WNBA",
                    "Liga Argentina", "Liga BetPlay", "LigaPro Ecuador",
                    "Primera Chile", "Liga 1 Perú", "Liga Portugal", "Eredivisie"]
    sorted_leagues = {}
    for lo in league_order:
        if lo in teams_by_league:
            sorted_leagues[lo] = teams_by_league.pop(lo)
    # Add remaining leagues alphabetically
    for lo in sorted(teams_by_league.keys()):
        sorted_leagues[lo] = teams_by_league[lo]

    total_teams = sum(len(t) for t in sorted_leagues.values())

    return templates.TemplateResponse(request, "teams_list.html", {
        "teams_by_league": sorted_leagues,
        "total_teams": total_teams,
        "POPULAR_TEAMS": POPULAR_TEAMS,
    })


# ── TikTok OAuth + Content Posting ─────────────────────

@app.get("/tiktok/login")
async def tiktok_login():
    """Redirect to TikTok OAuth authorization."""
    from fastapi.responses import RedirectResponse
    auth_url = get_tiktok_auth_url()
    return RedirectResponse(url=auth_url)


@app.get("/auth/tiktok/callback", response_class=HTMLResponse)
async def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle TikTok OAuth callback after user authorizes."""
    if error:
        return HTMLResponse(f"<h1>Error de autorización TikTok</h1><p>{error}</p>")

    if not code:
        return HTMLResponse("<h1>No se recibió código de autorización</h1>")

    # Exchange code for token
    result = await exchange_code_for_token(code)

    if "access_token" in result:
        user_info = await get_user_info()
        display_name = user_info.get("data", {}).get("user", {}).get("display_name", "Usuario")
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html lang="es">
        <head><meta charset="UTF-8"><title>TikTok Conectado | DondeVer</title>
        <style>
            body {{ font-family: system-ui; background: #0a0a0a; color: #fff; display: flex;
                   justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
            .card {{ background: #1a1a1a; border-radius: 16px; padding: 40px; text-align: center; max-width: 500px; }}
            .success {{ color: #25D366; font-size: 48px; }}
            h1 {{ margin: 16px 0 8px; }}
            .btn {{ display: inline-block; background: #fe2c55; color: #fff; padding: 14px 32px;
                    border-radius: 8px; text-decoration: none; margin-top: 20px; font-weight: 600; }}
            .btn:hover {{ opacity: 0.9; }}
        </style></head>
        <body><div class="card">
            <div class="success">✓</div>
            <h1>TikTok Conectado</h1>
            <p>Cuenta: <strong>{display_name}</strong></p>
            <p>Ahora puedes publicar videos automaticamente.</p>
            <a href="/tiktok/panel" class="btn">Ir al Panel TikTok</a>
        </div></body></html>
        """)
    else:
        error_msg = result.get("error_description", result.get("error", "Error desconocido"))
        return HTMLResponse(f"""
        <h1>Error al conectar TikTok</h1>
        <p>{error_msg}</p>
        <a href="/tiktok/login">Intentar de nuevo</a>
        """)


@app.get("/tiktok/panel", response_class=HTMLResponse)
async def tiktok_panel(request: Request):
    """TikTok management panel — shows status, generate & publish videos."""
    token_info = get_token_info()
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")

    # Check if today's video exists
    from pathlib import Path
    video_path = Path(f"static/tiktok/dondever_picks_{date_tag}.mp4")
    video_exists = video_path.exists()
    video_url = f"/static/tiktok/dondever_picks_{date_tag}.mp4" if video_exists else None

    # Check images
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")]) if images_dir.exists() else []

    return templates.TemplateResponse(request, "tiktok_panel.html", {
        "authenticated": token_info["authenticated"],
        "open_id": token_info.get("open_id"),
        "video_exists": video_exists,
        "video_url": video_url,
        "images": images,
        "date_tag": date_tag,
    })


@app.post("/tiktok/publicar")
async def tiktok_publish():
    """Publish today's video to TikTok."""
    if not is_authenticated():
        return JSONResponse({"error": "No conectado a TikTok. Ve a /tiktok/login"}, status_code=401)

    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"static/tiktok/dondever_picks_{date_tag}.mp4"

    from pathlib import Path
    if not Path(video_path).exists():
        # Try generating first
        from tiktok_generator import generate_daily_video
        video_path_gen = await generate_daily_video()
        if not video_path_gen:
            return JSONResponse({"error": "No hay juegos hoy para generar video"}, status_code=404)
        video_path = video_path_gen

    today = datetime.now(TZ_MX)
    title = f"Partidos de hoy {today.strftime('%d/%m')} | Donde verlos en vivo #deportes #futbol #nba #nfl #dondever"

    result = await upload_video_to_tiktok(video_path, title)
    return JSONResponse(result)


@app.get("/tiktok/status/{publish_id}")
async def tiktok_status(publish_id: str):
    """Check publishing status of a video."""
    result = await check_publish_status(publish_id)
    return JSONResponse(result)


# ── Push Notifications Admin ─────────────────────────────

@app.get("/admin/push-test")
async def push_test(token: str = ""):
    """Send a test push notification."""
    if token != os.getenv("ADMIN_TOKEN", "dondever2026"):
        return JSONResponse({"error": "unauthorized"}, 401)
    from push_notifications import send_push
    result = await send_push(
        heading="🏟️ DondeVer — Test",
        message="Las notificaciones push funcionan correctamente",
        url=APP_URL,
    )
    return JSONResponse(result)


# ── Push por equipo / partido (OneSignal subscription ids en push_store) ──
def _push_targets_for(game: dict) -> list[str]:
    """Suscripciones que siguen al local, al visitante o pusieron 🔔 al partido."""
    import push_store
    slugs = [x for x in (_team_name_to_slug(game.get("home", {}).get("name", "")),
                         _team_name_to_slug(game.get("away", {}).get("name", ""))) if x]
    return push_store.subs_for(slugs, str(game.get("id", "")))


def _push_url_for(game: dict) -> str:
    try:
        return f"{APP_URL}/partido/{_make_game_slug(game)}"
    except Exception:
        return APP_URL


async def _push_live_check():
    """Watcher (cada 60 s): inicio, gol/anotación, final → push a seguidores."""
    from push_notifications import check_live_pushes
    try:
        games = await get_todays_games()
        return await check_live_pushes(games, _push_targets_for, _push_url_for)
    except Exception as e:
        logger.error(f"Push live check failed: {e}")
        return []


@app.post("/api/push/subscribe")
async def api_push_subscribe(request: Request):
    """El navegador manda su OneSignal subscription id + equipos seguidos (dv_my_teams) + partidos con 🔔."""
    import push_store
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "json"}, status_code=400)
    sub_id = str(body.get("sub_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", sub_id):
        return JSONResponse({"error": "sub_id"}, status_code=400)
    teams = body.get("teams") if isinstance(body.get("teams"), list) else None
    games = body.get("games") if isinstance(body.get("games"), list) else None
    add_games = body.get("add_games") if isinstance(body.get("add_games"), list) else None
    try:
        cur = push_store.upsert(sub_id, teams=teams, games=games, add_games=add_games,
                                ua=request.headers.get("user-agent", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "teams": cur.get("teams", []), "games": cur.get("games", [])}


@app.post("/api/push/unsubscribe")
async def api_push_unsubscribe(request: Request):
    import push_store
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "json"}, status_code=400)
    return {"ok": push_store.remove(str(body.get("sub_id") or ""))}


@app.get("/api/internal/push-tick")
async def api_push_tick(token: str = ""):
    """Corre el watcher en vivo una vez (para cron externo). Protegido por ADMIN_TOKEN."""
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    import push_store
    sent = await _push_live_check()
    return {"ok": True, "sent": sent, "store": push_store.stats()}


@app.get("/api/internal/push-stats")
async def api_push_stats(token: str = ""):
    if not token or token != os.getenv("ADMIN_TOKEN", ""):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    import push_store
    from push_notifications import _live_state, _live_sent
    return {"store": push_store.stats(), "watching": len(_live_state), "sent_keys": len(_live_sent)}


@app.get("/admin/push-summary")
async def push_summary_now(token: str = ""):
    """Trigger daily push summary now."""
    if token != os.getenv("ADMIN_TOKEN", "dondever2026"):
        return JSONResponse({"error": "unauthorized"}, 401)
    from push_notifications import send_daily_push_summary
    games = await get_todays_games()
    result = await send_daily_push_summary(games)
    return JSONResponse(result)


# ── Health ───────────────────────────────────────────────

@app.get("/health")
async def health():
    import resource
    mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB → MB on Linux
    # On macOS ru_maxrss is in bytes, on Linux in KB
    import sys
    if sys.platform == "darwin":
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    from sports_api import _cache, _tv_cache, _odds_cache, _summary_cache, _standings_cache, _meli_cache
    from og_image import _og_cache
    import memoria as _mem
    return {
        "status": "ok",
        # ru_maxrss es la marca MÁS ALTA desde el arranque y nunca baja: dice
        # si alguna vez rozamos el límite, no cuánto ocupamos ahora.
        "memory_mb": round(mem_mb, 1),
        "memoria_actual_mb": round(_mem.rss_mb(), 1),
        "purgas": _mem.purgas_totales,
        "ultima_purga": _mem.ultima_purga,
        "caches": {
            "espn": len(_cache), "tv": len(_tv_cache), "odds": len(_odds_cache),
            "summary": len(_summary_cache), "standings": len(_standings_cache),
            "meli": len(_meli_cache), "og_images": len(_og_cache),
        },
    }


@app.get("/admin/memoria")
async def admin_memoria(token: str = ""):
    """Bytes reales por caché, de mayor a menor.

    Render mató la instancia cuatro veces el 24/09/2026 y ningún dato del
    proceso decía por qué: /health contaba entradas, no bytes, y un dict de
    400 páginas HTML reporta unos pocos KB si se pregunta con getsizeof.
    Esta es la medición que faltaba. Lleva token porque recorrer todos los
    cachés cuesta CPU y el plan tiene media.
    """
    if token != os.getenv("ADMIN_TOKEN", "dondever2026"):
        return JSONResponse({"error": "unauthorized"}, 401)
    import memoria as _mem
    return JSONResponse(_mem.inventario())


# ── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
