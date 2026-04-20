"""
DondeVer.app — Main FastAPI server
Where to watch sports in Mexico & USA
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.twiml.messaging_response import MessagingResponse

from config import AFFILIATES, LEAGUES, ALL_LEAGUES, APP_URL, TZ_MX, TZ_ET, TEAM_ALIASES
from sports_api import get_todays_games, search_games, get_team_stats, fetch_odds, match_odds_to_game
from whatsapp_bot import handle_whatsapp_message
from tiktok_auth import (
    get_tiktok_auth_url, exchange_code_for_token, get_user_info,
    upload_video_to_tiktok, check_publish_status, is_authenticated,
    get_token_info,
)

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dondever")

# ── App ──────────────────────────────────────────────────
app = FastAPI(
    title="DondeVer.app",
    description="Donde ver juegos deportivos en Mexico y USA",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Google Analytics middleware ──────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware


class GAInjectMiddleware(BaseHTTPMiddleware):
    """Inject Google Analytics 4 and Microsoft Clarity snippets into every HTML response.
    Activated per-tool when env vars are set:
      - GA_MEASUREMENT_ID (format: G-XXXXXXXXXX) for GA4
      - CLARITY_PROJECT_ID (format: lowercase alphanumeric) for Microsoft Clarity
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ga_id = os.getenv("GA_MEASUREMENT_ID", "").strip()
        gads_id = os.getenv("GOOGLE_ADS_ID", "").strip()  # format: AW-XXXXXXXXXXX
        clarity_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
        gtm_id = os.getenv("GTM_CONTAINER_ID", "").strip()
        if not ga_id and not clarity_id and not gtm_id and not gads_id:
            return response

        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype:
            return response

        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            snippet = ""
            if gtm_id:
                snippet += (
                    f'<!-- Google Tag Manager -->\n'
                    f'<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{"gtm.start":\n'
                    f'new Date().getTime(),event:"gtm.js"}});var f=d.getElementsByTagName(s)[0],\n'
                    f'j=d.createElement(s),dl=l!="dataLayer"?"&l="+l:"";j.async=true;j.src=\n'
                    f'"https://www.googletagmanager.com/gtm.js?id="+i+dl;f.parentNode.insertBefore(j,f);\n'
                    f'}})(window,document,"script","dataLayer","{gtm_id}");</script>\n'
                    f'<!-- End Google Tag Manager -->\n'
                )
            if ga_id:
                snippet += (
                    f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>\n'
                    f'<script>\n'
                    f'  window.dataLayer = window.dataLayer || [];\n'
                    f'  function gtag(){{dataLayer.push(arguments);}}\n'
                    f'  gtag("js", new Date());\n'
                    f'  gtag("config", "{ga_id}", {{ anonymize_ip: true }});\n'
                    + (f'  gtag("config", "{gads_id}");\n' if gads_id else '')
                    + f'</script>\n'
                )
            if clarity_id:
                snippet += (
                    f'<script>\n'
                    f'  (function(c,l,a,r,i,t,y){{\n'
                    f'    c[a]=c[a]||function(){{(c[a].q=c[a].q||[]).push(arguments)}};\n'
                    f'    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;\n'
                    f'    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);\n'
                    f'  }})(window, document, "clarity", "script", "{clarity_id}");\n'
                    f'</script>\n'
                )
            snippet = snippet.encode("utf-8")

            if b"</head>" in body:
                body = body.replace(b"</head>", snippet + b"</head>", 1)

            # GTM also needs a <noscript> iframe right after <body>
            if gtm_id:
                gtm_noscript = (
                    f'\n<!-- Google Tag Manager (noscript) -->\n'
                    f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}"\n'
                    f'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
                    f'<!-- End Google Tag Manager (noscript) -->\n'
                ).encode("utf-8")
                # Match both <body> and <body ...> variants
                import re as _re
                body = _re.sub(
                    rb"(<body\b[^>]*>)",
                    lambda m: m.group(1) + gtm_noscript,
                    body, count=1, flags=_re.IGNORECASE,
                )

            from starlette.responses import Response
            # Strip content-length so Starlette recalculates
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=ctype,
            )
        except Exception as e:
            logger = logging.getLogger("dondever")
            logger.warning(f"GA inject failed: {e}")
            return response


app.add_middleware(GAInjectMiddleware)


# ── Template helpers ─────────────────────────────────────
def format_mx_time(iso_date: str) -> str:
    """Convert ISO date to Mexico City time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        mx = dt.astimezone(TZ_MX)
        return mx.strftime("%I:%M %p")
    except Exception:
        return ""


def format_us_time(iso_date: str) -> str:
    """Convert ISO date to US Eastern time (DST-aware)."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        et = dt.astimezone(TZ_ET)
        return et.strftime("%I:%M %p ET")
    except Exception:
        return ""


templates.env.globals["format_mx_time"] = format_mx_time
templates.env.globals["format_us_time"] = format_us_time
templates.env.globals["affiliates"] = AFFILIATES
templates.env.globals["app_url"] = APP_URL
templates.env.globals["now"] = lambda: datetime.now(TZ_MX)


