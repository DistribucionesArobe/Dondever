"""Vigilancia de memoria del proceso web.

Por qué existe
──────────────
El 24/09/2026 Render mató la instancia cuatro veces en un día (2:48, 7:03,
7:30 y 10:53 AM) por pasarse del límite de 512 MB. La gráfica no muestra un
pico de tráfico: muestra una sierra. Después de cada reinicio arranca en ~35%
y sube sostenidamente hasta ~90% en tres o cuatro horas. Eso es crecimiento,
no carga.

Leyendo el código no se puede decir cuál de los veintitantos cachés es el que
crece: todos son TTLCache con maxsize, así que todos *deberían* estar acotados.
Alguno no lo está, o alguno guarda objetos mucho más grandes de lo que su
comentario dice. Este módulo hace dos cosas, y son distintas a propósito:

  1. MEDIR   — `inventario()` recorre cada caché y devuelve bytes reales, no
               número de entradas. Sin esto seguiríamos adivinando.
  2. AGUANTAR — `vigilar()` corre cada minuto. Si la memoria pasa del umbral,
               vacía los cachés más pesados y llama al recolector. Los cachés
               se vuelven a llenar solos desde las APIs; lo único que se pierde
               es velocidad durante unos minutos.

El punto 2 no arregla nada. Convierte una caída (503 para todo el mundo,
arranque en frío de treinta segundos) en una ralentización que nadie nota.
Es un parche, y hay que tratarlo como parche: el arreglo de verdad sale del
punto 1, cuando sepamos qué crece.
"""

from __future__ import annotations

import gc
import logging
import sys
import time as _time
from typing import Any

logger = logging.getLogger("dondever")

# Render mata el contenedor en 512 MB. A 400 MB todavía hay margen de sobra
# para vaciar con calma; a 470 ya estaríamos jugando contra el reloj.
UMBRAL_MB = 400.0

# Cuánto hay que bajar para considerar que la purga sirvió. Si liberamos menos
# que esto, el problema no está en los cachés y conviene decirlo en el log en
# vez de purgar en vano cada minuto.
MINIMO_LIBERADO_MB = 20.0

# Cuánto esperar antes de volver a vaciar cachés si vaciarlos no sirvió.
# Media hora es a propósito: reconstruir los cachés cuesta decenas de
# peticiones a ESPN y GatoTV y deja páginas de tres segundos. Hacerlo cada
# minuto sin ganar memoria —que es lo que pasó el 24/09— es puro daño.
DESCANSO_TRAS_FRACASO_S = 1800

# Hasta cuándo no volver a intentar el paso caro.
_no_purgar_hasta = 0.0


def rss_mb() -> float:
    """Memoria residente *actual* del proceso, en MB.

    `/health` usaba `resource.getrusage().ru_maxrss`, que es la marca más alta
    desde que arrancó el proceso y nunca baja. Sirve para saber si alguna vez
    nos acercamos al límite, pero no para decidir si hay que purgar ahora.
    `/proc/self/statm` sí da el valor del momento.
    """
    try:
        with open("/proc/self/statm", "r") as fh:
            paginas_residentes = int(fh.read().split()[1])
        import os
        return paginas_residentes * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        # macOS y cualquier sistema sin /proc: caemos a la marca alta, que al
        # menos no miente hacia abajo.
        try:
            import resource
            bruto = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return bruto / (1024 * 1024) if sys.platform == "darwin" else bruto / 1024
        except Exception:
            return 0.0


def _pesar(obj: Any, vistos: set | None = None, profundidad: int = 0) -> int:
    """Bytes que ocupa un objeto contando lo que cuelga de él.

    `sys.getsizeof` de un dict devuelve el tamaño de la tabla, no el de las
    cosas guardadas dentro: un dict con 400 páginas HTML de 15 KB reporta unos
    pocos KB. Por eso hay que bajar a mano.

    El `vistos` evita contar dos veces un objeto compartido y evita ciclos
    infinitos. El tope de profundidad es por seguridad: esto corre dentro de
    un endpoint y no puede colgarse.
    """
    if profundidad > 8:
        return 0
    if vistos is None:
        vistos = set()
    ident = id(obj)
    if ident in vistos:
        return 0
    vistos.add(ident)

    try:
        total = sys.getsizeof(obj)
    except Exception:
        return 0

    try:
        if isinstance(obj, dict):
            for clave, valor in list(obj.items()):
                total += _pesar(clave, vistos, profundidad + 1)
                total += _pesar(valor, vistos, profundidad + 1)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            for item in list(obj):
                total += _pesar(item, vistos, profundidad + 1)
        elif hasattr(obj, "__dict__"):
            total += _pesar(vars(obj), vistos, profundidad + 1)
    except Exception:
        # Un caché que alguien está modificando mientras lo recorremos. No es
        # motivo para tumbar el endpoint de salud.
        pass
    return total


