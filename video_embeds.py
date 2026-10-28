"""Video incrustado de transmisiones gratuitas y oficiales.

Qué es y qué no es:

  - SÍ: incrustar un video que el titular de los derechos subió a su propio
    canal y dejó incrustable. Eso no requiere licencia; el permiso lo da el
    que sube el video al habilitar la incrustación.
  - NO: retransmitir una señal. Jamás. Un reclamo de derechos tumba AdSense,
    que hoy es el único ingreso real del sitio.

La lista es curada a mano en video_embeds.json, igual que boxing_events.json,
porque no hay forma automática de saber si un video es oficial. Adivinar aquí
es exactamente el tipo de dato sin verificar que no debe salir en el sitio.

Apagado por defecto: hace falta VIDEO_EMBEDS=1 en el entorno. Así el código
puede estar desplegado antes de que exista el primer video que incrustar.
"""

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from cachetools import TTLCache

logger = logging.getLogger("dondever.video")

_BASE = os.path.dirname(os.path.abspath(__file__))
_ARCHIVO = os.path.join(_BASE, "video_embeds.json")
_ARCHIVO_CANALES = os.path.join(_BASE, "video_canales.json")

# La fecha de un partido se guarda en UTC. Para casar con la fecha curada hay
# que pasarla a la zona de la liga: un juego de la LVBP a las 00:30 UTC del
# martes es el lunes por la noche en Caracas, que es como lo anuncia la liga.
_TZ_LIGA = {
    "lvbp": "America/Caracas",
    "lidom": "America/Santo_Domingo",
    "lmp": "America/Mazatlan",
    "lmb": "America/Mexico_City",
    "lnbp": "America/Mexico_City",
}
_TZ_POR_DEFECTO = "America/Mexico_City"

_cache = {"mtime": None, "videos": []}


def habilitado() -> bool:
    return os.getenv("VIDEO_EMBEDS", "").strip() in ("1", "true", "yes", "si", "sí")


def _cargar() -> list[dict]:
    """Relee el JSON solo si cambió en disco."""
    try:
        mtime = os.path.getmtime(_ARCHIVO)
    except OSError:
        return []
    if _cache["mtime"] == mtime:
        return _cache["videos"]
    try:
        with open(_ARCHIVO, "r", encoding="utf-8") as f:
            crudo = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("video_embeds.json no se pudo leer: %s", e)
        return []

    videos = []
    for v in crudo.get("videos", []):
        yid = (v.get("youtube_id") or "").strip()
        # El id de ejemplo del archivo nunca debe llegar a producción.
        if not yid or yid == "PEGA_AQUI_EL_ID":
            continue
        if not v.get("verificado"):
            logger.warning("Video %s sin fecha de verificación: se ignora.", yid)
            continue
        videos.append({
            "partido_id": str(v.get("partido_id") or ""),
            "liga": (v.get("liga") or "").lower(),
            "fecha": v.get("fecha") or "",
            "youtube_id": yid,
            "titulo": v.get("titulo") or "Transmisión oficial",
            "fuente": v.get("fuente") or "",
            "verificado": v.get("verificado"),
        })
    _cache["mtime"] = mtime
    _cache["videos"] = videos
    return videos


def _fecha_local(iso_utc: str, liga: str) -> str:
    """ISO en UTC → 'YYYY-MM-DD' en la zona de la liga."""
    if not iso_utc:
        return ""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return (iso_utc or "")[:10]
    if dt.tzinfo is None:
        return iso_utc[:10]
    tz = ZoneInfo(_TZ_LIGA.get(liga, _TZ_POR_DEFECTO))
    return dt.astimezone(tz).strftime("%Y-%m-%d")


