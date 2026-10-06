# De dónde sacar el dato de "en qué canal"

Probado el 5 de octubre de 2026. Cada fuente de aquí la consulté de verdad y
pegué lo que devolvió. Las que no pude consultar están marcadas como no
comprobadas, no como malas.

El caso de prueba fue el día real: playoffs de MLB (White Sox–Guardians,
Yankees–Rays) y la jornada de Liga MX / fútbol colombiano.

---

## Lo que ya teníamos

| Fuente | Qué da | Estado |
|---|---|---|
| ESPN `site.api.espn.com` | Calendario, marcador, estado, y `geoBroadcasts` | **Solo Estados Unidos.** Ver abajo. |
| TheSportsDB | `strTVStation` por evento | Irregular, pero gratis y a veces acierta |
| GatoTV | Parrilla publicada por canal y país | **Funciona.** Único que cubre Venezuela |
| epgshare01 | XMLTV por país, un archivo al día | **Funciona.** Trae béisbol, que GatoTV no |

### Qué país consulta cada una — esto importa más de lo que parece

Lo leí del código, no lo supuse (`gatotv.GATOTV_SPORTS_CHANNELS` y
`epgshare.EPG_TAGS`):

```
GatoTV   : VE  PA  DO  CO  PE  EC          ← NO tiene México
epgshare :     PA  DO  CO  PE  EC  MX      ← la ÚNICA que da México
```

Dos consecuencias:

1. **Para México dependemos de una sola fuente.** El "🇲🇽 México — ESPN MX" que
   salió hoy en la ficha de Yankees vs Rays vino de epgshare, no de GatoTV. Si
   epgshare se cae o cambia, México se queda sin canal confirmado.
2. **Ninguna de las dos cubre España**, y hay página `/donde-ver-en-espana`.
   Hoy va con lista curada a mano. Movistar+ España (más abajo) taparía eso.

---

## Descartadas — probadas y no sirven

### ESPN con `region=mx&lang=es`

Era la hipótesis más barata y la más prometedora: usar la misma API pero
pidiéndole la región de México.

Sí cambia cosas: traduce los nombres de equipos, apunta los enlaces a
espn.com.mx, y hasta cambia la casa de apuestas a Caliente
(`"provider":{"id":"1009","name":"Caliente"}`).

Pero `geoBroadcasts` **no cambia**:

```json
"geoBroadcasts":[
  {"type":{"shortName":"Radio"},"media":{"shortName":"ERADM"},"lang":"en","region":"us"},
  {"type":{"shortName":"TV"},"media":{"shortName":"TBS"},"lang":"en","region":"us"},
  {"type":{"shortName":"TV"},"media":{"shortName":"truTV"},"lang":"en","region":"us"}
]
```

Las tres entradas, `region: "us"`. Revisé las 7 entradas del día: todas `us`.
**ESPN no tiene el dato de México y no lo va a tener.** Conclusión cerrada.

### MLB Stats API (`statsapi.mlb.com`)

Tiene un campo `language`, y sí trae entradas en español:

```json
{"name":"Univision/ TUDN","type":"TV","language":"es","isNational":true}
{"name":"WADO 1280","type":"AM","language":"es"}
{"name":"WQBN/1300AM","type":"AM","language":"es"}
```

Pero son emisoras **hispanas de Estados Unidos**, no mexicanas. Y el objeto no
tiene ningún campo de país: lo más cercano es
`availability.availabilityCode`, cuyos valores son `"national"` y
`"local_out_of_market"` — los dos son conceptos del mercado gringo.

Sirve como filtro de idioma para un lector hispano en EE.UU. No sirve para
saber qué prende alguien en Guadalajara.

### API de la NHL (`api-web.nhle.com`)

Es la mejor estructurada de todas: `tvBroadcasts[].countryCode` es un campo de
país de verdad.

Y es inútil para nosotros: de 113 entradas, los valores fueron **solo `US` y
`CA`**. Ni una de México, España o LATAM. Tampoco tiene campo de idioma.

### mi.tv