# ── Web Routes ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    date: Optional[str] = Query(None, description="Date YYYYMMDD"),
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
):
    """Main page — today's games."""
    games = await get_todays_games(
        date_str=date, sport_filter=sport, league_filter=league
    )

    # Group games by sport
    sports_grouped = {}
    for game in games:
        sport_key = game["league_slug"].split("-")[0] if "-" in game["league_slug"] else game["league_slug"]
        # Use league_name for grouping
        league_name = game["league_name"]
        if league_name not in sports_grouped:
            sports_grouped[league_name] = {
                "emoji": game["emoji"],
                "games": [],
            }
        sports_grouped[league_name]["games"].append(game)

    # Pick del dia — choose most interesting upcoming game
    pick_game = None
    priority_leagues = ["liga-mx", "premier-league", "champions", "nfl", "nba", "la-liga", "mlb"]
    upcoming = [g for g in games if g["status"]["state"] == "pre" and g["broadcasts"]]
    if upcoming:
        # Try priority leagues first
        for pl in priority_leagues:
            pick = next((g for g in upcoming if g["league_slug"] == pl), None)
            if pick:
                pick_game = pick
                break
        if not pick_game:
            pick_game = upcoming[0]
    elif games:
        # If no upcoming, pick a live game
        live = [g for g in games if g["status"]["state"] == "in"]
        if live:
            pick_game = live[0]

    # Available sports for filter
    sport_types = sorted(set(v[0] for v in LEAGUES.values()))

    today = datetime.now(TZ_MX)

    # Date navigation
    if date:
        try:
            viewing_date = datetime.strptime(date, "%Y%m%d").replace(tzinfo=TZ_MX)
        except ValueError:
            viewing_date = today
    else:
        viewing_date = today

    prev_date = (viewing_date - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (viewing_date + timedelta(days=1)).strftime("%Y%m%d")

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "games": games,
            "sports_grouped": sports_grouped,
            "sport_types": sport_types,
            "leagues": LEAGUES,
            "current_sport": sport,
            "current_league": league,
            "current_date": date or today.strftime("%Y%m%d"),
            "today_display": viewing_date.strftime("%A %d de %B, %Y"),
            "prev_date": prev_date,
            "next_date": next_date,
            "total_games": len(games),
            "pick_game": pick_game,
        },
    )


@app.get("/juego/{event_id}", response_class=HTMLResponse)
async def game_detail(request: Request, event_id: str):
    """Individual game page — good for SEO."""
    all_games = await get_todays_games()
    game = next((g for g in all_games if g["id"] == event_id), None)

    if not game:
        # 410 Gone: le dice a Google que la URL existio pero ya no.
        # Google desindexa mas rapido con 410 que con 404.
        return templates.TemplateResponse(
            request, "404.html", status_code=410,
            context={"message": "Este juego ya termino. Ve los juegos de hoy en la home."}
        )

    # Fetch odds if game hasn't started yet
    odds = None
    if game["status"]["state"] == "pre":
        try:
            league_slug = game.get("league_slug", "")
            odds_list = await fetch_odds(league_slug)
            odds = match_odds_to_game(game, odds_list)
        except Exception as e:
            logger.warning(f"Odds fetch failed for game {event_id}: {e}")

    return templates.TemplateResponse(
        request, "game.html", context={"game": game, "odds": odds}
    )


# ── Affiliate click tracking ──────────────────────────────
import json as _json
from pathlib import Path as _Path
from datetime import date as _date

_CLICKS_FILE = os.getenv("CLICKS_FILE", os.path.join(
    os.path.dirname(os.getenv("SUBSCRIBERS_FILE", ".")), "affiliate_clicks.json"
))


def _track_click(affiliate: str, source: str):
    """Persist affiliate click count by day/affiliate/source."""
    try:
        _Path(_CLICKS_FILE).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CLICKS_FILE, "r") as f:
                data = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            data = {}
        today = _date.today().isoformat()
        data.setdefault(today, {})
        key = f"{affiliate}:{source}"
        data[today][key] = data[today].get(key, 0) + 1
        with open(_CLICKS_FILE, "w") as f:
            _json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Click tracking failed: {e}")


def get_click_stats(days: int = 7) -> dict:
    """Get click stats for the last N days."""
    try:
        with open(_CLICKS_FILE, "r") as f:
            data = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        data = {}
    from datetime import timedelta
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    result = {}
    for day, clicks in data.items():
        if day >= cutoff:
            for k, v in clicks.items():
                result[k] = result.get(k, 0) + v
    return result


# Branded affiliate redirect — "dondever.app/go/betsson" en vez de links largos
@app.get("/go/{key}")
async def affiliate_redirect(key: str, s: str = "web"):
    """
    Redirige a la URL del afiliado con tracking de source.
    Uso: /go/betsson?s=twitter  →  link afiliado real + sub1=twitter
    """
    from fastapi.responses import RedirectResponse
    from config import get_affiliate_url
    _track_click(key, s)  # track antes de redirigir
    target = get_affiliate_url(key, source=s)
    if target == "#":
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=target, status_code=302)


# Legacy URLs que Google sigue rastreando de versiones viejas del sitio
# Redirect 301 permanente a la home para recuperar SEO
@app.get("/game/{old_id}")
async def legacy_game_redirect(old_id: str):
    """Redirect de /game/* (URL vieja) a /juego/* (URL actual) o a home."""
    from fastapi.responses import RedirectResponse
    # Si el ID existe hoy, redirige al /juego/{id}, si no, a la home
    all_games = await get_todays_games()
    if any(g["id"] == old_id for g in all_games):
        return RedirectResponse(url=f"/juego/{old_id}", status_code=301)
    return RedirectResponse(url="/", status_code=301)


@app.get("/liga/{league_slug}", response_class=HTMLResponse)
async def league_page(request: Request, league_slug: str):
    """
    Permanent league landing page — always has content for Google to index.
    e.g. /liga/liga-mx, /liga/nfl, /liga/nba
    """
    if league_slug not in ALL_LEAGUES:
        return templates.TemplateResponse(
            request, "404.html", status_code=404
        )

    sport, league_id, display_name, emoji = ALL_LEAGUES[league_slug]
    games = await get_todays_games(league_filter=league_slug)

    return templates.TemplateResponse(
        request, "league.html", context={
            "league_slug": league_slug,
            "league_name": display_name,
            "emoji": emoji,
            "sport": sport,
            "games": games,
            "total_games": len(games),
        }
    )


# ── API Routes ───────────────────────────────────────────

@app.get("/api/games")
async def api_games(
    date: Optional[str] = None,
    sport: Optional[str] = None,
    league: Optional[str] = None,
    q: Optional[str] = None,
):
    """JSON API for games."""
    if q:
        games = await search_games(q, date_str=date)
    else:
        games = await get_todays_games(
            date_str=date, sport_filter=sport, league_filter=league
        )
    return JSONResponse({"games": games, "count": len(games)})