def _cachés() -> list[tuple[str, Any]]:
    """Todos los cachés del proceso, con su nombre para el log.

    Se importa aquí dentro y no arriba porque este módulo lo carga `server.py`
    durante su propio arranque: importar `server` desde aquí sería circular.
    Cada import va en su propio try porque un módulo que falle no debe dejarnos
    sin la medición de los demás.
    """
    salida: list[tuple[str, Any]] = []

    def añadir(modulo: str, *nombres: str) -> None:
        try:
            mod = __import__(modulo)
        except Exception:
            return
        for nombre in nombres:
            objeto = getattr(mod, nombre, None)
            if objeto is not None:
                salida.append((f"{modulo}.{nombre}", objeto))

    añadir("server", "_HTML_CACHE", "_HOME_CACHE", "_seen_wamids")
    añadir("sports_api", "_cache", "_tv_cache", "_odds_cache", "_odds_fail_cache",
           "_summary_cache", "_tv_day_cache", "_sportsdb_events_cache",
           "_sportsdb_standings_cache", "_sportsdb_team_cache", "_standings_cache",
           "_form_cache", "_leaders_cache", "_meli_cache", "_cache_ts")
    añadir("events_api", "_events_cache", "_season_cache", "_season_stale")
    añadir("gatotv", "_grid_cache")
    añadir("epgshare", "_cache")
    añadir("og_image", "_og_cache")
    return salida


def inventario() -> dict:
    """Qué hay en memoria, en bytes reales y ordenado de mayor a menor.

    Esto es lo que contesta la pregunta que el código no contesta: cuál de los
    cachés se está comiendo el contenedor.
    """
    filas = []
    for nombre, caché in _cachés():
        try:
            entradas = len(caché)
        except Exception:
            entradas = -1
        filas.append({
            "cache": nombre,
            "entradas": entradas,
            "mb": round(_pesar(caché) / (1024 * 1024), 2),
        })
    filas.sort(key=lambda f: f["mb"], reverse=True)
    total_cachés = round(sum(f["mb"] for f in filas), 1)
    residente = round(rss_mb(), 1)
    return {
        "rss_mb": residente,
        "cachés_mb": total_cachés,
        # Lo que el proceso ocupa y NO son cachés: intérprete, plantillas Jinja
        # compiladas, conexiones, y —si existe— la fuga que buscamos. Si este
        # número es el que crece, los cachés son inocentes.
        "fuera_de_cachés_mb": round(residente - total_cachés, 1),
        "umbral_mb": UMBRAL_MB,
        "detalle": filas,
    }


def devolver_al_sistema() -> bool:
    """Pide a glibc que devuelva al sistema la memoria que Python ya liberó.

    Esto es lo que faltaba, y explica por qué la primera versión de este
    módulo no servía de nada. En producción se vieron 65 purgas seguidas:
    vaciaba los catorce cachés y la memoria residente no se movía ni un MB
    (443.4 → 443.4). La conclusión fácil era "hay una fuga en otro lado".
    La conclusión correcta es otra.

    `gc.collect()` libera los objetos de Python, pero el que le pidió la
    memoria al sistema operativo no es Python: es malloc. Y glibc se queda
    con los bloques libres en sus arenas para reutilizarlos, sin devolverlos.
    Desde fuera —que es donde mira Render— el proceso sigue ocupando lo
    mismo aunque por dentro esté medio vacío. Por eso te matan el contenedor
    con memoria que ya no usas.

    `malloc_trim(0)` es la forma de pedirle a glibc que suelte esas arenas.
    Devuelve True si liberó algo.

    Si no hay glibc (macOS, Alpine) simplemente no hace nada y devuelve
    False; el resto del módulo sigue funcionando igual.
    """
    try:
        import ctypes
        import ctypes.util
        nombre = ctypes.util.find_library("c")
        if not nombre:
            return False
        libc = ctypes.CDLL(nombre)
        if not hasattr(libc, "malloc_trim"):
            return False
        return bool(libc.malloc_trim(0))
    except Exception:
        return False