# ── Emparejado automático desde el feed del canal oficial ───────────────────
#
# La lista a mano no escala: son los partidos de cada jornada, de cada liga,
# todos los días, para siempre. En cuanto se queda vieja es peor que no tener
# nada, porque el sitio ofrece "ver aquí" y no hay nada que ver.
#
# POR QUÉ RSS Y NO LA API DE YOUTUBE
# Cada canal publica https://www.youtube.com/feeds/videos.xml?channel_id=UC...
# con sus últimos 15 videos. Es gratis, no pide API key, no gasta cuota y no
# mete otro secreto que rotar. Comprobado el 24/10/2026 sobre cuatro canales:
# LNBP, Futsal RFEF, FIP y IVC devolvieron HTTP 200 con 15 entradas.
#
# QUÉ SE PUEDE EMPAREJAR Y QUÉ NO
# El emparejado necesita que el título nombre a los dos equipos. Medido el
# mismo día:
#
#   LNBP         "DORADOS DE CHIHUAHUA VS FUERZA REGIA"        → casa
#   Futsal RFEF  "Resumen #LigaPrimeFutsal Catgas 3-4 ElPozo"  → es un resumen
#   FIP pádel    "FIP BRONZE VIÑA DEL MAR - Finals"            → sin equipos
#
# Por eso esto NO enciende una liga por el hecho de agregar su canal: enciende
# los partidos donde el patrón existe de verdad. Si un canal sube resúmenes,
# simplemente no casa nada y la página se queda como está.
#
# LA REPETICIÓN
# La LNBP subió "DORADOS DE CHIHUAHUA VS FUERZA REGIA" el 23 y otra vez el 24.
# Emparejar sólo por equipos pondría la resubida de ayer en el partido de hoy.
# Por eso se exige que la fecha de publicación caiga en la ventana del partido
# y, si hay varias, gana la más antigua: la original, no la resubida.
#
# Y NO SE DICE "EN VIVO". El feed dice que existe un video, no que sea un
# directo. Se muestra la fecha de publicación y se deja que el usuario vea qué
# es. Afirmar "en vivo" sin saberlo es exactamente el dato sin verificar que
# no debe salir en el sitio.

_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_feed_cache = TTLCache(maxsize=32, ttl=600)   # 10 min
_canales_cache = {"mtime": None, "canales": []}

# Palabras que no distinguen a un equipo. "Dorados de Chihuahua" tiene que
# casar por "dorados" y "chihuahua", no por "de".
_VACIAS = {"de", "del", "la", "las", "los", "el", "y", "fc", "cf", "club",
           "cd", "ac", "sc", "deportivo", "sd", "ud"}


def _sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _sin_acentos(s or "").lower()).strip()


def _nombre_equipo(x) -> str:
    """home/away llega como dict (ESPN) o como texto (TheSportsDB)."""
    if isinstance(x, dict):
        return x.get("name") or x.get("displayName") or ""
    return str(x or "")


def _tokens(nombre: str) -> list[str]:
    return [t for t in _norm(nombre).split() if t not in _VACIAS and len(t) > 2]


def _equipo_en_titulo(nombre: str, titulo_norm: str) -> bool:
    toks = _tokens(nombre)
    if not toks:
        return False
    # Todos los tokens significativos, para no casar "Lobos Puebla" con
    # "Lobos BUAP" ni "Tigres UANL" con "Tigres de Quintana Roo".
    return all(re.search(r"\b" + re.escape(t) + r"\b", titulo_norm) for t in toks)


def _fecha_en_titulo(titulo: str, anio_ref: int) -> str:
    """'... | 24/10/2026 ⚽' → '2026-10-24'. Cadena vacía si no hay fecha.

    IVC y varios canales ponen la fecha del partido en el título. Cuando está,
    es mejor dato que `published`, que en un directo programado es la fecha en
    que se creó la transmisión.
    """
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b", titulo or "")
    if not m:
        return ""
    d, mes, a = m.group(1), m.group(2), m.group(3)
    try:
        dia, mes_i = int(d), int(mes)
        if not (1 <= dia <= 31 and 1 <= mes_i <= 12):
            return ""
        if a is None:
            anio = anio_ref
        else:
            anio = int(a)
            if anio < 100:
                anio += 2000
        return f"{anio:04d}-{mes_i:02d}-{dia:02d}"
    except ValueError:
        return ""


def _cargar_canales() -> list[dict]:
    """Canales oficiales verificados a mano. Relee sólo si cambió el archivo."""
    try:
        mtime = os.path.getmtime(_ARCHIVO_CANALES)
    except OSError:
        return []
    if _canales_cache["mtime"] == mtime:
        return _canales_cache["canales"]
    try:
        with open(_ARCHIVO_CANALES, "r", encoding="utf-8") as f:
            crudo = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("video_canales.json no se pudo leer: %s", e)
        return []
    canales = []
    for c in crudo.get("canales", []):
        cid = (c.get("channel_id") or "").strip()
        if not cid.startswith("UC") or not c.get("verificado"):
            continue
        canales.append({
            "liga": (c.get("liga") or "").lower(),
            "channel_id": cid,
            "nombre": c.get("nombre") or "canal oficial",
            "url": c.get("url") or "",
        })
    _canales_cache["mtime"] = mtime
    _canales_cache["canales"] = canales
    return canales


