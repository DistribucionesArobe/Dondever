# Configuracion: Publicacion Automatica en Instagram

Guia para configurar la publicacion automatica de "Juegos de Hoy" en @dondeverapp.

## Paso 1: Convertir a cuenta Business

1. Abre Instagram → tu perfil → Configuracion
2. Cuenta → Cambiar tipo de cuenta → Cambiar a cuenta profesional
3. Selecciona "Empresa" (Business)
4. Categoria: "Medio de comunicacion" o "Sitio web de deportes"
5. Listo — tu cuenta ahora es Business

## Paso 2: Crear Facebook Page

1. Ve a https://www.facebook.com/pages/create
2. Nombre: "DondeVer.app"
3. Categoria: "Sitio web de deportes y recreacion"
4. Una vez creada, ve a Configuracion de la Page → Instagram
5. Conecta tu cuenta @dondeverapp

## Paso 3: Crear Meta Developer App

1. Ve a https://developers.facebook.com/
2. Inicia sesion con tu cuenta de Facebook
3. Click "Crear App" (My Apps → Create App)
4. Tipo: "Business" (Empresa)
5. Nombre: "DondeVer Instagram Bot"
6. Selecciona tu Business portfolio (o crea uno)

### Agregar producto Instagram:
7. En el dashboard de tu app, click "Add Product"
8. Busca "Instagram" → click "Set Up"
9. Ve a Instagram → Basic Display (o Instagram Graph API)

## Paso 4: Obtener permisos

En tu app de Meta Developer:

1. Ve a App Review → Permissions and Features
2. Solicita estos permisos:
   - `instagram_basic` — leer info de la cuenta
   - `instagram_content_publish` — publicar contenido
   - `pages_show_list` — ver tus Pages
   - `pages_read_engagement` — leer engagement

Nota: Para modo desarrollo (testing), puedes usar estos permisos
sin aprobacion, pero solo funcionan con tu propia cuenta.

## Paso 5: Obtener Access Token

### Opcion A: Graph API Explorer (mas facil para empezar)

1. Ve a https://developers.facebook.com/tools/explorer/
2. Selecciona tu app "DondeVer Instagram Bot"
3. Click "Generate Access Token"
4. Selecciona permisos: `instagram_basic`, `instagram_content_publish`, `pages_show_list`
5. Autoriza con tu cuenta de Facebook
6. Copia el token generado

### Convertir a token de larga duracion (60 dias):
```
curl "https://graph.facebook.com/v21.0/oauth/access_token?\
grant_type=fb_exchange_token&\
client_id=TU_APP_ID&\
client_secret=TU_APP_SECRET&\
fb_exchange_token=TU_TOKEN_CORTO"
```

### Opcion B: Desde el script
```bash
# Despues de tener el token largo:
python post_instagram.py --refresh-token
```

## Paso 6: Obtener tu Instagram User ID

Con tu access token, ejecuta:
```
curl "https://graph.facebook.com/v21.0/me/accounts?access_token=TU_TOKEN"
```

Esto te da el ID de tu Facebook Page. Luego:
```
curl "https://graph.facebook.com/v21.0/TU_PAGE_ID?fields=instagram_business_account&access_token=TU_TOKEN"
```

El campo `instagram_business_account.id` es tu INSTAGRAM_USER_ID.

## Paso 7: Configurar variables de entorno

### En tu maquina local (para testing):
```bash
export INSTAGRAM_USER_ID="123456789"
export INSTAGRAM_ACCESS_TOKEN="EAAxxxxxx..."
export META_APP_ID="tu_app_id"
export META_APP_SECRET="tu_app_secret"
```

### En Render (para produccion):
1. Ve a tu dashboard de Render → tu servicio DondeVer
2. Environment → Add Environment Variable
3. Agrega las 4 variables de arriba

## Paso 8: Probar

```bash
# Primero prueba sin publicar:
python post_instagram.py --dry-run

# Si todo se ve bien, publica:
python post_instagram.py
```

## Paso 9: Automatizar (Cron Job)

### Opcion A: Render Cron Job
1. En Render, crea un nuevo "Cron Job"
2. Repo: el mismo de DondeVer
3. Comando: `python post_instagram.py`
4. Schedule: `0 13 * * *` (7:00 AM Mexico = 1:00 PM UTC)
5. Agrega las mismas variables de entorno

### Opcion B: Cron en tu Mac
```bash
crontab -e
# Agrega esta linea (7:00 AM CDMX):
0 7 * * * cd ~/Desktop/Trabajo/Proyectos/Donde\ ver && python3 post_instagram.py >> logs/instagram.log 2>&1
```

## Renovar Token (cada 60 dias)

El token de larga duracion expira en 60 dias. Para renovarlo:
```bash
python post_instagram.py --refresh-token
```

Esto genera un nuevo token y te dice como actualizarlo.
Recomendacion: pon un recordatorio cada 50 dias para renovar.

## Troubleshooting

**Error 190: Invalid access token**
→ Tu token expiro. Ejecuta `--refresh-token` o genera uno nuevo en Graph API Explorer.

**Error: OAuthException**
→ Verifica que tu app tenga los permisos correctos y que la cuenta de Instagram este conectada a la Facebook Page.

**Error 400: Image URL not accessible**
→ La imagen debe estar en una URL publica (https://dondever.app/static/instagram/...). Verifica que el servidor este corriendo.

**No games found**
→ Algunos dias no hay juegos en ninguna liga. El script no publica nada esos dias.
