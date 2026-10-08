#!/usr/bin/env python3
"""Radar de transmisiones gratuitas y oficiales en YouTube.

Qué hace: revisa la pestaña "En vivo" de los canales oficiales de los
transmisores, ve qué están pasando, lo cruza con los partidos del día y
propone candidatos para video_embeds.json.

Qué NO hace: publicar. Escribe en video_embeds_candidatos.json, que no lo lee
nadie. Tú copias a video_embeds.json lo que apruebes. Esto es a propósito: un
video mal identificado en el sitio es un dato sin verificar, y además un video
no oficial sería un reclamo de derechos.

Uso:
    .venv/bin/python radar_transmisiones.py              # todos los canales
    .venv/bin/python radar_transmisiones.py --canal clarosports
    .venv/bin/python radar_transmisiones.py --todo       # sin cruzar con partidos

Ojo: lee el HTML público de YouTube, no una API. Si YouTube cambia su
estructura interna esto deja de encontrar videos — y cuando eso pase avisa en
voz alta en vez de devolver una lista vacía en silencio.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

import httpx

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "video_embeds_candidatos.json")

# IDs comprobados el 8 de octubre de 2026 leyendo cada canal.
# Para agregar uno: abre youtube.com/@handle, ve el código fuente y busca
# "channelId". No inventes el id a partir del nombre.
_CANALES = {
    "clarosports":  ("Claro Sports",       "UCqfCJBfrFSO4tZM1LNZTBFQ"),
    "tvcdeportes":  ("TVC Deportes",       "UCDrKETcUoV46jwBG114FSpQ"),
    "aztecadeportes": ("TV Azteca Deportes", "UCe3Ev4a4Sm9L5aO9sJJ_WXw"),
    "lvbp":         ("LVBP oficial",       "UCQ8bCihIENEhlDuB93xWFTw"),
}

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Palabras que aparecen en el título pero no identifican al equipo.
_RUIDO = {
    "en", "vivo", "el", "la", "los", "las", "de", "del", "vs", "contra", "y",
    "transmision", "transmisión", "partido", "juego", "hoy", "jornada", "fecha",
    "liga", "copa", "torneo", "apertura", "clausura", "femenil", "varonil",
    "fc", "cf", "club", "deportivo", "cd", "ac", "sc", "ca", "afc", "sad",
    "gratis", "completo", "resumen", "highlights", "previa", "post",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def _tokens(nombre: str) -> set[str]:
    """Palabras distintivas de un equipo: 'Leones Negros' → {leones, negros}."""
    return {p for p in _norm(nombre).split() if len(p) > 2 and p not in _RUIDO}


# ── Leer un canal ────────────────────────────────────────

def _extraer_de_html(html: str) -> list[dict]:
    """HTML de la pestaña /streams → lista de videos. Función pura, testeable."""
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise RuntimeError(
            "No se encontró ytInitialData. YouTube cambió su HTML: hay que "
            "actualizar radar_transmisiones.py antes de confiar en el resultado."
        )
    datos = json.loads(m.group(1))

    encontrados: list[dict] = []
    vistos: set[str] = set()

    def recorrer(o):
        if isinstance(o, dict):
            lock = o.get("lockupViewModel")
            if isinstance(lock, dict) and lock.get("contentId") not in vistos:
                vid = lock.get("contentId")
                if vid:
                    vistos.add(vid)
                    encontrados.append(_de_lockup(lock))
            for v in o.values():
                recorrer(v)
        elif isinstance(o, list):
            for v in o:
                recorrer(v)

    recorrer(datos)
    return [e for e in encontrados if e["id"]]


def _recoger_insignias(o, salida: list[str]) -> None:
    """Junta el texto de todas las insignias, recorriendo la estructura.

    A propósito NO se usa una expresión regular sobre el JSON serializado:
    eso depende de si hay espacio después de los dos puntos, y rompe en
    silencio — que es el peor modo de fallar para algo que decide si un
    video es gratis o de paga.
    """
    if isinstance(o, dict):
        tb = o.get("thumbnailBadgeViewModel")
        if isinstance(tb, dict) and tb.get("text"):
            salida.append(str(tb["text"]))
        bv = o.get("badgeViewModel")
        if isinstance(bv, dict) and bv.get("badgeText"):
            salida.append(str(bv["badgeText"]))
        for v in o.values():
            _recoger_insignias(v, salida)
    elif isinstance(o, list):
        for v in o:
            _recoger_insignias(v, salida)


def _de_lockup(lock: dict) -> dict:
    meta = (lock.get("metadata") or {}).get("lockupMetadataViewModel") or {}
    titulo = ((meta.get("title") or {}).get("content") or "").strip()

    insignias: list[str] = []
    _recoger_insignias(lock, insignias)
    texto_insignias = " ".join(insignias)

    # El título miente: Claro Sports pone "en vivo" en transmisiones que son
    # de paga. Solo las insignias dicen la verdad, así que solo ellas deciden.
    en_vivo = bool(re.search(r"en vivo|\blive\b|transmitiendo", texto_insignias, re.I))
    proximo = (not en_vivo) and bool(
        re.search(r"pr.ximamente|upcoming|programado|estreno", texto_insignias, re.I)
    )
    miembros = bool(re.search(r"solo miembros|members only|de pago", texto_insignias, re.I))

    return {
        "id": lock.get("contentId", ""),
        "titulo": titulo,
        "en_vivo": en_vivo,
        "proximo": proximo,
        "solo_miembros": miembros,
        "insignias": texto_insignias.strip(),
    }


async def leer_canal(cliente: httpx.AsyncClient, canal_id: str) -> list[dict]:
    url = f"https://www.youtube.com/channel/{canal_id}/streams"
    r = await cliente.get(url, headers={"User-Agent": _UA, "Accept-Language": "es-MX,es"})
    r.raise_for_status()
    return _extraer_de_html(r.text)


async def es_embebible(cliente: httpx.AsyncClient, video_id: str) -> bool | None:
    """oEmbed responde 401 cuando el dueño deshabilitó la incrustación.

    Devuelve None si no se pudo determinar — que no es lo mismo que False.
    """
    try:
        r = await cliente.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            headers={"User-Agent": _UA},
        )
    except httpx.HTTPError:
        return None
    if r.status_code == 200:
        return True
    if r.status_code in (401, 403):
        return False
    return None


# ── Cruzar con los partidos del día ──────────────────────

def cruzar(videos: list[dict], juegos: list[dict]) -> list[dict]:
    """Empareja por nombres de equipo. Exige los DOS equipos en el título.

    Con uno solo habría demasiados falsos positivos: "lo mejor de Chivas" no
    es el partido de Chivas.
    """
    salida = []
    for v in videos:
        t = _norm(v["titulo"])
        palabras = set(t.split())
        for j in juegos:
            casa = _tokens((j.get("home") or {}).get("name", ""))
            visita = _tokens((j.get("away") or {}).get("name", ""))
            if not casa or not visita:
                continue
            if (casa & palabras) and (visita & palabras):
                salida.append({**v, "juego": j})
                break
    return salida


async def juegos_de_hoy() -> list[dict]:
    sys.path.insert(0, AQUI)
    from sports_api import get_todays_games
    return await get_todays_games()


# ── Programa ─────────────────────────────────────────────

def _marca(v: dict) -> str:
    if v["solo_miembros"]:
        return "DE PAGA"
    if v["en_vivo"]:
        return "EN VIVO"
    if v["proximo"]:
        return "PRÓXIMO"
    return "grabado"


async def principal(args) -> int:
    canales = _CANALES
    if args.canal:
        if args.canal not in _CANALES:
            print(f"Canal desconocido: {args.canal}. Hay: {', '.join(_CANALES)}")
            return 2
        canales = {args.canal: _CANALES[args.canal]}

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
        tareas = {k: leer_canal(cli, cid) for k, (_, cid) in canales.items()}
        resultados = await asyncio.gather(*tareas.values(), return_exceptions=True)

        por_canal: dict[str, list[dict]] = {}
        for clave, res in zip(tareas, resultados):
            nombre = canales[clave][0]
            if isinstance(res, Exception):
                print(f"  {nombre}: ERROR — {res}")
                por_canal[clave] = []
            else:
                por_canal[clave] = res
                print(f"  {nombre}: {len(res)} videos en la pestaña En vivo")

        # Solo interesa lo que está pasando o va a pasar, y que no sea de paga.
        utiles = []
        for clave, vids in por_canal.items():
            for v in vids:
                if v["solo_miembros"]:
                    continue
                if args.todo or v["en_vivo"] or v["proximo"]:
                    utiles.append({**v, "canal": canales[clave][0], "canal_slug": clave})

        if not args.todo:
            juegos = await juegos_de_hoy()
            print(f"\n  Partidos de hoy en la base: {len(juegos)}")
            candidatos = cruzar(utiles, juegos)
        else:
            candidatos = utiles

        # Comprobar incrustación solo de los candidatos, para no abusar de oEmbed.
        for c in candidatos:
            c["embebible"] = await es_embebible(cli, c["id"])

    print("\n" + "=" * 72)
    if not candidatos:
        print("Sin candidatos. Ningún partido de hoy coincide con una transmisión")
        print("gratuita abierta en estos canales. Esto es normal la mayoría de los días.")
    for c in candidatos:
        emb = {True: "incrustable", False: "NO incrustable", None: "sin comprobar"}[c["embebible"]]
        print(f"\n[{_marca(c)}] {c['canal']} — {emb}")
        print(f"  {c['titulo'][:100]}")
        print(f"  https://www.youtube.com/watch?v={c['id']}")
        if c.get("juego"):
            j = c["juego"]
            print(f"  → {j.get('name', '')}  ({j.get('league_slug', '')})  id {j.get('id', '')}")

    listos = [c for c in candidatos if c["embebible"] and c.get("juego")]
    paquete = {
        "_generado": datetime.now().isoformat(timespec="seconds"),
        "_lee_esto": (
            "Candidatos SIN verificar a mano. Antes de pasar uno a "
            "video_embeds.json: ábrelo, confirma que es la transmisión oficial "
            "del partido correcto y que reproduce. Luego pon la fecha de hoy en "
            "'verificado'."
        ),
        "candidatos": [
            {
                "liga": (c["juego"] or {}).get("league_slug", ""),
                "partido_id": str((c["juego"] or {}).get("id", "")),
                "youtube_id": c["id"],
                "titulo": f"{(c['juego'] or {}).get('name', '')} — transmisión oficial de {c['canal']}",
                "fuente": f"https://www.youtube.com/watch?v={c['id']}",
                "verificado": "",
                "_titulo_en_youtube": c["titulo"],
                "_estado": _marca(c),
            }
            for c in listos
        ],
    }
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(paquete, f, ensure_ascii=False, indent=2)
    print("\n" + "=" * 72)
    print(f"{len(listos)} candidato(s) listos en video_embeds_candidatos.json")
    print("Ninguno se publica hasta que lo copies a video_embeds.json.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--canal", help=f"solo un canal: {', '.join(_CANALES)}")
    p.add_argument("--todo", action="store_true",
                   help="listar todo sin cruzar con los partidos del día")
    sys.exit(asyncio.run(principal(p.parse_args())))
