"""
TikTok OAuth + Content Posting API integration for DondeVer.app
Handles: Login Kit (OAuth2) → token → upload video via Content Posting API
"""

import os
import logging
import httpx
from urllib.parse import urlencode

logger = logging.getLogger("dondever.tiktok")

# ── TikTok Credentials ─────────────────────────────────
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "aw0indw4yqw478q7")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "SczUe8BxUfydmdSeL58wjJnx0qgG40tq")
TIKTOK_REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "https://dondever.app/auth/tiktok/callback")

# Scopes needed: user.info.basic + video.publish + video.upload
TIKTOK_SCOPES = "user.info.basic,video.publish,video.upload"

# Token storage (in production, use a database)
_tiktok_tokens = {
    "access_token": None,
    "refresh_token": None,
    "open_id": None,
    "expires_in": None,
}


def get_tiktok_auth_url(state: str = "dondever") -> str:
    """Generate TikTok OAuth authorization URL."""
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "response_type": "code",
        "scope": TIKTOK_SCOPES,
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "state": state,
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for access token."""
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    data = {
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TIKTOK_REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        result = resp.json()

    if "access_token" in result:
        _tiktok_tokens["access_token"] = result["access_token"]
        _tiktok_tokens["refresh_token"] = result.get("refresh_token")
        _tiktok_tokens["open_id"] = result.get("open_id")
        _tiktok_tokens["expires_in"] = result.get("expires_in")
        logger.info(f"TikTok token obtained for user {result.get('open_id')}")
    else:
        logger.error(f"TikTok token exchange failed: {result}")

    return result


async def refresh_access_token() -> dict:
    """Refresh an expired access token."""
    if not _tiktok_tokens["refresh_token"]:
        return {"error": "No refresh token available"}

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    data = {
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": _tiktok_tokens["refresh_token"],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        result = resp.json()

    if "access_token" in result:
        _tiktok_tokens["access_token"] = result["access_token"]
        _tiktok_tokens["refresh_token"] = result.get("refresh_token", _tiktok_tokens["refresh_token"])
        _tiktok_tokens["expires_in"] = result.get("expires_in")
        logger.info("TikTok token refreshed")

    return result


async def get_user_info() -> dict:
    """Get basic info about the authenticated TikTok user."""
    token = _tiktok_tokens.get("access_token")
    if not token:
        return {"error": "Not authenticated"}

    url = "https://open.tiktokapis.com/v2/user/info/"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"fields": "open_id,union_id,avatar_url,display_name"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, params=params)
        return resp.json()


async def upload_video_to_tiktok(video_path: str, title: str = "") -> dict:
    """
    Upload a video to TikTok using Content Posting API.

    Flow:
    1. Initialize upload → get upload_url
    2. Upload video file to upload_url
    3. TikTok processes and publishes
    """
    token = _tiktok_tokens.get("access_token")
    if not token:
        return {"error": "Not authenticated. Go to /tiktok/login first."}

    # Step 1: Initialize video upload
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Get video file size
    import os as _os
    video_size = _os.path.getsize(video_path)

    init_body = {
        "post_info": {
            "title": title[:150] if title else "Partidos de hoy | DondeVer.app",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,  # Single chunk upload
            "total_chunk_count": 1,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        # Init upload
        resp = await client.post(init_url, headers=headers, json=init_body)
        init_result = resp.json()
        logger.info(f"TikTok upload init: {init_result}")

        if "error" in init_result and init_result["error"].get("code") != "ok":
            return {"error": f"Init failed: {init_result}"}

        data = init_result.get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")

        if not upload_url:
            return {"error": "No upload URL returned", "details": init_result}

        # Step 2: Upload the video file
        with open(video_path, "rb") as f:
            video_data = f.read()

        upload_headers = {
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
            "Content-Type": "video/mp4",
        }
        upload_resp = await client.put(upload_url, content=video_data, headers=upload_headers)
        logger.info(f"TikTok upload response: {upload_resp.status_code}")

    return {
        "status": "uploaded",
        "publish_id": publish_id,
        "upload_status": upload_resp.status_code,
        "message": "Video enviado a TikTok. Puede tardar unos minutos en procesarse.",
    }


async def check_publish_status(publish_id: str) -> dict:
    """Check the status of a published video."""
    token = _tiktok_tokens.get("access_token")
    if not token:
        return {"error": "Not authenticated"}

    url = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"publish_id": publish_id}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        return resp.json()


def is_authenticated() -> bool:
    """Check if we have a valid TikTok token."""
    return _tiktok_tokens.get("access_token") is not None


def get_token_info() -> dict:
    """Get current token info (without exposing secrets)."""
    return {
        "authenticated": is_authenticated(),
        "open_id": _tiktok_tokens.get("open_id"),
        "has_refresh": _tiktok_tokens.get("refresh_token") is not None,
    }
