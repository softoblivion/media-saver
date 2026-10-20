// App self-update: the version label in the title bar and its popover (same family as the engine
// popover). States come from UpdateStatus (docs/BRIDGE_API.md): idle / up_to_date -> "Check for
// updates"; checking -> indeterminate bar; available -> latest version, size, date and release notes,
// "Download"; downloading -> determinate bar; ready -> "Restart to update" (apply_update);
// error -> the message and "Try again". The first "available" of a session raises one toast.

import { call, on } from './api.js';
import { gel } from './glass.js';
import { Overlay } from './overlay.js';
import { icon } from './icons.js';
import { escapeHtml, bytes, shortDate, timeAgo } from './format.js';
import { toast } from './toast.js';

const IDLE = { stage: 'idle', current: '', latest: null, notes: null, size: null, published_at: null, progress: null, message: '', checked_at: null, can_apply: false };
const CAN_CHECK = new Set(['idle', 'up_to_date', 'error']);

let label, pop, overlay;
let u = IDLE;
let lastBusy = null;      // 'checking' | 'downloading': what an error interrupted
let toasted = false;
let hasActiveJobs = () => false;

/**
 * @param {object} state  State from ready()
 * @param {{activeJobs?: () => boolean}} opts
 */
export function initUpdate(state, opts = {}) {
  if (opts.activeJobs) hasActiveJobs = opts.activeJobs;
  label = document.getElementById('app-ver');
  pop = document.getElementById('update-pop');
  overlay = new Overlay(pop, {
    preset: 'sheet',
    place(surface, origin) {
      const r = (origin || label).getBoundingClientRect();
      surface.style.top = `${Math.round(r.bottom + 10)}px`;
      surface.style.right = `${Math.max(12, Math.round(innerWidth - r.right - 60))}px`;
    },
  });
  gel(label);
  label.addEventListener('click', () => {
    if (!overlay.open) renderPop();
    overlay.toggle(label);
  });
  pop.addEventListener('click', onClick);
  on('update', render);
  render({ ...IDLE, current: state.app_version || '', ...(state.update || {}) });
}

/** Open the panel from elsewhere (settings "Check for updates"); optionally start a check. */
export function openUpdatePanel({ check = false } = {}) {
  renderPop();
  overlay.show(label);
  if (check && CAN_CHECK.has(u.stage)) runCheck();
}

export function appVersion() {
  return u.current || '';
}

function render(next) {
  if (!next) return;
  const prev = u;
  u = { ...IDLE, ...next };
  if (u.stage === 'checking' || u.stage === 'downloading') lastBusy = u.stage;
  label.dataset.stage = u.stage;
  label.querySelector('.ver-text').textContent = u.current ? `v${u.current}` : 'Version';
  const tip = {
    available: `Media Saver ${u.latest} is available`,
    downloading: `Downloading Media Saver ${u.latest}`,
    ready: `Media Saver ${u.latest} is ready: restart to update`,
  }[u.stage] || 'Media Saver version and updates';
  label.title = tip;
  label.setAttribute('aria-label', tip);
  if (u.stage === 'available' && !toasted) {
    toasted = true;
    toast('info', `Media Saver ${u.latest} is available`, {
      ms: 8000,
      action: { label: 'View', run: () => openUpdatePanel() },
    });
  }
  if (overlay.open || prev.stage !== u.stage) renderPop();
}

function subline() {
  const d = {
    idle: 'Updates come from GitHub Releases',
    checking: 'Checking for updates…',
    up_to_date: 'You have the latest version',
    available: `Version ${u.latest} is available`,
    downloading: `Downloading version ${u.latest}…`,
    ready: `Version ${u.latest} is ready to install`,
    error: lastBusy === 'downloading' ? 'The download did not finish' : 'Could not check for updates',
  }[u.stage];
  if (u.stage === 'error') return d;
  return u.message || d || '';
}

let popKey = '';

