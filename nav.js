/* ============================================================
   CCC SHARED NAV — nav.js
   Injects nav HTML, handles dropdowns, dashboard visibility
   ============================================================ */
(function() {
  const PAGE = location.pathname.split('/').pop() || 'index.html';

  function isActive(href) {
    if (href === '#') return false;
    return PAGE === href;
  }
  function isInGroup(pages) {
    return pages.some(p => PAGE === p);
  }

  const learningActive = isInGroup(['learn.html','puzzles.html','openings.html']);
  const clubActive = isInGroup(['tournaments.html','news.html','strategy.html','leaderboard.html','leagues.html']);

  // Build nav HTML
  const navEl = document.getElementById('cccNav');
  if (!navEl) return;

  // Determine what extra right-side content exists (like XP bars, streak badges, difficulty selectors)
  // We preserve any existing .ccc-nav-right-extra content
  const extraRight = navEl.querySelector('.ccc-nav-right-extra');

  navEl.className = 'ccc-nav';
  navEl.innerHTML = `
    <a href="index.html" class="logo">
      <div class="logo-icon"><span></span><span></span><span></span><span></span></div>
      <div class="logo-text">The Classical Chess <b>Collegium</b></div>
    </a>
    <ul class="ccc-nav-items">
      <li class="ccc-nav-item ${PAGE === 'index.html' ? 'active' : ''}">
        <a href="index.html">Home</a>
      </li>
      <li class="ccc-nav-item ${learningActive ? 'active' : ''}" data-dropdown="learning">
        <button onclick="cccToggleDD(this)">Learning <span class="chevron">▾</span></button>
        <div class="ccc-dropdown">
          <a href="learn.html"><span class="dd-icon">♟</span> Academy</a>
          <a href="puzzles.html"><span class="dd-icon">♛</span> Puzzles</a>
          <a href="openings.html"><span class="dd-icon">♝</span> Openings</a>
          <div class="dd-divider"></div>
          <a href="assessment.html"><span class="dd-icon">♚</span> Cognitive Assessment</a>
        </div>
      </li>
      <li class="ccc-nav-item ${PAGE === 'play.html' ? 'active' : ''}">
        <a href="play.html">Play</a>
      </li>
      <li class="ccc-nav-item ${clubActive ? 'active' : ''}" data-dropdown="club">
        <button onclick="cccToggleDD(this)">Club <span class="chevron">▾</span></button>
        <div class="ccc-dropdown">
          <a href="tournaments.html"><span class="dd-icon">🏆</span> Tournament Locator</a>
          <a href="leagues.html"><span class="dd-icon">⚔</span> Leagues &amp; Standings</a>
          <a href="leaderboard.html"><span class="dd-icon">👑</span> Top Players</a>
          <div class="dd-divider"></div>
          <a href="news.html"><span class="dd-icon">📰</span> Chess News</a>
          <a href="strategy.html"><span class="dd-icon">📖</span> Strategy &amp; Tips</a>
        </div>
      </li>
      <li class="ccc-nav-item ${PAGE === 'profile.html' ? 'active' : ''}">
        <a href="profile.html">Profile</a>
      </li>
      <li class="ccc-nav-item teacher-only" id="cccDashboardItem">
        <a href="dashboard.html">Dashboard</a>
      </li>
    </ul>
    <div class="ccc-nav-right" id="cccNavRight"></div>
  `;

  // Re-insert any extra right-side content
  if (extraRight) {
    document.getElementById('cccNavRight').appendChild(extraRight);
  }

  // ── DROPDOWN CLICK LOGIC ──
  window.cccToggleDD = function(btn) {
    const item = btn.closest('.ccc-nav-item');
    const wasOpen = item.classList.contains('open');
    // Close all
    document.querySelectorAll('.ccc-nav-item.open').forEach(el => el.classList.remove('open'));
    // Toggle this one
    if (!wasOpen) item.classList.add('open');
  };

  // Close dropdowns when clicking outside
  document.addEventListener('click', function(e) {
    if (!e.target.closest('.ccc-nav-item[data-dropdown]')) {
      document.querySelectorAll('.ccc-nav-item.open').forEach(el => el.classList.remove('open'));
    }
  });

  // ── DASHBOARD VISIBILITY (teacher/parent only) ──
  // This checks auth via any existing supabase instance on the page
  function checkDashboardAccess() {
    if (!window.supabase) return;
    const SB_URL = 'https://nlsmrveieqadjmytsmjm.supabase.co';
    const SB_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5sc21ydmVpZXFhZGpteXRzbWptIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE3MTg4NjcsImV4cCI6MjA4NzI5NDg2N30.9uIWjPJsOhdXXHqGLIMcdY5eitbkGoHyhbreAEe-apM';
    try {
      const sb = window.supabase.createClient(SB_URL, SB_KEY, { auth: { persistSession:true, storageKey:'ccc-auth' } });
      sb.auth.getSession().then(({ data: { session } }) => {
        if (!session) return;
        sb.from('profiles').select('role').eq('id', session.user.id).single().then(({ data }) => {
          if (data?.role === 'teacher' || data?.role === 'parent') {
            const el = document.getElementById('cccDashboardItem');
            if (el) el.classList.add('show');
          }
        });
      });
    } catch(e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkDashboardAccess);
  } else {
    checkDashboardAccess();
  }
})();
