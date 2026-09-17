"""Parrillas por país desde epgshare01.online (XMLTV prearmado).

POR QUÉ EXISTE
--------------
GatoTV nos resolvió el fútbol europeo en seis países, pero dejó dos huecos que
duelen: no lista béisbol (MLB fuera de México seguía en blanco) ni las ligas
locales completas. Y cuesta caro: hasta ~70 peticiones HTTP para armar una sola
ficha, con 12 s medidos en frío.

epgshare01.online publica la misma información en XMLTV ya armado: UN archivo
comprimido por país, actualizado a diario. Verificado el 17/09/2026:

    <programme start="20260917180000 -0500" channel="Canal.2...(Teleantillas).do">
      <title>MLB</title>
      <sub-title>Filis de Filadelfia vs. Mets de Nueva York</sub-title>

Es decir: el enfrentamiento exacto, el canal, y la hora CON SU OFFSET. Eso
último importa mucho — con GatoTV tuvimos que adivinar la zona horaria y nos
costó bugs. Aquí viene declarada.

Enfrentamientos contados el 17/09/2026 (dos días de parrilla):
    MX 272 · CO 214 · PE 194 · DO 46 · EC 42 · PA 25
Incluye MLB en Dominicana (13) y Panamá (9), NFL, Liga BetPlay, fútbol peruano
de 1ª, 2ª y femenino, LMB y LNBP.

LÍMITES CONOCIDOS
-----------------
- NO hay archivo de Venezuela, que es nuestro primer país de LATAM. Para VE
  seguimos dependiendo de GatoTV. Esto no lo tapa.
- Colombia casi no transmite MLB (2 juegos en dos días). Es un dato real del
  mercado, no una falla de la fuente.
- Es un volcado comunitario, no una API con contrato: puede romperse. Ante
  cualquier problema devolvemos vacío y el sitio sigue igual.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from cachetools import TTLCache

import gatotv as _gatotv   # reutilizamos su comparador de nombres

logger = logging.getLogger("dondever")

BASE = "https://epgshare01.online/epgshare01"

# País → etiqueta del archivo. Verificadas una por una contra el índice del
# sitio el 17/09/2026; VE1 devuelve 404, por eso Venezuela no está.
EPG_TAGS = {
    "CO": "CO1",
    "DO": "DO1",
    "PA": "PA1",
    "PE": "PE1",
    "EC": "EC1",
    "MX": "MX1",
}

# El archivo se regenera una vez al día (visto 17-Sep-2026 11:35 en el índice).
# 12 h es holgado y nos deja dos refrescos diarios.
_cache: TTLCache = TTLCache(maxsize=16, ttl=43200)
_fail_until: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


# ── Nombres en español ───────────────────────────────────────────────────────
# Las parrillas dominicanas y panameñas traen los equipos de MLB traducidos:
# "Rojos de Cincinnati", "Filis de Filadelfia", "Medias Rojas de Boston".
# Nosotros los tenemos en inglés desde ESPN. Sin esta tabla no cruza ni uno.
#
# Se traduce por FRASE y no por palabra suelta porque "Medias Rojas" y "Medias
# Blancas" son dos palabras y solo se distinguen por la segunda.
_ES_EN = [
    ("medias rojas", "red sox"), ("medias blancas", "white sox"),
    ("nueva york", "new york"), ("san luis", "st louis"),
    ("filadelfia", "philadelphia"), ("filis", "phillies"),
    ("yanquis", "yankees"), ("rojos", "reds"), ("cachorros", "cubs"),
    ("cerveceros", "brewers"), ("piratas", "pirates"),
    ("cardenales", "cardinals"), ("bravos", "braves"),
    ("nacionales", "nationals"), ("gigantes", "giants"),
    ("marineros", "mariners"), ("angelinos", "angels"),
    ("atleticos", "athletics"), ("reales", "royals"),
    ("mellizos", "twins"), ("guardianes", "guardians"),
    ("azulejos", "blue jays"), ("cascabeles", "diamondbacks"),
]


def _es_to_en(s: str) -> str:
    for es, en in _ES_EN:
        s = s.replace(es, en)
    return s


# ── Descarga y parseo ────────────────────────────────────────────────────────

_CH_RE = re.compile(
    r'<channel id="([^"]+)">\s*<display-name[^>]*>([^<]*)</display-name>', re.I)
_PR_RE = re.compile(
    r'<programme start="(\d{14})\s*([+\-]\d{4})"[^>]*channel="([^"]+)"[^>]*>'
    r'\s*<title[^>]*>([^<]*)</title>'
    r'\s*<sub-title[^>]*>([^<]*)</sub-title>', re.I)

# Cortamos el sub-título en la primera coma: varias entradas traen
# "Cincinnati Reds vs. Chicago White Sox, Temporada regular, Guaranteed Rate Field"
# y el estadio no aporta nada al cruce.
_TAIL_RE = re.compile(r",.*$")


def _parse(xml: str) -> list[dict]:
    """XMLTV → solo los programas que son un enfrentamiento.

    Filtramos a ' vs ' a propósito: el archivo de México trae 17,365 programas
    y solo 272 son partidos. Guardar el resto sería cargar dibujos animados en
    memoria para siempre.
    """
    nombres = dict(_CH_RE.findall(xml))
    out: list[dict] = []
    for start, off, ch, title, sub in _PR_RE.findall(xml):
        if " vs" not in sub.lower():
            continue
        limpio = _TAIL_RE.sub("", sub).strip()
        try:
            tz = timezone(timedelta(
                hours=int(off[1:3]) * (1 if off[0] == "+" else -1),
                minutes=int(off[3:5]) * (1 if off[0] == "+" else -1)))
            dt = datetime.strptime(start, "%Y%m%d%H%M%S").replace(tzinfo=tz)
        except Exception:
            continue
        out.append({
            "start": dt.astimezone(timezone.utc),
            "title": limpio,
            "programa": title.strip(),
            "canal": nombres.get(ch, ch),
        })
    return out


async def grid(cc: str) -> list[dict]:
    """Parrilla de enfrentamientos de un país. [] ante cualquier problema."""
    tag = EPG_TAGS.get(cc.upper())
    if not tag:
        return []
    if tag in _cache:
        return _cache[tag]

    import time as _time
    if _fail_until.get(tag, 0) > _time.time():
        return []

    # Un solo descargador por país: si entran diez visitas a la vez, no bajamos
    # el archivo diez veces.
    lock = _locks.setdefault(tag, asyncio.Lock())
    async with lock:
        if tag in _cache:
            return _cache[tag]
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
                r = await client.get(f"{BASE}/epg_ripper_{tag}.xml.gz", headers={
                    "User-Agent": "Mozilla/5.0 (compatible; DondeVerBot/1.0; +https://dondever.app)",
                })
                if r.status_code != 200:
                    _fail_until[tag] = _time.time() + 3600
                    return []
                datos = _parse(gzip.decompress(r.content).decode("utf-8", "ignore"))
        except Exception as e:
            logger.warning(f"epgshare {tag}: {e}")
            _fail_until[tag] = _time.time() + 600
            return []

        _cache[tag] = datos
        logger.info(f"epgshare {tag}: {len(datos)} enfrentamientos")
        return datos


# ── Cruce con un partido nuestro ─────────────────────────────────────────────

_SPLIT = re.compile(r"\s+vs\.?\s+|\s+v\s+", re.I)


def _match(programas: list[dict], home: str, away: str,
           start: datetime | None) -> list[dict]:
    """Programas que son ESTE partido, el más cercano en hora primero.

    Aquí SÍ filtramos por hora, al revés que en gatotv.py: estas horas vienen
    con su offset declarado, así que son de fiar. Preferimos la transmisión en
    vivo, pero si solo hay repetición la damos igual — el canal es el mismo y
    es la respuesta que el usuario busca.
    """
    hk, ak = _gatotv._keys(home), _gatotv._keys(away)
    if not hk or not ak:
        return []

    hits = []
    for p in programas:
        partes = _SPLIT.split(_es_to_en(_gatotv._norm(p["title"])))
        if len(partes) != 2:
            continue
        izq = [t for t in partes[0].split() if len(t) > 2 and t not in _gatotv._STOP]
        der = [t for t in partes[1].split() if len(t) > 2 and t not in _gatotv._STOP]
        ok = (_gatotv._same_team(hk, izq) and _gatotv._same_team(ak, der)) or \
             (_gatotv._same_team(hk, der) and _gatotv._same_team(ak, izq))
        if not ok:
            continue
        delta = abs((p["start"] - start).total_seconds()) if start else 0
        hits.append((delta, p))

    hits.sort(key=lambda x: x[0])
    return [p for _d, p in hits]


async def channels_by_country_for_game(home: str, away: str,
                                       game_start: datetime | None = None,
                                       countries: list[str] | None = None,
                                       ) -> dict[str, list[str]]:
    """{'DO': ['Canal 2 (Teleantillas)', 'ESPN 3']…} para un partido."""
    ccs = [c for c in (countries or list(EPG_TAGS)) if c.upper() in EPG_TAGS]
    # En serie y no en paralelo: cada archivo son ~5 MB descomprimidos y no
    # queremos seis a la vez en memoria en un plan chico de Render.
    out: dict[str, list[str]] = {}
    for cc in ccs:
        try:
            programas = await grid(cc)
        except Exception:
            continue
        if not programas:
            continue
        canales: list[str] = []
        for p in _match(programas, home, away, game_start):
            if p["canal"] not in canales:
                canales.append(p["canal"])
        if canales:
            out[cc.upper()] = canales[:4]
    return out