Las páginas cargan y el patrón de URL por fecha funciona, pero las filas de la
parrilla salen de `/async/channel/...` y eso devolvió cuerpo vacío en los
cuatro intentos. **No pude ver un solo título de programa**, así que no puedo
decir que sirva.

Además su lineup de México no tiene TUDN, ni TNT Sports, ni Claro Sports, y los
nombres están viejos ("Azteca 13"). Es justo donde GatoTV ya nos sirve.

### iptv-org/epg

No existe un XMLTV ya armado. Es un juego de scrapers que tú corres
(`npm run grab --- --sites=...`). De los tres servidores comunitarios que
listan, dos están caídos y el que vive sirve **2 canales**.

Lo útil de ahí es el catálogo: te dice qué sitios tienen parrilla y cuántos
canales. Así encontré lo de Telefónica.

### Operadores de México

- Sky México: cuerpo vacío
- Totalplay: cuerpo vacío
- izzi: publica su lineup **como imagen JPEG**. Cero datos.

Callejón sin salida.

### NBA — no comprobada

Seis intentos entre `cdn.nba.com`, `stats.nba.com` y `data.nba.net`: todos
devolvieron cuerpo vacío, sin error. No es un "no sirve", es un "no se pudo
medir". Hay que volver a probarlo desde otra salida de red antes de descartarlo.

---

## Encontradas y verificadas

### Telefónica / Movistar — Colombia y Perú ★

La mejor de la ronda. JSON, GET simple, **sin llave ni cookies**, y acepta
varios canales en una sola llamada.

Lista de canales (160 en Colombia):

```
https://contentapi-co.cdn.telefonica.com/33/default/es-CO/contents/all
  ?contentTypes=LCH&fields=Pid,Name&limit=1000
```

Trae Win Sports (`LCH2761`), Win+Futbol (`LCH3972`), ESPN 1 a 7,
BaseBall Network 1 (`LCH7100`), Deportivo 1 y 2.

Parrilla:

```
https://contentapi-co.cdn.telefonica.com/33/default/es-CO/schedules
  ?fields=Title,Start,End,LiveChannelPid
  &starttime={unix}&endtime={unix}
  &livechannelpids=LCH2722,LCH2761,LCH7100,LCH3972
```

Y lo que devolvió, textual:

```json
{"Start":1791244800,"Title":"Fútbol Colombiano Primera División : Independiente Medellín vs. Independiente Santa Fe","LiveChannelPid":"LCH2761","End":1791252000}
{"Start":1791237600,"Title":"Fútbol Colombiano Segunda División : Bogotá vs. Envigado","LiveChannelPid":"LCH3972","End":1791244800}
```

**Los dos equipos vienen en el título**, con hora unix de inicio y fin y el
canal. Es exactamente la forma que necesitamos.

Perú es el mismo patrón con `contentapi-pe` y `/28/default/es-PE/` (219
canales: ESPN 1–7 HD, Fox Sports Premium, L1 Max, M Deportes).

**Un defecto observado:** BaseBall Network 1 pone `"1 BN"` como título, sin
equipos. El béisbol en ese canal no se puede emparejar.

### Movistar+ España

Misma idea, otro endpoint:

```
https://ottcache.dof6.com/movistarplus/webplayer/OTT/epg
  ?from=2026-10-05T18:00:00&span=6&channel=VAMOSD&version=8
  &mdrm=true&tlsstream=true&demarcation=18
```

Ojo: hay que leer **dos campos**. `Titulo` trae la competencia y
`TituloEpisodio` los equipos:

```
"TituloEpisodio":"\"Talleres - Belgrano\"","TituloVerLinea1":"Liga Argentina. Torneo Clausura"
"TituloEpisodio":"\"India  - Brasil\"","TituloVerLinea1":"Partido Amistoso"
"TituloEpisodio":"\"Alcaraz - Munar\"","TituloVerLinea1":"Torneo de Tokio"
```

Sirve para endurecer `/donde-ver-en-espana`, que hoy va con lista curada.

### reportv.com.ar — candidata, no hallazgo

Dominio argentino pero el lineup es **venezolano** (Meridiano TV, DirecTV
Sports, Claro Sports, IVC, Venevisión, Televen), con zona horaria
`America/Caracas`.

