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
}

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


async def fetch_events(kind: str, days_back: int = 3, days_ahead: int = 90) -> list[dict]:
    """Upcoming (and very recent) events for a kind, parsed and sorted by date."""
    if kind == "boxing":
        return load_boxing_events(days_back=days_back, days_ahead=days_ahead)
    sport, league = EVENT_SOURCES[kind]
    now = datetime.now(timezone.utc)
    raw = await _fetch_range(sport, league, now - timedelta(days=days_back), now + timedelta(days=days_ahead))
    parser = _parse_ufc if kind == "ufc" else _parse_f1
    out = []
    for ev in raw:
        try:
            out.append(parser(ev))
        except Exception as e:
            logger.warning(f"parse {kind} event failed: {e}")
    out.sort(key=lambda e: e.get("date", ""))
    return out


async def fetch_all_events(days_ahead: int = 90) -> list[dict]:
    import asyncio
    ufc, f1 = await asyncio.gather(fetch_events("ufc", days_ahead=days_ahead),
                                   fetch_events("f1", days_ahead=days_ahead))
    box = load_boxing_events(days_ahead=days_ahead)
    allev = ufc + f1 + box
    allev.sort(key=lambda e: e.get("date", ""))
    return allev


async def get_event_by_slug(slug: str) -> Optional[dict]:
    """Find an event by slug across UFC/F1 (last 30 days + next 120) and boxing."""
    for kind in ("ufc", "f1"):
        for ev in await fetch_events(kind, days_back=30, days_ahead=120):
            if ev["slug"] == slug:
                return ev
    for ev in load_boxing_events(days_back=365, days_ahead=365):
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
