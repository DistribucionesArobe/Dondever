#!/usr/bin/env python3
"""¿De cuántos partidos sabemos el canal de verdad, y en qué países?

Por qué existe
--------------
Todo el sitio dice "probable" porque el canal sale del reparto de derechos de
la liga y no de la transmisión de ESE partido. Para saber si eso se puede
arreglar hace falta un número que no teníamos: de los partidos de un día, en
cuántos la PARRILLA PUBLICADA (GatoTV / epgshare) encuentra el partido, y en
qué países.

Si México sale al 70%, el trabajo es llevar ese dato a más páginas.
Si sale al 15%, hay que buscar otra fuente antes de construir nada.

Este script no cambia nada. Solo mide y escribe un reporte.

Cómo correrlo
-------------
Tiene que correr en tu máquina o en Render: GatoTV y epgshare bloquean las
salidas de red de los sandboxes.

    cd "ruta/al/proyecto"
    python3 scripts/medir_cobertura.py                 # los partidos de hoy
    python3 scripts/medir_cobertura.py --limite 20     # solo 20, para probar
    python3 scripts/medir_cobertura.py --liga mlb      # una sola liga
    python3 scripts/medir_cobertura.py --json cob.json # además guarda el crudo

La primera corrida es lenta (minutos): las parrillas se piden una por una y la
caché está vacía. La segunda es rápida. Por eso va con un límite de
concurrencia bajo — no vale la pena que nos bloqueen por ir con prisa.

Lo que hay que saber antes de leer el resultado
-----------------------------------------------
Los países que cada fuente puede responder NO son los mismos, y eso explica
casi todo el reporte:

    GatoTV   : VE PA DO CO PE EC        (no tiene México)
    epgshare : CO DO PA PE EC MX        (es la ÚNICA que da México)

Ninguna de las dos cubre España. Un 0% en un país que la fuente no consulta no
es una falla de cobertura, es que no está configurado. El reporte lo marca.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# El script vive en scripts/ pero los módulos están en la raíz del proyecto.
# Sin esto, `python3 scripts/medir_cobertura.py` truena con ModuleNotFoundError
# y hay que acordarse de exportar PYTHONPATH, que nadie se acuerda.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gatotv  # noqa: E402
import epgshare  # noqa: E402
from sports_api import get_todays_games  # noqa: E402

# Lo que cada fuente puede contestar. Si un país no está aquí, su 0% no
# significa nada.
PAISES_GATOTV = list(gatotv.GATOTV_SPORTS_CHANNELS)
PAISES_EPG = list(epgshare.EPG_TAGS)
PAISES = sorted(set(PAISES_GATOTV) | set(PAISES_EPG))

# Las parrillas se piden canal por canal. Con la caché fría, un partido puede
# disparar decenas de peticiones; de a cuatro partidos es suficiente para que
# acabe en minutos sin parecer un ataque.
CONCURRENCIA = 4


async def _mirar_un_partido(g: dict, sem: asyncio.Semaphore) -> dict:
    """Busca UN partido en las parrillas de todos los países. No lanza."""
    home = (g.get("home") or {}).get("name", "")
    away = (g.get("away") or {}).get("name", "")
    fila = {
        "liga": g.get("league_name") or g.get("league_slug") or "?",
        "partido": f"{home} vs {away}",
        "hora_utc": g.get("date", ""),
        # Lo que el sitio dice HOY, para poder comparar el antes y el después.
        "confirmado_hoy": bool(g.get("channels_confirmed", True)),
        "canales_hoy": [b.get("channel") for b in (g.get("broadcasts") or [])][:4],
        "gatotv": {},
        "epgshare": {},
        "error": "",
    }
    if not home or not away or "TBD" in (home, away):
        fila["error"] = "sin equipos"
        return fila

    try:
        inicio = datetime.fromisoformat(str(g.get("date", "")).replace("Z", "+00:00"))
    except Exception:
        inicio = datetime.now(timezone.utc)
        fila["error"] = "fecha ilegible, usé ahora"

    async with sem:
        dia = gatotv.grid_date_for(inicio)
        tareas = [
            gatotv.channels_by_country_for_game(
                dia, home, away, inicio, sport=g.get("sport")
            ),
            epgshare.channels_by_country_for_game(home, away, inicio),
        ]
        res = await asyncio.gather(*tareas, return_exceptions=True)

    for nombre, r in zip(("gatotv", "epgshare"), res):
        if isinstance(r, Exception):
            # Una fuente caída no debe tumbar la medición de la otra, pero sí
            # tiene que quedar escrito: un 0% por excepción y un 0% por "no
            # está en la parrilla" son dos cosas muy distintas.
            fila["error"] = (fila["error"] + f" | {nombre}: {type(r).__name__}").strip(" |")
        else:
            fila[nombre] = {cc: chs for cc, chs in (r or {}).items() if chs}

    return fila


def _pct(parte: int, total: int) -> str:
    return "—" if not total else f"{100 * parte / total:4.0f}%"


def _reportar(filas: list[dict]) -> None:
    total = len(filas)
    utiles = [f for f in filas if not f["error"].startswith("sin equipos")]
    n = len(utiles)

    print()
    print("=" * 74)
    print(f"  COBERTURA DE PARRILLA — {total} partidos del día ({n} con equipos)")
    print("=" * 74)

    if not n:
        print("\n  No hubo partidos que medir.\n")
        return

    # ── 1. El número que importa: ¿sabemos el canal, por país? ──────────
    print("\n1) ¿La parrilla encontró el partido? (por país)\n")
    print(f"   {'País':<6} {'GatoTV':>8} {'epgshare':>10} {'alguna':>8}   consultado por")
    print(f"   {'-'*6} {'-'*8} {'-'*10} {'-'*8}   {'-'*28}")
    por_pais = {}
    for cc in PAISES:
        g_ok = sum(1 for f in utiles if f["gatotv"].get(cc))
        e_ok = sum(1 for f in utiles if f["epgshare"].get(cc))
        any_ok = sum(1 for f in utiles if f["gatotv"].get(cc) or f["epgshare"].get(cc))
        por_pais[cc] = {"gatotv": g_ok, "epgshare": e_ok, "alguna": any_ok, "de": n}
        quien = []
        if cc in PAISES_GATOTV:
            quien.append("GatoTV")
        if cc in PAISES_EPG:
            quien.append("epgshare")
        print(f"   {cc:<6} {_pct(g_ok, n):>8} {_pct(e_ok, n):>10} {_pct(any_ok, n):>8}   {' + '.join(quien)}")

    print()
    print("   Recordatorio: GatoTV NO consulta México. epgshare es la única que")
    print("   lo hace. Ninguna de las dos cubre España.")

    # ── 2. Por liga, solo México: es el país que paga las cuentas ───────
    print("\n2) México, liga por liga (es de donde viene el tráfico)\n")
    print(f"   {'Liga':<30} {'partidos':>9} {'con canal MX':>13}")
    print(f"   {'-'*30} {'-'*9} {'-'*13}")
    por_liga = defaultdict(lambda: {"total": 0, "mx": 0})
    for f in utiles:
        d = por_liga[f["liga"]]
        d["total"] += 1
        if f["gatotv"].get("MX") or f["epgshare"].get("MX"):
            d["mx"] += 1
    for liga, d in sorted(por_liga.items(), key=lambda kv: -kv[1]["total"]):
        print(f"   {liga[:30]:<30} {d['total']:>9} {_pct(d['mx'], d['total']):>13}")

    # ── 3. El antes y el después ────────────────────────────────────────
    #
    # Esto es la razón de ser del script. "ganados" son los partidos que hoy
    # salen como "probable" y que la parrilla SÍ tiene: cada uno es un
    # "probable" que se puede volver un hecho sin inventar nada.
    conf_hoy = sum(1 for f in utiles if f["confirmado_hoy"])
    ganados = [f for f in utiles
               if not f["confirmado_hoy"] and (f["gatotv"].get("MX") or f["epgshare"].get("MX"))]
    print("\n3) Qué cambiaría\n")
    print(f"   Confirmados hoy (vía ESPN)        {conf_hoy:>4} de {n}   {_pct(conf_hoy, n)}")
    print(f"   Que la parrilla puede confirmar   {len(ganados):>4} de {n}   {_pct(len(ganados), n)}")
    print(f"   Total si usamos la parrilla       {conf_hoy + len(ganados):>4} de {n}   {_pct(conf_hoy + len(ganados), n)}")

    if ganados:
        print("\n   Ejemplos de los que se ganarían (hoy dicen 'probable'):")
        for f in ganados[:8]:
            mx = (f["gatotv"].get("MX") or []) + (f["epgshare"].get("MX") or [])
            print(f"     · {f['partido'][:44]:<44} → {', '.join(mx[:3])}")

    # ── 4. Fallas, para no confundirlas con falta de cobertura ──────────
    errores = [f for f in utiles if f["error"]]
    if errores:
        print(f"\n4) {len(errores)} partidos con error de fuente (no es falta de cobertura)\n")
        tipos = defaultdict(int)
        for f in errores:
            tipos[f["error"]] += 1
        for t, c in sorted(tipos.items(), key=lambda kv: -kv[1]):
            print(f"   {c:>4}x  {t}")

    # ── 5. Qué hacer con esto ───────────────────────────────────────────
    mx = por_pais.get("MX", {})
    mx_pct = 100 * mx.get("alguna", 0) / n if n else 0
    print("\n" + "=" * 74)
    print("  LECTURA")
    print("=" * 74)
    if mx_pct >= 60:
        print(f"\n  México al {mx_pct:.0f}%. La fuente ya sirve. El trabajo es llevar")
        print("  ese dato a /equipo/, /liga/ y al widget, no buscar otra fuente.")
    elif mx_pct >= 25:
        print(f"\n  México al {mx_pct:.0f}%. Sirve a medias: vale la pena usarlo donde")
        print("  acierta, pero no alcanza para dejar de decir 'probable' en general.")
    else:
        print(f"\n  México al {mx_pct:.0f}%. La fuente no alcanza. Antes de construir")
        print("  nada encima hay que conseguir otra parrilla mexicana.")
    print("\n  Ojo con el día: una muestra de un martes de octubre está dominada por")
    print("  playoffs de MLB. Córrelo también un sábado de Liga MX antes de decidir.\n")


async def _principal(args) -> int:
    print(f"Pidiendo los partidos de hoy{' de ' + args.liga if args.liga else ''}…",
          file=sys.stderr)
    juegos = await get_todays_games(league_filter=args.liga or None)
    juegos = [g for g in juegos if (g.get("home") or {}).get("name") != "TBD"]
    if args.limite:
        juegos = juegos[: args.limite]
    if not juegos:
        print("No hay partidos hoy con esos filtros.", file=sys.stderr)
        return 1

    print(f"{len(juegos)} partidos. Consultando parrillas de {len(PAISES)} países "
          f"(de a {CONCURRENCIA}; la primera vez es lenta)…", file=sys.stderr)

    sem = asyncio.Semaphore(CONCURRENCIA)
    filas = await asyncio.gather(*[_mirar_un_partido(g, sem) for g in juegos])

    _reportar(list(filas))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"medido": datetime.now(timezone.utc).isoformat(),
                       "paises_gatotv": PAISES_GATOTV,
                       "paises_epgshare": PAISES_EPG,
                       "partidos": list(filas)}, fh, ensure_ascii=False, indent=2)
        print(f"Crudo guardado en {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Mide la cobertura real de las parrillas de TV.")
    p.add_argument("--liga", default="", help="slug de una liga (mlb, liga-mx, nfl…)")
    p.add_argument("--limite", type=int, default=0, help="medir solo N partidos")
    p.add_argument("--json", default="", help="guardar el resultado crudo en este archivo")
    sys.exit(asyncio.run(_principal(p.parse_args())))
