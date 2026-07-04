#!/usr/bin/env python3
"""
Patch script: aplica el fix del broadcast (template first).
Corre con: python3 patch_broadcast.py
"""
import re

# === PATCH whatsapp_broadcast.py ===
with open("whatsapp_broadcast.py", "r") as f:
    code = f.read()

old_block = '''            # Strategy: try freeform first (richer message), fall back to template
            try:
                msg = client.messages.create(
                    body=message_text,
                    from_=from_number,
                    to=to_number,
                )
            except Exception as freeform_err:
                freeform_detail = str(freeform_err)
                # If freeform fails (outside 24h window), try template
                if CONTENT_SID and ("63016" in freeform_detail or "63032" in freeform_detail or "21408" in freeform_detail):
                    import json as _json
                    logger.info(f"Freeform failed for {to_number}, trying template...")
                    msg = client.messages.create(
                        content_sid=CONTENT_SID,
                        content_variables=_json.dumps({"1": template_summary}),
                        from_=from_number,
                        to=to_number,
                    )
                else:
                    raise freeform_err  # re-raise if not a 24h window issue'''

new_block = '''            # Strategy: use Content Template FIRST for broadcasts (works outside 24h window).
            # Freeform messages get accepted by Twilio but silently fail delivery
            # when the user hasn't messaged in 24h — the error is async and we never see it.
            if CONTENT_SID:
                import json as _json
                try:
                    msg = client.messages.create(
                        content_sid=CONTENT_SID,
                        content_variables=_json.dumps({"1": template_summary}),
                        from_=from_number,
                        to=to_number,
                    )
                    logger.info(f"Template msg sent to {to_number}")
                except Exception as tmpl_err:
                    # Template failed — try freeform as fallback (user might be in 24h window)
                    logger.warning(f"Template failed for {to_number}: {tmpl_err}, trying freeform...")
                    msg = client.messages.create(
                        body=message_text,
                        from_=from_number,
                        to=to_number,
                    )
                    logger.info(f"Freeform fallback sent to {to_number}")
            else:
                # No template configured — freeform only (works within 24h window)
                msg = client.messages.create(
                    body=message_text,
                    from_=from_number,
                    to=to_number,
                )'''

if old_block in code:
    code = code.replace(old_block, new_block)
    with open("whatsapp_broadcast.py", "w") as f:
        f.write(code)
    print("✅ whatsapp_broadcast.py parcheado")
else:
    print("⚠️  whatsapp_broadcast.py: bloque no encontrado (ya parcheado?)")


# === PATCH server.py ===
with open("server.py", "r") as f:
    code = f.read()

changes_made = 0

# 1. Add _last_broadcast dict + change broadcast-now to accept GET + add broadcast-status + check-delivery
old_broadcast_now = '''@app.post("/whatsapp/broadcast-now")
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


@app.post("/whatsapp/broadcast-to")'''

new_broadcast_now = '''# Store last broadcast result for diagnostics
_last_broadcast = {"ran_at": None, "result": None, "error": None}


@app.api_route("/whatsapp/broadcast-now", methods=["GET", "POST"])
async def whatsapp_broadcast_now(token: str = ""):
    """Disparar el broadcast diario ahora mismo a todos los suscriptores.
    Acepta GET y POST para compatibilidad con cron externos (cron-job.org, etc.)."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token or token != admin_token:
        return {"ok": False, "error": "token invalido"}
    try:
        from whatsapp_broadcast import send_daily_broadcast
        result = await send_daily_broadcast()
        _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
        _last_broadcast["result"] = result
        _last_broadcast["error"] = None
        return {"ok": True, "result": result}
    except Exception as e:
        _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
        _last_broadcast["result"] = None
        _last_broadcast["error"] = str(e)
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.get("/whatsapp/broadcast-status")
async def whatsapp_broadcast_status():
    """Ver el resultado del ultimo broadcast (sin auth)."""
    from subscribers import get_active_subscribers
    from whatsapp_broadcast import CONTENT_SID
    active = get_active_subscribers()
    return {
        "last_broadcast": _last_broadcast,
        "active_subscribers": len(active),
        "content_template_configured": bool(CONTENT_SID),
        "hint": "Sin CONTENT_SID, los broadcasts solo llegan a usuarios que mandaron msg en las ultimas 24h."
    }


@app.get("/whatsapp/check-delivery")
async def whatsapp_check_delivery():
    """Verifica el estado de entrega de los ultimos mensajes enviados por Twilio."""
    from whatsapp_broadcast import get_twilio_client
    client = get_twilio_client()
    if not client:
        return {"ok": False, "error": "Twilio no configurado"}
    try:
        messages = client.messages.list(limit=10)
        results = []
        for m in messages:
            results.append({
                "sid": m.sid[:12] + "...",
                "to": m.to,
                "status": m.status,
                "error_code": m.error_code,
                "error_message": m.error_message,
                "date_sent": str(m.date_sent) if m.date_sent else None,
                "date_created": str(m.date_created),
                "direction": m.direction,
            })
        return {"ok": True, "messages": results}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.post("/whatsapp/broadcast-to")'''

