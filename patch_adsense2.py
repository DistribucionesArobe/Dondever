#!/usr/bin/env python3
"""
Patch 2: Agrega ruta /ads.txt + fix check-delivery para Content API messages.
Corre con: python3 patch_adsense2.py
"""

with open("server.py", "r") as f:
    code = f.read()

changes = 0

# ═══ 1. Add /ads.txt route before /robots.txt ═══
# Match the ACTUAL decorator (with response_class)
old_robots = '@app.get("/robots.txt", response_class=PlainTextResponse)'

new_robots = '''@app.get("/ads.txt", response_class=PlainTextResponse)
async def ads_txt():
    """Serve ads.txt for Google AdSense verification."""
    pub_id = os.getenv("ADSENSE_PUB_ID", "ca-pub-2576227882415709")
    return f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\\n"


@app.get("/robots.txt", response_class=PlainTextResponse)'''

if "/ads.txt" not in code:
    if old_robots in code:
        code = code.replace(old_robots, new_robots)
        changes += 1
        print("✅ 1. Ruta /ads.txt agregada")
    else:
        print("❌ 1. No encontre ruta /robots.txt para insertar antes")
else:
    print("⚠️  1. Ruta /ads.txt ya existe")


# ═══ 2. Fix check-delivery to show MORE messages and recent ones ═══
old_check = '        messages = client.messages.list(limit=10)'
new_check = '        messages = client.messages.list(limit=20, date_sent_after=datetime.now(timezone.utc) - timedelta(days=3))'

if old_check in code:
    code = code.replace(old_check, new_check)
    changes += 1
    print("✅ 2. check-delivery ahora busca los ultimos 3 dias (20 msgs)")
else:
    print("⚠️  2. check-delivery ya parcheado o no encontrado")


# ═══ Save ═══
if changes > 0:
    with open("server.py", "w") as f:
        f.write(code)
    print(f"\n✅ server.py guardado ({changes} cambios)")
else:
    print("\n⚠️  No se hicieron cambios")

print("""
🎯 Ahora corre:
   git add server.py
   git commit -m "Add ads.txt route + fix check-delivery"
   git push origin main
""")
