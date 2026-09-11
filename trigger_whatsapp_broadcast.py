#!/usr/bin/env python3
"""
Trigger DondeVer WhatsApp daily broadcast via HTTP.

Designed to run from Render Cron Job or any external scheduler.
Hits the /whatsapp/broadcast-now endpoint on the web service.

Usage:
    python trigger_whatsapp_broadcast.py
"""
import os
import sys
import httpx

APP_URL = os.getenv("APP_URL", "https://dondever.app")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def main():
    if not ADMIN_TOKEN:
        print("ERROR: ADMIN_TOKEN not set")
        sys.exit(1)

    url = f"{APP_URL}/whatsapp/broadcast-now"
    params = {"token": ADMIN_TOKEN, "sync": "1", "force": "0"}

    print(f"Triggering broadcast: {url}")
    try:
        resp = httpx.get(url, params=params, timeout=120.0)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                print("Broadcast OK")
            else:
                print(f"Broadcast error: {data.get('error')}")
                sys.exit(1)
        else:
            print(f"HTTP error: {resp.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