@app.get("/api/leagues")
async def api_leagues():
    """List available leagues."""
    return JSONResponse({
        "leagues": [
            {"slug": slug, "sport": sport, "league": league, "name": name, "emoji": emoji}
            for slug, (sport, league, name, emoji) in LEAGUES.items()
        ]
    })


# ── WhatsApp Webhook ─────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    Body: str = Form(""),
    From: str = Form(""),
):
    """Twilio WhatsApp webhook — receives messages, responds with game info."""
    logger.info(f"WhatsApp from {From}: {Body!r}")

    try:
        response_text = await handle_whatsapp_message(Body, From)
        if not response_text:
            logger.warning(f"WhatsApp handler returned empty response for body={Body!r}")
            response_text = (
                "Hmm, no entendi. Escribe *ayuda* para ver comandos, "
                "*hoy* para juegos, o *picks* para el pick del dia."
            )
    except Exception as e:
        logger.exception(f"WhatsApp handler crashed on body={Body!r}: {e}")
        response_text = (
            "Tuvimos un problema procesando tu mensaje. Intenta de nuevo o escribe *ayuda*."
        )

    logger.info(f"WhatsApp reply to {From}: {response_text[:100]}...")

    twiml = MessagingResponse()
    twiml.message(response_text)
    return HTMLResponse(content=str(twiml), media_type="application/xml")


@app.get("/webhook/whatsapp")
async def whatsapp_verify():
    """Health check for Twilio webhook verification."""
    return {"status": "ok", "service": "dondever-whatsapp"}


@app.get("/whatsapp/debug")
async def whatsapp_debug():
    """Diagnostico del webhook de WhatsApp."""
    import os as _os
    from subscribers import get_active_subscribers, get_subscriber_count
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")
    info = {
        "twilio_sid_set": bool(sid),
        "twilio_sid_prefix": sid[:6] + "..." if sid else None,
        "twilio_token_set": bool(token),
        "whatsapp_number": wa_num,
        "webhook_url_expected": "https://dondever.app/webhook/whatsapp (POST)",
        "total_subscribers": 0,
    }
    try:
        info["total_subscribers"] = get_subscriber_count()
    except Exception as e:
        info["subscribers_error"] = str(e)
    return info


@app.get("/admin/subscribers")
async def admin_subscribers(token: str = ""):
    """
    Lista detallada de suscriptores. Protegido por ADMIN_TOKEN.
    Uso: https://dondever.app/admin/subscribers?token=TU_TOKEN
    """
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    from subscribers import _load
    data = _load()
    subs = data.get("subscribers", {})
    active = [(p, info) for p, info in subs.items() if info.get("active", True)]
    inactive = [(p, info) for p, info in subs.items() if not info.get("active", True)]

    def mask(phone: str) -> str:
        # Muestra +52155***1234 para privacidad en logs
        if len(phone) > 6:
            return phone[:5] + "***" + phone[-4:]
        return phone

    return {
        "ok": True,
        "total": len(subs),
        "active_count": len(active),
        "inactive_count": len(inactive),
        "active": [
            {
                "phone": mask(p),
                "subscribed_at": info.get("subscribed_at"),
                "last_active": info.get("last_active"),
            }
            for p, info in active
        ],
        "inactive": [
            {"phone": mask(p), "subscribed_at": info.get("subscribed_at")}
            for p, info in inactive
        ],
    }


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, token: str = ""):
    """Dashboard admin con métricas clave de DondeVer."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return HTMLResponse("<h1>Token inválido</h1><p>Usa ?token=TU_ADMIN_TOKEN</p>", status_code=403)

    from subscribers import _load, get_subscriber_count, get_active_subscribers
    from twitter_bot import _tweet_timestamps, _posted_games, MAX_TWEETS_PER_DAY

    # Subscribers
    subs_data = _load()
    all_subs = subs_data.get("subscribers", {})
    active_subs = [(p, i) for p, i in all_subs.items() if i.get("active", True)]
    inactive_subs = [(p, i) for p, i in all_subs.items() if not i.get("active", True)]

    # Today's tweets
    now_ts = __import__("time").time()
    tweets_today = len(_tweet_timestamps)
    tweets_last_hour = sum(1 for t in _tweet_timestamps if t > now_ts - 3600)

    # Games posted today
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games_posted = len(_posted_games.get(today_key, set()))

    # Affiliate clicks
    clicks_7d = get_click_stats(7)
    clicks_today = get_click_stats(1)

    # Group clicks by affiliate and source
    def group_clicks(raw: dict) -> dict:
        by_aff = {}
        by_src = {}
        total = 0
        for k, v in raw.items():
            aff, src = k.split(":", 1) if ":" in k else (k, "unknown")
            by_aff[aff] = by_aff.get(aff, 0) + v
            by_src[src] = by_src.get(src, 0) + v
            total += v
        return {"by_affiliate": by_aff, "by_source": by_src, "total": total}

    clicks_7d_grouped = group_clicks(clicks_7d)
    clicks_today_grouped = group_clicks(clicks_today)

    # Today's games count
    try:
        games = await get_todays_games()
        total_games = len(games)
        live_games = sum(1 for g in games if g["status"]["state"] == "in")
    except Exception:
        total_games = 0
        live_games = 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "active_count": len(active_subs),
        "inactive_count": len(inactive_subs),
        "active_subs": active_subs,
        "tweets_today": tweets_today,
        "tweets_max": MAX_TWEETS_PER_DAY,
        "tweets_last_hour": tweets_last_hour,
        "games_posted": games_posted,
        "total_games": total_games,
        "live_games": live_games,
        "clicks_today": clicks_today_grouped,
        "clicks_7d": clicks_7d_grouped,
        "token": token,
    })


@app.post("/whatsapp/test-send")
async def whatsapp_test_send(to: str):
    """Enviar un mensaje de prueba a un numero via Twilio. ej: /whatsapp/test-send?to=+521XXXXXXXXXX"""
    import os as _os
    from twilio.rest import Client as TwilioClient
    sid = _os.getenv("TWILIO_ACCOUNT_SID", "")
    token = _os.getenv("TWILIO_AUTH_TOKEN", "")
    wa_num = _os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+15715463202")
    if not sid or not token:
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN no configurados"}
    if not to.startswith("whatsapp:"):
        to = f"whatsapp:{to}"
    try:
        client = TwilioClient(sid, token)
        msg = client.messages.create(
            body="Test de DondeVer.app — si recibes este mensaje, el webhook de salida funciona. Responde *suscribir* para probar el flujo de entrada.",
            from_=wa_num,
            to=to,
        )
        return {"ok": True, "message_sid": msg.sid, "status": msg.status, "to": to, "from": wa_num}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.get("/whatsapp/debug")
async def whatsapp_debug():
    """Diagnostico completo del sistema WhatsApp: subscribers, twilio, scheduler."""
    from subscribers import get_active_subscribers, _load, SUBSCRIBERS_FILE
    from whatsapp_broadcast import get_twilio_client, CONTENT_SID
    from config import TWILIO_WA_NUMBER

    # Subscriber info
    data = _load()
    active = get_active_subscribers()

    # Twilio check
    client = get_twilio_client()
    twilio_ok = client is not None

    return {
        "subscribers_file": SUBSCRIBERS_FILE,
        "total_subscribers": len(data.get("subscribers", {})),
        "active_subscribers": len(active),
        "subscriber_numbers": active,  # remove in production if privacy concern
        "all_data": data,
        "twilio_configured": twilio_ok,
        "twilio_from": TWILIO_WA_NUMBER,
        "content_template_sid": CONTENT_SID or "NOT SET — broadcasts only work within 24h window",
        "hint": "Si el broadcast falla, el usuario debe mandar un mensaje al bot dentro de las 24h previas, O configura TWILIO_CONTENT_SID con un template aprobado.",
    }


@app.post("/whatsapp/broadcast-now")
async def whatsapp_broadcast_now(token: str = ""):
    """Disparar el broadcast diario ahora mismo a todos los suscriptores."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    try:
        from whatsapp_broadcast import send_daily_broadcast
        result = await send_daily_broadcast()
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.post("/whatsapp/broadcast-to")
async def whatsapp_broadcast_to(to: str):
    """Mandar el broadcast diario a un solo numero. ej: /whatsapp/broadcast-to?to=+521XXXXXXXXXX"""
    try:
        from whatsapp_broadcast import compose_daily_broadcast, get_twilio_client, CONTENT_SID
        from config import TWILIO_WA_NUMBER
        msg = await compose_daily_broadcast()
        if not msg:
            return {"ok": False, "error": "No hay juegos hoy"}
        client = get_twilio_client()
        if not client:
            return {"ok": False, "error": "Twilio no configurado"}
        from whatsapp_broadcast import _ensure_wa_number
        to_num = _ensure_wa_number(to)

        if CONTENT_SID:
            import json as _json
            m = client.messages.create(content_sid=CONTENT_SID, content_variables=_json.dumps({"1": msg}), from_=TWILIO_WA_NUMBER, to=to_num)
        else:
            m = client.messages.create(body=msg, from_=TWILIO_WA_NUMBER, to=to_num)

        return {
            "ok": True, "sid": m.sid, "status": m.status, "to": to_num,
            "used_template": bool(CONTENT_SID),
            "preview": msg[:200],
        }
    except Exception as e:
        error_msg = str(e)
        hint = ""
        if "63016" in error_msg or "63032" in error_msg or "outside" in error_msg.lower():
            hint = "El usuario esta fuera de la ventana de 24h. Necesitas un Content Template aprobado en Twilio."
        elif "21408" in error_msg:
            hint = "El numero no tiene sesion activa de WhatsApp. El usuario debe mandar un mensaje primero."
        elif "credentials" in error_msg.lower() or "auth" in error_msg.lower():
            hint = "Credenciales de Twilio invalidas. Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN."
        return {"ok": False, "error": error_msg, "type": type(e).__name__, "hint": hint}