Vi la lista completa de canales. **No vi una sola fila de programa**: la
parrilla pide POST y no pude hacerlo. Queda como la única parrilla de Venezuela
publicada que encontré, pero sin comprobar.

---

## LA MEDICIÓN — 5 de octubre de 2026, 35 partidos

Medido contra producción, no en laboratorio: se consultaron las 35 fichas de
`/partido/` del día y se leyó lo que la página realmente muestra. Así se mide
lo que ve el usuario, con el caché y el timeout de 2.5 s de verdad.

| | partidos | % |
|---|---|---|
| **Con canal de México CONFIRMADO** (fila "🇲🇽 México" en Transmisiones por país) | **2** | **6%** |
| Con canal de México nombrado arriba, en "DÓNDE VERLO" | 30 | 86% |
| Con el bloque "Transmisiones por país" presente | 8 | 23% |
| Con la pregunta frecuente diciendo "por confirmar" | 31 | 89% |
| Con encabezado "DÓNDE SUELE VERSE" (el arreglo nuevo) | 0 | 0% |

Los dos confirmados son los dos de MLB. Nada más.

### CORRECCIÓN — el 6% no era culpa de la fuente

Lo que escribí abajo ("la parrilla no alcanza") estaba mal, y vale la pena
dejar escrito el error porque es instructivo.

**GatoTV sí tiene México. Nunca se lo pedimos.** `GATOTV_SPORTS_CHANNELS` iba
de Venezuela a Ecuador y se saltaba el país del que viene la mayor parte del
tráfico. El 6% no medía la cobertura de la fuente: medía un hueco de
configuración.

Comprobado el 05/10/2026 contra gatotv.com, con la fecha real:

```
/canal/tudn_mexico/2026-10-05
   15:00–17:00  Monterrey Vs. Cruz Azul      Fútbol Mexicano Primera División
   17:00–19:00  América vs. Tigres UANL      Femenil Primera División
   23:00–01:00  Monterrey vs. América        Femenil Primera División

/canal/fox_sports_2_mexico/2026-10-05
   06:00–08:00  UCAM Murcia vs. Barca
   14:30–16:30  Kosner Baskonia vs. FIATC Giron
```

Mismo patrón de URL y misma estructura HTML que los otros seis países, así que
el parser, la caché y el comparador de nombres ya servían. Lo único que faltaba
era la lista.

Canales activados (los nombres son los que ya usa el sitio):

| siempre | solo fútbol |
|---|---|
| `tudn_mexico` → TUDN | `9_de_mexico` → Canal 9 |
| `5_mexico` → Canal 5 | `azteca_uno` → Azteca Uno |
| `azteca_7` → Azteca 7 | `aym_sports` → AYM Sports |
| `espn_mexico` → ESPN MX | `multimedios_plus` → Multimedios |
| `fox_sports_mexico` → Fox Sports MX | `espn_2_mexico`, `espn_3_mexico` |
| `fox_sports_2_mexico` → Fox Sports 2 MX | `fox_sports_3_mexico` |

Van separados para no pedir trece parrillas en un juego de beisbol, donde los
canales de fútbol no aportan nada. Fútbol pide 13, beisbol 6.

`tudn_mexico`, `fox_sports_2_mexico` y `9_de_mexico` los consulté uno por uno;
los demás los leí del índice de canales del propio GatoTV
(`/guia_tv/mexico`). **Hay que volver a medir** con el script después de
desplegar: el 6% es de antes de esto y ya no sirve de referencia.

**Un riesgo que vi en la parrilla de TUDN:** trae programas de resumen
("Liga MX en 60", "Fut en 60", "NFL en 60") que también nombran a los dos
equipos sin ser la transmisión. La ventana de horario los descarta casi
siempre, pero si algún día aparece un canal raro en un partido viejo, ahí hay
que mirar primero.

---

### Lo que decía ese 6% (y por qué estaba mal)

**La parrilla no alcanza.** Yo mismo había puesto el umbral: abajo de 25% hay
que conseguir otra fuente antes de construir encima. Salió 6%.

