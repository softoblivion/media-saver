// Engine status chip in the title bar, its hover card with versions and the popover with components
// and "Update now" (update_engine).

import { call, on } from './api.js';
import { Lens, gel } from './glass.js';
import { Overlay } from './overlay.js';
import { icon } from './icons.js';
import { escapeHtml, dateTime } from './format.js';
import { toast } from './toast.js';

const BUSY = new Set(['starting', 'checking', 'downloading', 'updating']);
const LABEL = {
  starting: 'Starting',
  checking: 'Checking',
  downloading: 'Updating',
  updating: 'Updating',
  ready: 'Engine ready',
  offline: 'Offline',
  error: 'Engine error',
};
const COMPONENT_NAMES = { 'yt-dlp': 'yt-dlp', 'gallery-dl': 'gallery-dl', ffmpeg: 'FFmpeg', deno: 'Deno' };

let chip, pop, overlay, hover, status = null;

export function initEngine(initial) {
  chip = document.getElementById('engine-chip');
  chip.innerHTML = `
    <span class="eng-ind" aria-hidden="true">
      <svg viewBox="0 0 20 20" class="eng-ring"><circle cx="10" cy="10" r="7.5" class="eng-ring-track"/><circle cx="10" cy="10" r="7.5" class="eng-ring-bar" pathLength="100"/></svg>
      <span class="eng-dot"></span>
    </span>
    <span class="eng-text"></span>`;
  new Lens(chip, 'pill');
  gel(chip);

  hover = document.createElement('div');
  hover.className = 'eng-hover glass-lite';
  hover.setAttribute('role', 'tooltip');
  hover.hidden = true;
  document.body.appendChild(hover);
  let hoverTimer = 0;
  chip.addEventListener('pointerenter', () => {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => showHover(true), 380);
  });
  chip.addEventListener('pointerleave', () => {
    clearTimeout(hoverTimer);
    showHover(false);
  });

  pop = document.getElementById('engine-pop');
  overlay = new Overlay(pop, {
    preset: 'sheet',
    place(surface, origin) {
      const r = origin.getBoundingClientRect();
      surface.style.top = `${Math.round(r.bottom + 10)}px`;
      surface.style.right = `${Math.max(12, Math.round(innerWidth - r.right - 40))}px`;
    },
  });
  chip.addEventListener('click', () => {
    clearTimeout(hoverTimer);
    showHover(false);
    overlay.toggle(chip);
  });
  pop.addEventListener('click', (e) => {
    if (e.target.closest('[data-act="update"]')) {
      call('update_engine').catch((err) => toast('error', err.message));
    }
  });

  on('engine', render);
  render(initial);
}

function showHover(v) {
  if (v && !overlay.open && status) {
    const r = chip.getBoundingClientRect();
    hover.innerHTML = componentsLine(status);
    hover.hidden = false;
    hover.style.top = `${Math.round(r.bottom + 8)}px`;
    hover.style.left = `${Math.round(Math.min(innerWidth - hover.offsetWidth - 12, r.left + r.width / 2 - hover.offsetWidth / 2))}px`;
    hover.animate([{ opacity: 0, transform: 'translateY(-4px)' }, { opacity: 1, transform: 'none' }], { duration: 180, easing: 'ease-out' });
  } else {
    hover.hidden = true;
  }
}

function componentsLine(s) {
  const c = s.components || {};
  const parts = Object.keys(COMPONENT_NAMES)
    .filter((k) => c[k])
    .map((k) => `<span><b>${COMPONENT_NAMES[k]}</b> ${escapeHtml(c[k].version || '—')}</span>`);
  return parts.length ? parts.join('<i>·</i>') : 'Versions will appear after the check';
}

function render(s) {
  if (!s) return;
  status = s;
  const busy = BUSY.has(s.stage);
  chip.dataset.stage = s.stage;
  chip.classList.toggle('is-busy', busy);
  chip.classList.toggle('is-determinate', busy && s.progress != null);
  const text = busy && s.progress != null ? `${LABEL[s.stage]} ${Math.round(s.progress * 100)}%` : LABEL[s.stage] || s.stage;
  chip.querySelector('.eng-text').textContent = text;
  chip.style.setProperty('--ep', String(s.progress ?? 0.25));
  chip.setAttribute('aria-label', `Engine: ${s.message || text}. Click for details`);
  if (!hover.hidden) hover.innerHTML = componentsLine(s);
  renderPop(s);
}

function renderPop(s) {
  const c = s.components || {};
  const busy = BUSY.has(s.stage);
  const rows = Object.keys(COMPONENT_NAMES).map((k) => {
    const x = c[k];
    if (!x) return '';
    const tags = [];
    if (x.channel) tags.push(x.channel === 'nightly' ? 'nightly' : 'stable');
    if (x.source) tags.push(x.source === 'system' ? 'system' : 'bundled');
    if (x.updated) tags.push('<em class="tag-new">updated</em>');
    return `<li class="comp ${x.ok ? '' : 'is-bad'}">
      <span class="comp-ic">${icon(x.ok ? 'check' : 'warn')}</span>
      <span class="comp-name">${COMPONENT_NAMES[k]}</span>
      <span class="comp-ver">${escapeHtml(x.version || '—')}</span>
      <span class="comp-tags">${tags.map((t) => (t.startsWith('<') ? t : `<em>${t}</em>`)).join('')}</span>
    </li>`;
  }).join('');
  pop.querySelector('.ov-content').innerHTML = `
    <div class="pop-head">
      <div class="pop-title">Download engine</div>
      <div class="pop-sub">${escapeHtml(s.message || LABEL[s.stage] || '')}</div>
    </div>
    ${busy ? `<div class="pop-progress ${s.progress == null ? 'is-indet' : ''}" style="--p:${s.progress ?? 0}"><span></span></div>` : ''}
    <ul class="comp-list">${rows || '<li class="comp-empty">Checking components…</li>'}</ul>
    <div class="pop-foot">
      <span class="pop-meta">${s.last_check ? `Checked ${escapeHtml(dateTime(s.last_check))}` : 'Not checked yet'}</span>
      <button type="button" class="btn btn-tinted ov-focus" data-act="update" ${busy ? 'disabled' : ''}>${icon('refresh')}<span>Update now</span></button>
    </div>`;
  pop.querySelectorAll('.btn').forEach((b) => gel(b));
}
