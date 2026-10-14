/* Refresco de marcadores en vivo para /liga/ y los hubs por deporte.
 *
 * Antes, las dos plantillas traían esta línea suelta:
 *
 *     if (document.querySelector('.live-badge')) { setTimeout(() => location.reload(), 60000); }
 *
 * Eso recarga la PÁGINA COMPLETA cada minuto mientras haya un partido en vivo,
 * y como el script vuelve a correr al cargar, en la práctica es un bucle sin fin.
 * Se veía en Analytics: /beisbol-hoy con 58 vistas de 3 usuarios —diecinueve
 * vistas por persona—. Parecía un bot y era esto.
 *
 * Por qué importa más allá de las métricas sucias:
 *
 *   1. Cada recarga es una vista de página con anuncios que nadie va a clicar.
 *      Diecinueve impresiones de una sola persona hunden el CTR, que es justo
 *      lo que estuvimos subiendo. Y un navegador recargando anuncios solo, en
 *      bucle, es el patrón que AdSense mira con lupa.
 *   2. Se pierde el scroll y cualquier filtro que el usuario haya puesto.
 *   3. Seguía recargando con la pestaña en segundo plano, que es de donde
 *      salen casi todas esas diecinueve.
 *
 * Qué hace ahora:
 *   · No recarga si la pestaña no está visible; espera a que vuelvas.
 *   · Para después de 20 recargas (unos 20 minutos). Quien dejó la pestaña
 *     abierta toda la tarde ya no suma vistas infinitas.
 *   · Cuenta en sessionStorage, así que el contador sobrevive a la recarga.
 *
 * Esto NO es el arreglo bueno. El arreglo bueno es lo que ya hace la portada:
 * pedir /api/live-scores y cambiar solo el marcador, sin recargar nada. Eso
 * necesita marcar las filas con data-game-id en estas dos plantillas, que hoy
 * no lo tienen. Mientras tanto, esto corta lo peor sin inventar marcado nuevo.
 */
(function () {
  'use strict';

  var CADA_MS = 60000;
  var MAX_RECARGAS = 20;
  var CLAVE = 'dv_live_reloads';

  if (!document.querySelector('.live-badge')) return;

  function llevadas() {
    try { return parseInt(sessionStorage.getItem(CLAVE) || '0', 10) || 0; }
    catch (e) { return 0; }  // modo privado: seguimos, solo sin contador
  }

  function anotar(n) {
    try { sessionStorage.setItem(CLAVE, String(n)); } catch (e) {}
  }

  if (llevadas() >= MAX_RECARGAS) return;

  setTimeout(function recargar() {
    if (document.visibilityState !== 'visible') {
      // Pestaña en segundo plano: no gastamos una vista. Lo reintentamos
      // cuando el usuario vuelva, no en un minuto fijo.
      document.addEventListener('visibilitychange', function alVolver() {
        if (document.visibilityState !== 'visible') return;
        document.removeEventListener('visibilitychange', alVolver);
        recargar();
      });
      return;
    }
    anotar(llevadas() + 1);
    location.reload();
  }, CADA_MS);
})();
