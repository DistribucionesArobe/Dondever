"""
Email subscriber management for DondeVer.
Stores email addresses that opted in for daily picks newsletter.

Uses JSON file storage on Render Persistent Disk (/data/email_subscribers.json).
Set EMAIL_SUBSCRIBERS_FILE env var to point to persistent storage.
"""

import json
import logging
import os
import re
import uuid
from pathlib import Path
from datetime import datetime
from config import TZ_MX

logger = logging.getLogger("dondever.email_subscribers")

EMAIL_SUBSCRIBERS_FILE = os.getenv("EMAIL_SUBSCRIBERS_FILE", "email_subscribers.json")

# Ensure directory exists
Path(EMAIL_SUBSCRIBERS_FILE).parent.mkdir(parents=True, exist_ok=True)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _load() -> dict:
    """Load email subscribers from file."""
    try:
        with open(EMAIL_SUBSCRIBERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"subscribers": {}}


def _save(data: dict):
    """Save email subscribers to file."""
    try:
        with open(EMAIL_SUBSCRIBERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save email subscribers: {e}")


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    return bool(EMAIL_REGEX.match(email.strip().lower()))


def subscribe(email: str) -> dict:
    """
    Add an email to the newsletter list.
    Returns {"success": bool, "is_new": bool, "token": str}.
    Token is used for unsubscribe links.
    """
    email = email.strip().lower()
    if not is_valid_email(email):
        return {"success": False, "is_new": False, "error": "invalid_email"}

    data = _load()

    if email in data["subscribers"]:
        sub = data["subscribers"][email]
        if sub.get("active", True):
            return {"success": True, "is_new": False, "token": sub.get("token", "")}
        # Re-subscribe
        sub["active"] = True
        sub["resubscribed_at"] = datetime.now(TZ_MX).isoformat()
        _save(data)
        logger.info(f"Re-subscribed email: {email}")
        return {"success": True, "is_new": True, "token": sub.get("token", "")}

    token = uuid.uuid4().hex[:16]
    data["subscribers"][email] = {
        "subscribed_at": datetime.now(TZ_MX).isoformat(),
        "active": True,
        "token": token,
    }
    _save(data)
    logger.info(f"New email subscriber: {email}")
    return {"success": True, "is_new": True, "token": token}


def unsubscribe(email: str = "", token: str = "") -> bool:
    """
    Remove an email from the newsletter list.
    Can unsubscribe by email or by token.
    Returns True if was subscribed.
    """
    data = _load()

    # Find by token
    if token and not email:
        for e, info in data["subscribers"].items():
            if info.get("token") == token:
                email = e
                break

    email = email.strip().lower()
    if email in data["subscribers"]:
        data["subscribers"][email]["active"] = False
        _save(data)
        logger.info(f"Unsubscribed email: {email}")
        return True
    return False


def get_active_subscribers() -> list[dict]:
    """Get all active email subscribers as [{"email": "...", "token": "..."}]."""
    data = _load()
    return [
        {"email": email, "token": info.get("token", "")}
        for email, info in data["subscribers"].items()
        if info.get("active", True)
    ]


def get_subscriber_count() -> int:
    """Get count of active email subscribers."""
    return len(get_active_subscribers())
