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

META_API_VERSION = "v21.0"


def _get_credentials() -> tuple[str, str]:
    """Return (access_token, phone_number_id). Empty strings if not configured."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    return token, phone_id


def is_configured() -> bool:
    token, phone_id = _get_credentials()
    return bool(token and phone_id)


def _normalize_to(to: str) -> str:
    """Strip 'whatsapp:' prefix and '+' sign — Meta Cloud API wants bare E.164 digits."""
    to = to.strip()
    if to.startswith("whatsapp:"):
        to = to[len("whatsapp:"):]
    if to.startswith("+"):
        to = to[1:]
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


def send_template(to: str, template_name: str, language: str = "es_MX", components: Optional[list] = None) -> dict:
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
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
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
            return {"ok": True, "id": msg_id, "error": None}
    except Exception as e:
        logger.exception(f"Meta template exception: {e}")
        return {"ok": False, "id": None, "error": str(e)}


def parse_inbound_webhook(payload: dict) -> list[dict]:
    """
    Parse inbound webhook from Meta. Returns list of normalized message dicts:
    [{"from": "+521...", "body": "hola", "message_id": "wamid...", "timestamp": "..."}]
    Ignores status callbacks (delivered/read/sent).
    """
    messages = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        # Only handle text messages for now
                        # Later: interactive, button, list replies
                        continue
                    messages.append({
                        "from": "+" + msg.get("from", ""),
                        "body": (msg.get("text") or {}).get("body", ""),
                        "message_id": msg.get("id"),
                        "timestamp": msg.get("timestamp"),
                    })
    except Exception as e:
        logger.exception(f"Failed to parse inbound Meta webhook: {e}")
    return messages
