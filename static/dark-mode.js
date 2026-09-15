/* DondeVer — Dark Mode Toggle + Live Score Polling */
(function(){
  // ── Dark Mode ───────────────────────────
  var html = document.documentElement;
  var saved = localStorage.getItem('dv-theme');
  // Default: follow system preference
  if (saved) {
    html.setAttribute('data-theme', saved);
  } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    html.setAttribute('data-theme', 'dark');
  }
  // Update theme-color meta for PWA
  function updateThemeMeta() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.content = html.getAttribute('data-theme') === 'dark' ? '#0b1120' : '#10b981';
    }
  }
  updateThemeMeta();

  // Toggle handler — called by button click
  window.toggleDarkMode = function() {
    var current = html.getAttribute('data-theme');
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('dv-theme', next);
    // Update toggle button icons (header + sticky bar)
    var icon = next === 'dark' ? '☀️' : '🌙';
    var btn = document.getElementById('dm-btn');
    if (btn) btn.textContent = icon;
    var stickyBtn = document.querySelector('.sticky-brand-toggle');
    if (stickyBtn) stickyBtn.textContent = icon;
    updateThemeMeta();
  };

  // Set initial icon when DOM is ready
  document.addEventListener('DOMContentLoaded', function(){
    var icon = html.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
    var btn = document.getElementById('dm-btn');
    if (btn) btn.textContent = icon;
    var stickyBtn = document.querySelector('.sticky-brand-toggle');
    if (stickyBtn) stickyBtn.textContent = icon;
  });

  // ── Live Score Polling ──────────────────
  var POLL_INTERVAL = 30000; // 30 seconds
  var hasLiveGames = document.querySelector('.game-card.live');

  function updateLiveScores() {
    fetch('/api/live-scores')
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (!data.games) return;
        data.games.forEach(function(g){
          // Find score elements by game ID
          var card = document.querySelector('[data-game-id="' + g.id + '"]');
          if (!card) return;
          var homeScore = card.querySelector('.score-home');
          var awayScore = card.querySelector('.score-away');
          var clock = card.querySelector('.gc-clock');
          if (homeScore && g.home_score !== undefined) homeScore.textContent = g.home_score;
          if (awayScore && g.away_score !== undefined) awayScore.textContent = g.away_score;
          if (clock && g.clock) clock.textContent = g.clock;
          // If game just ended, update badge
          if (g.state === 'post') {
            var badge = card.querySelector('.live-badge');
            if (badge) {
              badge.className = 'final-badge';
              badge.innerHTML = 'FINAL';
            }
            card.classList.remove('live');
            card.classList.add('final');
          }
        });
      })
      .catch(function(){});
  }

  // Only poll if there are live games on the page
  if (hasLiveGames) {
    setInterval(updateLiveScores, POLL_INTERVAL);
  }

  // ── Game Notifications ─────────────────
  var NOTIFY_KEY = 'dv-notify-games';

  function getNotifyGames() {
    try {
      return JSON.parse(localStorage.getItem(NOTIFY_KEY) || '{}');
    } catch(e) { return {}; }
  }

  function saveNotifyGames(obj) {
    localStorage.setItem(NOTIFY_KEY, JSON.stringify(obj));
  }

  // Toggle notification for a game
  window.dvNotify = function(btn) {
    var gid = btn.getAttribute('data-game-id');
    var kickoff = btn.getAttribute('data-kickoff');
    var title = btn.getAttribute('data-title');
    var league = btn.getAttribute('data-league');
    var channels = btn.getAttribute('data-channels');
    var games = getNotifyGames();

    if (games[gid]) {
      // Already set — remove it
      delete games[gid];
      saveNotifyGames(games);
      btn.classList.remove('active');
      btn.innerHTML = btn.getAttribute('data-label-off') || '&#128276;';
      if (window.dvPush) window.dvPush.sync(true);
      return;
    }

    // Request notification permission if needed
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().then(function(perm) {
        if (perm === 'granted') {
          addNotifyGame(btn, gid, kickoff, title, league, channels);
        }
      });
    } else {
      addNotifyGame(btn, gid, kickoff, title, league, channels);
    }
  };

  function addNotifyGame(btn, gid, kickoff, title, league, channels) {
    var games = getNotifyGames();
    games[gid] = {
      kickoff: kickoff,
      title: title,
      league: league,
      channels: channels,
      notified: false
    };
    saveNotifyGames(games);
    btn.classList.add('active');
    btn.innerHTML = btn.getAttribute('data-label-on') || '&#128276; <span class="notify-label">Alertas</span>';
    // Push real (OneSignal): inicio, anotaciones y final aunque la pestaña esté cerrada
    if (window.dvPush) window.dvPush.enable({ add_games: [gid] });
  }

  // ── Push por equipo / partido (OneSignal v16 + /api/push/subscribe) ──
  // El id de suscripción de OneSignal se manda al servidor junto con dv_my_teams
  // (equipos seguidos) y los partidos con 🔔; el servidor manda el push solo a quien sigue.
  var PUSH_SYNC_KEY = 'dv-push-sync';
  function onOneSignal(cb) {
    window.OneSignalDeferred = window.OneSignalDeferred || [];
    window.OneSignalDeferred.push(function(OS) { try { cb(OS); } catch (e) {} });
  }
  function myTeams() { try { return JSON.parse(localStorage.getItem('dv_my_teams') || '[]'); } catch (e) { return []; } }
  function postSub(id, extra) {
    var body = { sub_id: id, teams: myTeams(), games: Object.keys(getNotifyGames()) };
    if (extra) for (var k in extra) body[k] = extra[k];
    return fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function() { localStorage.setItem(PUSH_SYNC_KEY, JSON.stringify({ t: Date.now(), teams: body.teams.join(','), games: body.games.length })); })
      .catch(function() {});
  }
  window.dvPush = {
    // Sincroniza equipos/partidos con el servidor si ya hay suscripción (barato: solo si cambió algo o pasó 1 día)
    sync: function(force) {
      onOneSignal(function(OS) {
        var ps = OS.User && OS.User.PushSubscription;
        if (!ps || !ps.id || ps.optedIn === false) return;
        var last = {}; try { last = JSON.parse(localStorage.getItem(PUSH_SYNC_KEY) || '{}'); } catch (e) {}
        var teams = myTeams().join(','), games = Object.keys(getNotifyGames()).length;
        if (!force && last.teams === teams && last.games === games && (Date.now() - (last.t || 0)) < 86400000) return;
        postSub(ps.id);
      });
    },
    // Pide permiso (si hace falta) y registra la suscripción con los equipos seguidos
    enable: function(extra) {
      onOneSignal(async function(OS) {
        try {
          if (!(OS.Notifications && OS.Notifications.permission)) await OS.Notifications.requestPermission();
          var ps = OS.User && OS.User.PushSubscription;
          if (ps && ps.optedIn === false) await ps.optIn();
          var tries = 0;
          (function waitId() {
            var id = OS.User && OS.User.PushSubscription && OS.User.PushSubscription.id;
            if (id) { postSub(id, extra); return; }
            if (tries++ < 10) setTimeout(waitId, 800);
          })();
        } catch (e) {}
      });
    }
  };
  // Al cargar: si ya está suscrito, mantener el servidor al día con los equipos seguidos
  setTimeout(function() { window.dvPush.sync(false); }, 4000);
  onOneSignal(function(OS) {
    try {
      OS.User.PushSubscription.addEventListener('change', function(ev) {
        if (ev && ev.current && ev.current.id && ev.current.optedIn !== false) postSub(ev.current.id);
      });
    } catch (e) {}
  });

  // Mark already-set notifications on page load
  document.addEventListener('DOMContentLoaded', function() {
    var games = getNotifyGames();
    var now = new Date();
    var changed = false;
    // Clean up past games
    for (var gid in games) {
      var ko = new Date(games[gid].kickoff);
      // Se conserva mientras el partido puede seguir en juego (hasta 5 h después del inicio)
      if (isNaN(ko.getTime()) || (now - ko) > 5 * 3600 * 1000) {
        delete games[gid];
        changed = true;
      }
    }
    if (changed) saveNotifyGames(games);
    // Highlight active bells
    var btns = document.querySelectorAll('.gc-notify-btn, .gd-follow-btn');
    for (var i = 0; i < btns.length; i++) {
      var id = btns[i].getAttribute('data-game-id');
      if (games[id]) {
        btns[i].classList.add('active');
        btns[i].innerHTML = btns[i].getAttribute('data-label-on') || '&#128276; <span class="notify-label">Alertas</span>';
      }
    }
  });

  // Check every 60s if any saved game is within 15 minutes
  setInterval(function() {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    var games = getNotifyGames();
    var now = new Date();
    var changed = false;
    for (var gid in games) {
      var g = games[gid];
      if (g.notified) continue;
      var ko = new Date(g.kickoff);
      var diffMin = (ko - now) / 60000;
      if (diffMin <= 15 && diffMin > -5) {
        // Fire notification
        var body = g.league + (g.channels ? ' · ' + g.channels : '') + ' · ¡En ' + Math.max(1, Math.round(diffMin)) + ' min!';
        try {
          new Notification('🏟️ ' + g.title, {
            body: body,
            icon: '/static/logo.png',
            tag: 'dv-game-' + gid,
            data: { url: '/juego/' + gid }
          });
        } catch(e) {}
        g.notified = true;
        changed = true;
      }
      // Clean up old entries
      if (diffMin < -30) {
        delete games[gid];
        changed = true;
      }
    }
    if (changed) saveNotifyGames(games);
  }, 60000);
})();
