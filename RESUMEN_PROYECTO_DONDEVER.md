# DondeVer.app — Resumen Completo del Proyecto

## Qué es

**DondeVer** (https://dondever.app) es una guía de streaming deportivo en español para México, USA y Latinoamérica. Responde la pregunta "¿dónde puedo ver este partido?" mostrando canales, horarios por zona, marcadores en vivo, pronósticos y enlaces de afiliado geo-segmentados.

**Repo**: https://github.com/DistribucionesArobe/Dondever.git (301 commits al 7 sep 2026)
**Deploy**: Render.com (render.yaml incluido)
**Dominio**: dondever.app

---

## Stack Técnico

- **Backend**: Python 3.10 + FastAPI + Uvicorn
- **Templates**: Jinja2 (30+ templates HTML)
- **Base de datos**: PostgreSQL async (asyncpg + SQLAlchemy async)
- **APIs externas**: ESPN public API (schedules/scores), TheSportsDB Premium (broadcasts/enrichment), The Odds API (betting odds)
- **Cache**: cachetools TTLCache (en memoria)
- **OG Images**: Pillow (generación dinámica)
- **PWA**: manifest.json + service worker
- **WhatsApp**: Twilio (legacy) + Meta Cloud API (actual)
- **Social**: Twitter/X bot (tweepy), TikTok, Instagram generator
- **Email**: Propio (email_subscribers.py + send_email_daily.py)

### Dependencias (requirements.txt)
```
fastapi==0.115.0, uvicorn[standard]==0.30.0, httpx==0.27.0, asyncpg==0.29.0,
sqlalchemy[asyncio]==2.0.35, alembic==1.13.0, python-dotenv==1.0.1, twilio==9.3.0,
apscheduler==3.10.4, jinja2==3.1.4, python-multipart==0.0.9, cachetools==5.5.0,
tweepy==4.14.0, Pillow==10.4.0, playwright==1.52.0
```

---

## Estructura de Archivos

### Python (backend)
| Archivo | Líneas | Función |
|---------|--------|---------|
| `server.py` | 5,617 | App principal FastAPI, 70+ rutas, lógica de negocio |
| `config.py` | 1,363 | Configuración: afiliados, ligas, canales, aliases, STREAMING_AFFILIATES |
| `sports_api.py` | 2,106 | Fetcher de datos ESPN + TheSportsDB + odds |
| `db.py` | 238 | Capa de persistencia PostgreSQL async |
| `og_image.py` | — | Generador de imágenes OpenGraph con Pillow |
| `game_card.py` | — | Lógica de game cards |
| `user_favs.py` | — | Sistema de favoritos (equipos + ligas) |
| `send_whatsapp_daily.py` | — | Resumen diario por WhatsApp (Meta Cloud API) |
| `send_email_daily.py` | — | Resumen diario por email |
| `twitter_bot.py` | — | Bot de Twitter/X |
| `tiktok_generator.py` | — | Generador de contenido TikTok |
| `generate_instagram.py` | — | Generador de imágenes Instagram |
| `whatsapp_bot.py` | — | Bot conversacional WhatsApp |
| `whatsapp_broadcast.py` | — | Broadcast masivo WhatsApp |
| `push_notifications.py` | — | Push notifications (OneSignal) |
| `subscribers.py` | — | Gestión de suscriptores |
| `email_subscribers.py` | — | Suscriptores email |

### Templates principales (templates/)
| Template | Líneas | Página |
|----------|--------|--------|
| `index.html` | 3,508 | Homepage — partidos del día, filtros por deporte, favoritos, "Lo imperdible", "Gratis hoy" |
| `game.html` | 1,590 | Detalle de partido — canales, H2H, alineaciones, odds, previa editorial, post-game |
| `team.html` | 1,161 | Página de equipo — próximos partidos, standings, forma, calendario |
| `league.html` | 661 | Página de liga — próxima jornada, standings, calendario |
| `matchup.html` | 790 | Página "Dónde ver X vs Y" (SEO) |
| `sport_today.html` | 505 | Hub de deporte: /futbol-hoy, /beisbol-hoy, etc. |
| `casinos.html` | 360 | Comparador de casas de apuestas |
| `streaming.html` | 354 | Guía de plataformas streaming |
| `gratis_hoy.html` | 246 | Partidos gratis hoy |
| `canal.html` | 215 | Página individual de canal |
| `country.html` | 241 | "Dónde ver en [país]" |
| `team_country.html` | 225 | "Dónde ver [equipo] en [país]" |
| `team_calendar.html` | 165 | Calendario semanal del equipo |
| `recap.html` | 222 | Resumen post-partido |
| `pronosticos.html` | 186 | Pronósticos del día |
| `teams_list.html` | 149 | Índice de todos los equipos |
| `canales_index.html` | 136 | Índice de canales |

### Guías SEO (templates/guides/)
28 guías estáticas tipo "Cómo ver [liga/canal] en México", "Dónde ver [deporte] en [país]", "Mejores apps/streaming/casas de apuestas"

### Static
- `timezone.js` (300 líneas) — Geo-detección por timezone, toggle dark mode, favoritos localStorage, auto-refresh scores
- `dark-mode.js` / `dark-mode.css` — Dark mode
- `manifest.json` — PWA manifest
- `affiliates/` — SVGs de logos de afiliados
- Iconos PWA (192px, 512px)

---

## Rutas Principales (server.py)

### Páginas públicas
```
GET  /                          → Homepage (partidos del día)
GET  /?date=YYYYMMDD            → Partidos de fecha específica
GET  /partido/{slug}            → Detalle de partido (slug semántico)
GET  /juego/{event_id}          → Redirect 301 → /partido/{slug}
GET  /game/{old_id}             → Redirect legacy
GET  /liga/{league_slug}        → Página de liga
GET  /equipo/{team_slug}        → Página de equipo
GET  /equipo/{slug}/calendario  → Calendario semanal
GET  /equipo/{slug}/en/{country}→ Equipo en país específico
GET  /resultado/{slug}          → Recap post-partido
GET  /equipos                   → Índice de todos los equipos
GET  /canales                   → Índice de canales
GET  /canal/{channel_slug}      → Página de canal individual
GET  /donde-ver/{matchup_slug}  → "Dónde ver X vs Y"
GET  /donde-ver-en-{country}    → "Dónde ver en [país]"
GET  /gratis-hoy                → Partidos gratuitos hoy
GET  /futbol-hoy                → Hub fútbol
GET  /futbol-americano-hoy      → Hub NFL
GET  /basquetbol-hoy            → Hub NBA
GET  /beisbol-hoy               → Hub MLB
GET  /hockey-hoy                → Hub NHL
GET  /pronosticos-hoy           → Pronósticos/picks
GET  /momios-hoy                → Redirect → /pronosticos-hoy
GET  /apuestas-deportivas-hoy   → Redirect → /pronosticos-hoy
GET  /nfl-hoy                   → Redirect → /liga/nfl
GET  /nfl-en-vivo               → Redirect → /liga/nfl
GET  /streaming                 → Guía de plataformas
GET  /casinos                   → Comparador casas de apuestas
GET  /guia/{guide_slug}         → Guías SEO estáticas
GET  /sobre-nosotros            → About (E-E-A-T)
GET  /privacidad                → Política de privacidad
GET  /terminos                  → Términos de uso
```

### Afiliados
```
GET  /go/{key}                  → Smart redirect afiliado (geo-targeted)
     keys: bet, betsson, vpn, cj, amazon, surfshark, jubilee, vivento, 1xbet,
           espnplus, peacock, paramount, appletv, mlbtv, nflplus, max, disneyplus
```

### API endpoints
```
GET  /api/games?date=           → JSON de partidos
GET  /api/live-scores           → Marcadores en vivo
GET  /api/leagues               → Lista de ligas
GET  /api/team/{slug}           → Quick team data
GET  /api/instagram-image       → Imagen para Instagram
```

### OG Images
```
GET  /og/partido/{slug}.png     → OG image dinámica para partido
GET  /og/equipo/{slug}.png      → OG image dinámica para equipo
```

### SEO/Technical
```
GET  /sitemap.xml               → Sitemap dinámico (todas las rutas)
GET  /robots.txt                → Robots.txt
GET  /ads.txt                   → AdSense ads.txt
GET  /manifest.json             → PWA manifest
GET  /pwa-sw.js                 → Service worker
GET  /health                    → Health check
```

### Admin/Internal
```
GET  /admin/dashboard           → Dashboard admin (token-protected)
GET  /admin/subscribers         → Lista de suscriptores
GET  /admin/push-test           → Test push notification
GET  /admin/push-summary        → Enviar resumen push
```

### WhatsApp
```
POST /webhook/whatsapp          → Webhook Twilio
GET  /webhook/whatsapp          → Verificación Twilio
POST /webhook/meta-whatsapp     → Webhook Meta Cloud API
GET  /webhook/meta-whatsapp     → Verificación Meta
POST /api/whatsapp/subscribe    → Suscribir número
GET  /whatsapp/broadcast-now    → Enviar broadcast
GET  /whatsapp/broadcast-preview→ Preview del broadcast
GET  /whatsapp/broadcast-status → Estado del broadcast
POST /whatsapp/broadcast-to     → Broadcast a número específico
GET  /whatsapp/debug            → Debug WhatsApp
```

### Social/TikTok/Twitter
```
GET  /tiktok/hoy                → Generar contenido TikTok
GET  /tiktok/generar            → Generar imágenes TikTok
GET  /tiktok/login              → OAuth TikTok
GET  /auth/tiktok/callback      → Callback OAuth
GET  /tiktok/panel              → Panel de gestión TikTok
POST /tiktok/publicar           → Publicar en TikTok
GET  /twitter/debug             → Debug Twitter
POST /twitter/test-tweet        → Test tweet
POST /twitter/trigger/{job}     → Disparar job de Twitter
```

### Email
```
POST /api/email-subscribe       → Suscribir email
GET  /email-unsubscribe         → Desuscribir email
```

### Favoritos
```
POST /api/favs/save             → Guardar favoritos
GET  /api/favs/load             → Cargar favoritos
```

---

## Configuración Clave (config.py)

### AFFILIATES (7 afiliados de apuestas/VPN)
```python
AFFILIATES = {
    "betsson":  { url: record.betsson.mx/...,  bonus: "$3,000 bono + $100 freebet" },  # US
    "cj":       { url: anrdoezrs.net/...,       name: "NordVPN" },                      # VPN
    "amazon":   { url: amazon.com/...tag=dondever2000-20 },                              # Streaming
    "surfshark": { url: jdoqocy.com/... },                                               # VPN
    "jubilee":  { url: jubilee.mx/... },                                                 # MX
    "vivento":  { url: vivento.mx/... },                                                 # MX
    "1xbet":    { url: reffpa.com/... },                                                 # LATAM/resto
}
# Geo-routing: MX → Jubilee/Vivento, US → Betsson, LATAM → 1xBet
```

### STREAMING_AFFILIATES (13 plataformas de streaming)
```
Prime Video, ViX, ESPN+, Peacock, Paramount+, Apple TV+, MLS Season Pass,
MLB.TV, NFL+, Max, Disney+, ESPN MX
```
Cada una con: key, aliases, url (env var override), CTA, countries, bg/color, is_affiliate flag.

### LEAGUES (47 ligas activas + 5 individuales)
Formato tuple: `(sport_type, espn_slug, display_name, emoji/label)`

**Fútbol (36)**: Liga MX, MLS, Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions, Europa League, Liga Portugal, Eredivisie, Concacaf CL, Copa América, World Cup, Liga Colombia, Liga Argentina, Liga Ecuador, Liga Panamá, Liga Chile, Liga Perú, Libertadores, Sudamericana, Copa del Rey, FA Cup, Carabao Cup, DFB-Pokal, Coppa Italia, Coupe de France, US Open Cup, Copa Argentina, Leagues Cup, Club World Cup, Euro, Copa Oro, Eliminatorias CONMEBOL/CONCACAF, Nations League UEFA/CONCACAF, Amistosos

**Americano (2)**: NFL (sport_type="football"), College Football (sport_type="college-football")
**Basquetbol (2)**: NBA, WNBA
**Béisbol (1)**: MLB
**Hockey (1)**: NHL
**Combate (1)**: UFC

**Individuales** (no aparecen en homepage): F1, NASCAR, ATP, WTA, PGA Tour

### TEAM_SHOP_MELI (130+ equipos)
Links de afiliado MercadoLibre (meli.la) por equipo para jerseys. Cubre Liga MX (17), Europa (17), MLS (6), NBA (16), NFL (31), MLB (30), NHL (11), Premier League restantes, La Liga restantes.

### PAYING_BOOKMAKER_KEYS
Solo se muestran odds de: `{"betsson", "1xbet", "jubilee", "vivento"}`

### Geo-targeting (timezone.js)
Detección JS vía `Intl.DateTimeFormat().resolvedOptions().timeZone`:
- Timezone contiene "Mexico" → `data-geo="mx"`
- Timezone en lista US → `data-geo="us"`
- Resto → `data-geo="latam"`
Elementos con `data-geo` se muestran/ocultan según la geo del usuario.

---

## Base de Datos (db.py)

PostgreSQL async con SQLAlchemy. Almacena cada juego visto de ESPN para que las páginas de equipo/liga siempre tengan datos históricos, incluso cuando la API de hoy no devuelve nada.

```python
engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=5)
```

---

## Flujo de Datos

1. **ESPN API** → `sports_api.py` fetch de schedules por liga (ESPN_BASE + sport/league/scoreboard)
2. **TheSportsDB Premium** → Enrichment de broadcasts (canales TV) por evento
3. **The Odds API** → Betting odds de bookmakers
4. **Cache en memoria** → TTLCache por ruta (evita re-fetch en cada request)
5. **PostgreSQL** → Persistencia histórica de juegos vistos
6. **Jinja2 templates** → Renderizado server-side con datos enriquecidos
7. **timezone.js** → Geo-detección client-side, ajuste de contenido por región

---

## Funcionalidades Implementadas (189 tareas completadas)

### Core
- Partidos del día con canales, horarios, marcadores en vivo
- Páginas de detalle de partido con H2H, alineaciones, previa editorial, odds
- Páginas de equipo con standings, forma (últimos 5), calendario
- Páginas de liga con próxima jornada, tabla de posiciones
- Slugs semánticos (/partido/cowboys-vs-eagles-nfl-2026-09-07)
- Deduplicación de partidos en portada

### UX/UI
- Dark mode
- PWA (installable)
- Búsqueda autocomplete con favoritos
- Favoritos de equipos y ligas (localStorage)
- "Lo imperdible" — Top 5 partidos del día (scoring algorithm)
- "Gratis hoy" — Partidos en canales abiertos MX
- Game cards estilo Apple Sports (compactas)
- Personalización: ligas favoritas ordenan primero
- Marcadores en vivo con auto-refresh
- Post-game: "¿Qué sigue?" con próximos partidos de cada equipo

### SEO
- Sitemap XML dinámico (todas las rutas)
- Schema.org SportsEvent en home + game
- OG images dinámicas (Pillow)
- hreflang tags LATAM
- 28 guías SEO estáticas
- Páginas "Dónde ver [equipo] en [país]"
- Recaps post-partido
- Breadcrumbs
- Cross-linking equipo↔liga↔canal↔partido
- noindex para ?date= no-hoy

### Monetización
- 7 afiliados de apuestas/VPN con smart redirect geo-targeted (/go/{key})
- 13 STREAMING_AFFILIATES con CTAs por plataforma
- 130+ links MercadoLibre por equipo (jerseys)
- AdSense integrado
- Odds comparison con solo bookmakers que pagan
- Banners geo-segmentados (MX vs US vs LATAM)
- Admitad: aplicado a Disney+ LATAM y Amazon Prime Video LATAM (PENDIENTE aprobación)

### Social/Notificaciones
- WhatsApp bot + broadcast diario (Meta Cloud API)
- Twitter/X bot automático
- TikTok content generator + publisher
- Instagram image generator
- Email daily digest
- Push notifications (OneSignal)

---

## Variables de Entorno Importantes

```
DATABASE_URL          → PostgreSQL connection string
SPORTSDB_API_KEY      → TheSportsDB premium key (default: 154704)
APP_URL               → https://dondever.app
SECRET_KEY            → Admin auth
TWILIO_ACCOUNT_SID    → Twilio (legacy WhatsApp)
TWILIO_AUTH_TOKEN
TWILIO_WHATSAPP_NUMBER
AFFILIATE_BETSSON     → Override URLs de afiliados
AFFILIATE_JUBILEE
AFFILIATE_1XBET
AFFILIATE_AMAZON
AFFILIATE_DISNEYPLUS  → Pendiente: Admitad deeplink cuando sea aprobado
AFFILIATE_VIX
AFFILIATE_ESPNPLUS
AFFILIATE_PARAMOUNT
AFFILIATE_APPLETV
```

---

## Pendientes / Siguiente Pasos

1. **Admitad affiliate links**: Cuando Disney+ LATAM y Amazon Prime Video LATAM sean aprobados en store.admitad.com (Ad Space "Donde Ver", ID 2992605), actualizar AFFILIATE_DISNEYPLUS y crear AFFILIATE_PRIMEVIDEO en .env con los deeplink URLs de Admitad.

2. **Verificación de canales**: Los canales de transmisión vienen de TheSportsDB y a veces faltan datos para México. El template ya muestra "por confirmar" cuando no hay canales MX.

3. **Affiliates Jubilee/Vivento**: Son casas MX pequeñas que no aparecen en The Odds API, pero están incluidas en PAYING_BOOKMAKER_KEYS para que se muestren cuando/si aparecen.

4. **Optimización continua**: El servidor corre ~5,600 líneas en server.py. Considerar modularizar en routers FastAPI si crece más.

---

## Notas Técnicas para Claude

- **LEAGUE_CONFIG tuple**: `LEAGUES["nfl"] = ("football", "nfl", "NFL", "NFL")` → `(sport_type, espn_slug, display_name, emoji)`
- **sport_counts**: Dict en server.py (línea 657) que cuenta juegos por sport_type para los chips de filtro en la home
- **college-football tiene sport_type propio**: Se separó de "football" para que el chip NFL no cuente partidos universitarios
- **Canales pueden ser dict o string**: Template team.html tiene check `is mapping` porque a veces llegan como `{"name": "NBC", "country": "US"}` y otras como string plano
- **Geo-targeting es JS-only**: No hay geo server-side; todo se resuelve en el cliente con data-geo attributes
- **El filtro Jinja2 `game_url` es un custom filter**: Registrado at runtime en server.py, no en config. Si haces lint del template por separado dará error, es esperado.
- **Ads en homepage**: Cada 4ª liga hay un banner de afiliado. El counter `league_idx` debe estar DENTRO del guard `{% if deduped_games %}` para no dispararse en ligas vacías.
- **El /go/bet smart redirect**: Detecta geo del request y redirige a Jubilee (MX), Betsson (US), o 1xBet (LATAM)
