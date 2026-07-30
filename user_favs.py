"""
User favorites persistence for DondeVer.
Links favorite teams to a WhatsApp phone number (no auth, no password).
Uses JSON file storage on Render Persistent Disk.
"""

import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime
from config import TZ_MX

logger = logging.getLogger("dondever.user_favs")

FAVS_FILE = os.getenv("USER_FAVS_FILE", "user_favs.json")
Path(FAVS_FILE).parent.mkdir(parents=True, exist_ok=True)

PHONE_REGEX = re.compile(r"^\+?\d{7,15}$")


def _load() -> dict:
    try:
        with open(FAVS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"users": {}}


def _save(data: dict):
    try:
        with open(FAVS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving favs: {e}")


def _normalize_phone(phone: str) -> str:
    """Strip spaces/dashes, keep digits and leading +."""
    cleaned = re.sub(r"[\s\-\(\).]", "", phone.strip())
    if not cleaned.startswith("+"):
        # Assume Mexico if no country code
        if len(cleaned) == 10:
            cleaned = "+52" + cleaned
        elif not cleaned.startswith("52") and len(cleaned) <= 12:
            cleaned = "+" + cleaned
        else:
            cleaned = "+" + cleaned
    return cleaned


def save_favs(phone: str, favs: list) -> dict:
    """Save favorite teams for a phone number."""
    phone = _normalize_phone(phone)
    if not PHONE_REGEX.match(phone):
        return {"success": False, "error": "invalid_phone"}

    data = _load()
    now = datetime.now(TZ_MX).isoformat()

    if phone in data["users"]:
        data["users"][phone]["favs"] = favs
        data["users"][phone]["updated"] = now
    else:
        data["users"][phone] = {
            "favs": favs,
            "created": now,
            "updated": now,
        }

    _save(data)
    logger.info(f"Saved {len(favs)} favs for {phone[-4:]}")
    return {"success": True, "count": len(favs)}


def load_favs(phone: str) -> dict:
    """Load favorite teams for a phone number."""
    phone = _normalize_phone(phone)
    if not PHONE_REGEX.match(phone):
        return {"success": False, "error": "invalid_phone", "favs": []}

    data = _load()
    user = data["users"].get(phone)
    if not user:
        return {"success": True, "favs": [], "found": False}

    return {"success": True, "favs": user["favs"], "found": True}


def get_user_count() -> int:
    data = _load()
    return len(data.get("users", {}))