# ── Twitter Bot Scheduler ────────────────────────────────

import os
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from twitter_bot import setup_twitter_scheduler
    from whatsapp_broadcast import send_daily_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():
        # Twitter bot (only if credentials set)
        if os.getenv("TWITTER_API_KEY"):
            setup_twitter_scheduler(scheduler)

        # WhatsApp daily broadcast at 9:00 AM MX time (15:00 UTC)
        scheduler.add_job(
            send_daily_broadcast,
            CronTrigger(hour=15, minute=0),
            id="whatsapp_daily_broadcast",
            name="Daily WhatsApp picks broadcast",
            replace_existing=True,
        )
        logger.info("WhatsApp broadcast scheduled at 9:00 AM MX")

        # WhatsApp pre-game alerts every 5 minutes
        scheduler.add_job(
            send_pregame_alerts,
            IntervalTrigger(minutes=5),
            id="whatsapp_pregame_alerts",
            name="Pre-game WhatsApp alerts",
            replace_existing=True,
        )
        logger.info("Pre-game alerts scheduled every 5 min")

        # TikTok/Reels daily video + images at 7:30 AM MX (13:30 UTC)
        scheduler.add_job(
            generate_daily_video,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_video",
            name="Daily TikTok video generation",
            replace_existing=True,
        )
        scheduler.add_job(
            generate_daily_images,
            CronTrigger(hour=13, minute=30),
            id="tiktok_daily_images",
            name="Daily TikTok images generation",
            replace_existing=True,
        )
        logger.info("TikTok video generation scheduled at 7:30 AM MX")

        scheduler.start()
        logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def stop_scheduler():
        scheduler.shutdown()

except ImportError:
    logger.warning("APScheduler not installed, scheduled jobs disabled")


# ── SEO: Sitemap & Robots ───────────────────────────────

@app.get("/tiktokVCdYT0dv6jrqTL4pncMRP6dXaRB54Aka.txt", response_class=PlainTextResponse)
async def tiktok_verification_old():
    """TikTok domain verification file (legacy — sandbox)."""
    return "tiktok-developers-site-verification=VCdYT0dv6jrqTL4pncMRP6dXaRB54Aka"


