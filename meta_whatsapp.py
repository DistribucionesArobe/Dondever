"""
Meta WhatsApp Cloud API client for DondeVer.
Replaces Twilio for sending WhatsApp messages.

Env vars needed:
- WHATSAPP_ACCESS_TOKEN  (permanent System User token from Meta Business)
- WHATSAPP_PHONE_NUMBER_ID  (phone number ID, NOT the phone number itself)
- WHATSAPP_VERIFY_TOKEN  (random string you choose for webhook verification)
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("dondever.meta_whatsapp")

META_API_VERSION = "v25.0"


def _get_credentials() -> tuple[str, str]:
    """Return (access_token, phone_number_id). Empty strings if not configured."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    return token, phone_id


def is_configured() -> bool:
    token, phone_id = _get_credentials()
    return bool(token and phone_id)


def _normalize_to(to: str) -> str:
    """Strip 'whatsapp:' prefix and '+' sign — Meta Cloud API wants bare E.164 digits.
    Also normalizes Mexican numbers: old format 521XXXXXXXXXX → 52XXXXXXXXXX."""
    to = to.strip()
    if to.startswith("whatsapp:"):
        to = to[len("whatsapp:"):]
    if to.startswith("+"):
        to = to[1:]
    # Mexico: strip deprecated "1" after country code (521 → 52)
    # Old format: 521 + 10 digits = 13 chars; new format: 52 + 10 digits = 12 chars
    if to.startswith("521") and len(to) == 13:
        to = "52" + to[3:]
    return to


def send_text(to: str, body: str) -> dict:
    """
    Send a free-form text message via Meta Cloud API.
    Works only inside the 24h customer service window (user messaged us in last 24h).
    For pro-active messages, use send_template() instead.

    Returns: {"ok": bool, "id": str|None, "error": str|None}
    """
    token, phone_id = _get_credentials()
    if not token or not phone_id:
        logger.error("Meta WhatsApp not configured: missing WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID")
        return {"ok": False, "id": None, "error": "not_configured"}

    url = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_to(to),
        "type": "text",
        "text": {"preview_url": True, "body": body},
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                logger.warning(f"Meta send failed {resp.status_code}: {data}")
                return {
                    "ok": False,
                    "id": None,
                    "error": data.get("error", {}).get("message", f"HTTP {resp.status_code}"),
                    "raw": data,
                }
            msg_id = (data.get("messages") or [{}])[0].get("id")
            return {"ok": True, "id": msg_id, "error": None, "raw": data}
    except Exception as e:
        logger.exception(f"Meta send exception to {to}: {e}")
        return {"ok": False, "id": None, "error": str(e)}