Y el 86% contra el 6% es la medida exacta del problema: el sitio nombra un
canal mexicano en 30 de 35 partidos y solo sabe de verdad en 2.

El 0% de la última fila confirma otra cosa: **el arreglo de "probable" no está
desplegado.** Está en la rama, no en `main`.

### Tres cosas que aparecieron y no sabíamos

**1. Defaults absurdos en amistosos.** Estos cuatro partidos salen hoy con
"MX: TUDN, ViX":

- Mauricio vs Sri Lanka
- Uganda vs Congo RD
- Ruanda vs Kenia
- Liechtenstein vs Gibraltar

TUDN no transmite Mauricio–Sri Lanka. El default de `club-friendly` se le pega
a cualquier partido de selecciones y produce respuestas que cualquiera
reconoce como falsas de inmediato. **Un default que acierta casi nunca es peor
que no decir nada**: destruye la confianza en los que sí están bien.

De paso, esos cuatro están clasificados como `/liga/club-friendly`
("amistosos de clubes") y son partidos de selecciones.

**2. A la NFL le falta justo México.** En Falcons vs Saints, "Transmisiones
por país" lista Venezuela, Panamá, Dominicana, Colombia, Perú y Ecuador — seis
países — y **no tiene fila de México**, mientras arriba nombra tres canales
mexicanos. Es el único país que falta y es el principal.

**3. Cero España en doce partidos europeos.** Nueve de UEFA Nations League, y
el sitio no nombra un solo canal español, aunque sí calcula la hora de España
para cada uno. Esto confirma el hueco: ni GatoTV ni epgshare cubren España.

### La contradicción, cuantificada

De los 11 partidos de ligas de EE.UU., **los 7 que nombran un canal de México
arriba dicen "por confirmar" en las preguntas frecuentes**. Los 4 que no
nombran canal mexicano responden bien. O sea: la pregunta frecuente es la
única parte honesta de la página, porque es la única que consulta
`channels_confirmed`.

Para Google eso es peor que para una persona: el bloque de preguntas es el que
se lee como dato estructurado. Al buscador le estamos diciendo "no sé" en 31
partidos donde a la gente le mostramos un canal.

Los dos de MLB se contradicen tres veces en la misma página:

| Dónde | Qué dice |
|---|---|
| Bloque principal | `MX: TNT Sports` |
| Transmisiones por país | `🇲🇽 México: ESPN MX` |
| Pregunta frecuente | `"está por confirmar"` |
| Meta descripción | `"Transmite: TNT Sports."` |

---

## Lo que esto significa, sin adornos

**Para México no encontré nada nuevo.** Telefónica no tiene televisión de paga
en México, así que el mejor hallazgo de la ronda no toca al país que más nos
importa. Las parrillas de los operadores mexicanos están cerradas o son
imágenes. ESPN quedó descartado para siempre.

Pero hay un dato que corrige lo que yo mismo creía: **GatoTV y epgshare sí
están dando México.** La ficha de Yankees vs Rays de hoy mostraba
"🇲🇽 México — ESPN MX", y eso salió de ahí. O sea que para México la fuente ya
existe y funciona; lo que no tenemos es la **medida** de cuánto cubre.

Y el peso de los países no ayuda: Colombia es el 4% de los clics y Perú ~2%.
Telefónica suma ~6% más España. **Venezuela es el 17%** y ahí el único
candidato necesita POST.

### El orden que tiene sentido

1. **Medir antes de agregar.** Correr GatoTV y epgshare contra todos los
   partidos de un día y sacar el porcentaje de cobertura por país y por liga.
   Ese número no lo tenemos y decide todo lo demás. Si México ya va al 70%, el
   trabajo es usarlo en más páginas; si va al 15%, hay que buscar otra fuente.
2. **Telefónica CO/PE + Movistar ES.** Barato —un GET— y bien delimitado. Suma
   ~6% de los clics más España.
3. **Venezuela.** Es el hueco grande. reportv por POST es la pista; hay que
   comprobar primero que tenga filas de verdad.

El paso 1 cuesta una tarde y puede ahorrar las otras dos.