@app.get("/tiktokaCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg.txt", response_class=PlainTextResponse)
async def tiktok_verification_prod():
    """TikTok domain verification file (production)."""
    return "tiktok-developers-site-verification=aCYk4BWSaFsTrBg1sjS4kQ1JZjaIpTRg"


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt for search engine crawlers."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /juego/\n"
        "Allow: /liga/\n"
        "Disallow: /api/\n"
        "Disallow: /webhook/\n"
        f"\nSitemap: {APP_URL}/sitemap.xml\n"
    )


@app.get("/tiktok/hoy")
async def tiktok_today():
    """Show today's TikTok video and images for easy download."""
    from pathlib import Path
    from datetime import datetime
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"/static/tiktok/dondever_picks_{date_tag}.mp4"
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = []
    if images_dir.exists():
        images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")])
    return {
        "date": date_tag,
        "video": video_path,
        "images": images,
        "instructions": "Descarga el video y subelo a TikTok/Reels/Shorts. Las imagenes sirven para carrusel de Instagram.",
    }


@app.get("/twitter/debug")
async def twitter_debug():
    """Diagnostico del bot de Twitter — para saber por que no tweetea."""
    from twitter_bot import (
        twitter_credentials_valid, get_twitter_client,
        _tweet_timestamps, MAX_TWEETS_PER_HOUR, MAX_TWEETS_PER_DAY,
        MIN_SECONDS_BETWEEN_TWEETS, _can_post_now,
    )
    from sports_api import get_todays_games
    from datetime import datetime, timezone
    import time as _time

    info = {
        "credentials_set": twitter_credentials_valid(),
        "tweets_posted_last_24h": len(_tweet_timestamps),
        "limits": {
            "per_hour": MAX_TWEETS_PER_HOUR,
            "per_day": MAX_TWEETS_PER_DAY,
            "min_seconds_between": MIN_SECONDS_BETWEEN_TWEETS,
        },
        "can_post_now": None,
        "rate_limit_reason": None,
        "auth_check": None,
        "upcoming_games_20min": [],
        "games_today": 0,
    }

    allowed, reason = _can_post_now()
    info["can_post_now"] = allowed
    info["rate_limit_reason"] = reason or None

    # Verifica que los tokens funcionen (sin postear)
    try:
        client = get_twitter_client()
        if client:
            me = client.get_me()
            info["auth_check"] = {"ok": True, "username": me.data.username if me.data else None}
        else:
            info["auth_check"] = {"ok": False, "error": "no client (credentials missing)"}
    except Exception as e:
        info["auth_check"] = {"ok": False, "error": str(e)}

    # Juegos próximos en los siguientes 20 min
    try:
        games = await get_todays_games()
        info["games_today"] = len(games)
        now = datetime.now(timezone.utc)
        for g in games:
            if g["status"]["state"] != "pre":
                continue
            try:
                gt = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                diff_min = (gt - now).total_seconds() / 60
                if 0 < diff_min <= 20:
                    info["upcoming_games_20min"].append({
                        "name": g["name"], "in_minutes": round(diff_min, 1),
                    })
            except Exception:
                pass
    except Exception as e:
        info["games_error"] = str(e)

    return info


@app.post("/twitter/test-tweet")
async def twitter_test_tweet():
    """Postea un tweet de prueba MANUALMENTE. Solo usar para verificar que funciona."""
    from twitter_bot import post_tweet
    from datetime import datetime
    text = f"Test de DondeVer.app — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC. Juegos de hoy en https://dondever.app"
    result = post_tweet(text)
    return result


@app.get("/tiktok/generar")
async def tiktok_generate_now(images: bool = False):
    """Manually trigger TikTok video generation. Pass ?images=true para generar carrusel tambien."""
    try:
        from tiktok_generator import generate_daily_video, generate_daily_images
        from sports_api import get_todays_games

        # Verificar primero que si hay juegos (para distinguir no_games vs error ffmpeg)
        games_check = await get_todays_games()
        if not games_check:
            return JSONResponse({"status": "no_games", "message": "No hay juegos hoy"})

        logger.info(f"[generar] {len(games_check)} juegos encontrados, generando video...")
        video = await generate_daily_video()

        if not video:
            # Hay juegos pero el video no se genero → error en ffmpeg/PIL
            return JSONResponse(
                {"status": "error", "error": "Video generation returned empty (ffmpeg/PIL error)", "games_found": len(games_check)},
                status_code=500,
            )

        img_count = 0
        if images:
            img_list = await generate_daily_images()
            img_count = len(img_list) if img_list else 0

        return JSONResponse({
            "video": video,
            "images_count": img_count,
            "games_used": len(games_check),
            "status": "ok",
        })
    except Exception as e:
        logger.exception("tiktok_generate_now failed")
        return JSONResponse(
            {"status": "error", "error": str(e), "type": type(e).__name__},
            status_code=500,
        )