function renderPop() {
  const busy = u.stage === 'checking' || u.stage === 'downloading';
  // Progress ticks only move the bar: a rebuild would reset the scroll of the release notes.
  const key = [u.stage, u.current, u.latest, u.size, u.published_at, !!u.notes, u.can_apply, u.message, u.checked_at, u.progress == null].join('|');
  if (key === popKey && u.stage === 'downloading') {
    const bar = pop.querySelector('.pop-progress');
    if (bar) bar.style.setProperty('--p', String(u.progress ?? 0));
    const pct = pop.querySelector('.upd-pct');
    if (pct && u.progress != null) {
      pct.querySelector('b').textContent = `${Math.round(u.progress * 100)}%`;
      pct.querySelector('span').textContent = u.size ? `${bytes(u.size * u.progress)} of ${bytes(u.size)}` : '';
    }
    return;
  }
  popKey = key;
  const newer = !!u.latest && ['available', 'downloading', 'ready'].includes(u.stage);
  const rows = [];
  rows.push(`<li class="comp">
      <span class="comp-ic">${icon('app')}</span>
      <span class="comp-name">Installed</span>
      <span class="comp-ver">${escapeHtml(u.current ? `v${u.current}` : '—')}</span>
      <span class="comp-tags"><em>running</em></span>
    </li>`);
  if (u.latest && u.latest !== u.current) {
    const tags = [];
    if (u.size) tags.push(escapeHtml(bytes(u.size)));
    if (u.published_at) tags.push(escapeHtml(shortDate(u.published_at)));
    if (u.stage === 'ready') tags.push('<em class="tag-new">downloaded</em>');
    rows.push(`<li class="comp ${u.stage === 'ready' ? 'is-ready' : 'is-new'}">
      <span class="comp-ic">${icon(u.stage === 'ready' ? 'check' : 'down')}</span>
      <span class="comp-name">Latest</span>
      <span class="comp-ver">${escapeHtml(`v${u.latest}`)}</span>
      <span class="comp-tags">${tags.map((t) => (t.startsWith('<') ? t : `<em>${t}</em>`)).join('')}</span>
    </li>`);
  }
  let progress = '';
  if (u.stage === 'checking') progress = '<div class="pop-progress is-indet"><span></span></div>';
  if (u.stage === 'downloading') {
    const p = u.progress;
    progress = `<div class="pop-progress ${p == null ? 'is-indet' : ''}" style="--p:${p ?? 0}"><span></span></div>`;
    if (p != null) {
      const got = u.size ? `${bytes(u.size * p)} of ${bytes(u.size)}` : '';
      progress += `<div class="upd-pct"><b>${Math.round(p * 100)}%</b><span>${escapeHtml(got)}</span></div>`;
    }
  }
  const notes = newer && u.notes ? `<div class="upd-notes" tabindex="0" aria-label="Release notes">${renderMarkdown(u.notes)}</div>` : '';
  let box = '';
  if (u.stage === 'error' && u.message) box = `<p class="upd-msg" role="alert">${escapeHtml(u.message)}</p>`;
  let action;
  switch (u.stage) {
    case 'checking':
      action = `<button type="button" class="btn ov-focus" disabled>${icon('refresh')}<span>Checking…</span></button>`;
      break;
    case 'available':
      action = `<button type="button" class="btn btn-tinted ov-focus" data-act="download">${icon('down')}<span>Download</span></button>`;
      break;
    case 'downloading':
      action = `<button type="button" class="btn ov-focus" disabled>${icon('down')}<span>Downloading…</span></button>`;
      break;
    case 'ready':
      action = `<button type="button" class="btn btn-tinted ov-focus" data-act="apply" ${u.can_apply ? '' : 'disabled'} ${u.can_apply ? '' : `title="${escapeHtml(u.message || 'Available in the packaged app only')}"`}>${icon('refresh')}<span>Restart to update</span></button>`;
      break;
    case 'error':
      action = `<button type="button" class="btn ov-focus" data-act="retry">${icon('retry')}<span>Try again</span></button>`;
      break;
    default:
      action = `<button type="button" class="btn ov-focus" data-act="check">${icon('refresh')}<span>Check for updates</span></button>`;
  }
  pop.querySelector('.ov-content').innerHTML = `
    <div class="pop-head">
      <div class="pop-title">Media Saver</div>
      <div class="pop-sub">${escapeHtml(subline())}</div>
    </div>
    ${progress}
    ${box}
    <ul class="comp-list upd-rows">${rows.join('')}</ul>
    ${notes}
    <div class="pop-foot">
      <span class="pop-meta">${u.checked_at ? `Checked ${escapeHtml(timeAgo(u.checked_at))}` : 'Not checked yet'}</span>
      ${action}
    </div>`;
  pop.setAttribute('aria-busy', String(busy));
  pop.querySelectorAll('.btn').forEach((b) => gel(b));
}

function runCheck() {
  call('check_updates').then((s) => s && render(s)).catch((err) => toast('error', err.message));
}

function onClick(e) {
  const a = e.target.closest('a[data-url]');
  if (a) {
    e.preventDefault();
    call('open_url', a.dataset.url).catch((err) => toast('error', err.message));
    return;
  }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const fail = (err) => toast('error', err.message);
  switch (b.dataset.act) {
    case 'check':
      return runCheck();
    case 'download':
      return call('download_update').catch(fail);
    case 'retry':
      // Retry what the error interrupted: the download when a newer version is known, else the check.
      if (lastBusy === 'downloading' && u.latest) return call('download_update').catch(fail);
      return runCheck();
    case 'apply': {
      const go = () => call('apply_update').catch(fail);
      if (hasActiveJobs()) {
        return toast('warning', 'Downloads in progress will stop when the app restarts', {
          ms: 8000,
          action: { label: 'Restart anyway', run: go },
        });
      }
      return go();
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Minimal, safe Markdown for release notes: headings, bullets, paragraphs, **bold**, `code` and
// https links (opened through open_url). Everything is HTML-escaped first; nothing else is parsed.

export function renderMarkdown(md) {
  const lines = String(md || '').replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let para = [];
  let inList = false;
  const flush = () => {
    if (para.length) html += `<p>${inline(para.join(' '))}</p>`;
    para = [];
  };
  const closeList = () => {
    if (inList) html += '</ul>';
    inList = false;
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    let m;
    if (!line.trim()) {
      flush();
      closeList();
      continue;
    }
    if ((m = line.match(/^ {0,3}(#{1,6})\s+(.*?)\s*#*$/))) {
      flush();
      closeList();
      html += `<h4${m[1].length === 1 ? ' class="h-top"' : ''}>${inline(m[2])}</h4>`;
      continue;
    }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flush();
      if (!inList) html += '<ul>';
      inList = true;
      html += `<li>${inline(m[1])}</li>`;
      continue;
    }
    if (inList && /^\s{2,}\S/.test(line)) {
      // Continuation line of the previous bullet.
      html = html.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`);
      continue;
    }
    closeList();
    para.push(line.trim());
  }
  flush();
  closeList();
  return html;
}

function inline(s) {
  const codes = [];
  let t = escapeHtml(s).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/__(.+?)__/g, '<strong>$1</strong>');
  t = t.replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, (_, text, url) => `<a href="#" data-url="${url}">${text}</a>`);
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}
