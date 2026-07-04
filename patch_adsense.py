#!/usr/bin/env python3
"""
Patch: agrega Google AdSense a DondeVer.app
1. Inyecta AdSense script via middleware (se agrega a todas las paginas automaticamente)
2. Crea ads.txt
3. Agrega ruta /ads.txt en server.py

Corre con: python3 patch_adsense.py
"""

PUBLISHER_ID = "ca-pub-2576227882415709"

# ═══════════════════════════════════════════════════════════
# 1. Patch server.py — add AdSense to middleware
# ═══════════════════════════════════════════════════════════
with open("server.py", "r") as f:
    code = f.read()

changes = 0

# 1a. Add ADSENSE_PUB_ID env var read in middleware
old_middleware_vars = '''        ga_id = os.getenv("GA_MEASUREMENT_ID", "").strip()
        gads_id = os.getenv("GOOGLE_ADS_ID", "").strip()  # format: AW-XXXXXXXXXXX
        clarity_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
        gtm_id = os.getenv("GTM_CONTAINER_ID", "").strip()
        if not ga_id and not clarity_id and not gtm_id and not gads_id:'''

new_middleware_vars = '''        ga_id = os.getenv("GA_MEASUREMENT_ID", "").strip()
        gads_id = os.getenv("GOOGLE_ADS_ID", "").strip()  # format: AW-XXXXXXXXXXX
        clarity_id = os.getenv("CLARITY_PROJECT_ID", "").strip()
        gtm_id = os.getenv("GTM_CONTAINER_ID", "").strip()
        adsense_id = os.getenv("ADSENSE_PUB_ID", "").strip()  # format: ca-pub-XXXXXXXXXXXXXXXX
        if not ga_id and not clarity_id and not gtm_id and not gads_id and not adsense_id:'''

if old_middleware_vars in code:
    code = code.replace(old_middleware_vars, new_middleware_vars)
    changes += 1
    print("✅ 1a. Variable adsense_id agregada al middleware")
else:
    print("⚠️  1a. Variables del middleware no encontradas (ya parcheado?)")

# 1b. Add AdSense script snippet after Clarity block
old_snippet_encode = '''            snippet = snippet.encode("utf-8")'''

new_snippet_encode = '''            if adsense_id:
                snippet += (
                    f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={adsense_id}"\\n'
                    f'     crossorigin="anonymous"></script>\\n'
                )
            snippet = snippet.encode("utf-8")'''

if old_snippet_encode in code:
    code = code.replace(old_snippet_encode, new_snippet_encode)
    changes += 1
    print("✅ 1b. Script de AdSense agregado al middleware")
else:
    print("⚠️  1b. snippet encode no encontrado (ya parcheado?)")

# 1c. Add /ads.txt route (check if it already exists)
if "/ads.txt" not in code:
    # Add right after robots.txt route
    old_robots = '''@app.get("/robots.txt")'''

    new_robots = '''@app.get("/ads.txt")
async def ads_txt():
    """Serve ads.txt for Google AdSense verification."""
    adsense_pub = os.getenv("ADSENSE_PUB_ID", "ca-pub-2576227882415709")
    content = f"google.com, {adsense_pub}, DIRECT, f08c47fec0942fa0\\n"
    return PlainTextResponse(content)


@app.get("/robots.txt")'''

    if old_robots in code:
        code = code.replace(old_robots, new_robots)
        changes += 1
        print("✅ 1c. Ruta /ads.txt agregada")
    else:
        print("⚠️  1c. Ruta /robots.txt no encontrada para insertar ads.txt")
else:
    print("⚠️  1c. Ruta /ads.txt ya existe")

if changes > 0:
    with open("server.py", "w") as f:
        f.write(code)
    print(f"\n✅ server.py guardado ({changes} cambios)")
else:
    print("\n⚠️  server.py: no se hicieron cambios")


# ═══════════════════════════════════════════════════════════
# 2. Create static/ads.txt (backup — the route serves it dynamically)
# ═══════════════════════════════════════════════════════════
import os
os.makedirs("static", exist_ok=True)
with open("static/ads.txt", "w") as f:
    f.write(f"google.com, {PUBLISHER_ID}, DIRECT, f08c47fec0942fa0\n")
print("✅ 2. static/ads.txt creado")


# ═══════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════
print(f"""
🎯 Listo! Ahora:

1. Agrega esta variable en Render (Environment):
   ADSENSE_PUB_ID = {PUBLISHER_ID}

2. Haz deploy:
   git add server.py static/ads.txt patch_adsense.py
   git commit -m "Add Google AdSense integration"
   git push origin main

3. Verifica que funcione:
   curl https://dondever.app/ads.txt

4. Ve a AdSense y solicita revision.
""")