@app.get("/sitemap.xml")
async def sitemap_xml():
    """Dynamic sitemap with today's game pages for Google indexing."""
    today = datetime.now(TZ_MX)
    today_str = today.strftime("%Y-%m-%d")

    games = await get_todays_games()

    urls = [
        f'  <url>\n    <loc>{APP_URL}</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>hourly</changefreq>\n'
        f'    <priority>1.0</priority>\n  </url>'
    ]

    for game in games:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/juego/{game["id"]}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>hourly</changefreq>\n'
            f'    <priority>0.8</priority>\n  </url>'
        )

    # Static pages (legal + guides)
    static_pages = [
        ("sobre-nosotros", "monthly", "0.5"),
        ("privacidad", "monthly", "0.3"),
        ("terminos", "monthly", "0.3"),
        ("guia/donde-ver-liga-mx", "weekly", "0.8"),
        ("guia/donde-ver-nfl-en-mexico", "weekly", "0.8"),
        ("guia/donde-ver-nba-en-mexico", "weekly", "0.8"),
        ("guia/mejores-streaming-deportes-mexico", "weekly", "0.8"),
        ("guia/donde-ver-champions-league", "weekly", "0.8"),
        ("guia/como-ver-tudn-en-usa", "weekly", "0.8"),
        ("guia/mejores-casas-apuestas-liga-mx", "weekly", "0.9"),
        ("guia/donde-ver-champions-en-mexico", "weekly", "0.8"),
    ]
    for page, freq, priority in static_pages:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/{page}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>{freq}</changefreq>\n'
            f'    <priority>{priority}</priority>\n  </url>'
        )

    # Permanent league landing pages (high priority — always have content)
    for slug in LEAGUES:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/liga/{slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.9</priority>\n  </url>'
        )

    # Streaming comparator
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/streaming</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>monthly</changefreq>\n'
        f'    <priority>0.8</priority>\n  </url>'
    )

    # Casinos comparator (high-value page for affiliate conversion)
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/casinos</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>weekly</changefreq>\n'
        f'    <priority>0.9</priority>\n  </url>'
    )

    # Team pages (SEO goldmine)
    urls.append(
        f'  <url>\n    <loc>{APP_URL}/equipos</loc>\n'
        f'    <lastmod>{today_str}</lastmod>\n'
        f'    <changefreq>daily</changefreq>\n'
        f'    <priority>0.8</priority>\n  </url>'
    )
    for team_slug in POPULAR_TEAMS:
        urls.append(
            f'  <url>\n    <loc>{APP_URL}/equipo/{team_slug}</loc>\n'
            f'    <lastmod>{today_str}</lastmod>\n'
            f'    <changefreq>daily</changefreq>\n'
            f'    <priority>0.7</priority>\n  </url>'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) +
        '\n</urlset>'
    )

    return Response(content=xml, media_type="application/xml")


# ── Static Pages (Legal + Guides for AdSense) ───────────

