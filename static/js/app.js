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

  // Sidebar collapse (desktop)
  const sidebar = document.querySelector('.sidebar');
  const sidebarToggles = document.querySelectorAll('#sidebar-toggle, #sidebar-toggle-mobile');
  const overlay = document.getElementById('sidebar-overlay');
  const main = document.querySelector('.main');

  function toggleSidebar(collapse) {
    if (collapse === undefined) collapse = !sidebar.classList.contains('collapsed');
    sidebar.classList.toggle('collapsed', collapse);
    main.classList.toggle('sidebar-collapsed', collapse);
    localStorage.setItem('arvo-sidebar-collapsed', collapse);
  }

  sidebarToggles.forEach(btn => btn.addEventListener('click', () => toggleSidebar()));
  overlay?.addEventListener('click', () => toggleSidebar(false));

  // Mobile sidebar open/close
  function openSidebar() {
    sidebar.classList.add('open');
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  }
  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.add('hidden');
    document.body.style.overflow = '';
  }

  // Restore sidebar state
  if (localStorage.getItem('arvo-sidebar-collapsed') === 'true') toggleSidebar(true);

  // Mobile sidebar toggle
  const mobileToggle = document.getElementById('sidebar-toggle-mobile');
  if (mobileToggle) {
    mobileToggle.addEventListener('click', () => {
      if (sidebar.classList.contains('open')) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });
  }

  // Close sidebar on navigation (mobile)
  document.body.addEventListener('htmx:beforeRequest', () => {
    if (window.innerWidth < 768) closeSidebar();
  });

  // Bottom nav + sidebar active state sync (CSS owns colors via [aria-current])
  function syncActiveState() {
    const path = window.location.pathname;
    document.querySelectorAll('.nav-item, .sidebar-nav a, .pill').forEach(el => {
      const href = el.getAttribute('href');
      if (!href) return;
      const isActive = href === path;
      if (isActive) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });
  }

  document.body.addEventListener('htmx:afterSwap', syncActiveState);
  window.addEventListener('popstate', syncActiveState);
  // Initial sync
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

  // Keyboard navigation for sidebar (desktop)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && sidebar?.classList.contains('open')) {
      closeSidebar();
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