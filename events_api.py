"""
DondeVer — Eventos de combate y motor (UFC, F1, Boxeo).

A diferencia de los partidos (home vs away, un solo horario), estos eventos son
"carteleras" con varios segmentos: UFC tiene preliminares + estelar con 11-14
peleas; un Gran Premio tiene 5 sesiones en 3 días. La intención de búsqueda
("dónde ver UFC 331", "a qué hora es la carrera del GP de México") necesita
una página por evento con todos los horarios, no una card genérica.

Fuentes:
  - UFC y F1: ESPN scoreboard con rango de fechas (gratis, sin key).
  - Boxeo: ESPN no tiene API → boxing_events.json curado a mano.

Modelo de evento (dict):
  kind: "ufc" | "f1" | "boxing"
  id, slug, name, short_name
  date (ISO UTC del evento principal), start_date (primer segmento)
  status: pre | in | post
  venue, city, country
  segments: [{"key","name","date","status"}]        # UFC: prelims/estelar; F1: FP1..Race
  fights:   [{"weight","fighters":[{name,flag,record,headshot}],"segment","is_main","is_comain"}]
  broadcasts_us: [str]
  channels_mx: [str]
"""

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from cachetools import TTLCache

from config import TZ_MX

logger = logging.getLogger("dondever.events")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

EVENT_SOURCES = {
    "ufc": ("mma", "ufc"),
    "f1": ("racing", "f1"),
    "nascar": ("racing", "nascar-premier"),   # NASCAR Cup Series
    "indycar": ("racing", "irl"),             # IndyCar Series
}
# MotoGP no está en ESPN → TheSportsDB (key premium) eventsseason, agrupado por GP.
SPORTSDB_MOTOGP_ID = "4407"
RACE_KINDS = ("f1", "motogp", "nascar", "indycar")
ALL_KINDS = ("ufc", "f1", "boxing", "motogp", "nascar", "indycar")

# Canales por país (rights 2026). Se muestran en la página de evento.
EVENT_CHANNELS = {
    "ufc": {
        "MX": ["Paramount+", "Fox Sports MX"],
        "US": ["Paramount+"],
        "VE": ["Paramount+", "ESPN Latinoamérica"],
        "CO": ["Paramount+", "ESPN Latinoamérica"],
        "AR": ["Paramount+", "ESPN Latinoamérica"],
        "ES": ["Eurosport", "HBO Max"],
    },
    "f1": {
        "MX": ["Fox Sports MX", "Canal 5", "F1 TV Pro"],
        "US": ["Apple TV", "F1 TV Pro"],
        "VE": ["Disney+", "ESPN Latinoamérica"],
        "CO": ["Disney+", "ESPN Latinoamérica"],
        "AR": ["Disney+", "ESPN Latinoamérica"],
        "ES": ["DAZN"],
    },
    "boxing": {
        "MX": ["TV Azteca", "Canal 5", "DAZN"],
        "US": ["DAZN", "Netflix", "ESPN+"],
        "VE": ["DAZN", "ESPN Latinoamérica"],
        "CO": ["DAZN", "ESPN Latinoamérica"],
        "AR": ["DAZN", "ESPN Latinoamérica"],
        "ES": ["DAZN"],
    },
    "motogp": {
        "MX": ["ESPN MX", "Disney+"],
        "US": ["TNT Sports", "HBO Max"],
        "VE": ["ESPN Latinoamérica", "Disney+"],
        "CO": ["ESPN Latinoamérica", "Disney+"],
        "AR": ["ESPN Latinoamérica", "Disney+"],
        "ES": ["DAZN"],
    },
    "nascar": {
        "MX": ["Fox Sports MX"],
        "US": ["NBC", "USA Network", "HBO Max"],   # se sobreescribe con broadcasts de ESPN por carrera
        "VE": ["Fox Sports Latinoamérica"],
        "CO": ["Fox Sports Latinoamérica"],
        "AR": ["Fox Sports Latinoamérica"],
        "ES": ["DAZN"],
    },
    "indycar": {
        "MX": ["ESPN MX", "Disney+"],
        "US": ["FOX", "FS1"],
        "VE": ["ESPN Latinoamérica", "Disney+"],
        "CO": ["ESPN Latinoamérica", "Disney+"],
        "AR": ["ESPN Latinoamérica", "Disney+"],
        "ES": ["DAZN"],
    },
}

