// Toasts: glass capsules dropping from the top centre with a spring, at most three on screen.
// Host: the element passed to initToasts(), else the page's `#toasts` (looked up on first use).

import { springAnimate, SPRINGS, whenDone } from './motion.js';
import { Lens, gel } from './glass.js';
import { icon } from './icons.js';
import { escapeHtml } from './format.js';

const MAX = 3;
const ICONS = { info: 'info', success: 'check', warning: 'warn', error: 'warn' };
let root = null;

export function initToasts(el = document.getElementById('toasts')) {
  root = el;
}

/**
 * @param {'info'|'success'|'warning'|'error'} level
 * @param {string} text one short English sentence
 * @param {{action?:{label:string, run:Function}, ms?:number, sticky?:boolean}} opts
 */
export function toast(level, text, opts = {}) {
  if (!root) root = document.getElementById('toasts');
  if (!root || !text) return null;
  level = ICONS[level] ? level : 'info';
  for (const t of root.children) {
    if (t.dataset.text === text && !t.classList.contains('is-leaving')) {
      t.__gel && t.__gel.pulse();
      clearTimeout(t.__timer);
      if (!t.__sticky) t.__timer = setTimeout(() => dismiss(t), opts.ms || 3800);
      return t;
    }
  }
  const el = document.createElement('div');
  el.className = `toast toast-${level}`;
  el.dataset.text = text;
  el.setAttribute('role', level === 'error' ? 'alert' : 'status');
  el.innerHTML = `<span class="toast-ic">${icon(ICONS[level])}</span><span class="toast-text">${escapeHtml(text)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'toast-action';
    b.textContent = opts.action.label;
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      opts.action.run();
      dismiss(el);
    });
    el.appendChild(b);
  }
  el.addEventListener('click', () => dismiss(el));
  const live = Array.from(root.children).filter((t) => !t.classList.contains('is-leaving'));
  if (live.length >= MAX) dismiss(live.filter((t) => !t.__sticky).pop() || live[live.length - 1]);
  flip(() => root.prepend(el));
  const lens = new Lens(el, 'capsule', { strength: 0 });
  el.__lensRef = lens;
  el.__gel = gel(el, { amount: 0.6 });
  lens.animateStrength(1);
  springAnimate(el, [
    { transform: 'translateY(-40px) scale(0.74, 0.62)', opacity: 0 },
    { transform: 'translateY(0) scale(1)', opacity: 1 },
  ], SPRINGS.bouncy);
  el.__sticky = !!opts.sticky;
  if (!opts.sticky) el.__timer = setTimeout(() => dismiss(el), opts.ms || (level === 'error' ? 6000 : 3800));
  return el;
}

export function dismiss(el) {
  if (!el || el.classList.contains('is-leaving')) return;
  el.classList.add('is-leaving');
  clearTimeout(el.__timer);
  el.__lensRef && el.__lensRef.animateStrength(0, SPRINGS.sheetClose);
  const a = springAnimate(el, [
    { transform: 'translateY(0) scale(1)', opacity: 1 },
    { transform: 'translateY(-24px) scale(0.86)', opacity: 0 },
  ], SPRINGS.sheetClose);
  whenDone(a, () => {
    flip(() => el.remove());
    el.__lensRef && el.__lensRef.destroy();
  });
}

function flip(change) {
  const items = Array.from(root.children);
  const before = new Map(items.map((t) => [t, t.getBoundingClientRect().top]));
  change();
  for (const t of root.children) {
    if (!before.has(t) || t.classList.contains('is-leaving')) continue;
    const dy = before.get(t) - t.getBoundingClientRect().top;
    if (Math.abs(dy) > 0.5) springAnimate(t, [{ translate: `0 ${dy}px` }, { translate: '0 0' }], SPRINGS.bouncy, { composite: 'add', fill: 'none' });
  }
}
