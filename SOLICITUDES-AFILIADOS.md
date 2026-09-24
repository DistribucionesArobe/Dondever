# Paquete de solicitudes de afiliado — DondeVer.app

Preparado el 24 de septiembre de 2026, el día que Betsson terminó el programa.

Todo lo que aquí dice **"copiar"** está listo para pegar tal cual. Los datos
salen de Search Console y GA4 de hoy, así que son reales y verificables si
alguien pregunta.

---

## 0. Antes de empezar: el hallazgo que motivó esto

De los 12 servicios de streaming que el sitio recomienda, **once son enlaces
normales**. Solo Prime Video (vía Admitad) paga algo.

| Servicio | Estado hoy |
|---|---|
| Prime Video | ✅ Admitad |
| ViX, ESPN+, Peacock, Paramount+, Apple TV+, MLS Season Pass, MLB.TV, NFL+, Max, Disney+, ESPN MX | ❌ enlace normal, cero comisión |

Cada vez que alguien busca "dónde ver Dodgers", el sitio responde "MLB.TV",
la persona hace clic y no se gana nada. Eso pasa en **todas** las páginas de
equipo, liga y partido: es literalmente lo que el sitio hace.

Un CTA de apuestas es un añadido lateral. El enlace al canal **es la respuesta
que la persona vino a buscar**. Tiene más intención y hasta hoy vale cero.

---

## 1. Lo primero, y es gratis: buscar en las cuentas que ya tienes

El catálogo público de las redes no dice qué anunciantes tienen disponibles en
México. Eso solo se ve entrando. Y tú ya tienes dos cuentas.

### CJ (Commission Junction) — REVISADA A FONDO EL 24/09/2026

**Conclusión: CJ no sirve para lo que necesitamos.** Ya no hay que buscar más
ahí. Esto es lo que se revisó y lo que salió.

**Corrección importante:** la versión anterior de este documento decía "Sling TV
corre su programa en CJ — verificado". **Era falso.** Se buscó `sling` en el
catálogo completo de 2,847 anunciantes y los únicos resultados son una marca de
zapatos y una de mochilas. Sling TV no está en CJ. Esa línea salió de una
búsqueda web, no del panel, y no debió escribirse como verificada.

**Búsquedas por nombre en el catálogo (2,847 anunciantes, cuenta 7865318):**

| Marca | Resultado |
|---|---|
| Sling | ❌ no existe |
| DAZN | ❌ no existe |
| Fubo | ❌ no existe |
| ESPN | ❌ no existe |
| Paramount | ❌ solo SkyShowTime (su empresa conjunta en Europa) |

**Categorías Television + Videos/Movies + Entertainment + Professional Sports
Organizations: 71 anunciantes en total.** Los únicos con relación real con ver
deportes:

| Anunciante | Mercado | Pago |
|---|---|---|
| DIRECTV | EE.UU. | $110–150 USD por venta |
| SkyShowTime ES | **España** | 4.60 EUR por venta |
| Starz | EE.UU. | $15 USD por lead |
| New England Sports Network | EE.UU. (regional) | $5 USD |
| Frndly TV | EE.UU. | $10 USD |
| Plex | global | 10% |

**Cero para México, Venezuela, Panamá, Colombia y República Dominicana** —
que son más del 80% del tráfico. La búsqueda `deportes` devuelve cuatro
resultados y ninguno es televisión. La búsqueda `mexico` devuelve hoteles,
vuelos y Temu.

**Lo único que valdría la pena de CJ:**
- **SkyShowTime ES** — es el único que coincide con un mercado real nuestro
  (España: 233 clics en 28 días, +183%). Falta verificar si SkyShowTime
  transmite deporte en España; si no transmite, tampoco sirve.
- **DIRECTV** — $110–150 por venta es el pago más alto de toda la lista, y el
  tráfico de EE.UU. (313 clics) es hispanohablante. Falta verificar si el
  programa acepta tráfico en español.

### Lo que ya está aprobado y nadie está usando

La cuenta tiene **18 anunciantes activos** que nunca se han puesto en el sitio:
NordVPN, NordPass, Surfshark, Wondershare, SwitchBot, aloSIM, Click & Grow,
FlowerDelivery, GearUP, Personalabs, Puma Golf, Abt, MFI Medical, Jalbum,
PandaHall, HideMy.Name, CarmelLimo, Skytours, Abracadabra NYC.

Ninguno encaja con "dónde ver el partido". Los VPN son el único que roza el
tema, y solo con la promesa de "ver partidos bloqueados en tu país" — que es
justo la clase de afirmación que el sitio no puede hacer sin verificarla.