_events_cache = TTLCache(maxsize=8, ttl=1800)  # 30 min


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text)


# ── F1: nombres en español ───────────────────────────────

_GP_ES = {
    "australian": ("Australia", "gp-de-australia"),
    "chinese": ("China", "gp-de-china"),
    "japanese": ("Japón", "gp-de-japon"),
    "bahrain": ("Baréin", "gp-de-barein"),
    "saudi arabian": ("Arabia Saudita", "gp-de-arabia-saudita"),
    "miami": ("Miami", "gp-de-miami"),
    "canadian": ("Canadá", "gp-de-canada"),
    "monaco": ("Mónaco", "gp-de-monaco"),
    "spanish": ("España", "gp-de-espana"),
    "austrian": ("Austria", "gp-de-austria"),
    "british": ("Gran Bretaña", "gp-de-gran-bretana"),
    "belgian": ("Bélgica", "gp-de-belgica"),
    "hungarian": ("Hungría", "gp-de-hungria"),
    "dutch": ("Países Bajos", "gp-de-paises-bajos"),
    "italian": ("Italia", "gp-de-italia"),
    "azerbaijan": ("Azerbaiyán", "gp-de-azerbaiyan"),
    "singapore": ("Singapur", "gp-de-singapur"),
    "united states": ("Estados Unidos", "gp-de-estados-unidos"),
    "mexico city": ("México", "gp-de-mexico"),
    "mexican": ("México", "gp-de-mexico"),
    "sao paulo": ("São Paulo", "gp-de-sao-paulo"),
    "brazilian": ("Brasil", "gp-de-brasil"),
    "las vegas": ("Las Vegas", "gp-de-las-vegas"),
    "qatar": ("Catar", "gp-de-catar"),
    "abu dhabi": ("Abu Dabi", "gp-de-abu-dabi"),
    "emilia romagna": ("Emilia-Romaña", "gp-de-emilia-romana"),
    "madrid": ("Madrid", "gp-de-madrid"),
}

_F1_SESSIONS_ES = {
    "FP1": "Práctica Libre 1", "FP2": "Práctica Libre 2", "FP3": "Práctica Libre 3",
    "Qual": "Clasificación", "Qualifying": "Clasificación", "Race": "Carrera",
    "Sprint": "Carrera Sprint", "Sprint Qual": "Clasificación Sprint", "SQ": "Clasificación Sprint",
    "Sprint Shootout": "Clasificación Sprint",
}


def _gp_spanish(name: str, year: int) -> tuple[str, str]:
    """'Tag Heuer Spanish Grand Prix' → ('Gran Premio de España 2026', 'gp-de-espana-2026')."""
    low = slugify(name).replace("-", " ")
    for key, (es, slug) in _GP_ES.items():
        if key in low:
            return f"Gran Premio de {es} {year}", f"{slug}-{year}"
    base = re.sub(r"\bgrand prix\b", "", name, flags=re.I).strip()
    return f"Gran Premio de {base} {year}", f"gp-de-{slugify(base)}-{year}"


# ── ESPN fetch ───────────────────────────────────────────

