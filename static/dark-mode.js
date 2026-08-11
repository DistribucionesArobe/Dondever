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
})();
