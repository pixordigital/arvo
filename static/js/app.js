(function() {
  // Theme toggle
  const THEME_KEY = 'arvo-theme';
  const html = document.documentElement;
  const toggles = document.querySelectorAll('#theme-toggle-topbar, #theme-toggle-desktop');

  function applyTheme(theme) {
    html.dataset.theme = theme;
    html.classList.toggle('dark', theme === 'dark');
    html.classList.toggle('light', theme === 'light');
    localStorage.setItem(THEME_KEY, theme);
  }

  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));
  }

  toggles.forEach(btn => {
    btn.addEventListener('click', () => {
      const current = html.dataset.theme;
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  });

  // Watch system preference
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    if (!localStorage.getItem(THEME_KEY)) applyTheme(e.matches ? 'dark' : 'light');
  });

  initTheme();

  // Sidebar: desktop collapse rail vs mobile drawer are separate behaviors.
  // (Was: burger bound to both, overlay called the wrong closer, and the
  // collapse toggle hid itself — a one-way trap. Each fixed below.)
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  const collapseBtn = document.getElementById('sidebar-toggle');
  const drawerBtn = document.getElementById('sidebar-toggle-mobile');
  const mqDesktop = window.matchMedia('(min-width: 768px)');
  const norm = (p) => (p.length > 1 ? p.replace(/\/+$/, '') : p);

  function setCollapsed(collapse) {
    // Look .main up fresh: htmx swaps used to replace it, leaving stale refs.
    const main = document.querySelector('.main');
    sidebar.classList.toggle('collapsed', collapse);
    main?.classList.toggle('sidebar-collapsed', collapse);
    collapseBtn?.setAttribute('aria-expanded', String(!collapse));
    try { localStorage.setItem('arvo-sidebar-collapsed', String(collapse)); } catch { /* private mode */ }
  }

  function openDrawer() {
    sidebar.classList.add('open');
    overlay?.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    drawerBtn?.setAttribute('aria-expanded', 'true');
    // Move focus into the drawer for keyboard users.
    sidebar.querySelector('.sidebar-nav a')?.focus({ preventScroll: true });
  }
  function closeDrawer() {
    if (!sidebar.classList.contains('open')) return;
    sidebar.classList.remove('open');
    overlay?.classList.add('hidden');
    document.body.style.overflow = '';
    drawerBtn?.setAttribute('aria-expanded', 'false');
  }

  collapseBtn?.addEventListener('click', () => {
    setCollapsed(!sidebar.classList.contains('collapsed'));
  });
  drawerBtn?.addEventListener('click', () => {
    // Mobile burger only drives the drawer — never desktop collapse state.
    if (sidebar.classList.contains('open')) closeDrawer();
    else openDrawer();
  });
  overlay?.addEventListener('click', closeDrawer);

  // Restore desktop rail state (desktop only; never leak collapse to mobile).
  try {
    if (mqDesktop.matches && localStorage.getItem('arvo-sidebar-collapsed') === 'true') setCollapsed(true);
    else setCollapsed(false);
  } catch { /* private mode */ }
  mqDesktop.addEventListener?.('change', (e) => {
    if (!e.matches) { setCollapsed(false); closeDrawer(); }
  });

  // Close drawer on navigation (plain links; chrome no longer htmx-swaps).
  document.body.addEventListener('htmx:beforeRequest', () => closeDrawer());
  document.querySelectorAll('.sidebar-nav a').forEach((a) => {
    a.addEventListener('click', () => closeDrawer());
  });

  // Active-state sync (server already renders aria-current; this only
  // normalizes trailing slashes client-side).
  function syncActiveState() {
    const path = norm(window.location.pathname);
    document.querySelectorAll('.nav-item, .sidebar-nav a, .pill').forEach((el) => {
      const href = el.getAttribute('href');
      if (!href || !href.startsWith('/')) return;
      if (norm(href.split('?')[0]) === path) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });
  }

  document.body.addEventListener('htmx:afterSwap', syncActiveState);
  window.addEventListener('popstate', syncActiveState);
  syncActiveState();

  // Toast helper — elements with [data-toast] show transient feedback
  let toastTimer;
  function toast(msg, kind) {
    const el = document.getElementById('arvo-toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast show' + (kind ? ' toast-' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-toast]');
    if (t) toast(t.getAttribute('data-toast'), 'success');
  });

  // Copy helper used by settings page
  window.copyEnv = async function(key) {
    try {
      await navigator.clipboard.writeText(key);
      toast('Copiado: ' + key, 'success');
    } catch {
      toast('Não foi possível copiar', 'error');
    }
  };
  // Table scroll hint visibility
  function updateTableHints() {
    document.querySelectorAll('.table-wrap').forEach(wrap => {
      const table = wrap.querySelector('table');
      if (table && table.scrollWidth > wrap.clientWidth) {
        wrap.classList.add('scrollable');
      } else {
        wrap.classList.remove('scrollable');
      }
    });
  }

  // Check on load and resize
  updateTableHints();
  window.addEventListener('resize', updateTableHints);
  // Also check after HTMX swaps
  document.body.addEventListener('htmx:afterSwap', updateTableHints);

  // Keyboard: Escape closes the drawer, focus returns to the burger.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && sidebar?.classList.contains('open')) {
      closeDrawer();
      drawerBtn?.focus({ preventScroll: true });
    }
  });

  // Prevent body scroll when sidebar open on mobile
  const observer = new MutationObserver(() => {
    if (sidebar?.classList.contains('open')) {
      document.body.style.overflow = 'hidden';
    }
  });
  observer.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
})();