async def _fetch_range(sport: str, league: str, start: datetime, end: datetime) -> list[dict]:
    key = f"{sport}:{league}:{start:%Y%m%d}-{end:%Y%m%d}"
    if key in _events_cache:
        return _events_cache[key]
    url = f"{ESPN_BASE}/{sport}/{league}/scoreboard"
    params = {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": "100"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            events = r.json().get("events") or []
    except Exception as e:
        logger.warning(f"ESPN events fetch failed {sport}/{league}: {e}")
        events = []
    _events_cache[key] = events
    return events


def _state(obj: dict) -> str:
    return ((obj.get("status") or {}).get("type") or {}).get("state", "pre")


def mx_date(iso: str) -> str:
    """UTC ISO → YYYY-MM-DD en hora de México (los eventos de noche cruzan medianoche UTC)."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ_MX).strftime("%Y-%m-%d")
    except Exception:
        return (iso or "")[:10]


_WEIGHT_ES = {
    "Strawweight": "Peso paja", "Flyweight": "Peso mosca", "Bantamweight": "Peso gallo",
    "Featherweight": "Peso pluma", "Lightweight": "Peso ligero", "Welterweight": "Peso wélter",
    "Middleweight": "Peso mediano", "Light Heavyweight": "Peso semipesado", "Heavyweight": "Peso pesado",
    "Catchweight": "Peso pactado", "Catch Weight": "Peso pactado",
}


def weight_es(abbr: str) -> str:
    a = (abbr or "").strip()
    fem = a.startswith("W ") or a.startswith("Women's ")
    base = a.replace("W ", "").replace("Women's ", "")
    es = _WEIGHT_ES.get(base, base)
    return f"{es} femenil" if fem else es


def _parse_ufc(ev: dict) -> dict:
    comps = ev.get("competitions") or []
    name = ev.get("name", "")
    short = ev.get("shortName", "") or name.split(":")[0]
    is_contender = "contender series" in name.lower()
    venues = ev.get("venues") or []
    venue = venues[0] if venues else ((comps[0].get("venue") if comps else None) or {})
    addr = venue.get("address") or {}

    # Segments = distinct start times, ascending. Last = cartelera estelar.
    times = sorted({c.get("date", "") for c in comps if c.get("date")})
    seg_names = {1: ["Cartelera"], 2: ["Preliminares", "Cartelera estelar"],
                 3: ["Preliminares tempranas", "Preliminares", "Cartelera estelar"]}
    names = seg_names.get(len(times), ["Segmento %d" % (i + 1) for i in range(len(times))])
    segments = [{"key": f"seg{i}", "name": names[i], "date": t,
                 "status": "pre"} for i, t in enumerate(times)]
    seg_by_date = {t: i for i, t in enumerate(times)}

    fights = []
    for c in comps:
        fighters = []
        for comp in sorted(c.get("competitors") or [], key=lambda x: x.get("order", 0)):
            ath = comp.get("athlete") or {}
            fighters.append({
                "name": ath.get("displayName") or ath.get("fullName", ""),
                "flag": (ath.get("flag") or {}).get("alt", ""),
                "flag_url": (ath.get("flag") or {}).get("href", ""),
                "record": comp.get("record", "") or "",
                "headshot": (ath.get("headshot") or {}).get("href", ""),
                "winner": bool(comp.get("winner")),
            })
        fights.append({
            "id": c.get("id", ""),
            "weight": weight_es((c.get("type") or {}).get("abbreviation") or ""),
            "fighters": fighters,
            "segment": seg_by_date.get(c.get("date", ""), 0),
            "date": c.get("date", ""),
            "status": _state(c),
            "rounds": ((c.get("format") or {}).get("regulation") or {}).get("periods", 3),
            "is_main": False, "is_comain": False,
        })
    # ESPN lists the card in order; main event is the last one with 5 rounds (or just last)
    if fights:
        fights[-1]["is_main"] = True
        if len(fights) > 1:
            fights[-2]["is_comain"] = True
        for f in fights:
            if f["rounds"] == 5 and not f["is_main"]:
                pass  # title fights in co-main also 5 rounds; keep last as main
        # Order for display: main first
        fights = list(reversed(fights))
    for s in segments:
        s["status"] = "post" if all(f["status"] == "post" for f in fights if f["segment"] == seg_by_date[s["date"]]) else s["status"]

    us_bc = []
    for c in comps:
        for g in c.get("geoBroadcasts") or []:
            n = (g.get("media") or {}).get("shortName")
            if n and n not in us_bc:
                us_bc.append(n)

    main_date = times[-1] if times else ev.get("date", "")
    year = (ev.get("date") or "")[:4]
    slug = f"{slugify(short or name)}-{mx_date(main_date)}" if is_contender or not short else f"{slugify(name)}-{mx_date(main_date)}"
    return {
        "kind": "ufc",
        "id": ev.get("id", ""),
        "slug": slug,
        "name": name,
        "short_name": short,
        "date": main_date,
        "start_date": times[0] if times else ev.get("date", ""),
        "status": _state(ev),
        "venue": venue.get("fullName", ""),
        "city": addr.get("city", ""),
        "country": addr.get("country", ""),
        "segments": segments,
        "fights": fights,
        "sessions": [],
        "broadcasts_us": us_bc,
        "channels": EVENT_CHANNELS["ufc"],
        "is_minor": is_contender,
        "espn_link": ((ev.get("links") or [{}])[0]).get("href", ""),
    }


def _parse_f1(ev: dict) -> dict:
    comps = ev.get("competitions") or []
    name = ev.get("name", "")
    year = int((ev.get("date") or "2026")[:4])
    name_es, slug = _gp_spanish(name, year)
    circuit = (comps[0].get("venue") if comps else None) or {}
    addr = circuit.get("address") or {}
    sessions = []
    race_date = ev.get("date", "")
    for c in comps:
        code = ((c.get("type") or {}).get("abbreviation") or "").strip()
        sessions.append({
            "key": code,
            "name": _F1_SESSIONS_ES.get(code, (c.get("type") or {}).get("text") or code),
            "date": c.get("date", ""),
            "status": _state(c),
        })
        if code.lower() == "race":
            race_date = c.get("date", race_date)
    sessions.sort(key=lambda s: s["date"])
    us_bc = []
    for c in comps:
        for g in c.get("geoBroadcasts") or []:
            n = (g.get("media") or {}).get("shortName")
            if n and n not in us_bc:
                us_bc.append(n)
    return {
        "kind": "f1",
        "id": ev.get("id", ""),
        "slug": slug,
        "name": name_es,
        "short_name": name_es.replace("Gran Premio", "GP"),
        "name_en": name,
        "date": race_date,
        "start_date": sessions[0]["date"] if sessions else race_date,
        "status": _state(ev),
        "venue": circuit.get("fullName", ""),
        "city": addr.get("city", ""),
        "country": addr.get("country", ""),
        "segments": [],
        "fights": [],
        "sessions": sessions,
        "broadcasts_us": us_bc,
        "channels": EVENT_CHANNELS["f1"],
        "is_minor": False,
        "espn_link": ((ev.get("links") or [{}])[0]).get("href", ""),
    }


# ── NASCAR Cup / IndyCar (ESPN): una carrera por evento ──

_NASCAR_TRACK_ES = {
    "world wide technology raceway": "Gateway", "charlotte roval": "Charlotte (Roval)",
    "las vegas": "Las Vegas", "talladega": "Talladega", "martinsville": "Martinsville",
    "phoenix": "Phoenix", "homestead": "Homestead-Miami", "daytona": "Daytona",
}


def _race_name_es(kind: str, name: str) -> tuple[str, str]:
    """'NASCAR Cup Series at Bristol' → ('NASCAR Cup Series en Bristol', 'NASCAR en Bristol')
       'Grand Prix of Monterey' → ('Gran Premio de Monterey (IndyCar)', 'IndyCar en Monterey')"""
    n = name or ""
    if kind == "nascar":
        track = re.sub(r"^.*?\bat\b\s*", "", n, flags=re.I).strip() if re.search(r"\bat\b", n, re.I) else n
        track = _NASCAR_TRACK_ES.get(track.lower(), track)
        return f"NASCAR Cup Series en {track}", f"NASCAR en {track}"
    # IndyCar
    m = re.search(r"grand prix of (.+)", n, re.I)
    place = m.group(1).strip() if m else re.sub(r"\b(grand prix|gp)\b", "", n, flags=re.I).strip()
    if "indianapolis 500" in n.lower() or "indy 500" in n.lower():
        return "Indy 500", "Indy 500"
    return f"Gran Premio de {place} (IndyCar)", f"IndyCar en {place}"


def _parse_race(kind: str, ev: dict) -> dict:
    comps = ev.get("competitions") or []
    name = ev.get("name", "")
    date = ev.get("date", "")
    name_es, short = _race_name_es(kind, name)
    circuit = (comps[0].get("venue") if comps else None) or {}
    addr = circuit.get("address") or {}
    for c in comps:
        if ((c.get("type") or {}).get("abbreviation") or "").lower() == "race":
            date = c.get("date", date)
    us_bc = []
    for c in comps:
        for b in c.get("broadcasts") or []:
            for n in b.get("names") or []:
                if n and n not in us_bc:
                    us_bc.append(n)
        for g in c.get("geoBroadcasts") or []:
            n = (g.get("media") or {}).get("shortName")
            if n and n not in us_bc:
                us_bc.append(n)
    season = ev.get("season") or {}
    playoffs = season.get("type") == 3 or "playoff" in (season.get("slug") or "")
    chans = dict(EVENT_CHANNELS[kind])
    if us_bc:
        chans["US"] = us_bc[:3]
    track_slug = slugify(short.split(" en ", 1)[-1] if " en " in short else short)
    return {
        "kind": kind,
        "id": ev.get("id", ""),
        "slug": f"{kind}-{track_slug}-{mx_date(date)}",
        "name": name_es + (" — Playoffs" if playoffs and kind == "nascar" else ""),
        "short_name": short,
        "name_en": name,
        "date": date,
        "start_date": date,
        "status": _state(ev),
        "venue": circuit.get("fullName", ""),
        "city": addr.get("city", ""),
        "country": addr.get("country", "") or ("Estados Unidos" if addr.get("state") else ""),
        "segments": [],
        "fights": [],
        "sessions": [{"key": "Race", "name": "Carrera", "date": date, "status": _state(ev)}],
        "broadcasts_us": us_bc,
        "channels": chans,
        "is_minor": False,
        "playoffs": playoffs,
        "espn_link": ((ev.get("links") or [{}])[0]).get("href", ""),
    }


# ── MotoGP (TheSportsDB eventsseason, agrupado por GP) ──

_MOTOGP_GP_ES = {
    "thailand": ("Tailandia", "gp-de-tailandia"), "brazil": ("Brasil", "gp-de-brasil"),
    "americas": ("las Américas", "gp-de-las-americas"), "usa": ("las Américas", "gp-de-las-americas"),
    "qatar": ("Catar", "gp-de-catar"), "spain": ("España", "gp-de-espana"), "jerez": ("España", "gp-de-espana"),
    "france": ("Francia", "gp-de-francia"), "catalunya": ("Cataluña", "gp-de-cataluna"),
    "catalonia": ("Cataluña", "gp-de-cataluna"), "italy": ("Italia", "gp-de-italia"),
    "hungary": ("Hungría", "gp-de-hungria"), "czech": ("República Checa", "gp-de-republica-checa"),
    "czechia": ("República Checa", "gp-de-republica-checa"),
    "netherlands": ("Países Bajos", "gp-de-paises-bajos"), "dutch": ("Países Bajos", "gp-de-paises-bajos"),
    "germany": ("Alemania", "gp-de-alemania"), "britain": ("Gran Bretaña", "gp-de-gran-bretana"),
    "british": ("Gran Bretaña", "gp-de-gran-bretana"), "aragon": ("Aragón", "gp-de-aragon"),
    "san marino": ("San Marino", "gp-de-san-marino"), "austria": ("Austria", "gp-de-austria"),
    "japan": ("Japón", "gp-de-japon"), "indonesia": ("Indonesia", "gp-de-indonesia"),
    "australia": ("Australia", "gp-de-australia"), "malaysia": ("Malasia", "gp-de-malasia"),
    "portugal": ("Portugal", "gp-de-portugal"), "valencia": ("Valencia", "gp-de-valencia"),
    "argentina": ("Argentina", "gp-de-argentina"), "india": ("India", "gp-de-india"),
    "kazakhstan": ("Kazajistán", "gp-de-kazajistan"),
}
_MOTOGP_SESSION_RE = re.compile(
    r"\s+(Free Practice 1|Free Practice 2|Practice|Qualifying 1|Qualifying 2|Qualifying|Sprint Race|Sprint|Warm Up|GP|Race)$",
    re.I)
_MOTOGP_SESSIONS_ES = {
    "free practice 1": ("FP1", "Práctica Libre 1"), "practice": ("PR", "Práctica"),
    "free practice 2": ("FP2", "Práctica Libre 2"), "qualifying 1": ("Q1", "Clasificación Q1"),
    "qualifying 2": ("Q2", "Clasificación Q2"), "qualifying": ("Qual", "Clasificación"),
    "sprint race": ("Sprint", "Carrera Sprint"), "sprint": ("Sprint", "Carrera Sprint"),
    "warm up": ("WU", "Warm Up"), "gp": ("Race", "Carrera"), "race": ("Race", "Carrera"),
}


_season_cache = TTLCache(maxsize=4, ttl=6 * 3600)   # calendario de temporada: cambia poco
_season_stale: dict[str, list] = {}                  # último resultado bueno (fallback ante 429)


async def _fetch_sportsdb_season(league_id: str, season: str) -> list[dict]:
    """eventsseason con reintento ante 429 (TheSportsDB: 100 req/min compartidos con
    el resto del sitio) y fallback al último resultado bueno. Nunca cachea vacío."""
    import asyncio
    key = f"sportsdb:{league_id}:{season}"
    if key in _season_cache:
        return _season_cache[key]
    from config import SPORTSDB_BASE
    events: list = []
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(f"{SPORTSDB_BASE}/eventsseason.php", params={"id": league_id, "s": season})
            if r.status_code == 429:
                logger.warning(f"TheSportsDB 429 season {league_id}/{season} (intento {attempt + 1})")
                await asyncio.sleep(2.5 * (attempt + 1))
                continue
            r.raise_for_status()
            events = r.json().get("events") or []
            break
        except Exception as e:
            logger.warning(f"TheSportsDB season fetch failed {league_id}/{season}: {e}")
            await asyncio.sleep(1.5)
    if events:
        _season_cache[key] = events
        _season_stale[key] = events
        return events
    return _season_stale.get(key, [])


def _sdb_iso(e: dict) -> str:
    ts = e.get("strTimestamp") or ""
    if ts:
        return ts if ts.endswith("Z") else ts + "Z"
    return f"{e.get('dateEvent', '')}T{e.get('strTime') or '00:00:00'}Z"


def _group_motogp(raw: list[dict], now: datetime) -> list[dict]:
    """Sesiones sueltas ('Austria Sprint Race', 'Austria GP') → un evento por Gran Premio."""
    groups: dict[str, dict] = {}
    for e in raw:
        title = (e.get("strEvent") or "").strip()
        m = _MOTOGP_SESSION_RE.search(title)
        if not m:
            continue  # tests, shakedowns
        gp_key = title[: m.start()].strip()
        sess_raw = m.group(1).lower()
        if gp_key.lower() in ("valencia test", "sepang test", "shakedown test", "buriram test"):
            continue
        code, sname = _MOTOGP_SESSIONS_ES.get(sess_raw, (sess_raw, sess_raw.title()))
        iso = _sdb_iso(e)
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except Exception:
            continue
        g = groups.setdefault(gp_key, {"sessions": [], "venue": "", "country": "", "year": dt.year, "status_raw": []})
        g["sessions"].append({"key": code, "name": sname, "date": iso,
                              "status": "post" if (e.get("strStatus") or "") in ("FT", "Match Finished") or dt < now - timedelta(hours=2) else ("in" if dt <= now else "pre")})
        g["venue"] = g["venue"] or (e.get("strVenue") or "")
        g["country"] = g["country"] or (e.get("strCountry") or "")
    out = []
    for gp_key, g in groups.items():
        sessions = sorted(g["sessions"], key=lambda s: s["date"])
        race = next((s for s in sessions if s["key"] == "Race"), None)
        if not race:
            continue
        low = gp_key.lower()
        es, slug_base = next(((es, sl) for k, (es, sl) in _MOTOGP_GP_ES.items() if k in low), (gp_key, f"gp-de-{slugify(gp_key)}"))
        year = g["year"]
        race_dt = datetime.fromisoformat(race["date"].replace("Z", "+00:00"))
        status = "post" if race_dt < now - timedelta(hours=2) else ("in" if race_dt <= now else "pre")
        out.append({
            "kind": "motogp",
            "id": f"motogp-{slug_base}-{year}",
            "slug": f"motogp-{slug_base}-{year}",
            "name": f"Gran Premio de {es} de MotoGP {year}",
            "short_name": f"MotoGP {es}",
            "name_en": gp_key,
            "date": race["date"],
            "start_date": sessions[0]["date"],
            "status": status,
            "venue": g["venue"],
            "city": "",
            "country": g["country"],
            "segments": [],
            "fights": [],
            "sessions": sessions,
            "broadcasts_us": EVENT_CHANNELS["motogp"]["US"],
            "channels": EVENT_CHANNELS["motogp"],
            "is_minor": False,
            "espn_link": "",
        })
    out.sort(key=lambda e: e["date"])
    return out


async def fetch_motogp(days_back: int = 3, days_ahead: int = 90) -> list[dict]:
    now = datetime.now(timezone.utc)
    seasons = [str(now.year)] + ([str(now.year + 1)] if now.month >= 11 else [])
    raw = []
    for s in seasons:
        raw += await _fetch_sportsdb_season(SPORTSDB_MOTOGP_ID, s)
    lo, hi = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    out = []
    for ev in _group_motogp(raw, now):
        dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        if lo <= dt <= hi:
            out.append(ev)
    return out


async def fetch_events(kind: str, days_back: int = 3, days_ahead: int = 90) -> list[dict]:
    """Upcoming (and very recent) events for a kind, parsed and sorted by date."""
    if kind == "boxing":
        return load_boxing_events(days_back=days_back, days_ahead=days_ahead)
    if kind == "motogp":
        return await fetch_motogp(days_back=days_back, days_ahead=days_ahead)
    sport, league = EVENT_SOURCES[kind]
    now = datetime.now(timezone.utc)
    raw = await _fetch_range(sport, league, now - timedelta(days=days_back), now + timedelta(days=days_ahead))
    if kind == "ufc":
        parser = _parse_ufc
    elif kind == "f1":
        parser = _parse_f1
    else:
        parser = lambda ev: _parse_race(kind, ev)
    out = []
    for ev in raw:
        try:
            out.append(parser(ev))
        except Exception as e:
            logger.warning(f"parse {kind} event failed: {e}")
    out.sort(key=lambda e: e.get("date", ""))
    return out


async def fetch_all_events(days_ahead: int = 90, kinds: tuple = ALL_KINDS) -> list[dict]:
    import asyncio
    results = await asyncio.gather(*[fetch_events(k, days_ahead=days_ahead) for k in kinds], return_exceptions=True)
    allev = []
    for k, r in zip(kinds, results):
        if isinstance(r, Exception):
            logger.warning(f"fetch_all_events {k} failed: {r}")
            continue
        allev += r
    allev.sort(key=lambda e: e.get("date", ""))
    return allev


async def get_event_by_slug(slug: str) -> Optional[dict]:
    """Find an event by slug across all kinds (last 30 days + next 120; boxing ±365)."""
    for ev in load_boxing_events(days_back=365, days_ahead=365):  # local, sin red
        if ev["slug"] == slug:
            return ev
    # El prefijo del slug dice el kind → una sola llamada en la mayoría de los casos
    order = [k for k in ("motogp", "nascar", "indycar", "ufc") if slug.startswith(k + "-")]
    if slug.startswith("gp-de-"):
        order.append("f1")
    order += [k for k in ("ufc", "f1", "motogp", "nascar", "indycar") if k not in order]
    for kind in order:
        for ev in await fetch_events(kind, days_back=30, days_ahead=120):
            if ev["slug"] == slug:
                return ev
    return None


# ── Boxeo curado ─────────────────────────────────────────

_BOXING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boxing_events.json")


def load_boxing_events(days_back: int = 3, days_ahead: int = 120) -> list[dict]:
    """Read boxing_events.json → same event model. Hand-curated (no free API exists)."""
    try:
        with open(_BOXING_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"boxing_events.json not loaded: {e}")
        return []
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    out = []
    for b in raw.get("events", []):
        try:
            dt = datetime.fromisoformat(b["date"].replace("Z", "+00:00"))
        except Exception:
            continue
        if not (lo <= dt <= hi):
            continue
        state = "post" if dt < now - timedelta(hours=4) else ("in" if dt <= now else "pre")
        fights = []
        for i, f in enumerate(b.get("fights", [])):
            fights.append({
                "id": f"{b['slug']}-{i}",
                "weight": f.get("weight", ""),
                "fighters": [{"name": x.get("name", ""), "flag": x.get("flag", ""), "flag_url": "",
                              "record": x.get("record", ""), "headshot": "", "winner": False}
                             for x in f.get("fighters", [])],
                "segment": 0, "date": b["date"], "status": state, "rounds": f.get("rounds", 12),
                "is_main": i == 0, "is_comain": i == 1,
            })
        chans = dict(EVENT_CHANNELS["boxing"])
        chans.update(b.get("channels", {}))
        out.append({
            "kind": "boxing",
            "id": b["slug"],
            "slug": b["slug"],
            "name": b["name"],
            "short_name": b.get("short_name", b["name"]),
            "date": b["date"],
            "start_date": b.get("start_date", b["date"]),
            "status": state,
            "venue": b.get("venue", ""),
            "city": b.get("city", ""),
            "country": b.get("country", ""),
            "segments": [{"key": "main", "name": "Función estelar", "date": b["date"], "status": state}],
            "fights": fights,
            "sessions": [],
            "broadcasts_us": chans.get("US", []),
            "channels": chans,
            "is_minor": False,
            "time_confirmed": bool(b.get("time_confirmed", True)),
            "ppv_price_mx": b.get("ppv_price_mx", ""),
            "notes": b.get("notes", ""),
            "espn_link": "",
        })
    out.sort(key=lambda e: e["date"])
    return out
