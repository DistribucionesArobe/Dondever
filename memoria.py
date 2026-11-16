"""Instrumentación de memoria. Existe porque diagnosticar a ciegas salió mal.

El 16/11/2026 el servicio llevaba días cayéndose con "Ran out of memory (used
over 2GB)" —cuatro veces en una mañana— y se persiguieron tres causas, las tres
equivocadas:

  1. Las cachés de HTML. Medidas: 13,5 KB por página comprimida, 2.300 entradas
     = 30 MB de 2.048. No eran.
  2. La caché de ESPN infradimensionada. Sin una sola evidencia: los 429 del log
     eran todos de TheSportsDB. Se subió y se revirtió.
  3. Una estampida de descargas en gatotv. El log lo sugería, pero gatotv.py ya
     limita la concurrencia con un Semaphore(5).

El patrón del error es siempre el mismo: una teoría plausible, un cambio, y
ninguna medición que lo confirme o lo tumbe. Y no se puede reproducir en local
porque sin salida a red las rutas que revientan en producción contestan 200.

Así que esto no arregla nada. Su único trabajo es que el PRÓXIMO pico venga con
el nombre de lo que creció.

Dos piezas:

  · Un vigía casi gratis, siempre encendido. Lee VmRSS de /proc/self/status
    (una lectura de un archivo pequeño, sin asignar memoria que valga la pena) y
    solo escribe en el log cuando la memoria marca un máximo nuevo, de 50 MB en
    50 MB. Con cada aviso va la ruta que lo provocó y el tamaño de TODAS las
    cachés conocidas. Si la memoria sube mientras las cachés están quietas, no
    son las cachés — y eso descarta de un vistazo la familia entera de teorías
    que ya falló tres veces.

  · tracemalloc, APAGADO por defecto y encendible con DV_TRACEMALLOC=1. Va
    apagado a propósito: duplica el coste de cada asignación rastreada, y meterle
    más memoria a un proceso que se muere de memoria es la manera de convertir
    un diagnóstico en una caída. Se enciende cuando se quiera cazar de verdad,
    sabiendo lo que cuesta.

Y /admin/memoria para no tener que esperar a un pico: pregunta en cualquier
momento, con el mismo ADMIN_TOKEN que el resto de las rutas de diagnóstico.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("dondever.memoria")

# Cada cuántos MB de máximo nuevo se escribe una línea. 50 es un compromiso:
# bastante grande para no ensuciar el log en una tarde normal, bastante pequeño
# para que entre los 300 MB de arranque y los 2.048 del límite queden ~35 avisos,
# que es una curva con forma suficiente para leerla.
_ESCALON_MB = 50
_maximo_visto = 0.0

_TRACEMALLOC = os.getenv("DV_TRACEMALLOC", "") == "1"
if _TRACEMALLOC:  # pragma: no cover - solo cuando se pide a mano
    import tracemalloc
    tracemalloc.start(10)
    logger.warning("tracemalloc ENCENDIDO (DV_TRACEMALLOC=1): cuesta memoria, no dejarlo puesto")


def rss_mb() -> float:
    """Memoria residente AHORA, en MB. 0.0 si no se puede leer.

    Se usa /proc/self/status y no resource.getrusage porque getrusage devuelve
    ru_maxrss, que es el MÁXIMO histórico del proceso y por tanto nunca baja.
    Para ver si algo se libera hace falta el valor actual.
    """
    try:
        with open("/proc/self/status", "r") as f:
            for linea in f:
                if linea.startswith("VmRSS:"):
                    return float(linea.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _tamanos_de_cache() -> str:
    """Cuántas entradas tiene ahora mismo cada caché conocida.

    Importa el len(), no el peso: si la memoria sube y estos números no, las
    cachés quedan descartadas sin tener que medir cuánto ocupa cada entrada.
    """
    partes = []
    try:
        import sports_api as _s
        for nombre in ("_cache", "_tv_cache", "_tv_fail_cache", "_tv_stale",
                       "_tv_day_cache", "_odds_cache", "_form_cache",
                       "_standings_cache", "_sportsdb_team_cache"):
            c = getattr(_s, nombre, None)
            if c is not None:
                partes.append(f"{nombre}={len(c)}/{c.maxsize}")
    except Exception:
        pass
    try:
        import server as _srv
        for nombre in ("_HTML_CACHE", "_HTML_CACHE_LARGO", "_HOME_CACHE",
                       "_HOME_OTRO_DIA_CACHE"):
            c = getattr(_srv, nombre, None)
            if c is not None:
                partes.append(f"{nombre}={len(c)}/{c.maxsize}")
    except Exception:
        pass
    try:
        import gatotv as _g
        partes.append(f"_grid_cache={len(_g._grid_cache)}/{_g._grid_cache.maxsize}")
    except Exception:
        pass
    return " ".join(partes)


def _top_asignaciones(n: int = 10) -> str:
    if not _TRACEMALLOC:
        return ""
    try:
        import tracemalloc
        snap = tracemalloc.take_snapshot()
        top = snap.statistics("lineno")[:n]
        return " | ".join(
            f"{t.traceback[0].filename.split('/')[-1]}:{t.traceback[0].lineno} "
            f"{t.size / 1048576:.1f}MB x{t.count}"
            for t in top
        )
    except Exception:
        return ""


def revisar(ruta: str = "") -> None:
    """Escribe una línea SOLO si la memoria marcó un máximo nuevo.

    Pensado para llamarse en cada petición: en el caso normal son una lectura de
    archivo y una comparación.
    """
    global _maximo_visto
    ahora = rss_mb()
    if ahora <= 0 or ahora < _maximo_visto + _ESCALON_MB:
        return
    _maximo_visto = ahora
    msg = f"MEMORIA máximo nuevo {ahora:.0f} MB tras {ruta or '?'} — {_tamanos_de_cache()}"
    top = _top_asignaciones()
    if top:
        msg += f" || TOP: {top}"
    logger.warning(msg)


def informe() -> dict:
    """Lo que devuelve /admin/memoria."""
    datos = {
        "rss_mb": round(rss_mb(), 1),
        "maximo_visto_mb": round(_maximo_visto, 1),
        "escalon_mb": _ESCALON_MB,
        "tracemalloc": _TRACEMALLOC,
        "caches": _tamanos_de_cache(),
    }
    top = _top_asignaciones(15)
    if top:
        datos["top_asignaciones"] = top
    return datos