### Las 9 solicitudes pendientes — ninguna sirve

ACDSee, Dashlane, DICK'S Sporting Goods, Golf Galaxy, Jomashop, SkyTrak Golf,
Sundek, TGW (golf) y Withings. Cuatro son de golf, todas venden producto físico
en EE.UU., ninguna es de televisión. Aunque las aprobaran mañana no habría
dónde poner el enlace.

### Admitad — REVISADA A FONDO EL 24/09/2026. **Aquí sí hay algo.**

Se leyeron los **775 anunciantes** del catálogo de la cuenta (ad space
"Donde Ver", id 2992605) y se buscó marca por marca. Resultado:

**De los 11 servicios sin monetizar, solo uno tiene programa disponible:
Disney+. Y resulta ser el que más importa.**

En México, Disney+ es donde están ESPN, Liga MX, NFL y Champions. Es la
respuesta que da el sitio en una parte enorme de sus páginas. No es un
afiliado más: es *el* afiliado.

Hay **dos programas**, los dos con `can_connect: true` (o sea, se puede
solicitar hoy). Cifras sacadas del panel, no de un blog:

| | Disney+ LATAM | DisneyPlus Many GEOs | *Prime Video (el que ya tienes)* |
|---|---|---|---|
| Comisión | $1.69 – $21.18 USD | **$17.50 USD fijos** | *2.10%* |
| Conversión | 0.6% | **3.8%** | *0.1%* |
| Aprobación de ventas | 94% | 88% | *100%* |
| Días de pago | 40 | 63 | *78* |
| Deeplink | **Sí** | No | No |
| Moderación | manual | manual | — |
| ID | 34712 | 147283 | — |

Léelo bien: **DisneyPlus Many GEOs convierte 38 veces mejor que Prime
Video**, y paga fijo. Prime Video convierte 0.1% a 2.1% de comisión —
prácticamente nada. Disney+ LATAM convierte 6 veces mejor que Prime y
además permite deeplink, que es lo que nos deja mandar a la persona a la
página concreta del partido en vez de a la portada.

**Lo que falta verificar y no pude:** si "Many GEOs" incluye México. El
catálogo no expone la lista de países por programa. Se ve al abrir la
ficha del programa. Si incluye México, ese es el bueno; si no, va
Disney+ LATAM, que por nombre sí lo cubre.

**Nada más sirve.** No existen en Admitad: ViX, Max, Paramount+, Peacock,
ESPN+, MLB.TV, NFL+, Apple TV+, DAZN, Fubo ni Sling.

### Lo que ya quedó listo en el código

`config.py` ya leía `AFFILIATE_DISNEYPLUS`, y esa misma variable alimenta
**Disney+ y ESPN MX** (las dos apuntan a disneyplus.com). Además se cambió
`is_affiliate` para que se deduzca solo de si hay enlace de afiliado.

Traducido: **el día que Disney apruebe, es UNA variable de entorno en
Render y ya.** Sin deploy, sin tocar código. Pegas el enlace de Admitad en
`AFFILIATE_DISNEYPLUS` y los dos proveedores se marcan, se declaran como
publicidad y se miden en GA4 solos.

### Cómo solicitarlo (10 minutos, lo tienes que hacer tú)

1. store.admitad.com → **Programs → All affiliate programs**
2. Buscar `Disney`
3. Abrir los dos y ver en cuál aparece México en la lista de países
4. **Join / Add program** — ahí hay que aceptar los términos del anunciante,
   y eso lo firmas tú, no yo
5. Cuando aprueben: copiar el enlace y ponerlo en Render como
   `AFFILIATE_DISNEYPLUS`

---

## 2. Red nueva que sí vale la pena: Impact

**Fubo** y **Paramount+** corren sus programas en Impact (verificado). Impact
es de las serias del sector y tiene buena cobertura de streaming.

- Registro: impact.com → *Partners / Publishers*
- Es cuenta nueva: **la creas tú** (no puedo crear cuentas)

---

## 3. Datos del sitio — para cualquier formulario

Copiar tal cual:

**Sitio:** https://dondever.app
**Nombre:** DondeVer.app
**Categoría:** Deportes / Guía de programación televisiva
**Idioma:** Español
**Modelo:** Contenido editorial + display (Google AdSense) + afiliados

**Tráfico (Google Search Console, últimos 90 días):**
- 9,290 clics desde búsqueda orgánica
- 688,000 impresiones
- 6,580 páginas indexadas en Google

