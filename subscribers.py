"""
WhatsApp subscriber management for DondeVer.
Stores phone numbers that opted in for daily picks broadcast.

Uses JSON file storage on Render Persistent Disk (/data/subscribers.json).
Set SUBSCRIBERS_FILE env var to point to persistent storage.
Fallback: local subscribers.json (ephemeral, lost on redeploy).
"""

import json
import logging
import os
from pathlib import Path
from datetime import datetime
from config import TZ_MX

logger = logging.getLogger("dondever.subscribers")

SUBSCRIBERS_FILE = os.getenv("SUBSCRIBERS_FILE", "subscribers.json")

# Ensure directory exists (first deploy on persistent disk)
Path(SUBSCRIBERS_FILE).parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    """Load subscribers from file."""
    try:
        with open(SUBSCRIBERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"subscribers": {}}


def _save(data: dict):
    """Save subscribers to file."""
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save subscribers: {e}")


def subscribe(phone: str) -> bool:
    """
    Add a phone number to the broadcast list.
    Returns True if newly subscribed, False if already subscribed.
    """
    phone = phone.strip()
    data = _load()

    if phone in data["subscribers"]:
        # Update last active
        data["subscribers"][phone]["last_active"] = datetime.now(TZ_MX).isoformat()
        _save(data)
        return False

    data["subscribers"][phone] = {
        "subscribed_at": datetime.now(TZ_MX).isoformat(),
        "last_active": datetime.now(TZ_MX).isoformat(),
        "active": True,
    }
    _save(data)
    logger.info(f"New subscriber: {phone}")
    return True


def unsubscribe(phone: str) -> bool:
    """
    Remove a phone number from the broadcast list.
    Returns True if was subscribed, False if wasn't.
    """
    phone = phone.strip()
    data = _load()

    if phone in data["subscribers"]:
        data["subscribers"][phone]["active"] = False
        _save(data)
        logger.info(f"Unsubscribed: {phone}")
        return True
    return False


def update_last_active(phone: str):
    """Update the last active timestamp (called on every message)."""
    phone = phone.strip()
    data = _load()
    if phone in data["subscribers"]:
        data["subscribers"][phone]["last_active"] = datetime.now(TZ_MX).isoformat()
        _save(data)


def get_active_subscribers() -> list[str]:
    """Get all active subscriber phone numbers."""
    data = _load()
    return [
        phone for phone, info in data["subscribers"].items()
        if info.get("active", True)
    ]


def get_subscriber_count() -> int:
    """Get count of active subscribers."""
    return len(get_active_subscribers())
