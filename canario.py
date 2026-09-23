"""Canario: revisa cada mañana que el sitio no esté diciendo mentiras.

Por qué existe
──────────────
En un solo día salieron: "posición #" dentro del schema, una tabla que
numeraba filas como si fueran posiciones de liga, 26% de los enlaces de
afiliado sin medir, /equipo/asdfghjkl devolviendo 200 con página completa, y
49 errores 500 subiendo desde hacía mes y medio.

Ninguno era visible. Todos se veían bien. Ese es el problema: el producto es
que la gente nos crea el canal y la hora, y un dato mal puesto no se nota
hasta que alguien prende la tele a la hora equivocada y no vuelve.

Diseñado para quien NO puede revisar todos los días
────────────────────────────────────────────────────
- Silencio = todo bien. Si no hay nada roto, no manda nada.
- No repite. Algo que lleva cinco días roto no son cinco correos: se avisa
  una vez y se vuelve a insistir a los tres días, no antes.
- Un solo mensaje con todo junto y ya ordenado por gravedad.
- Sin listas que mantener: las URLs salen de la portada del día.
- Si el canario mismo se cae, avisa de eso en vez de morir callado.

Cron sugerido: 0 13 * * *  (7 AM CDMX)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("canario")

SITIO = os.getenv("APP_URL", "https://dondever.app").rstrip("/")
AVISAR_A = os.getenv("CANARIO_EMAIL", "ealejandro.robledo@gmail.com")
# Cada cuántos días se vuelve a insistir con algo que sigue roto.
DIAS_INSISTIR = 3
UA = "Mozilla/5.0 (compatible; DondeVerCanario/1.0; +https://dondever.app)"

GRAVE, MEDIO = "grave", "medio"


@dataclass
class Hallazgo:
    grave: str          # GRAVE o MEDIO
    que: str            # qué está mal, en una línea
    donde: str          # URL
    detalle: str = ""   # el texto exacto que lo delata

    @property
    def huella(self) -> str:
        """Identifica el problema, no la vez que lo vimos."""
        return hashlib.sha1(f"{self.que}|{self.donde}".encode()).hexdigest()[:16]


# ── Delatores ──────────────────────────────────────────────────────────────
# Cada uno salió de un error real. No se agregan por si acaso: se agregan
# cuando algo ya se rompió así una vez.
DELATORES = [
    (r"posici[oó]n\s*#\s*(?:en|de|\)|<)", "Un 'posición #' sin número"),
    (r"\bNone\b",                          "Un 'None' de Python en la página"),
    (r"\bundefined\b",                     "Un 'undefined' de JavaScript"),
    (r"\{\{|\}\}",                         "Una plantilla Jinja sin renderizar"),
    (r"TBD\s+vs\s+TBD",                    "Un partido 'TBD vs TBD'"),
    (r"\bnan\b",                           "Un 'nan' (división entre cero)"),
    # Ojo: esto se busca DESPUÉS de decodificar entidades una vez. `O&#39;Ward`
    # en el HTML es correcto y al decodificar queda `O'Ward`. Si después de
    # decodificar TODAVÍA se lee `&#39;`, es porque estaba escapado dos veces
    # — el bug de "anotación". Buscarlo sin decodificar antes daría falsa
    # alarma en cualquier página con un apóstrofo.
    (r"&#\d+;|&quot;|&amp;",               "Texto escapado dos veces (se lee el código, no el símbolo)"),
]

# Rutas estructurales: son rutas, no una lista curada de contenido.
FIJAS = ["/", "/gratis-hoy", "/canales", "/equipos",
         "/donde-ver-en-mexico", "/donde-ver-en-venezuela", "/donde-ver-en-espana"]


def _texto_visible(html: str) -> str:
    """Lo que un lector ve: sin scripts, sin estilos, sin etiquetas.

    Se decodifican las entidades UNA vez, que es lo que hace el navegador.
    Por eso `O&#39;Ward` queda en `O'Ward` y no dispara falsa alarma, pero
    `O&amp;#39;Ward` (escapado dos veces) sigue leyéndose `O&#39;Ward` y sí
    la dispara. La diferencia entre las dos es justo el bug que buscamos.
    """
    import html as _html
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s)


def revisar_pagina(url: str, status: int, html: str) -> list[Hallazgo]:
    """Todo lo que se puede comprobar de una sola página."""
    out: list[Hallazgo] = []

    if status >= 500:
        out.append(Hallazgo(GRAVE, f"Error de servidor ({status})", url))
        return out          # sin cuerpo útil, no tiene caso seguir
    if status != 200:
        out.append(Hallazgo(MEDIO, f"Responde {status} y debería ser 200", url))
        return out

    visible = _texto_visible(html)

    for patron, descripcion in DELATORES:
        m = re.search(patron, visible, re.I)
        if m:
            i = max(0, m.start() - 60)
            out.append(Hallazgo(GRAVE, descripcion, url,
                                visible[i:m.end() + 60].strip()))

    # Título: es lo que ve la gente en Google antes de decidir si entra.
    t = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    titulo = (t.group(1).strip() if t else "")
    if not titulo:
        out.append(Hallazgo(GRAVE, "Página sin <title>", url))
    elif len(titulo) > 70:
        out.append(Hallazgo(MEDIO, f"Título de {len(titulo)} caracteres (Google corta en ~70)",
                            url, titulo))

    # El schema es lo que leen Google y los asistentes de IA. Si no parsea,
    # no lo leen — y no hay forma de enterarse mirando la página.
    for bloque in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html, re.I):
        try:
            json.loads(bloque)
        except json.JSONDecodeError as e:
            out.append(Hallazgo(GRAVE, "JSON-LD roto: Google no lo puede leer", url, str(e)[:120]))
            break

    # Si la página promete canal, que haya canal.
    m = re.search(r"Donde verlo:?\s*(.{0,80})", visible, re.I)
    if m and not re.search(r"[A-Za-z0-9]", m.group(1)):
        out.append(Hallazgo(GRAVE, "Dice 'Donde verlo' y no nombra ningún canal", url))

    # Enlaces de afiliado sin medición: invisibles por definición.
    if "/go/" in html and "affiliate.js" not in html:
        out.append(Hallazgo(GRAVE, "Tiene enlaces de afiliado pero no carga affiliate.js", url))

    return out


async def _bajar(cli: httpx.AsyncClient, url: str) -> tuple[int, str]:
    try:
        r = await cli.get(url, headers={"User-Agent": UA}, follow_redirects=True)
        return r.status_code, r.text
    except Exception as e:
        log.warning("no se pudo bajar %s: %s", url, e)
        return 0, ""


async def rutas_del_dia(cli: httpx.AsyncClient) -> list[str]:
    """URLs a revisar, sacadas de la portada de hoy.

    A propósito no hay lista fija de equipos ni de partidos: lo que hay que
    vigilar es lo que el sitio está mostrando HOY. Así no hay nada que
    mantener y siempre se revisa lo que la gente está viendo.
    """
    urls = [SITIO + r for r in FIJAS]
    _, html = await _bajar(cli, SITIO + "/")
    for patron, cuantas in ((r'href="(/partido/[^"#?]+)"', 6),
                            (r'href="(/equipo/[^"#?]+)"', 6),
                            (r'href="(/liga/[^"#?]+)"', 4)):
        vistos: list[str] = []
        for m in re.findall(patron, html):
            if m not in vistos:
                vistos.append(m)
            if len(vistos) >= cuantas:
                break
        urls += [SITIO + u for u in vistos]

    # Una URL basura: debe dar 404. Si da 200, volvimos a fabricar soft 404.
    urls.append(SITIO + "/equipo/zzz-no-existe-canario")
    return urls


async def recolectar() -> list[Hallazgo]:
    async with httpx.AsyncClient(timeout=25) as cli:
        urls = await rutas_del_dia(cli)
        log.info("revisando %d páginas", len(urls))
        hallazgos: list[Hallazgo] = []
        sin_respuesta = 0

        for url in urls:
            status, html = await _bajar(cli, url)

            if url.endswith("/equipo/zzz-no-existe-canario"):
                if status == 200:
                    hallazgos.append(Hallazgo(
                        GRAVE, "Un equipo inventado devuelve 200 en vez de 404", url,
                        "Vuelve a fabricar páginas vacías indexables"))
                continue

            if status == 0:
                sin_respuesta += 1
                hallazgos.append(Hallazgo(GRAVE, "La página no responde", url))
                continue
            hallazgos += revisar_pagina(url, status, html)

        # Si casi nada respondió, el problema no son N páginas: o el sitio
        # está caído o el canario se quedó sin red. Mandar siete avisos
        # idénticos es justo lo que hace que un canario se empiece a ignorar,
        # y además puede mandar a alguien a buscar siete bugs que no existen.
        # Un aviso, dicho con honestidad sobre lo que no sabemos.
        if sin_respuesta and sin_respuesta >= max(2, len(urls) // 2):
            hallazgos = [h for h in hallazgos if h.que != "La página no responde"]
            hallazgos.insert(0, Hallazgo(
                GRAVE, "No pude alcanzar el sitio", SITIO,
                f"{sin_respuesta} de {len(urls)} páginas no respondieron. "
                f"Puede ser que el sitio esté caído, o que el canario se haya "
                f"quedado sin red. Ábrelo en el navegador para saber cuál de las dos."))
        return hallazgos


# ── Memoria entre corridas ─────────────────────────────────────────────────
SQL_TABLA = """
CREATE TABLE IF NOT EXISTS canario (
    huella      TEXT PRIMARY KEY,
    grave       TEXT NOT NULL,
    que         TEXT NOT NULL,
    donde       TEXT NOT NULL,
    detalle     TEXT,
    visto_1a_vez TIMESTAMPTZ NOT NULL,
    visto_ult   TIMESTAMPTZ NOT NULL,
    avisado_ult TIMESTAMPTZ
)"""


def decidir_aviso(h: Hallazgo, previo: dict | None, ahora: datetime) -> dict | None:
    """¿Este hallazgo amerita mensaje hoy? Función pura, para poder probarla.

    Las reglas están pensadas para alguien que no revisa todos los días:
    - Nuevo  -> se avisa.
    - Ya avisado y sigue ahí -> se calla, salvo que sea grave y ya pasaron
      DIAS_INSISTIR desde la última vez. Insistir a diario garantiza que
      dejes de leer el correo; insistir cada tres días mantiene el aviso vivo
      sin volverse ruido.
    - Lo menor nunca insiste: si no lo arreglaste, es que no te urgía.
    """
    nunca_avisado = not previo or not previo.get("avisado_ult")
    toca_insistir = bool(
        previo and previo.get("avisado_ult") and h.grave == GRAVE
        and (ahora - previo["avisado_ult"]) >= timedelta(days=DIAS_INSISTIR)
    )
    if not (nunca_avisado or toca_insistir):
        return None
    desde = previo["visto_1a_vez"] if previo else ahora
    return {**h.__dict__, "huella": h.huella,
            "dias": (ahora - desde).days, "nuevo": nunca_avisado}


async def conciliar(hallazgos: list[Hallazgo]) -> tuple[list[dict], list[dict]]:
    """Guarda lo de hoy y decide qué amerita mensaje.

    Devuelve (para_avisar, resueltos). `para_avisar` son los nuevos más los
    graves que llevan DIAS_INSISTIR sin que se les vuelva a mencionar.
    """
    from sqlalchemy import text
    from db import async_session

    ahora = datetime.now(timezone.utc)
    vivas = {h.huella for h in hallazgos}

    async with async_session() as s:
        await s.execute(text(SQL_TABLA))

        previos = {r["huella"]: dict(r) for r in
                   (await s.execute(text("SELECT * FROM canario"))).mappings()}

        for h in hallazgos:
            await s.execute(text("""
                INSERT INTO canario (huella, grave, que, donde, detalle,
                                     visto_1a_vez, visto_ult)
                VALUES (:hu, :g, :q, :d, :det, :n, :n)
                ON CONFLICT (huella) DO UPDATE SET visto_ult = :n
            """), {"hu": h.huella, "g": h.grave, "q": h.que, "d": h.donde,
                   "det": h.detalle[:400], "n": ahora})

        # Lo que ya no aparece se arregló. Se menciona una vez y se borra.
        # Solo se menciona si en su momento te avisamos: no tiene sentido
        # contarte que se arregló algo de lo que nunca te enteraste.
        resueltos = [v for k, v in previos.items()
                     if k not in vivas and v.get("avisado_ult")]
        await s.execute(text("DELETE FROM canario WHERE huella != ALL(:hs)"),
                        {"hs": list(vivas) or [""]})

        avisar = [a for a in (decidir_aviso(h, previos.get(h.huella), ahora)
                              for h in hallazgos) if a]
        if avisar:
            await s.execute(text("UPDATE canario SET avisado_ult = :n WHERE huella = ANY(:hs)"),
                            {"n": ahora, "hs": [a["huella"] for a in avisar]})
        await s.commit()
    return avisar, resueltos


def redactar(avisar: list[dict], resueltos: list[dict]) -> tuple[str, str]:
    graves = [a for a in avisar if a["grave"] == GRAVE]
    medios = [a for a in avisar if a["grave"] == MEDIO]
    asunto = (f"DondeVer: {len(graves)} cosa{'s' if len(graves) != 1 else ''} rota{'s' if len(graves) != 1 else ''}"
              if graves else f"DondeVer: {len(medios)} detalle{'s' if len(medios) != 1 else ''} menor{'es' if len(medios) != 1 else ''}")

    def bloque(items, titulo, color):
        if not items:
            return ""
        filas = ""
        for a in items:
            viejo = (f' <span style="color:#b91c1c;font-weight:700;">lleva {a["dias"]} días</span>'
                     if a["dias"] >= 1 and not a["nuevo"] else "")
            det = (f'<div style="font-family:ui-monospace,monospace;font-size:12px;color:#6b7280;'
                   f'background:#f9fafb;padding:6px 8px;border-radius:6px;margin-top:6px;">'
                   f'{a["detalle"][:200]}</div>') if a["detalle"] else ""
            filas += (f'<li style="margin-bottom:14px;"><b>{a["que"]}</b>{viejo}<br>'
                      f'<a href="{a["donde"]}" style="color:#2563eb;font-size:13px;">{a["donde"]}</a>{det}</li>')
        return (f'<h3 style="color:{color};margin:18px 0 6px;font-size:15px;">{titulo}</h3>'
                f'<ul style="padding-left:18px;margin:0;">{filas}</ul>')

    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:640px;color:#111;">'
        '<p style="font-size:14px;color:#6b7280;margin:0 0 4px;">Canario de DondeVer</p>'
        f'{bloque(graves, "Rompe lo que el usuario ve", "#b91c1c")}'
        f'{bloque(medios, "Menor", "#b45309")}'
    )
    if resueltos:
        html += ('<h3 style="color:#059669;margin:18px 0 6px;font-size:15px;">Ya se arregló</h3>'
                 '<ul style="padding-left:18px;margin:0;color:#6b7280;font-size:13px;">'
                 + "".join(f'<li>{r["que"]} — {r["donde"]}</li>' for r in resueltos) + '</ul>')
    html += ('<p style="font-size:12px;color:#9ca3af;margin-top:22px;border-top:1px solid #e5e7eb;'
             'padding-top:10px;">Solo llega correo cuando hay algo. Lo que siga roto se repite '
             f'cada {DIAS_INSISTIR} días, no a diario.</p></div>')
    return asunto, html


async def main() -> int:
    try:
        hallazgos = await recolectar()
    except Exception as e:
        # Si el canario se rompe, que no muera callado: eso sería lo peor.
        log.exception("el canario falló")
        _enviar("DondeVer: el canario no pudo correr",
                f"<p>El canario falló y no revisó nada hoy.</p><pre>{e}</pre>")
        return 1

    avisar, resueltos = await conciliar(hallazgos)
    log.info("hallazgos=%d avisar=%d resueltos=%d", len(hallazgos), len(avisar), len(resueltos))

    if not avisar:
        log.info("nada que reportar — no se manda correo")
        return 0

    asunto, html = redactar(avisar, resueltos)
    _enviar(asunto, html)
    return 0


def _enviar(asunto: str, html: str):
    try:
        from send_email_daily import send_email
        r = send_email(AVISAR_A, asunto, html)
        log.info("correo -> %s: %s", AVISAR_A, r)
    except Exception:
        log.exception("no se pudo enviar el correo; el detalle queda en este log")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
