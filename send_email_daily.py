"""
Daily email newsletter for DondeVer — via Resend API.
Sends games + picks + betting tips in a professional HTML email.

Env vars:
  RESEND_API_KEY       — API key from resend.com
  RESEND_FROM_EMAIL    — Verified sender (default: picks@dondever.app)

Usage:
  python send_email_daily.py              # send to all email subscribers
  python send_email_daily.py --preview    # save HTML to file, don't send
  python send_email_daily.py --test email@example.com  # send to one email
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

import httpx

from config import APP_URL, TZ_MX, HOME_LEFT_SPORTS, get_short_affiliate_url
from sports_api import get_todays_games, fetch_odds, match_odds_to_game, fetch_standings, ODDS_SPORT_MAP
from email_subscribers import get_active_subscribers

APP_URL_BASE = os.getenv("APP_URL", "https://dondever.app")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
from send_whatsapp_daily import (
    LEAGUE_ESPN_MAP, LEAGUE_PRIORITY,
    _generate_stat_tip, _team_order, _fmt_time, _format_channels,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("dondever.email_daily")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM_EMAIL", "DondeVer Picks <picks@dondever.app>")


# ── Email HTML Template ────────────────────────────────────

def _confidence_color(confidence: str) -> str:
    if confidence == "Alta":
        return "#22c55e"
    elif confidence == "Media":
        return "#f59e0b"
    return "#ef4444"


def _sport_emoji(sport: str) -> str:
    return {
        "soccer": "⚽", "basketball": "🏀", "baseball": "⚾",
        "football": "🏈", "hockey": "🏒", "mma": "🥊",
        "boxing": "🥊", "tennis": "🎾", "golf": "⛳",
    }.get(sport, "🏆")


async def compose_email_content() -> dict | None:
    """
    Compose email subject + HTML body.
    Returns {"subject": str, "html": str} or None if no games.
    """
    games = await get_todays_games()
    now = datetime.now(TZ_MX)
    date_display = now.strftime("%d/%m/%Y")
    weekday_names = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    weekday = weekday_names[now.weekday()]

    if not games:
        return None

    upcoming = [g for g in games if g["status"]["state"] == "pre"]
    if not upcoming:
        return None

    # Fetch odds and standings
    leagues_in_play = set(g.get("league_slug", "") for g in upcoming)
    odds_by_league = {}
    for league_slug in leagues_in_play:
        if league_slug in ODDS_SPORT_MAP:
            odds_by_league[league_slug] = await fetch_odds(league_slug)

    standings_by_league = {}
    for league_slug in leagues_in_play:
        espn_info = LEAGUE_ESPN_MAP.get(league_slug)
        if espn_info:
            sport, league_id = espn_info
            try:
                standings_by_league[league_slug] = await fetch_standings(sport, league_id)
            except Exception:
                standings_by_league[league_slug] = []

    # Featured pick
    pick = None
    for pl in LEAGUE_PRIORITY:
        pick = next((g for g in upcoming if g.get("league_slug") == pl), None)
        if pick:
            break
    if not pick:
        pick = upcoming[0]

    pick_league = pick.get("league_slug", "")
    pick_odds_list = odds_by_league.get(pick_league, [])
    pick_odds = match_odds_to_game(pick, pick_odds_list) if pick_odds_list else None
    pick_standings = standings_by_league.get(pick_league, [])
    pick_tip = await _generate_stat_tip(pick, pick_odds, pick_standings)

    first, second = _team_order(pick)
    time_str = _fmt_time(pick["date"])
    channels = _format_channels(pick["broadcasts"])
    conf_color = _confidence_color(pick_tip["confidence"])
    sport_emoji = _sport_emoji(pick.get("sport", ""))

    # Other games
    other_games = [g for g in upcoming if g["id"] != pick["id"]]
    top_others = []
    for pl in LEAGUE_PRIORITY:
        for g in other_games:
            if g.get("league_slug") == pl and g not in top_others:
                top_others.append(g)
                if len(top_others) >= 6:
                    break
        if len(top_others) >= 6:
            break
    for g in other_games:
        if g not in top_others:
            top_others.append(g)
            if len(top_others) >= 6:
                break

    # Build game rows HTML
    game_rows = ""
    for g in top_others:
        f1, f2 = _team_order(g)
        t = _fmt_time(g["date"])
        ch = _format_channels(g["broadcasts"])
        gl = g.get("league_slug", "")
        g_odds_list = odds_by_league.get(gl, [])
        g_odds = match_odds_to_game(g, g_odds_list) if g_odds_list else None
        g_standings = standings_by_league.get(gl, [])
        g_tip = await _generate_stat_tip(g, g_odds, g_standings)
        g_emoji = _sport_emoji(g.get("sport", ""))
        g_conf_color = _confidence_color(g_tip["confidence"])

        game_rows += f"""
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #f1f5f9;">
            <span style="font-size:14px;">{g_emoji} <strong>{g.get('league_name','')}</strong></span><br>
            <span style="font-size:15px;">{f1} vs {f2}</span><br>
            <span style="font-size:13px;color:#64748b;">{t} MX &middot; {ch}</span><br>
            <span style="font-size:13px;">Tip: <strong>{g_tip['pick']}</strong>
              <span style="color:{g_conf_color};font-weight:600;">({g_tip['confidence']})</span>
              &middot; {g_tip['extra_market']}</span>
          </td>
        </tr>"""

    betsson_url = get_short_affiliate_url("betsson", source="email")
    remaining = len(upcoming) - 1 - len(top_others)
    remaining_text = f"<p style='text-align:center;color:#64748b;font-size:13px;'>...y {remaining} juegos mas en <a href='{APP_URL}' style='color:#2563eb;'>dondever.app</a></p>" if remaining > 0 else ""

    subject = f"{sport_emoji} Pick del Dia: {first} vs {second} — {weekday} {date_display}"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#ffffff;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1e293b 0%,#334155 100%);padding:24px;text-align:center;">
    <h1 style="margin:0;color:#ffffff;font-size:24px;letter-spacing:1px;">DONDE VER</h1>
    <p style="margin:4px 0 0;color:#94a3b8;font-size:13px;">Tu guia de deportes en vivo &middot; {weekday} {date_display}</p>
    <p style="margin:4px 0 0;color:#60a5fa;font-size:14px;font-weight:600;">{len(upcoming)} juegos hoy</p>
  </div>

  <!-- Pick del Dia -->
  <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:24px;color:#ffffff;">
    <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#60a5fa;">Pick del Dia</p>
    <h2 style="margin:0 0 8px;font-size:22px;">{first} vs {second}</h2>
    <p style="margin:0 0 4px;color:#94a3b8;font-size:14px;">{pick.get('league_name','')} &middot; {time_str} MX</p>
    <p style="margin:0 0 12px;color:#94a3b8;font-size:13px;">TV: {channels}</p>

    <div style="background:rgba(255,255,255,0.1);border-radius:8px;padding:16px;margin:8px 0;">
      <p style="margin:0 0 4px;font-size:16px;">
        Ganador: <strong style="color:#22d3ee;">{pick_tip['pick']}</strong>
        <span style="color:{conf_color};font-weight:700;"> ({pick_tip['confidence']})</span>
      </p>
      <p style="margin:4px 0 0;font-size:13px;color:#cbd5e1;">
        {pick_tip['reason']}
      </p>
      <p style="margin:8px 0 0;font-size:13px;color:#a5b4fc;">
        Mercado extra: {pick_tip['extra_market']}
      </p>
      {"<p style='margin:8px 0 0;font-size:12px;color:#94a3b8;'>Momios: " + pick_tip['odds_display'] + "</p>" if pick_tip['odds_display'] else ""}
    </div>
  </div>

  <!-- CTA Betsson -->
  <div style="padding:16px 24px;text-align:center;background:#fef3c7;">
    <a href="{betsson_url}" style="display:inline-block;background:#dc2626;color:#ffffff;text-decoration:none;padding:12px 32px;border-radius:8px;font-weight:700;font-size:15px;">
      Apuesta en Betsson
    </a>
    <p style="margin:8px 0 0;font-size:12px;color:#92400e;">Casino y apuestas deportivas con licencia SEGOB</p>
  </div>

  <!-- Mas juegos -->
  <div style="padding:20px 0;">
    <h3 style="margin:0 0 12px;padding:0 16px;font-size:16px;color:#1e293b;">Mas Juegos + Tips</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
      {game_rows}
    </table>
    {remaining_text}
  </div>

  <!-- Footer -->
  <div style="background:#f8fafc;padding:20px 24px;text-align:center;border-top:1px solid #e2e8f0;">
    <p style="margin:0 0 8px;">
      <a href="{APP_URL}" style="color:#2563eb;text-decoration:none;font-weight:600;font-size:14px;">
        Ver todos los juegos en dondever.app
      </a>
    </p>
    <p style="margin:0;font-size:11px;color:#94a3b8;">
      Solo entretenimiento. Apuesta responsable. +18<br>
      <a href="{APP_URL}/email-unsubscribe?token={{{{unsubscribe_token}}}}" style="color:#94a3b8;">Cancelar suscripcion</a>
    </p>
  </div>

</div>
</body>
</html>"""

    return {"subject": subject, "html": html}