def send_template(to: str, template_name: str, language: str = "en", components: Optional[list] = None) -> dict:
    """
    Send an approved template message via Meta Cloud API.
    Use this for pro-active messages outside the 24h window.
    Template must be pre-approved in Meta WhatsApp Manager.
    """
    token, phone_id = _get_credentials()
    if not token or not phone_id:
        return {"ok": False, "id": None, "error": "not_configured"}

    url = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    normalized = _normalize_to(to)
    logger.info(f"send_template: original={to} → normalized={normalized}")
    payload = {
        "messaging_product": "whatsapp",
        "to": normalized,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if components:
        payload["template"]["components"] = components

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                logger.warning(f"Meta template send failed {resp.status_code}: {data}")
                return {
                    "ok": False,
                    "id": None,
                    "error": data.get("error", {}).get("message", f"HTTP {resp.status_code}"),
                }
            msg_id = (data.get("messages") or [{}])[0].get("id")
            logger.info(f"send_template OK: to={normalized} status={resp.status_code} contacts={data.get('contacts')} messages={data.get('messages')}")
            return {"ok": True, "id": msg_id, "error": None}
    except Exception as e:
        logger.exception(f"Meta template exception: {e}")
        return {"ok": False, "id": None, "error": str(e)}


# Botones que acompañan CADA respuesta del bot: el usuario ve qué puede hacer sin escribir
# y cada toque cuenta como respuesta → mantiene abierta la ventana de 24 h (Meta solo entrega
# texto libre dentro de ella; las plantillas de marketing se bloquean con 131049).
DEFAULT_BUTTONS = [
    {"id": "btn_hoy", "title": "📺 Juegos de hoy"},
    {"id": "btn_picks", "title": "🎯 Pick del día"},
    {"id": "btn_equipos", "title": "⭐ Mis equipos"},
]
INTERACTIVE_BODY_MAX = 1024


def send_text_with_buttons(to: str, body: str, buttons: list[dict] | None = None,
                           footer: str = "", follow_up: str = "¿Qué más quieres ver? Toca un botón 👇") -> dict:
    """Manda `body` como mensaje interactivo con botones si cabe (≤1024 chars);
    si no, manda el texto y después un mensaje corto con los botones."""
    buttons = buttons or DEFAULT_BUTTONS
    if len(body) <= INTERACTIVE_BODY_MAX:
        r = send_interactive_buttons(to, body=body, buttons=buttons, footer=footer)
        if r.get("ok"):
            return r
        # fallback: interactivo rechazado (p. ej. formato) → texto normal
    r = send_text(to, body)
    if r.get("ok"):
        send_interactive_buttons(to, body=follow_up, buttons=buttons)
    return r


def send_interactive_buttons(to: str, body: str, buttons: list[dict], header: str = "", footer: str = "") -> dict:
    """
    Send a WhatsApp interactive message with reply buttons (max 3).

    buttons: [{"id": "btn_picks", "title": "Picks del Dia"}]
      - id: unique string (max 256 chars)
      - title: button label (max 20 chars)

    Returns: {"ok": bool, "id": str|None, "error": str|None}
    """
    token, phone_id = _get_credentials()
    if not token or not phone_id:
        return {"ok": False, "id": None, "error": "not_configured"}

    url = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                for b in buttons[:3]
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalize_to(to),
        "type": "interactive",
        "interactive": interactive,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                logger.warning(f"Meta interactive send failed {resp.status_code}: {data}")
                return {
                    "ok": False,
                    "id": None,
                    "error": data.get("error", {}).get("message", f"HTTP {resp.status_code}"),
                    "raw": data,
                }
            msg_id = (data.get("messages") or [{}])[0].get("id")
            return {"ok": True, "id": msg_id, "error": None, "raw": data}
    except Exception as e:
        logger.exception(f"Meta interactive send exception to {to}: {e}")
        return {"ok": False, "id": None, "error": str(e)}


def mark_as_read(message_id: str) -> bool:
    """Mark a message as read (blue check marks)."""
    token, phone_id = _get_credentials()
    if not token or not phone_id:
        return False
    url = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            return resp.status_code < 400
    except Exception:
        return False


# ── Ventana de 24 h por número ─────────────────────────────────────────────
# Meta solo permite texto libre si el usuario nos escribió en las últimas 24 h
# (si no: error 131047 asíncrono, aunque la API responda ok). Guardamos el último
# inbound por número para decidir freeform vs plantilla ANTES de enviar.
import json as _json
import time as _time
from pathlib import Path as _Path
_WINDOW_FILE = _Path(os.getenv("SUBSCRIBERS_FILE", "subscribers.json")).parent / "wa_last_inbound.json"
_last_inbound: dict = {}
try:
    _last_inbound = _json.loads(_WINDOW_FILE.read_text(encoding="utf-8"))
except Exception:
    _last_inbound = {}


def record_inbound(from_number: str, ts: float | None = None) -> None:
    """Llamar cuando llega un mensaje del usuario (webhook)."""
    key = _normalize_to(from_number)
    if not key:
        return
    _last_inbound[key] = float(ts or _time.time())
    try:
        _WINDOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WINDOW_FILE.write_text(_json.dumps(_last_inbound), encoding="utf-8")
    except Exception as e:
        logger.debug(f"no se pudo guardar wa_last_inbound: {e}")


def in_24h_window(to: str) -> bool:
    key = _normalize_to(to)
    ts = _last_inbound.get(key) or _last_inbound.get("521" + key[2:] if key.startswith("52") else "")
    return bool(ts) and (_time.time() - float(ts)) < 23.5 * 3600


WABA_ID = os.getenv("WHATSAPP_WABA_ID", "1224835083125902")  # Distribuciones Arobe
_tpl_cache: dict = {"ts": 0, "data": []}


def list_templates(force: bool = False) -> list[dict]:
    """Plantillas del WABA con status/categoría (cache 30 min)."""
    if not force and _tpl_cache["data"] and _time.time() - _tpl_cache["ts"] < 1800:
        return _tpl_cache["data"]
    token, _ = _get_credentials()
    try:
        r = httpx.get(f"https://graph.facebook.com/{META_API_VERSION}/{WABA_ID}/message_templates",
                      params={"fields": "name,language,status,category", "limit": "100", "access_token": token}, timeout=15)
        data = r.json().get("data", []) if r.status_code == 200 else []
        if data:
            _tpl_cache.update(ts=_time.time(), data=data)
        return data
    except Exception as e:
        logger.warning(f"list_templates failed: {e}")
        return _tpl_cache["data"]


DAILY_TEMPLATE_UTILITY = "dondever_resumen_diario"   # UTILITY: no le aplica el experimento de marketing
DAILY_TEMPLATE_MARKETING = ("dondever_picks_diarios", "en")


def pick_daily_template() -> tuple[str, str, str]:
    """(nombre, idioma, categoría) de la mejor plantilla aprobada para el resumen diario."""
    for t in list_templates():
        if t.get("name") == DAILY_TEMPLATE_UTILITY and t.get("status") == "APPROVED":
            return t["name"], t.get("language", "es_MX"), t.get("category", "UTILITY")
    return DAILY_TEMPLATE_MARKETING[0], DAILY_TEMPLATE_MARKETING[1], "MARKETING"


def create_daily_utility_template() -> dict:
    """Crea la plantilla UTILITY del resumen diario (Meta la revisa; minutos a 24 h)."""
    token, _ = _get_credentials()
    payload = {
        "name": DAILY_TEMPLATE_UTILITY,
        "language": "es_MX",
        "category": "UTILITY",
        "allow_category_change": False,
        "components": [
            {"type": "BODY",
             "text": "Hola 👋 Aquí está el resumen de partidos de hoy que solicitaste en DondeVer:\n\n{{1}}\n\nHorarios del centro de México. Responde VER para la lista completa con canales.",
             "example": {"body_text": [["⚽ América vs Chivas 8:00 PM · Canal 5, TUDN\n🏈 Cowboys vs Eagles 7:15 PM · Fox Sports"]]}},
            {"type": "FOOTER", "text": "Responde STOP para dejar de recibirlo."},
        ],
    }
    try:
        r = httpx.post(f"https://graph.facebook.com/{META_API_VERSION}/{WABA_ID}/message_templates",
                       params={"access_token": token}, json=payload, timeout=20)
        _tpl_cache["ts"] = 0
        return {"status": r.status_code, "response": r.json()}
    except Exception as e:
        return {"error": str(e)}


# Últimos estados de entrega (sent/delivered/read/failed) que Meta manda por webhook.
# Es la única forma de saber POR QUÉ una plantilla "aceptada" (ok:true) nunca llega:
# el error viene aquí (p. ej. 130472 'parte de un experimento', 131049 'límite por usuario',
# 131026 'no entregable', 131047 're-engagement').
from collections import deque as _deque
DELIVERY_LOG = _deque(maxlen=300)


def parse_status_webhook(payload: dict) -> list[dict]:
    out = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for st in value.get("statuses", []) or []:
                    errs = []
                    for e in st.get("errors", []) or []:
                        errs.append({"code": e.get("code"), "title": e.get("title"),
                                     "message": e.get("message"),
                                     "details": (e.get("error_data") or {}).get("details", "")})
                    rec = {
                        "id": st.get("id"), "status": st.get("status"),
                        "to": st.get("recipient_id"), "ts": st.get("timestamp"),
                        "conversation": ((st.get("conversation") or {}).get("origin") or {}).get("type"),
                        "billable": (st.get("pricing") or {}).get("billable"),
                        "category": (st.get("pricing") or {}).get("category"),
                        "errors": errs,
                    }
                    out.append(rec)
                    DELIVERY_LOG.appendleft(rec)
    except Exception as e:
        logger.exception(f"Failed to parse status webhook: {e}")
    return out


def parse_inbound_webhook(payload: dict) -> list[dict]:
    """
    Parse inbound webhook from Meta. Returns list of normalized message dicts:
    [{"from": "+521...", "body": "hola", "message_id": "wamid...", "timestamp": "..."}]
    Handles text messages AND interactive button replies.
    Ignores status callbacks (delivered/read/sent).
    """
    messages = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    msg_type = msg.get("type", "")
                    body = ""

                    if msg_type == "text":
                        body = (msg.get("text") or {}).get("body", "")
                    elif msg_type == "interactive":
                        # Button reply or list reply
                        interactive = msg.get("interactive", {})
                        ir_type = interactive.get("type", "")
                        if ir_type == "button_reply":
                            body = interactive.get("button_reply", {}).get("id", "")
                        elif ir_type == "list_reply":
                            body = interactive.get("list_reply", {}).get("id", "")
                    elif msg_type == "button":
                        # Quick reply button from template
                        body = (msg.get("button") or {}).get("text", "")
                    else:
                        continue

                    if body:
                        messages.append({
                            "from": "+" + msg.get("from", ""),
                            "body": body,
                            "message_id": msg.get("id"),
                            "timestamp": msg.get("timestamp"),
                        })
    except Exception as e:
        logger.exception(f"Failed to parse inbound Meta webhook: {e}")
    return messages
