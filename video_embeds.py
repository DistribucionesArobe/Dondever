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
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("dondever.video")

_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_embeds.json")

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


def video_de_partido(game: dict) -> dict | None:
    """Video incrustable para este partido, o None."""
    if not habilitado() or not game:
        return None
    videos = _cargar()
    if not videos:
        return None
    pid = str(game.get("id") or "")
    liga = (game.get("league_slug") or "").lower()

    for v in videos:
        if v["partido_id"] and v["partido_id"] == pid:
            return v

    fecha = _fecha_local(game.get("date", ""), liga)
    for v in videos:
        if not v["partido_id"] and v["liga"] == liga and v["fecha"] == fecha:
            return v
    return None


def video_de_liga(league_slug: str, hoy: datetime | None = None) -> dict | None:
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