# ── Resend API Sender ──────────────────────────────────────

def send_email(to: str, subject: str, html: str) -> dict:
    """Send one email via Resend API."""
    if not RESEND_API_KEY:
        return {"ok": False, "error": "RESEND_API_KEY not configured"}

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": RESEND_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                return {"ok": False, "error": data.get("message", f"HTTP {resp.status_code}")}
            return {"ok": True, "id": data.get("id"), "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Broadcast ──────────────────────────────────────────────

async def send_daily_email_broadcast(test_email: str | None = None):
    """Send daily email to all subscribers (or one test email)."""
    content = await compose_email_content()
    if not content:
        logger.info("No games today — skipping email broadcast")
        return {"sent": 0, "failed": 0, "skipped": "no_games"}

    subject = content["subject"]
    html_template = content["html"]

    if test_email:
        recipients = [{"email": test_email, "token": "test"}]
        logger.info(f"Test mode: sending to {test_email}")
    else:
        # Try API first (for cron jobs on separate services)
        if INTERNAL_API_KEY:
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.get(
                        f"{APP_URL_BASE}/api/internal/email-subscribers",
                        params={"key": INTERNAL_API_KEY}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        recipients = data.get("subscribers", [])
                        logger.info(f"Fetched {len(recipients)} email subscribers from API")
                    else:
                        logger.error(f"API returned {resp.status_code}, falling back to file")
                        recipients = get_active_subscribers()
            except Exception as e:
                logger.error(f"Failed to fetch from API: {e}, falling back to file")
                recipients = get_active_subscribers()
        else:
            recipients = get_active_subscribers()
        logger.info(f"Email broadcast to {len(recipients)} subscribers")

    if not recipients:
        logger.info("No email subscribers")
        return {"sent": 0, "failed": 0}

    sent = 0
    failed = 0
    errors = []

    for sub in recipients:
        email = sub["email"] if isinstance(sub, dict) else sub
        token = sub.get("token", "") if isinstance(sub, dict) else ""

        # Personalize unsubscribe link
        html = html_template.replace("{{unsubscribe_token}}", token)

        result = send_email(email, subject, html)
        if result["ok"]:
            sent += 1
            logger.info(f"Email sent to {email}")
        else:
            failed += 1
            errors.append({"email": email, "error": result["error"]})
            logger.error(f"Failed to email {email}: {result['error']}")

    summary = {"sent": sent, "failed": failed, "total": len(recipients), "errors": errors}
    logger.info(f"Email broadcast complete: {sent} sent, {failed} failed")
    return summary


# ── CLI ────────────────────────────────────────────────────

async def main():
    if "--preview" in sys.argv:
        content = await compose_email_content()
        if content:
            preview_path = "email_preview.html"
            with open(preview_path, "w") as f:
                f.write(content["html"].replace("{{unsubscribe_token}}", "preview-token"))
            print(f"Subject: {content['subject']}")
            print(f"HTML saved to {preview_path}")
            print(f"Open in browser to preview.")
        else:
            print("No games today.")
        return

    test = None
    if "--test" in sys.argv:
        idx = sys.argv.index("--test")
        if idx + 1 < len(sys.argv):
            test = sys.argv[idx + 1]
        else:
            print("Usage: python send_email_daily.py --test email@example.com")
            return

    result = await send_daily_email_broadcast(test_email=test)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