**Sesiones (Google Analytics 4, últimos 28 días):** 9,091
- 72% búsqueda orgánica, 26% directo

**Países principales (28 días, por clics):**
| País | Clics |
|---|---|
| México | 2,374 |
| Venezuela | 783 |
| Panamá | 367 |
| Estados Unidos | 313 |
| España | 233 |
| Rep. Dominicana | 199 |
| Colombia | 197 |

**Crecimiento:** el 57% de los clics del último trimestre ocurrieron en los
últimos 28 días. España +183%, Estados Unidos +228% contra el periodo previo.

---

## 4. Descripción del sitio — copiar

> DondeVer.app es una guía en español que responde una sola pregunta: en qué
> canal y a qué hora se transmite cada partido. Cubrimos más de 40 ligas
> —Liga MX, MLB, NFL, NBA, Champions, LaLiga, F1, MotoGP, UFC y las ligas
> invernales de béisbol de Venezuela, México y República Dominicana— con
> canales y horarios diferenciados por país: México, Venezuela, Panamá,
> Colombia, República Dominicana, España y Estados Unidos.
>
> El tráfico es de intención alta y momento exacto: la persona llega minutos
> antes del partido buscando dónde verlo. Cuando la respuesta es una
> plataforma de streaming, es el momento de mayor disposición a suscribirse
> que existe en todo el recorrido.
>
> El sitio publica aviso de juego responsable, declara sus relaciones de
> afiliación y verifica los derechos de transmisión contra fuentes oficiales
> antes de publicarlos.

**Versión en inglés — copiar:**

> DondeVer.app is a Spanish-language guide answering one question: which
> channel is showing this game, and at what time. We cover 40+ leagues —Liga
> MX, MLB, NFL, NBA, Champions League, LaLiga, F1, MotoGP, UFC and the winter
> baseball leagues of Venezuela, Mexico and the Dominican Republic— with
> channels and kickoff times broken out by country: Mexico, Venezuela, Panama,
> Colombia, Dominican Republic, Spain and the United States.
>
> Traffic is high-intent and time-critical: users arrive minutes before a game
> looking for where to watch it. When the answer is a streaming platform, that
> is the highest-intent subscription moment in the entire funnel.
>
> The site carries responsible-gambling notices, discloses affiliate
> relationships, and verifies broadcast rights against official sources before
> publishing them.

---

## 5. Cómo promocionarías (suelen preguntarlo) — copiar

> Enlaces contextuales dentro de la respuesta editorial. Cuando la guía indica
> que un partido se transmite por una plataforma concreta, el nombre de esa
> plataforma enlaza a ella. No usamos pop-ups, intersticiales, formatos que
> interfieran con la navegación, ni tráfico incentivado. Todos los enlaces de
> afiliado llevan rel="sponsored nofollow" y se declaran como publicidad.

---

## 6. Advertencia honesta

**La aprobación no es automática.** Y hay un dato que conviene tener presente:
Betsson terminó el programa el 24 de septiembre de 2026 bajo la cláusula
5.3(iv) de sus términos. Si algún formulario pregunta por programas previos,
más vale responder con la verdad — las redes se comunican entre ellas y una
omisión descubierta pesa más que el hecho.

Conviene solicitar con el sitio en su mejor momento. A favor juegan: el aviso
de juego responsable, la declaración de afiliación, los datos verificados
contra fuentes oficiales y el canario diario de integridad.

---

## 7. Orden sugerido

1. **Buscar en CJ** — cuenta ya aprobada, camino más corto (10 min)
2. **Buscar en Admitad** — ya la tienes, catálogo México (10 min)
3. **Crear cuenta en Impact** — para Fubo y Paramount+ (20 min)
4. Con lo aprobado, cambiar los enlaces en `STREAMING_AFFILIATES` de
   `config.py`

**Cuándo:** no esta semana. Los playoffs de MLB arrancan el 29 de septiembre y
hay cuatro cambios recientes asentándose. La semana del 6 de octubre está
mejor.

---

## Lo que no pude verificar

Dicho para que nadie lo dé por hecho:

- Qué red usan ESPN+, MLB.TV, Peacock, Max, Disney+ y ViX. El catálogo público
  no lo dice; hay que buscarlo dentro de CJ, Admitad e Impact.
- Si esos programas aceptan publishers de México.
- Las comisiones reales. Las cifras que circulan en blogs de afiliados no son
  fuente: la buena está en el panel de cada red, después de aprobación.
