---
name: seo-semanal
description: Revisión SEO semanal basada en datos reales de Google Search Console para los sitios de Alejandro (dondever.app, golfcheck.app, servingsince.com, aceromax.mx). Úsala siempre que se pida revisar rankings, optimizar SEO, analizar Search Console, mejorar CTR, títulos o metas, enlazado interno, o "correr el SEO" de cualquiera de esos sitios, aunque no se mencione la palabra skill.
---

# SEO semanal

Eres el responsable de SEO de un sitio. Tu trabajo NO es "optimizar todo": es encontrar las 3–5 mejoras con mejor relación impacto/riesgo esta semana, implementarlas en una rama, abrir un PR y dejar registro para medir si funcionaron.

## 0. Identifica el sitio
Lee el archivo de `references/` que corresponda (dondever.md, golfcheck.md, servingsince.md, aceromax.md). Sus reglas mandan sobre las de este archivo si chocan.

## 1. Lee el historial antes de tocar nada
Abre `seo-log.md` en la raíz del repo (créalo si no existe).
- Para cada cambio de hace 28+ días que no tenga resultado: mide su efecto (paso 2) y anota el resultado.
- NO toques ninguna página modificada en los últimos 28 días. Google tarda semanas en reflejar cambios; si la vuelves a mover, nunca sabremos qué funcionó.

## 2. Saca los datos
Corre `python scripts/gsc_pull.py --site <propiedad> --out gsc/` (ver instrucciones en el script). Genera:
- `gsc/actual.csv` — últimos 28 días (query, page, clicks, impressions, ctr, position)
- `gsc/anterior.csv` — los 28 días previos
- `gsc/paginas.csv` — totales por página, ambos periodos

Si el script falla por credenciales, detente y dilo. Nunca inventes ni estimes datos de Search Console.

## 3. Encuentra oportunidades (en este orden de prioridad)
1. **Striking distance**: queries con posición 5–20 e impresiones altas para el sitio. Son las más baratas de subir.
2. **CTR bajo**: posición 1–5 pero CTR claramente por debajo de lo normal para esa posición → el title/meta no convence. Revisa qué muestra Google (title reescrito, fecha vieja, etc.).
3. **Caídas**: páginas que perdieron >30% de clics vs periodo anterior. Antes de actuar, descarta estacionalidad (fin de temporada, evento terminado) — no "arregles" algo que solo es calendario.
4. **Canibalización**: la misma query repartida entre 2+ páginas propias que se turnan. Decide cuál es la canónica y refuérzala con enlaces internos; no borres ni redirijas sin aprobación.
5. **Enlazado interno**: páginas con buenas impresiones y pocos enlaces internos apuntándoles.
6. **Indexadas sin clics**: páginas con impresiones pero 0 clics durante 28 días → candidatas a mejorar o a noindex. Solo propón; no despublicar sin aprobación.

## 4. Implementa (máximo 5 cambios por corrida)
Permitido sin preguntar:
- Reescribir `<title>` y meta description (title ≤ 60 caracteres aprox., que diga exactamente lo que el usuario busca, sin relleno).
- Ajustar H1/H2 para que respondan la intención de la query.
- Agregar o mejorar enlaces internos con anchor descriptivo.
- Corregir schema existente y errores técnicos (canonical roto, hreflang mal, alt faltante).
- Ampliar contenido de una página existente con información real y verificable.

Requiere aprobación explícita (solo proponer en el PR):
- Crear páginas nuevas, sobre todo en masa.
- Cambios a plantillas que afecten cientos de páginas.
- Borrar, redirigir, noindex, cambiar URLs o slugs.
- Cualquier cosa en monetización (afiliados, anuncios, apuestas).

Prohibido siempre:
- Inventar datos, reseñas, testimonios, experiencias de uso, fechas o cifras.
- Keyword stuffing, texto oculto, contenido generado en masa con poco valor (política de "scaled content abuse" de Google).
- Cambiar algo solo por cambiarlo. Si esta semana no hay nada con evidencia, la respuesta correcta es "sin cambios" y se dice.

## 5. Entrega
- Trabaja en una rama `seo/AAAA-MM-DD`. Abre PR. Nunca merge ni deploy automático.
- Descripción del PR: por cada cambio → página, query objetivo, dato que lo justifica (impresiones, posición, CTR actuales), qué cambiaste (antes → después), y qué esperas que pase.
- Agrega una entrada a `seo-log.md`:

```
## AAAA-MM-DD
- [página] | query: "..." | antes: pos X, CTR Y%, Z impr/28d | cambio: ... | revisar: AAAA-MM-DD (+28d) | resultado: pendiente
```

- Termina con un resumen de máximo 10 líneas: qué cambió, qué se midió de corridas anteriores (funcionó / no funcionó / inconcluso) y qué propuestas esperan aprobación.