@app.get("/sobre-nosotros", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html")

@app.get("/privacidad", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")

@app.get("/terminos", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html")

@app.get("/casinos", response_class=HTMLResponse)
async def casinos_page(request: Request):
    """Casino comparison landing — for SEO + affiliate conversion."""
    from config import get_affiliate_url
    return templates.TemplateResponse(request, "casinos.html", {
        "caliente_url": get_affiliate_url("caliente", source="casinos"),
        "betsson_url": get_affiliate_url("betsson", source="casinos"),
    })

@app.get("/guia/{guide_slug}", response_class=HTMLResponse)
async def guide_page(request: Request, guide_slug: str):
    """Original content guides for SEO + AdSense."""
    template_name = f"guides/{guide_slug}.html"
    try:
        return templates.TemplateResponse(request, template_name)
    except Exception:
        return templates.TemplateResponse(request, "404.html", status_code=404)


# ── Streaming Comparator ────────────────────────────────

@app.get("/streaming", response_class=HTMLResponse)
async def streaming_page(request: Request):
    return templates.TemplateResponse(request, "streaming.html")


# ── Team Pages ──────────────────────────────────────────

# Popular teams for SEO (slug -> display name)
# slug -> {name, sport_label, league, keywords}
# sport_label se usa en SEO: "futbol", "basketball", "futbol americano", "beisbol"
POPULAR_TEAMS = {
    # Liga MX
    "chivas": {"name": "Guadalajara (Chivas)", "sport": "futbol", "league": "Liga MX", "aka": "Chivas, Guadalajara, Rebaño Sagrado"},
    "america": {"name": "Club América", "sport": "futbol", "league": "Liga MX", "aka": "América, Águilas, Club America"},
    "cruz-azul": {"name": "Cruz Azul", "sport": "futbol", "league": "Liga MX", "aka": "Cruz Azul, La Máquina, Cementeros"},
    "pumas": {"name": "Pumas UNAM", "sport": "futbol", "league": "Liga MX", "aka": "Pumas, UNAM, Auriazules"},
    "tigres": {"name": "Tigres UANL", "sport": "futbol", "league": "Liga MX", "aka": "Tigres, UANL, Tigres de Monterrey"},
    "monterrey": {"name": "Monterrey", "sport": "futbol", "league": "Liga MX", "aka": "Rayados, Monterrey, Rayados de Monterrey"},
    "toluca": {"name": "Toluca", "sport": "futbol", "league": "Liga MX", "aka": "Toluca, Diablos Rojos, Choriceros"},
    "santos": {"name": "Santos Laguna", "sport": "futbol", "league": "Liga MX", "aka": "Santos Laguna, Guerreros"},
    "leon": {"name": "León", "sport": "futbol", "league": "Liga MX", "aka": "León, Club León, La Fiera"},
    "pachuca": {"name": "Pachuca", "sport": "futbol", "league": "Liga MX", "aka": "Pachuca, Tuzos"},
    "atlas": {"name": "Atlas", "sport": "futbol", "league": "Liga MX", "aka": "Atlas, Zorros, Rojinegros"},
    "necaxa": {"name": "Necaxa", "sport": "futbol", "league": "Liga MX", "aka": "Necaxa, Rayos"},
    "puebla": {"name": "Puebla", "sport": "futbol", "league": "Liga MX", "aka": "Puebla, La Franja, Camoteros"},
    "queretaro": {"name": "Querétaro", "sport": "futbol", "league": "Liga MX", "aka": "Querétaro, Gallos Blancos"},
    "mazatlan": {"name": "Mazatlán FC", "sport": "futbol", "league": "Liga MX", "aka": "Mazatlán, Cañoneros"},
    "tijuana": {"name": "Club Tijuana", "sport": "futbol", "league": "Liga MX", "aka": "Tijuana, Xolos, Xoloitzcuintles"},
    "juarez": {"name": "FC Juárez", "sport": "futbol", "league": "Liga MX", "aka": "Juárez, Bravos"},
    # Europa
    "real-madrid": {"name": "Real Madrid", "sport": "futbol", "league": "La Liga", "aka": "Real Madrid, Merengues"},
    "barcelona": {"name": "FC Barcelona", "sport": "futbol", "league": "La Liga", "aka": "Barcelona, Barça, Blaugrana"},
    "liverpool": {"name": "Liverpool FC", "sport": "futbol", "league": "Premier League", "aka": "Liverpool, Reds"},
    "manchester-city": {"name": "Manchester City", "sport": "futbol", "league": "Premier League", "aka": "Man City, Citizens"},
    "manchester-united": {"name": "Manchester United", "sport": "futbol", "league": "Premier League", "aka": "Man United, Red Devils"},
    "arsenal": {"name": "Arsenal", "sport": "futbol", "league": "Premier League", "aka": "Arsenal, Gunners"},
    "chelsea": {"name": "Chelsea", "sport": "futbol", "league": "Premier League", "aka": "Chelsea, Blues"},
    "psg": {"name": "Paris Saint-Germain", "sport": "futbol", "league": "Ligue 1", "aka": "PSG, Paris"},
    "bayern": {"name": "Bayern Munich", "sport": "futbol", "league": "Bundesliga", "aka": "Bayern, Bayern München"},
    "juventus": {"name": "Juventus", "sport": "futbol", "league": "Serie A", "aka": "Juventus, Juve, Vecchia Signora"},
    "inter-milan": {"name": "Inter de Milán", "sport": "futbol", "league": "Serie A", "aka": "Inter, Nerazzurri"},
    # MLS
    "lafc": {"name": "Los Angeles FC", "sport": "futbol", "league": "MLS", "aka": "LAFC, Los Angeles FC"},
    "la-galaxy": {"name": "LA Galaxy", "sport": "futbol", "league": "MLS", "aka": "Galaxy, LA Galaxy"},
    # NBA
    "lakers": {"name": "Los Angeles Lakers", "sport": "basketball", "league": "NBA", "aka": "Lakers, LA Lakers"},
    "celtics": {"name": "Boston Celtics", "sport": "basketball", "league": "NBA", "aka": "Celtics, Boston"},
    "warriors": {"name": "Golden State Warriors", "sport": "basketball", "league": "NBA", "aka": "Warriors, Dubs, Golden State"},
    "bulls": {"name": "Chicago Bulls", "sport": "basketball", "league": "NBA", "aka": "Bulls, Chicago"},
    "heat": {"name": "Miami Heat", "sport": "basketball", "league": "NBA", "aka": "Heat, Miami"},
    "knicks": {"name": "New York Knicks", "sport": "basketball", "league": "NBA", "aka": "Knicks, NY Knicks"},
    "nuggets": {"name": "Denver Nuggets", "sport": "basketball", "league": "NBA", "aka": "Nuggets, Denver"},
    "bucks": {"name": "Milwaukee Bucks", "sport": "basketball", "league": "NBA", "aka": "Bucks, Milwaukee"},
    # NFL
    "cowboys": {"name": "Dallas Cowboys", "sport": "futbol americano", "league": "NFL", "aka": "Cowboys, Vaqueros, Dallas"},
    "chiefs": {"name": "Kansas City Chiefs", "sport": "futbol americano", "league": "NFL", "aka": "Chiefs, Kansas City"},
    "49ers": {"name": "San Francisco 49ers", "sport": "futbol americano", "league": "NFL", "aka": "49ers, Niners, San Francisco"},
    "eagles": {"name": "Philadelphia Eagles", "sport": "futbol americano", "league": "NFL", "aka": "Eagles, Philadelphia, Águilas"},
    "packers": {"name": "Green Bay Packers", "sport": "futbol americano", "league": "NFL", "aka": "Packers, Green Bay"},
    "steelers": {"name": "Pittsburgh Steelers", "sport": "futbol americano", "league": "NFL", "aka": "Steelers, Pittsburgh, Acereros"},
    # MLB
    "dodgers": {"name": "Los Angeles Dodgers", "sport": "beisbol", "league": "MLB", "aka": "Dodgers, LA Dodgers"},
    "yankees": {"name": "New York Yankees", "sport": "beisbol", "league": "MLB", "aka": "Yankees, Yanquis, NY Yankees"},
    "red-sox": {"name": "Boston Red Sox", "sport": "beisbol", "league": "MLB", "aka": "Red Sox, Medias Rojas, Boston"},
    "astros": {"name": "Houston Astros", "sport": "beisbol", "league": "MLB", "aka": "Astros, Houston"},
    "mets": {"name": "New York Mets", "sport": "beisbol", "league": "MLB", "aka": "Mets, NY Mets"},
    "padres": {"name": "San Diego Padres", "sport": "beisbol", "league": "MLB", "aka": "Padres, San Diego"},
    # NHL
    "bruins": {"name": "Boston Bruins", "sport": "hockey", "league": "NHL", "aka": "Bruins, Boston"},
    # UFC
    "ufc": {"name": "UFC", "sport": "MMA", "league": "UFC", "aka": "UFC, Ultimate Fighting"},
}

@app.get("/equipo/{team_slug}", response_class=HTMLResponse)
async def team_page(request: Request, team_slug: str):
    """Dynamic team page with today's games for that team."""
    # Resolve team info from slug
    team_info = POPULAR_TEAMS.get(team_slug)
    if team_info:
        team_name = team_info["name"]
        team_sport = team_info.get("sport", "")
        team_league_seo = team_info.get("league", "")
        team_aka = team_info.get("aka", team_name)
    else:
        # Fallback for unknown slugs
        clean_slug = team_slug.replace("-", " ")
        resolved = TEAM_ALIASES.get(clean_slug, clean_slug)
        team_name = resolved.title()
        team_sport = ""
        team_league_seo = ""
        team_aka = team_name

    # Search for games
    search_term = TEAM_ALIASES.get(team_slug.replace("-", " "), team_slug.replace("-", " "))
    games = await search_games(search_term)

    # Get team info from first game found
    team_logo = ""
    team_league = ""
    if games:
        for game in games:
            if search_term.lower() in game["home"]["name"].lower():
                team_logo = game["home"].get("logo", "")
                team_league = game.get("league_name", "")
                break
            elif search_term.lower() in game["away"]["name"].lower():
                team_logo = game["away"].get("logo", "")
                team_league = game.get("league_name", "")
                break

    # Fetch team stats (standings, record, etc.)
    stats = await get_team_stats(team_slug)

    # If we got stats but no logo from games, use logo from stats
    if not team_logo and stats.get("team_logo"):
        team_logo = stats["team_logo"]

    return templates.TemplateResponse(request, "team.html", {
        "team_name": team_name,
        "team_slug": team_slug,
        "team_logo": team_logo,
        "team_league": team_league or team_league_seo,
        "team_sport": team_sport,
        "team_aka": team_aka,
        "games": games,
        "stats": stats,
        "format_mx_time": format_mx_time,
    })


@app.get("/equipos", response_class=HTMLResponse)
async def teams_list(request: Request):
    """List all popular teams for SEO indexing."""
    teams_by_league = {
        "Liga MX": ["chivas", "america", "cruz-azul", "pumas", "tigres", "monterrey", "toluca", "santos", "leon", "pachuca", "atlas", "necaxa", "puebla", "queretaro"],
        "Premier League": ["liverpool", "manchester-city", "manchester-united", "arsenal", "chelsea"],
        "La Liga": ["real-madrid", "barcelona"],
        "Serie A": ["juventus", "inter-milan"],
        "Bundesliga": ["bayern"],
        "Ligue 1": ["psg"],
        "NBA": ["lakers", "celtics", "warriors", "bulls", "heat", "knicks"],
        "NFL": ["cowboys", "chiefs", "49ers", "eagles", "packers"],
        "MLB": ["dodgers", "yankees", "red-sox", "astros"],
    }
    return templates.TemplateResponse(request, "teams_list.html", {
        "teams_by_league": teams_by_league,
        "POPULAR_TEAMS": POPULAR_TEAMS,
    })


# ── TikTok OAuth + Content Posting ─────────────────────

@app.get("/tiktok/login")
async def tiktok_login():
    """Redirect to TikTok OAuth authorization."""
    from fastapi.responses import RedirectResponse
    auth_url = get_tiktok_auth_url()
    return RedirectResponse(url=auth_url)


@app.get("/auth/tiktok/callback", response_class=HTMLResponse)
async def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle TikTok OAuth callback after user authorizes."""
    if error:
        return HTMLResponse(f"<h1>Error de autorización TikTok</h1><p>{error}</p>")

    if not code:
        return HTMLResponse("<h1>No se recibió código de autorización</h1>")

    # Exchange code for token
    result = await exchange_code_for_token(code)

    if "access_token" in result:
        user_info = await get_user_info()
        display_name = user_info.get("data", {}).get("user", {}).get("display_name", "Usuario")
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html lang="es">
        <head><meta charset="UTF-8"><title>TikTok Conectado | DondeVer</title>
        <style>
            body {{ font-family: system-ui; background: #0a0a0a; color: #fff; display: flex;
                   justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
            .card {{ background: #1a1a1a; border-radius: 16px; padding: 40px; text-align: center; max-width: 500px; }}
            .success {{ color: #25D366; font-size: 48px; }}
            h1 {{ margin: 16px 0 8px; }}
            .btn {{ display: inline-block; background: #fe2c55; color: #fff; padding: 14px 32px;
                    border-radius: 8px; text-decoration: none; margin-top: 20px; font-weight: 600; }}
            .btn:hover {{ opacity: 0.9; }}
        </style></head>
        <body><div class="card">
            <div class="success">✓</div>
            <h1>TikTok Conectado</h1>
            <p>Cuenta: <strong>{display_name}</strong></p>
            <p>Ahora puedes publicar videos automaticamente.</p>
            <a href="/tiktok/panel" class="btn">Ir al Panel TikTok</a>
        </div></body></html>
        """)
    else:
        error_msg = result.get("error_description", result.get("error", "Error desconocido"))
        return HTMLResponse(f"""
        <h1>Error al conectar TikTok</h1>
        <p>{error_msg}</p>
        <a href="/tiktok/login">Intentar de nuevo</a>
        """)


@app.get("/tiktok/panel", response_class=HTMLResponse)
async def tiktok_panel(request: Request):
    """TikTok management panel — shows status, generate & publish videos."""
    token_info = get_token_info()
    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")

    # Check if today's video exists
    from pathlib import Path
    video_path = Path(f"static/tiktok/dondever_picks_{date_tag}.mp4")
    video_exists = video_path.exists()
    video_url = f"/static/tiktok/dondever_picks_{date_tag}.mp4" if video_exists else None

    # Check images
    images_dir = Path(f"static/tiktok/images/{date_tag}")
    images = sorted([f"/static/tiktok/images/{date_tag}/{f.name}" for f in images_dir.glob("*.png")]) if images_dir.exists() else []

    return templates.TemplateResponse(request, "tiktok_panel.html", {
        "authenticated": token_info["authenticated"],
        "open_id": token_info.get("open_id"),
        "video_exists": video_exists,
        "video_url": video_url,
        "images": images,
        "date_tag": date_tag,
    })


@app.post("/tiktok/publicar")
async def tiktok_publish():
    """Publish today's video to TikTok."""
    if not is_authenticated():
        return JSONResponse({"error": "No conectado a TikTok. Ve a /tiktok/login"}, status_code=401)

    date_tag = datetime.now(TZ_MX).strftime("%Y%m%d")
    video_path = f"static/tiktok/dondever_picks_{date_tag}.mp4"

    from pathlib import Path
    if not Path(video_path).exists():
        # Try generating first
        from tiktok_generator import generate_daily_video
        video_path_gen = await generate_daily_video()
        if not video_path_gen:
            return JSONResponse({"error": "No hay juegos hoy para generar video"}, status_code=404)
        video_path = video_path_gen

    today = datetime.now(TZ_MX)
    title = f"Partidos de hoy {today.strftime('%d/%m')} | Donde verlos en vivo #deportes #futbol #nba #nfl #dondever"

    result = await upload_video_to_tiktok(video_path, title)
    return JSONResponse(result)


@app.get("/tiktok/status/{publish_id}")
async def tiktok_status(publish_id: str):
    """Check publishing status of a video."""
    result = await check_publish_status(publish_id)
    return JSONResponse(result)


# ── Health ───────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "dondever.app", "version": "1.0.0"}


# ── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
