#!/usr/bin/env python3
"""
DondeVer.app — Instagram Auto-Publisher
Generates today's "Juegos de Hoy" image and publishes it to Instagram
using the Meta Content Publishing API.

Prerequisites:
    1. Instagram Business or Creator account (@dondeverapp)
    2. Facebook Page connected to the Instagram account
    3. Meta Developer App with instagram_content_publish permission
    4. Environment variables set (see below)

Environment variables:
    INSTAGRAM_USER_ID     — Your Instagram Business Account ID
    INSTAGRAM_ACCESS_TOKEN — Long-lived access token (60 days, auto-refreshed)
    DONDEVER_URL          — Base URL (default: https://dondever.app)

Usage:
    python post_instagram.py              # Generate + publish today
    python post_instagram.py --dry-run    # Generate only, don't publish
    python post_instagram.py 2026-07-25   # Specific date
    python post_instagram.py --refresh-token  # Refresh access token

Cron example (every day at 7:00 AM Mexico City time):
    0 7 * * * cd /path/to/dondever && python3 post_instagram.py >> logs/instagram.log 2>&1
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# ── Config ──────────────────────────────────────────────────

DONDEVER_URL = os.getenv("DONDEVER_URL", "https://dondever.app")
INSTAGRAM_USER_ID = os.getenv("INSTAGRAM_USER_ID", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")

GRAPH_API = "https://graph.facebook.com/v21.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dondever.instagram")


# ── Step 1: Generate Image via DondeVer API ─────────────────

def generate_image(date: str = None) -> dict:
    """Call DondeVer API to generate Instagram image."""
    url = f"{DONDEVER_URL}/api/instagram-image"
    params = {}
    if date:
        params["date"] = date

    log.info(f"Generating image from {url}...")

    with httpx.Client(timeout=120) as client:
        resp = client.get(url, params=params)

        if resp.status_code == 404:
            log.warning("No games found for this date.")
            return None

        resp.raise_for_status()
        data = resp.json()

    log.info(f"Image generated: {data['image_url']}")
    log.info(f"Games: {data['games_count']}")
    for g in data.get("games", []):
        log.info(f"  {g['time']} | {g['league']:15s} | {g['away']} vs {g['home']} | {g['channel']}")

    return data


# ── Step 2: Publish to Instagram via Meta Graph API ──────────

def create_media_container(image_url: str, caption: str) -> str:
    """Create a media container on Instagram (step 1 of publishing)."""
    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        raise ValueError(
            "Missing INSTAGRAM_USER_ID or INSTAGRAM_ACCESS_TOKEN. "
            "Set these environment variables. See setup guide."
        )

    url = f"{GRAPH_API}/{INSTAGRAM_USER_ID}/media"
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    log.info("Creating media container on Instagram...")

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, data=payload)
        data = resp.json()

    if "error" in data:
        error = data["error"]
        log.error(f"Meta API error: {error.get('message', 'Unknown error')}")
        log.error(f"Error type: {error.get('type', 'N/A')}, code: {error.get('code', 'N/A')}")

        if error.get("code") == 190:
            log.error("Access token is invalid or expired. Run: python post_instagram.py --refresh-token")

        raise RuntimeError(f"Failed to create media container: {error.get('message')}")

    container_id = data.get("id")
    log.info(f"Media container created: {container_id}")
    return container_id


def wait_for_container(container_id: str, max_wait: int = 60) -> bool:
    """Wait for the media container to finish processing."""
    url = f"{GRAPH_API}/{container_id}"
    params = {
        "fields": "status_code",
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    log.info("Waiting for media processing...")

    for i in range(max_wait // 5):
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            data = resp.json()

        status = data.get("status_code", "UNKNOWN")
        log.info(f"  Status: {status}")

        if status == "FINISHED":
            return True
        elif status == "ERROR":
            log.error(f"Media processing failed: {data}")
            return False

        time.sleep(5)

    log.error("Timed out waiting for media processing")
    return False


def publish_container(container_id: str) -> str:
    """Publish the media container to Instagram (step 2 of publishing)."""
    url = f"{GRAPH_API}/{INSTAGRAM_USER_ID}/media_publish"
    payload = {
        "creation_id": container_id,
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    log.info("Publishing to Instagram...")

    with httpx.Client(timeout=30) as client:
        resp = client.post(url, data=payload)
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Failed to publish: {data['error'].get('message')}")

    post_id = data.get("id")
    log.info(f"Published! Post ID: {post_id}")
    return post_id


# ── Token Management ─────────────────────────────────────────

def refresh_access_token():
    """Refresh a long-lived token (valid for 60 days → new 60 days).
    Must be called before the current token expires.
    """
    if not INSTAGRAM_ACCESS_TOKEN:
        log.error("No access token to refresh. Set INSTAGRAM_ACCESS_TOKEN first.")
        return

    url = f"{GRAPH_API}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": INSTAGRAM_ACCESS_TOKEN,
    }

    log.info("Refreshing access token...")

    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params)
        data = resp.json()

    if "error" in data:
        log.error(f"Token refresh failed: {data['error'].get('message')}")
        return

    new_token = data.get("access_token")
    expires_in = data.get("expires_in", 0)
    days = expires_in // 86400

    log.info(f"New token obtained! Expires in {days} days.")
    log.info(f"New token: {new_token[:20]}...")
    log.info("")
    log.info("UPDATE your environment variable:")
    log.info(f'  export INSTAGRAM_ACCESS_TOKEN="{new_token}"')
    log.info("")
    log.info("Or in Render dashboard → Environment → INSTAGRAM_ACCESS_TOKEN")

    # Also save to a local file for reference
    token_file = os.path.join(os.path.dirname(__file__), ".instagram_token")
    with open(token_file, "w") as f:
        json.dump({
            "access_token": new_token,
            "expires_in": expires_in,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    log.info(f"Token saved to {token_file}")


# ── Main ────────────────────────────────────────────────────

def main():
    date = None
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--refresh-token":
            refresh_access_token()
            return
        else:
            date = arg

    TZ_MX = timezone(timedelta(hours=-6))
    now_mx = datetime.now(TZ_MX)
    log.info(f"=== DondeVer Instagram Publisher ===")
    log.info(f"Date: {date or now_mx.strftime('%Y-%m-%d')}")
    log.info(f"Mode: {'DRY RUN' if dry_run else 'PUBLISH'}")
    log.info("")

    # Step 1: Generate image
    result = generate_image(date)
    if not result:
        log.info("No games today. Skipping post.")
        return

    image_url = result["image_url"]
    caption = result["caption"]

    log.info(f"\nImage URL: {image_url}")
    log.info(f"Caption preview:\n{caption[:200]}...")

    if dry_run:
        log.info("\n[DRY RUN] Would publish this image to Instagram.")
        log.info("Run without --dry-run to actually publish.")
        return

    # Step 2: Check credentials
    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        log.error("\nMissing Instagram credentials!")
        log.error("Set these environment variables:")
        log.error("  INSTAGRAM_USER_ID=your_ig_user_id")
        log.error("  INSTAGRAM_ACCESS_TOKEN=your_access_token")
        log.error("\nSee INSTAGRAM_SETUP.md for instructions.")
        log.error("\nImage was generated at: " + image_url)
        return

    # Step 3: Create media container
    try:
        container_id = create_media_container(image_url, caption)
    except Exception as e:
        log.error(f"Failed to create container: {e}")
        return

    # Step 4: Wait for processing
    if not wait_for_container(container_id):
        log.error("Media processing failed. Image might be invalid.")
        return

    # Step 5: Publish
    try:
        post_id = publish_container(container_id)
        log.info(f"\n✅ Successfully posted to Instagram!")
        log.info(f"Post ID: {post_id}")
        log.info(f"View at: https://www.instagram.com/dondeverapp/")
    except Exception as e:
        log.error(f"Failed to publish: {e}")


if __name__ == "__main__":
    main()
