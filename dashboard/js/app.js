/* ============================================================================
   SteamGuard Dashboard — shared client utilities
   Rivvak Community
   ========================================================================== */

const API_BASE = 'https://steamguard-775181381055.us-central1.run.app';

const REWARD_ICONS = {
  invite_friend:   'fa-user-plus',
  daily_check_in:  'fa-calendar-check',
  weekly_streak:   'fa-fire',
  youtube_sub:     'fa-youtube',
  server_boost:    'fa-rocket',
  share_card_post: 'fa-share-nodes',
  bug_report:      'fa-bug',
  first_heal:      'fa-heart-pulse',
};

const REWARD_NAMES = {
  invite_friend:   'Invite a Friend',
  daily_check_in:  'Daily Check-In',
  weekly_streak:   '7-Day Streak',
  youtube_sub:     'YouTube Sub',
  server_boost:    'Server Boost',
  share_card_post: 'Share Stats Card',
  bug_report:      'Bug Report',
  first_heal:      'First Heal',
};

/* --- token / user helpers ------------------------------------------------- */
function getToken() { return localStorage.getItem('sg_token'); }
function getUser()  { try { return JSON.parse(localStorage.getItem('sg_user') || '{}'); } catch { return {}; } }

/* --- API fetch wrapper ---------------------------------------------------- */
async function apiFetch(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`,
        ...(options.headers || {}),
      },
    });
  } catch (err) {
    console.error('Network error', path, err);
    return null;
  }
  if (res.status === 401) { logout(); return null; }
  if (!res.ok) {
    console.warn(`API ${path} -> ${res.status}`);
    try { return await res.json(); } catch { return null; }
  }
  try { return await res.json(); } catch { return null; }
}

/* --- auth flow helpers ---------------------------------------------------- */
function logout() {
  localStorage.clear();
  window.location.href = '/dashboard/index.html';
}

function requireAuth(adminRequired = false) {
  const token = getToken();
  if (!token) { window.location.href = '/dashboard/index.html'; return false; }
  const user = getUser();
  if (adminRequired && !user.is_admin) { window.location.href = '/dashboard/user.html'; return false; }
  return true;
}

/* --- formatting ----------------------------------------------------------- */
function formatDuration(hours) {
  if (hours === null || hours === undefined) return '—';
  if (hours >= 999) return '∞';
  const h = Math.floor(hours);
  const m = Math.floor((hours - h) * 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  }
  return `${h}h ${m}m`;
}

function formatDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return '—'; }
}

function timeAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then) / 1000;
  if (diff < 60)    return `${Math.floor(diff)}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/* --- clipboard ------------------------------------------------------------ */
async function copyToClipboard(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
  if (btn) {
    const orig = btn.dataset.label || btn.innerHTML;
    btn.dataset.label = orig;
    btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = orig; btn.classList.remove('copied'); }, 2000);
  }
}

/* --- toast notifications -------------------------------------------------- */
function showToast(message, type = 'info') {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    host.className = 'toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const icon = type === 'success' ? 'fa-circle-check'
             : type === 'error'   ? 'fa-circle-exclamation'
             : 'fa-circle-info';
  el.innerHTML = `<i class="fa-solid ${icon}"></i><span>${message}</span>`;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

/* --- single-page section switching --------------------------------------- */
function initNav() {
  const links = document.querySelectorAll('[data-section]');
  links.forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const target = link.getAttribute('data-section');
      switchSection(target);
      // close mobile drawer if open
      document.body.classList.remove('nav-open');
    });
  });
  // restore from hash
  const initial = (location.hash || '').replace('#', '') || 'overview';
  switchSection(initial);
}

function switchSection(name) {
  document.querySelectorAll('.section').forEach((s) => {
    s.classList.toggle('hidden', s.id !== `section-${name}`);
  });
  document.querySelectorAll('[data-section]').forEach((l) => {
    l.classList.toggle('active', l.getAttribute('data-section') === name);
  });
  location.hash = name;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* --- mobile nav toggle ---------------------------------------------------- */
function initMobileNav() {
  const btn = document.getElementById('mobile-menu-btn');
  if (btn) btn.addEventListener('click', () => document.body.classList.toggle('nav-open'));
  const overlay = document.getElementById('nav-overlay');
  if (overlay) overlay.addEventListener('click', () => document.body.classList.remove('nav-open'));
}

/* --- skeleton helper ------------------------------------------------------ */
function skeletonText(width = '100%') {
  return `<span class="skeleton skeleton-text" style="width:${width}"></span>`;
}