if old_broadcast_now in code:
    code = code.replace(old_broadcast_now, new_broadcast_now)
    changes_made += 1
    print("✅ server.py: broadcast-now + status + check-delivery parcheado")
else:
    print("⚠️  server.py: broadcast-now bloque no encontrado (ya parcheado?)")

# 2. Wrap send_daily_broadcast with tracking + add keep-alive
old_scheduler = '''    from whatsapp_broadcast import send_daily_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():'''

new_scheduler = '''    from whatsapp_broadcast import send_daily_broadcast as _raw_broadcast
    from tiktok_generator import generate_daily_video, generate_daily_images
    from whatsapp_alerts import send_pregame_alerts
    from apscheduler.triggers.interval import IntervalTrigger

    async def _tracked_broadcast():
        """Wrapper that logs broadcast results for diagnostics."""
        try:
            result = await _raw_broadcast()
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = result
            _last_broadcast["error"] = None
            _last_broadcast["source"] = "scheduler"
            logger.info(f"Scheduled broadcast completed: {result}")
        except Exception as e:
            _last_broadcast["ran_at"] = datetime.now(TZ_MX).isoformat()
            _last_broadcast["result"] = None
            _last_broadcast["error"] = str(e)
            _last_broadcast["source"] = "scheduler"
            logger.error(f"Scheduled broadcast FAILED: {e}")

    async def _keep_alive_ping():
        """Ping self every 13 min to prevent Render free-tier sleep."""
        import urllib.request
        try:
            ping_url = os.getenv("RENDER_EXTERNAL_URL", "https://dondever.app")
            urllib.request.urlopen(f"{ping_url}/health", timeout=10)
        except Exception:
            pass

    scheduler = AsyncIOScheduler()

    @app.on_event("startup")
    async def start_scheduler():'''

if old_scheduler in code:
    code = code.replace(old_scheduler, new_scheduler)
    changes_made += 1
    print("✅ server.py: tracked broadcast + keep-alive parcheado")
else:
    print("⚠️  server.py: scheduler bloque no encontrado (ya parcheado?)")

# 3. Replace send_daily_broadcast with _tracked_broadcast in scheduler job
old_job = '''        scheduler.add_job(
            send_daily_broadcast,
            CronTrigger(hour=15, minute=0),
            id="whatsapp_daily_broadcast",'''

new_job = '''        scheduler.add_job(
            _tracked_broadcast,
            CronTrigger(hour=15, minute=0),
            id="whatsapp_daily_broadcast",'''

if old_job in code:
    code = code.replace(old_job, new_job)
    changes_made += 1
    print("✅ server.py: scheduler job usa _tracked_broadcast")
else:
    print("⚠️  server.py: scheduler job no encontrado (ya parcheado?)")

# 4. Add keep-alive ping job before scheduler.start()
old_start = '''        logger.info("TikTok video generation scheduled at 7:30 AM MX")

        scheduler.start()
        logger.info("Scheduler started")'''

new_start = '''        logger.info("TikTok video generation scheduled at 7:30 AM MX")

        # Keep-alive ping every 13 min (prevents Render free-tier sleep at 15 min)
        scheduler.add_job(
            _keep_alive_ping,
            IntervalTrigger(minutes=13),
            id="keep_alive_ping",
            name="Keep-alive self-ping",
            replace_existing=True,
        )
        logger.info("Keep-alive ping scheduled every 13 min")

        scheduler.start()
        logger.info("Scheduler started")'''

if old_start in code:
    code = code.replace(old_start, new_start)
    changes_made += 1
    print("✅ server.py: keep-alive job agregado")
else:
    print("⚠️  server.py: scheduler.start() bloque no encontrado (ya parcheado?)")

if changes_made > 0:
    with open("server.py", "w") as f:
        f.write(code)
    print(f"\n✅ server.py guardado ({changes_made} cambios)")
else:
    print("\n⚠️  server.py: no se hicieron cambios")

print("\n🎯 Listo! Ahora corre:")
print("   git add server.py whatsapp_broadcast.py")
print('   git commit -m "Fix broadcast: template first"')
print("   git push origin main")