def purgar(limite_mb: float = UMBRAL_MB) -> dict | None:
    """Baja la memoria por debajo del umbral. None si no hizo falta.

    Dos pasos, en este orden y a propósito:

      1. Recolectar y pedirle a glibc que devuelva lo que ya está libre.
         Es gratis y no le quita nada a nadie.
      2. Solo si con eso no alcanzó, vaciar cachés — que sí cuesta: cada
         caché vaciado son decenas de peticiones a ESPN y GatoTV para
         reconstruirlo, y páginas de tres segundos mientras tanto.

    La primera versión hacía el paso 2 sin el paso 1, y como el paso 2 solo
    no baja la memoria residente, vaciaba todo cada minuto para nada. El
    sitio quedó más lento sin ganar un MB.
    """
    global _no_purgar_hasta

    antes = rss_mb()
    if antes < limite_mb:
        return None

    # Paso 1: lo barato.
    gc.collect()
    devolvio = devolver_al_sistema()
    tras_trim = rss_mb()
    if tras_trim < limite_mb:
        logger.info("memoria: %.1f MB → %.1f MB solo con recolectar y devolver "
                    "al sistema. No hizo falta tocar los cachés.", antes, tras_trim)
        return {
            "antes_mb": round(antes, 1),
            "después_mb": round(tras_trim, 1),
            "liberado_mb": round(antes - tras_trim, 1),
            "vaciados": [],
            "malloc_trim": devolvio,
        }

    # Paso 2: lo caro, y solo si no está en penitencia por no haber servido.
    if _time.time() < _no_purgar_hasta:
        return {
            "antes_mb": round(antes, 1),
            "después_mb": round(tras_trim, 1),
            "liberado_mb": round(antes - tras_trim, 1),
            "vaciados": [],
            "malloc_trim": devolvio,
            "nota": "sobre el umbral, pero vaciar cachés ya se probó y no sirvió",
        }

    vaciados: list[str] = []
    for nombre, caché in sorted(_cachés(), key=lambda par: _pesar(par[1]), reverse=True):
        if not hasattr(caché, "clear"):
            continue
        try:
            if len(caché) == 0:
                continue
            caché.clear()
            vaciados.append(nombre)
        except Exception:
            continue
        gc.collect()
        if rss_mb() < limite_mb * 0.85:
            break

    gc.collect()
    devolver_al_sistema()
    después = rss_mb()
    liberado = round(antes - después, 1)

    informe = {
        "antes_mb": round(antes, 1),
        "después_mb": round(después, 1),
        "liberado_mb": liberado,
        "vaciados": vaciados,
        "malloc_trim": devolvio,
    }

    if liberado < MINIMO_LIBERADO_MB:
        # Vaciar cachés no sirvió. Repetirlo cada minuto es lo peor de los dos
        # mundos: el sitio va lento reconstruyéndolos y la memoria sigue igual.
        # Se deja de intentar un buen rato; el paso barato (recolectar y
        # devolver al sistema) se sigue haciendo en cada vuelta.

        _no_purgar_hasta = _time.time() + DESCANSO_TRAS_FRACASO_S
        logger.error(
            "memoria: vacié %d cachés y solo bajé %.1f MB (%.1f → %.1f). "
            "Los cachés NO son el problema — no vuelvo a vaciarlos en %d min.",
            len(vaciados), liberado, antes, después,
            DESCANSO_TRAS_FRACASO_S // 60)
    else:
        logger.warning(
            "memoria: %.1f MB era demasiado, vacié %s y bajé a %.1f MB",
            antes, ", ".join(vaciados) or "nada", después)
    return informe


# Último informe, para poder verlo en /health sin tener que cazar el log.
ultima_purga: dict | None = None
purgas_totales = 0


async def vigilar() -> None:
    """Un vistazo. Pensado para correr cada minuto desde el scheduler."""
    global ultima_purga, purgas_totales
    try:
        informe = purgar()
    except Exception as e:
        # Nunca dejar que el vigilante sea el que tire el servidor.
        logger.warning("memoria: el vigilante falló: %s", e)
        return
    if informe is not None:
        ultima_purga = informe
        purgas_totales += 1