async def _entradas_feed(channel_id: str) -> list[dict]:
    """Últimos videos del canal. Lista vacía si el feed falla: nunca revienta.

    ASÍNCRONA A PROPÓSITO. La primera versión usaba httpx.get() síncrono, y
    las dos llamadas (ficha de partido y página de liga) viven dentro de rutas
    `async def`. Un get bloqueante ahí no frena sólo a esa petición: frena el
    bucle de eventos entero, o sea a todos los visitantes a la vez. Con el
    timeout de 10 s que tenía, un YouTube lento habría dejado el sitio clavado
    diez segundos por cada fallo de caché.

    El timeout es corto (4 s) por lo mismo: el video es un extra, y ninguna
    página debe esperar por él más de lo que tarda en pintarse.
    """
    if channel_id in _feed_cache:
        return _feed_cache[channel_id]
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(_FEED.format(cid=channel_id))
            r.raise_for_status()
            xml = r.text
    except Exception as e:
        logger.warning("feed de %s falló: %s", channel_id, e)
        _feed_cache[channel_id] = []
        return []
    entradas = []
    # Regex y no un parser XML: el feed es plano y estable, y así no se añade
    # una dependencia por 15 entradas.
    for bloque in xml.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", bloque)
        tit = re.search(r"<title>([^<]*)</title>", bloque)
        pub = re.search(r"<published>([^<]+)</published>", bloque)
        if not (vid and tit and pub):
            continue
        entradas.append({
            "youtube_id": vid.group(1),
            "titulo": tit.group(1),
            "publicado": pub.group(1),
        })
    _feed_cache[channel_id] = entradas
    return entradas


