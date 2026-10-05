# dondever.app

- Guía de streaming deportivo en español (México, USA, LATAM). Propiedad GSC: dondever.app
- Stack: FastAPI + Jinja2, PostgreSQL, deploy en Render. `server.py` es enorme: cambios quirúrgicos, no refactors.
- SEO programático: casi todas las páginas salen de plantillas. Un cambio de plantilla = miles de páginas → siempre requiere aprobación y se prueba primero en un subconjunto (una liga) antes de extender.
- El tráfico está muy concentrado en MLB (los Dodgers solos pesan muchísimo). Prioriza: (1) proteger lo que ya rankea, (2) replicar el patrón ganador a equipos/ligas similares.
- Estacionalidad brutal: una caída al terminar una temporada NO es un problema SEO. Compara siempre contra el calendario de la liga.
- Ya tiene Schema SportsEvent, hreflang LATAM, sitemap dinámico, OG dinámicas. Revisa que sigan válidos; no los dupliques.
- AdSense fue rechazado antes por "low-value content" y ahora está activo: NADA de páginas nuevas delgadas o casi duplicadas. Antes de proponer páginas nuevas, comprueba que tendrán contenido único (horarios, canales, datos reales del partido).
- Contenido de apuestas/afiliados: no lo toques ni lo amplíes. La separación editorial vs apuestas está pendiente y es decisión de Alejandro.
