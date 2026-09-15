// ARVO — app.js (vanilla, progressive enhancement)
// CSRF for POST forms: read meta if added later
document.addEventListener('DOMContentLoaded', () => {
  // HTMX CSRF example (if token in meta)
  const t = document.querySelector('meta[name="csrf-token"]');
  if (t) document.body.addEventListener('htmx:configRequest', e => { e.detail.headers['X-CSRF-Token'] = t.content; });
});
