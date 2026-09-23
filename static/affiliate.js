/* DondeVer — medición de clics de afiliado en GA4.
 *
 * Por qué existe este archivo: el listener vivía copiado y pegado dentro de
 * ocho plantillas, y solo escuchaba `a[data-affiliate]`. De los 52 enlaces de
 * afiliado del sitio, 14 no llevaban ese atributo y tres plantillas enteras
 * (canal, evento, pronosticos) ni siquiera incluían el script. Resultado: uno
 * de cada cuatro clics de afiliado era invisible en GA4, y nadie se enteraba
 * porque un enlace sin medir se ve idéntico a uno medido.
 *
 * Ahora el enganche es la URL, no un atributo: cualquier enlace a /go/ se mide
 * solo. Un enlace nuevo que alguien agregue mañana en cualquier plantilla entra
 * sin tocar nada.
 *
 * Params: provider, game, league, country, page, placement.
 */
(function () {
  'use strict';

  // Un solo listener por página, pase lo que pase con los includes.
  if (window.__dvAffiliateReady) return;
  window.__dvAffiliateReady = true;

  function providerDe(a) {
    // El atributo manda cuando existe; si no, se deduce de /go/{clave}.
    if (a.dataset && a.dataset.affiliate) return a.dataset.affiliate;
    try {
      var m = new URL(a.href, location.origin).pathname.match(/^\/go\/([^/?#]+)/);
      return m ? decodeURIComponent(m[1]) : '';
    } catch (err) {
      return '';
    }
  }

  document.addEventListener('click', function (e) {
    if (!e.target || !e.target.closest) return;
    var a = e.target.closest('a[data-affiliate], a[href*="/go/"]');
    if (!a || typeof gtag !== 'function') return;

    var provider = providerDe(a);
    if (!provider) return;  // /go/ sin clave: no es un afiliado, es un error de plantilla

    var ctx = window.DV_CTX || {};
    var placement = (a.dataset && a.dataset.placement) || '';
    if (!placement) {
      try {
        placement = new URL(a.href, location.origin).searchParams.get('s') || '';
      } catch (err) { /* href raro: se queda en unknown */ }
    }

    gtag('event', 'affiliate_click', {
      provider: provider,
      game: (a.dataset && a.dataset.game) || ctx.game || '',
      league: (a.dataset && a.dataset.league) || ctx.league || '',
      country: (a.dataset && a.dataset.country) || ctx.country || 'mx',
      page: location.pathname,
      placement: placement || 'unknown'
    });
  }, true);
})();
