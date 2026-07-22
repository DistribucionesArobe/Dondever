/**
 * DondeVer — Auto timezone conversion
 * Converts game times from Mexico City to user's local timezone.
 * Also detects user country for ad targeting.
 */
(function() {
  'use strict';

  var userTZ = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  var isMexico = userTZ.indexOf('Mexico') !== -1 || userTZ === 'America/Merida' || userTZ === 'America/Monterrey' || userTZ === 'America/Cancun' || userTZ === 'America/Chihuahua' || userTZ === 'America/Mazatlan' || userTZ === 'America/Hermosillo' || userTZ === 'America/Tijuana';

  // Country detection from timezone (for ads)
  var TZ_COUNTRY = {
    'Europe/Madrid': 'ES', 'Europe/London': 'GB',
    'America/Bogota': 'CO', 'America/Lima': 'PE',
    'America/Santiago': 'CL', 'America/Buenos_Aires': 'AR',
    'America/Argentina/Buenos_Aires': 'AR',
    'America/Caracas': 'VE', 'America/Guayaquil': 'EC',
    'America/Guatemala': 'GT', 'America/Tegucigalpa': 'HN',
    'America/Managua': 'NI', 'America/Panama': 'PA',
    'America/Costa_Rica': 'CR', 'America/El_Salvador': 'SV',
    'America/Santo_Domingo': 'DO', 'America/Havana': 'CU',
    'America/New_York': 'US', 'America/Chicago': 'US',
    'America/Denver': 'US', 'America/Los_Angeles': 'US',
    'America/Phoenix': 'US',
  };

  var TZ_LABELS = {
    'ES': 'Hora de España', 'CO': 'Hora de Colombia', 'PE': 'Hora de Perú',
    'CL': 'Hora de Chile', 'AR': 'Hora de Argentina', 'VE': 'Hora de Venezuela',
    'EC': 'Hora de Ecuador', 'GT': 'Hora de Guatemala', 'HN': 'Hora de Honduras',
    'NI': 'Hora de Nicaragua', 'PA': 'Hora de Panamá', 'CR': 'Hora de Costa Rica',
    'SV': 'Hora de El Salvador', 'DO': 'Hora de Rep. Dominicana', 'CU': 'Hora de Cuba',
    'US': 'Hora local USA', 'GB': 'Hora de UK',
  };

  // Expose country for ad targeting
  var country = 'MX';
  if (!isMexico) {
    country = TZ_COUNTRY[userTZ] || 'OTHER';
  }
  window.__DV_COUNTRY = country;
  window.__DV_TZ = userTZ;

  // If user is in Mexico, no conversion needed (times already in MX)
  if (isMexico) return;

  // Convert all elements with data-utc
  var els = document.querySelectorAll('[data-utc]');
  if (!els.length) return;

  var tzLabel = TZ_LABELS[country] || 'Tu hora local';

  els.forEach(function(el) {
    var utc = el.getAttribute('data-utc');
    if (!utc) return;
    try {
      var d = new Date(utc.replace('Z', '+00:00'));
      if (isNaN(d.getTime())) return;
      var local = d.toLocaleTimeString('es-MX', {
        hour: 'numeric', minute: '2-digit', hour12: true,
        timeZone: userTZ
      });
      // Capitalize AM/PM
      local = local.replace(/\s?(a|p)\.?\s?m\.?/i, function(m) {
        return ' ' + m.trim().toUpperCase().replace(/\./g, '');
      });
      el.textContent = local;
    } catch(e) {}
  });

  // Update timezone label if present
  var tzEl = document.getElementById('tz-label');
  if (tzEl) {
    tzEl.textContent = tzLabel;
  }

  // Add a small badge showing timezone on the page
  var badge = document.querySelector('.tz-badge');
  if (!badge) {
    var container = document.querySelector('.hero-inner') || document.querySelector('.main');
    if (container) {
      badge = document.createElement('div');
      badge.className = 'tz-badge';
      badge.style.cssText = 'text-align:center;font-size:0.65rem;color:#6b7280;margin-top:0.3rem;';
      badge.innerHTML = '🌍 ' + tzLabel;
      container.appendChild(badge);
    }
  }
})();
