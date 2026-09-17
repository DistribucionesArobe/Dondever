"""Canales por país a partir de la parrilla pública de GatoTV.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
Search Console (28 días) dice que el 38% de los clics del sitio viene de fuera
de México: Venezuela 17%, Panamá 8%, Dominicana 4%, Colombia 4%, Perú y Ecuador
~2% cada uno. Y toda nuestra lógica de canales era mexicana.

TheSportsDB no sirve para esto: su endpoint por evento (lookupeventtv.php)
devuelve 404 con nuestra clave, y el que sí responde (eventstv.php) no cubre
Latinoamérica — el 16/09/2026 traía 3 transmisiones de béisbol en todo el mundo
y ninguna de nuestros países.

GatoTV sí. Publica la parrilla de ESPN, Fox Sports y los canales locales
desglosada POR PAÍS, con el nombre del partido. Verificado el 16/09/2026:
ESPN Venezuela listaba "Atlético de Madrid vs. CA Osasuna" a las 10:50.

CÓMO SE COMPORTA
----------------
- Una petición por canal y día, cacheada 6 h. Con seis países son ~35 peticiones
  diarias en total, no por visita.
- Si GatoTV no responde o cambia el HTML, devolvemos vacío y el sitio sigue
  igual que hoy. Nunca revienta una página por esto.
- Es scraping de una web pública, no una API con contrato: puede romperse.
  Por eso todo lo que sale de aquí se marca como dato de GatoTV y el llamador
  decide si lo afirma o no.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx
from cachetools import TTLCache

logger = logging.getLogger("dondever")

GATOTV_BASE = "https://www.gatotv.com/canal"

# GatoTV publica las horas de la parrilla en UTC-5 fijo (sin horario de verano).
# Es lo mismo que asume el grabber de iptv-org para este sitio.
_GRID_TZ = timezone(timedelta(hours=-5))

# Canales deportivos por país. Solo los que de verdad transmiten deporte: la
# lista completa de GatoTV trae 1,993 canales y no vamos a pedir Cartoon Network.
# Los slugs están verificados contra https://www.gatotv.com/canales_de_tv.
# (Ojo: "rpc_paraguay" es de Paraguay, NO de Panamá. No está en esta tabla.)
GATOTV_SPORTS_CHANNELS: dict[str, list[tuple[str, str]]] = {
    "VE": [
        ("espn_venezuela", "ESPN"),
        ("espn_2_venezuela", "ESPN 2"),
        ("espn_3_venezuela", "ESPN 3"),
        ("fox_sports_venezuela", "Fox Sports"),
        ("fox_sports_2_venezuela", "Fox Sports 2"),
        ("tnt_venezuela", "TNT"),
    ],
    "PA": [
        ("espn_panama", "ESPN"),
        ("espn_2_panama", "ESPN 2"),
        ("fox_sports_panama", "Fox Sports"),
        ("fox_sports_2_panama", "Fox Sports 2"),
        ("tvmax", "TVMax"),
        ("tudn_panama", "TUDN"),
    ],
    "DO": [
        ("espn_republica_dominicana", "ESPN"),
        ("espn_2_republica_dominicana", "ESPN 2"),
        ("fox_sports_republica_dominicana", "Fox Sports"),
        ("cdn_deportes", "CDN Deportes"),
        ("telesistema_11", "Telesistema 11"),
        ("tnt_republica_dominicana", "TNT"),
    ],
    "CO": [
        ("espn_colombia", "ESPN"),
        ("espn_2_colombia", "ESPN 2"),
        ("fox_sports_colombia", "Fox Sports"),
        ("caracol_colombia", "Caracol"),
        ("rcn_colombia", "RCN"),
    ],
    "PE": [
        ("espn_peru", "ESPN"),
        ("espn_2_peru", "ESPN 2"),
        ("espn_3_peru", "ESPN 3"),
        ("fox_sports_peru", "Fox Sports"),
        ("america_television_peru", "América TV"),
        ("tv_peru", "TV Perú"),
    ],
    "EC": [
        ("espn_ecuador", "ESPN"),
        ("espn_2_ecuador", "ESPN 2"),
        ("fox_sports_ecuador", "Fox Sports"),
        ("goltv_latinoamerica", "GolTV"),
        ("teleamazonas", "Teleamazonas"),
        ("ecuavisa_ecuador", "Ecuavisa"),
    ],
}

# Canales que SOLO se consultan para fútbol.
#
# El fútbol europeo no vive en ESPN 1 y 2: está repartido por todo el abanico.
# Verificado el 16/09/2026 en GatoTV:
#   ESPN 3 Venezuela  → Rayo Vallecano-Espanyol, Villarreal-Betis, Lazio-Milan
#   ESPN 5 Panamá     → Porto-Manchester City, Lille-Betis (Champions)
#   ESPN 6 Venezuela  → Gaziantep-Fenerbahce, West Ham-Wrexham
#   ESPN 7 Venezuela  → Sunderland-AZ Alkmaar, Omonia-Celta de Vigo
#   ESPN 3 Panamá     → Liverpool-Tottenham, Everton-Wolverhampton
# Sin estos canales, a un venezolano o un panameño le decíamos "no sabemos"
# sobre media Champions y media Premier.
#
# Van aparte y no en la lista de arriba para no pedir diez parrillas de ESPN
# por un juego de béisbol, donde no aportan nada.
GATOTV_SOCCER_EXTRA: dict[str, list[tuple[str, str]]] = {
    "VE": [("espn_4_venezuela", "ESPN 4"), ("espn_5_venezuela", "ESPN 5"),
           ("espn_6_venezuela", "ESPN 6"), ("espn_7_venezuela", "ESPN 7"),
           ("fox_sports_3_venezuela", "Fox Sports 3")],
    "PA": [("espn_3_panama", "ESPN 3"), ("espn_4_panama", "ESPN 4"),
           ("espn_5_panama", "ESPN 5"), ("espn_6_panama", "ESPN 6"),
           ("fox_sports_3_panama", "Fox Sports 3")],
    "DO": [("espn_3_republica_dominicana", "ESPN 3"), ("espn_4_republica_dominicana", "ESPN 4"),
           ("espn_5_republica_dominicana", "ESPN 5"), ("espn_6_republica_dominicana", "ESPN 6"),
           ("fox_sports_2_republica_dominicana", "Fox Sports 2"),
           ("fox_sports_3_republica_dominicana", "Fox Sports 3")],
    "CO": [("espn_3_colombia", "ESPN 3"), ("espn_4_colombia", "ESPN 4"),
           ("espn_5_colombia", "ESPN 5"), ("espn_6_colombia", "ESPN 6"),
           ("espn_7_colombia", "ESPN 7"), ("fox_sports_2_colombia", "Fox Sports 2"),
           ("fox_sports_3_colombia", "Fox Sports 3"), ("win_sports", "Win Sports")],
    "PE": [("espn_4_peru", "ESPN 4"), ("espn_5_peru", "ESPN 5"),
           ("espn_6_peru", "ESPN 6"), ("espn_7_peru", "ESPN 7"),
           ("fox_sports_2_peru", "Fox Sports 2"), ("fox_sports_3_peru", "Fox Sports 3")],
    "EC": [("espn_3_ecuador", "ESPN 3"), ("espn_4_ecuador", "ESPN 4"),
           ("espn_5_ecuador", "ESPN 5"), ("espn_6_ecuador", "ESPN 6"),
           ("espn_7_ecuador", "ESPN 7"), ("fox_sports_2_ecuador", "Fox Sports 2"),
           ("fox_sports_3_ecuador", "Fox Sports 3")],
}


def channels_for(cc: str, sport: str | None = None) -> list[tuple[str, str]]:
    """Canales a consultar para ese país y deporte."""
    base = list(GATOTV_SPORTS_CHANNELS.get(cc.upper(), []))
    if (sport or "").lower() == "soccer":
        base += GATOTV_SOCCER_EXTRA.get(cc.upper(), [])
    return base

# 6 h: la parrilla del día no cambia, y así una jornada entera cuesta una
# petición por canal aunque la vean mil personas.
_grid_cache: TTLCache = TTLCache(maxsize=400, ttl=21600)
_fail_until: dict[str, float] = {}

# Estamos leyendo la web de alguien más. Como mucho 5 peticiones a la vez, para
# no parecer un ataque ni tumbarles el servidor en la primera visita del día.
_sem = asyncio.Semaphore(5)


def grid_date_for(dt: datetime) -> str:
    """Día de parrilla (YYYY-MM-DD) que le corresponde a un instante UTC."""
    return dt.astimezone(_GRID_TZ).strftime("%Y-%m-%d")

_ROW_RE = re.compile(
    r'<tr[^>]*class="[^"]*tbl_EPG_row(?:Alternate|_selected)?[^"]*"[^>]*>(.*?)</tr>',
    re.S | re.I,
)
_TIME_RE = re.compile(r'<time[^>]*datetime="(\d{1,2}:\d{2})"', re.I)
_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    """HTML → texto plano, sin etiquetas ni entidades."""
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", raw))).strip()


def parse_grid(html: str, date_iso: str) -> list[dict]:
    """Parrilla de un canal para un día: [{'start': datetime UTC, 'title': str}].

    Se parsea con expresiones regulares a propósito: BeautifulSoup no está en
    requirements.txt y no vale la pena agregar una dependencia al deploy por esto.
    """
    out: list[dict] = []
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
    except Exception:
        return out

    for row in _ROW_RE.findall(html or ""):
        tm = _TIME_RE.search(row)
        if not tm:
            continue
        # El título es el primer <span> con texto real de la fila.
        title = ""
        for sp in _SPAN_RE.findall(row):
            t = _text(sp)
            if len(t) > 2:
                title = t
                break
        if not title:
            continue
        try:
            hh, mm = (int(x) for x in tm.group(1).split(":"))
            start = datetime(y, m, d, hh, mm, tzinfo=_GRID_TZ).astimezone(timezone.utc)
        except Exception:
            continue
        out.append({"start": start, "title": title})
    return out


async def fetch_grid(channel_slug: str, date_iso: str) -> list[dict]:
    """Parrilla de un canal, cacheada. Devuelve [] ante cualquier problema."""
    key = f"{channel_slug}:{date_iso}"
    if key in _grid_cache:
        return _grid_cache[key]

    import time as _time
    if _fail_until.get(channel_slug, 0) > _time.time():
        return []

    url = f"{GATOTV_BASE}/{channel_slug}/{date_iso}"
    try:
        async with _sem, httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; DondeVerBot/1.0; +https://dondever.app)",
                "Accept-Language": "es",
            })
            if resp.status_code != 200:
                # Un canal que ya no existe no se reintenta en una hora.
                _fail_until[channel_slug] = _time.time() + 3600
                return []
            grid = parse_grid(resp.text, date_iso)
    except Exception as e:
        logger.warning(f"GatoTV {channel_slug} {date_iso}: {e}")
        _fail_until[channel_slug] = _time.time() + 600
        return []

    _grid_cache[key] = grid
    return grid


# ── Emparejar un partido nuestro con una línea de la parrilla ────────────────

# Solo conectores y sufijos societarios. NO se quitan "real", "atlético",
# "deportivo", "united"…: en español esas palabras SÍ distinguen. Quitarlas hacía
# que "Real Madrid vs Osasuna" cruzara con "Atlético de Madrid vs. CA Osasuna",
# porque de ambos equipos solo quedaba "madrid".
_STOP = {
    "fc", "cf", "cd", "sc", "ac", "afc", "club", "de", "del", "la", "las",
    "el", "los", "the", "y", "and", "vs",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _keys(team_name: str) -> list[str]:
    """Palabras que identifican al equipo: 'Atlético de Madrid' → ['madrid'].

    Se descartan las genéricas ('real', 'deportivo', 'club'…) porque aparecen en
    decenas de equipos y darían falsos positivos.
    """
    toks = [t for t in _norm(team_name).split() if len(t) > 2 and t not in _STOP]
    return toks or [t for t in _norm(team_name).split() if len(t) > 2]


def match_program(programs: list[dict], home: str, away: str,
                  game_start: datetime | None = None,
                  window_min: int = 150) -> dict | None:
    """¿Alguna línea de la parrilla es ESTE partido?

    Exige que TODAS las palabras identificadoras de cada equipo estén en el
    título, no solo una. Con "alguna" bastaba, "Real Madrid" cruzaba con
    "Atlético de Madrid". Ser estricto produce algún partido sin canal, que es
    un silencio; ser laxo produce un canal equivocado, que es una mentira.
    """
    hk, ak = _keys(home), _keys(away)
    if not hk or not ak:
        return None
    for p in programs:
        t = _norm(p["title"])
        if not all(k in t for k in hk) or not all(k in t for k in ak):
            continue
        if game_start is not None:
            delta = abs((p["start"] - game_start).total_seconds()) / 60
            if delta > window_min:
                continue
        return p
    return None


async def channels_for_game(cc: str, date_iso: str, home: str, away: str,
                            game_start: datetime | None = None,
                            sport: str | None = None) -> list[str]:
    """Canales de ese país que transmiten este partido, según GatoTV."""
    canales = channels_for(cc, sport)
    if not canales:
        return []
    grids = await asyncio.gather(
        *(fetch_grid(slug, date_iso) for slug, _ in canales),
        return_exceptions=True,
    )
    found: list[str] = []
    for (slug, display), grid in zip(canales, grids):
        if isinstance(grid, Exception) or not grid:
            continue
        if match_program(grid, home, away, game_start) and display not in found:
            found.append(display)
    return found


async def channels_by_country_for_game(date_iso: str, home: str, away: str,
                                       game_start: datetime | None = None,
                                       countries: list[str] | None = None,
                                       sport: str | None = None) -> dict[str, list[str]]:
    """{'VE': ['ESPN'], 'PA': ['Fox Sports']…} para un partido."""
    ccs = countries or list(GATOTV_SPORTS_CHANNELS)
    results = await asyncio.gather(
        *(channels_for_game(cc, date_iso, home, away, game_start, sport) for cc in ccs),
        return_exceptions=True,
    )
    out: dict[str, list[str]] = {}
    for cc, res in zip(ccs, results):
        if not isinstance(res, Exception) and res:
            out[cc] = res
    return out
