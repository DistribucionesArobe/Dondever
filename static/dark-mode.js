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
    // Update toggle button icon
    var btn = document.getElementById('dm-btn');
    if (btn) btn.textContent = next === 'dark' ? '☀️' : '🌙';
    updateThemeMeta();
  };

  // Set initial icon when DOM is ready
  document.addEventListener('DOMContentLoaded', function(){
    var btn = document.getElementById('dm-btn');
    if (btn) {
      btn.textContent = html.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
    }
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
      btn.innerHTML = '&#128276;';
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
    btn.innerHTML = '&#128276; <span class="notify-label">15 min</span>';
  }

  // Mark already-set notifications on page load
  document.addEventListener('DOMContentLoaded', function() {
    var games = getNotifyGames();
    var now = new Date();
    var changed = false;
    // Clean up past games
    for (var gid in games) {
      var ko = new Date(games[gid].kickoff);
      if (ko < now) {
        delete games[gid];
        changed = true;
      }
    }
    if (changed) saveNotifyGames(games);
    // Highlight active bells
    var btns = document.querySelectorAll('.gc-notify-btn');
    for (var i = 0; i < btns.length; i++) {
      var id = btns[i].getAttribute('data-game-id');
      if (games[id]) {
        btns[i].classList.add('active');
        btns[i].innerHTML = '&#128276; <span class="notify-label">15 min</span>';
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
