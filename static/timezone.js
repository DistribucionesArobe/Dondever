/**
 * DondeVer — Auto timezone conversion v2
 * Detects user country via: 1) stored preference, 2) IP geolocation, 3) browser timezone.
 * Converts game times from Mexico City to user's local timezone.
 * Supports manual country selector in header.
 */
(function() {
  'use strict';

  // ── Country → Timezone mapping ──────────────────────────
  var COUNTRY_TZ = {
    'MX': 'America/Mexico_City',
    'US': 'America/New_York',
    'CO': 'America/Bogota',
    'VE': 'America/Caracas',
    'PA': 'America/Panama',
    'AR': 'America/Argentina/Buenos_Aires',
    'CL': 'America/Santiago',
    'PE': 'America/Lima',
    'CR': 'America/Costa_Rica',
    'DO': 'America/Santo_Domingo',
    'EC': 'America/Guayaquil',
    'GT': 'America/Guatemala',
    'HN': 'America/Tegucigalpa',
    'NI': 'America/Managua',
    'SV': 'America/El_Salvador',
    'CU': 'America/Havana',
    'BO': 'America/La_Paz',
    'PY': 'America/Asuncion',
    'UY': 'America/Montevideo',
    'ES': 'Europe/Madrid',
    'BR': 'America/Sao_Paulo',
  };

  var COUNTRY_LABELS = {
    'MX': 'HORA MX', 'US': 'Hora USA', 'CO': 'Hora CO', 'VE': 'Hora VE',
    'PA': 'Hora PA', 'AR': 'Hora AR', 'CL': 'Hora CL', 'PE': 'Hora PE',
    'CR': 'Hora CR', 'DO': 'Hora RD', 'EC': 'Hora EC', 'GT': 'Hora GT',
    'HN': 'Hora HN', 'NI': 'Hora NI', 'SV': 'Hora SV', 'CU': 'Hora CU',
    'BO': 'Hora BO', 'PY': 'Hora PY', 'UY': 'Hora UY', 'ES': 'Hora ES',
    'BR': 'Hora BR',
  };

  var COUNTRY_NAMES = {
    'MX': 'México', 'US': 'Estados Unidos', 'CO': 'Colombia',
    'VE': 'Venezuela', 'PA': 'Panamá', 'AR': 'Argentina',
    'CL': 'Chile', 'PE': 'Perú', 'CR': 'Costa Rica',
    'DO': 'Rep. Dominicana', 'EC': 'Ecuador', 'GT': 'Guatemala',
    'HN': 'Honduras', 'NI': 'Nicaragua', 'SV': 'El Salvador',
    'CU': 'Cuba', 'BO': 'Bolivia', 'PY': 'Paraguay',
    'UY': 'Uruguay', 'ES': 'España', 'BR': 'Brasil',
  };

  var COUNTRY_FLAGS = {
    'MX': '\u{1F1F2}\u{1F1FD}', 'US': '\u{1F1FA}\u{1F1F8}', 'CO': '\u{1F1E8}\u{1F1F4}',
    'VE': '\u{1F1FB}\u{1F1EA}', 'PA': '\u{1F1F5}\u{1F1E6}', 'AR': '\u{1F1E6}\u{1F1F7}',
    'CL': '\u{1F1E8}\u{1F1F1}', 'PE': '\u{1F1F5}\u{1F1EA}', 'CR': '\u{1F1E8}\u{1F1F7}',
    'DO': '\u{1F1E9}\u{1F1F4}', 'EC': '\u{1F1EA}\u{1F1E8}', 'GT': '\u{1F1EC}\u{1F1F9}',
    'HN': '\u{1F1ED}\u{1F1F3}', 'NI': '\u{1F1F3}\u{1F1EE}', 'SV': '\u{1F1F8}\u{1F1FB}',
    'CU': '\u{1F1E8}\u{1F1FA}', 'BO': '\u{1F1E7}\u{1F1F4}', 'PY': '\u{1F1F5}\u{1F1FE}',
    'UY': '\u{1F1FA}\u{1F1FE}', 'ES': '\u{1F1EA}\u{1F1F8}', 'BR': '\u{1F1E7}\u{1F1F7}',
  };

  // ── Browser TZ → Country fallback ──────────────────────
  var TZ_COUNTRY = {
    'America/Bogota': 'CO', 'America/Lima': 'PE', 'America/Santiago': 'CL',
    'America/Buenos_Aires': 'AR', 'America/Argentina/Buenos_Aires': 'AR',
    'America/Caracas': 'VE', 'America/Guayaquil': 'EC', 'America/Guatemala': 'GT',
    'America/Tegucigalpa': 'HN', 'America/Managua': 'NI', 'America/Panama': 'PA',
    'America/Costa_Rica': 'CR', 'America/El_Salvador': 'SV', 'America/Santo_Domingo': 'DO',
    'America/Havana': 'CU', 'America/La_Paz': 'BO', 'America/Asuncion': 'PY',
    'America/Montevideo': 'UY', 'Europe/Madrid': 'ES', 'America/Sao_Paulo': 'BR',
    'America/New_York': 'US', 'America/Chicago': 'US', 'America/Denver': 'US',
    'America/Los_Angeles': 'US', 'America/Phoenix': 'US',
  };

  var MX_ZONES = ['America/Mexico_City','America/Merida','America/Monterrey','America/Cancun',
    'America/Chihuahua','America/Mazatlan','America/Hermosillo','America/Tijuana','America/Bahia_Banderas','America/Matamoros','America/Ojinaga'];

  var browserTZ = Intl.DateTimeFormat().resolvedOptions().timeZone || '';

  // ── Detect country ─────────────────────────────────────
  function detectCountryFromTZ() {
    if (MX_ZONES.indexOf(browserTZ) !== -1) return 'MX';
    return TZ_COUNTRY[browserTZ] || null;
  }

  function getStoredCountry() {
    try { return localStorage.getItem('dv_country'); } catch(e) { return null; }
  }

  function storeCountry(code) {
    try { localStorage.setItem('dv_country', code); } catch(e) {}
  }

  // ── Convert times ──────────────────────────────────────
  function convertTimes(countryCode) {
    var tz = COUNTRY_TZ[countryCode];
    if (!tz) return;

    var label = COUNTRY_LABELS[countryCode] || 'Tu hora';
    var isMX = (countryCode === 'MX');

    // Update all time elements
    if (!isMX) {
      var els = document.querySelectorAll('[data-utc]');
      els.forEach(function(el) {
        var utc = el.getAttribute('data-utc');
        if (!utc) return;
        try {
          var d = new Date(utc.replace('Z', '+00:00'));
          if (isNaN(d.getTime())) return;
          var local = d.toLocaleTimeString('es-MX', {
            hour: 'numeric', minute: '2-digit', hour12: true,
            timeZone: tz
          });
          local = local.replace(/\s?(a|p)\.?\s?m\.?/i, function(m) {
            return ' ' + m.trim().toUpperCase().replace(/\./g, '');
          });
          el.textContent = local;
        } catch(e) {}
      });
    }

    // Update all hora labels (class tz-hora-label or gc-sub containing "Hora")
    var horaEls = document.querySelectorAll('.tz-hora-label');
    horaEls.forEach(function(el) { el.textContent = label; });

    // Also update any gc-sub that says "Hora MX" or similar
    var gcSubs = document.querySelectorAll('.gc-sub');
    gcSubs.forEach(function(el) {
      if (el.textContent.trim().indexOf('Hora') === 0) {
        el.textContent = label;
      }
    });

    // Update "Hora CDMX" in team panel meta
    var metas = document.querySelectorAll('.tp-meta');
    metas.forEach(function(el) {
      el.textContent = el.textContent.replace(/Hora CDMX/g, label);
    });

    // Update #tz-label if present
    var tzEl = document.getElementById('tz-label');
    if (tzEl) tzEl.textContent = label;

    // Expose for ad targeting
    window.__DV_COUNTRY = countryCode;
    window.__DV_TZ = tz;
  }

  // ── Update header selector UI ──────────────────────────
  function updateSelectorUI(countryCode) {
    var btns = document.querySelectorAll('.country-btn[data-country]');
    btns.forEach(function(btn) {
      if (btn.getAttribute('data-country') === countryCode) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // Update the current-country display with flag + full name
    var display = document.getElementById('current-country');
    if (display) {
      var flag = COUNTRY_FLAGS[countryCode] || '';
      var name = COUNTRY_NAMES[countryCode] || countryCode;
      display.textContent = flag + ' ' + name;
    }
  }

  // ── Country selector click handler ─────────────────────
  function setupSelector() {
    // Dropdown toggle
    var toggle = document.getElementById('country-toggle');
    var dropdown = document.getElementById('country-dropdown');
    if (toggle && dropdown) {
      toggle.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('open');
      });
      document.addEventListener('click', function() {
        dropdown.classList.remove('open');
      });
    }

    // Country buttons
    var btns = document.querySelectorAll('.country-btn[data-country]');
    btns.forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var code = this.getAttribute('data-country');
        storeCountry(code);
        // Reload to reconvert all times from server-rendered MX times
        window.location.reload();
      });
    });
  }

  // ── IP-based detection (async, only if no stored pref) ─
  function detectFromIP(callback) {
    // Use free ipapi.co — returns just the 2-letter country code
    var xhr = new XMLHttpRequest();
    xhr.open('GET', 'https://ipapi.co/country/', true);
    xhr.timeout = 3000;
    xhr.onload = function() {
      if (xhr.status === 200) {
        var code = xhr.responseText.trim().toUpperCase();
        if (COUNTRY_TZ[code]) {
          callback(code);
        } else {
          callback(null);
        }
      } else {
        callback(null);
      }
    };
    xhr.onerror = xhr.ontimeout = function() { callback(null); };
    xhr.send();
  }

  // ── Affiliate geo-targeting (MX/US/LATAM) ──────────────
  var GEO_MAP = { 'mx': {key:'jubilee', name:'Jubilee'}, 'us': {key:'betsson', name:'Betsson'}, 'latam': {key:'1xbet', name:'1xBet'} };

  function applyAffiliateGeo(country) {
    var region = 'latam'; // default for all LATAM + rest of world
    if (country === 'MX') region = 'mx';
    else if (country === 'US') region = 'us';

    window.__geoMX = (region === 'mx');
    window.__geoRegion = region;

    // Show/hide geo-targeted elements
    document.querySelectorAll('[data-geo]').forEach(function(el) {
      var show = el.getAttribute('data-geo') === region;
      el.style.display = show ? (el.getAttribute('data-display') || '') : 'none';
    });

    // Rewrite /go/bet geo-smart links to use detected affiliate
    var target = GEO_MAP[region];
    document.querySelectorAll('a[href*="/go/bet"]').forEach(function(a) {
      var href = a.getAttribute('href') || '';
      // Match /go/bet but NOT /go/betsson or /go/bet365 etc.
      if (href.match(/\/go\/bet(\?|$)/)) {
        a.setAttribute('href', href.replace('/go/bet', '/go/' + target.key));
      }
    });

    // Rewrite untagged affiliate links (odds buttons etc.)
    // Store original href on first run so we can re-run when IP overrides TZ
    document.querySelectorAll('a[data-affiliate]:not([data-geo])').forEach(function(a) {
      if (!a.getAttribute('data-orig-href')) {
        a.setAttribute('data-orig-href', a.href);
        a.setAttribute('data-orig-name', a.innerHTML);
      }
      var origHref = a.getAttribute('data-orig-href');
      var origName = a.getAttribute('data-orig-name');
      a.href = origHref.replace(/\/go\/(betsson|jubilee|vivento|1xbet)/, '/go/' + target.key);
      a.setAttribute('data-affiliate', target.key);
      a.innerHTML = origName.replace(/(Betsson|Jubilee|Vivento|1xBet)/g, target.name);
    });

    // Update smart search brand if present
    if (typeof window._updateBetBrand === 'function') {
      window._updateBetBrand(region);
    }
  }

  // ── Main init ──────────────────────────────────────────
  function init(country) {
    convertTimes(country);
    updateSelectorUI(country);
    setupSelector();
    applyAffiliateGeo(country);
  }

  // Priority: 1) stored preference, 2) IP geo, 3) browser TZ
  var stored = getStoredCountry();
  if (stored && COUNTRY_TZ[stored]) {
    init(stored);
  } else {
    // Try IP detection first
    detectFromIP(function(ipCountry) {
      if (ipCountry) {
        storeCountry(ipCountry);
        init(ipCountry);
      } else {
        // Fall back to browser timezone
        var tzCountry = detectCountryFromTZ() || 'MX';
        init(tzCountry);
      }
    });
    // While IP detection loads, show based on browser TZ (instant, no flash)
    var quickCountry = detectCountryFromTZ() || 'MX';
    convertTimes(quickCountry);
    updateSelectorUI(quickCountry);
    setupSelector();
    applyAffiliateGeo(quickCountry);
  }
})();