async def _video_auto(game: dict, liga: str) -> dict | None:
    """Busca en los canales oficiales de esa liga un video de ESTE partido."""
    canales = [c for c in _cargar_canales() if c["liga"] == liga]
    if not canales:
        return None
    casa = _nombre_equipo(game.get("home"))
    fuera = _nombre_equipo(game.get("away"))
    if not casa or not fuera:
        return None

    try:
        dt = datetime.fromisoformat((game.get("date") or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    fecha_partido = _fecha_local(game.get("date", ""), liga)

    # OJO CON `published`. Para un directo PROGRAMADO, YouTube pone la fecha en
    # que se creó la transmisión, no la del partido. Comprobado el 24/10/2026
    # en el canal de IVC:
    #
    #   published 2026-10-22
    #   título    "EN VIVO: Caracas FC vs. Deportivo Táchira | Liga FUTVE | 24/10/2026"
    #
    # Una ventana que arrancara en la hora del partido habría descartado justo
    # el video bueno. Por eso, cuando el título trae fecha, manda el título; y
    # sólo si no la trae se cae a `published`.
    # LA VENTANA ARRANCABA 6 HORAS ANTES, Y ESO SOLO VALE PARA IVC.
    #
    # Con un titulo fechado da igual: manda el titulo. Pero hay canales cuyos
    # titulos NO llevan fecha, y ahi esta ventana es lo unico que decide.
    # Comprobado el 28/10/2026 en TVMAX Panamá:
    #
    #   titulo  "TAURO FC VS PLAZA AMADOR | J-13 | APERTURA 2026 LPF | #ENVIVO"
    #   fecha   no la trae
    #
    # Y sabemos, del propio IVC, que un directo programado se crea DIAS antes
    # del partido. Con 6 horas, el video bueno quedaba fuera por el lado de
    # atras y el canal no habria servido para nada — sin error, sin aviso, solo
    # un partido sin video. Cuatro dias cubre la antelacion habitual.
    #
    # El riesgo de abrirla es casar el partido equivocado si los dos mismos
    # equipos juegan dos veces en esa ventana. Pasa poco, y para eso esta el
    # desempate de abajo: sin fecha en el titulo, gana el directo creado MAS
    # CERCA de la hora del partido, no el mas antiguo.
    desde = dt - timedelta(days=4)
    hasta = dt + timedelta(days=2)

    candidatos = []
    for c in canales:
        for e in await _entradas_feed(c["channel_id"]):
            t = _norm(e["titulo"])
            if not (_equipo_en_titulo(casa, t) and _equipo_en_titulo(fuera, t)):
                continue
            try:
                pub = datetime.fromisoformat(e["publicado"].replace("Z", "+00:00"))
            except ValueError:
                continue
            f_tit = _fecha_en_titulo(e["titulo"], dt.year)
            if f_tit:
                if f_tit != fecha_partido:
                    continue
                # Prioridad 0: el propio título dice que es este partido.
                candidatos.append((0, pub, c, e))
            else:
                if not (desde <= pub <= hasta):
                    continue
                candidatos.append((1, pub, c, e))

    if not candidatos:
        return None
    # Primero los que el título confirma. Dentro de cada grupo, el desempate
    # depende de POR QUÉ entró el candidato:
    #
    #   prioridad 0 (el título trae la fecha del partido): gana el más
    #       ANTIGUO. La fecha ya garantiza que es este partido, así que lo
    #       único que queda por evitar es quedarse con la resubida del día
    #       siguiente en vez de la transmisión original. Es el caso de IVC.
    #
    #   prioridad 1 (sin fecha, entró por la ventana): gana el creado MÁS
    #       CERCA de la hora del partido. Aquí "el más antiguo" sería un mal
    #       criterio: con la ventana de cuatro días, el más antiguo es
    #       justamente el candidato con más probabilidad de ser otro partido
    #       entre los mismos dos equipos. Es el caso de TVMAX y de la LNBP.
    candidatos.sort(key=lambda x: (x[0], x[1] if x[0] == 0 else abs(x[1] - dt)))
    _, pub, c, e = candidatos[0]
    return {
        "partido_id": str(game.get("id") or ""),
        "liga": liga,
        "fecha": _fecha_local(game.get("date", ""), liga),
        "youtube_id": e["youtube_id"],
        "titulo": e["titulo"],
        "fuente": c["url"],
        # Para que la plantilla pueda decir la verdad: esto salió del feed del
        # canal, no lo revisó una persona.
        "verificado": None,
        "automatico": True,
        "canal": c["nombre"],
        "publicado": pub.strftime("%Y-%m-%d"),
    }


async def video_de_partido(game: dict) -> dict | None:
    """Video incrustable para este partido, o None.

    Orden: lo curado a mano gana siempre sobre lo automático.
    """
    if not habilitado() or not game:
        return None
    pid = str(game.get("id") or "")
    liga = (game.get("league_slug") or "").lower()
    videos = _cargar()

    for v in videos:
        if v["partido_id"] and v["partido_id"] == pid:
            return v

    fecha = _fecha_local(game.get("date", ""), liga)
    for v in videos:
        if not v["partido_id"] and v["liga"] == liga and v["fecha"] == fecha:
            return v

    try:
        return await _video_auto(game, liga)
    except Exception as e:   # el video es un extra: nunca debe tumbar la ficha
        logger.warning("emparejado automático falló (%s): %s", pid, e)
        return None


def liga_con_transmision(liga: str) -> bool:
    """¿Esta liga tiene alguna fuente de video configurada?

    Filtro barato para los listados. Sin él, marcar los partidos de la portada
    significaría llamar a video_de_partido() cincuenta veces; con él, las ligas
    sin canal (hoy, 55 de 57) se descartan con una vuelta sobre una lista que
    ya está en memoria. No toca la red ni el disco: _cargar_canales() y
    _cargar() releen sólo si cambió el mtime del archivo.
    """
    if not habilitado() or not liga:
        return False
    liga = liga.lower()
    if any(c["liga"] == liga for c in _cargar_canales()):
        return True
    return any(v["liga"] == liga for v in _cargar())


async def video_de_liga(league_slug: str, hoy: datetime | None = None) -> dict | None:
    """Video del día para una página de liga, o None."""
    if not habilitado() or not league_slug:
        return None
    liga = league_slug.lower()
    tz = ZoneInfo(_TZ_LIGA.get(liga, _TZ_POR_DEFECTO))
    fecha = (hoy or datetime.now(tz)).astimezone(tz).strftime("%Y-%m-%d")
    for v in _cargar():
        if not v["partido_id"] and v["liga"] == liga and v["fecha"] == fecha:
            return v
    return None
