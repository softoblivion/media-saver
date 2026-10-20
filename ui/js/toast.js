// Toasts: glass pills dropping from the top with a spring, at most three on screen.

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
 * Show a toast.
 * @param {'info'|'success'|'warning'|'error'} level
 * @param {string} text Russian text
 * @param {{action?:{label:string, run:Function}, ms?:number}} opts
 */
export function toast(level, text, opts = {}) {
  if (!root) return;
  // Same text already visible: pulse it instead of stacking duplicates.
  for (const t of root.children) {
    if (t.dataset.text === text && !t.classList.contains('is-leaving')) {
      t.__gel && t.__gel.pulse();
      clearTimeout(t.__timer);
      t.__timer = setTimeout(() => dismiss(t), opts.ms || 3800);
      return t;
    }
  }
  const el = document.createElement('div');
  el.className = `toast toast-${level}`;
  el.dataset.text = text;
  el.setAttribute('role', level === 'error' ? 'alert' : 'status');
  el.innerHTML = `<span class="toast-ic">${icon(ICONS[level] || 'info')}</span><span class="toast-text">${escapeHtml(text)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
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
  if (live.length >= MAX) dismiss(live[live.length - 1]);
  flip(() => root.prepend(el));
  const lens = new Lens(el, 'capsule', { strength: 0 });
  el.__lens = lens;
  el.__gel = gel(el, { amount: 0.6, stretch: false });
  lens.animateStrength(1);
  springAnimate(el, [
    { transform: 'translateY(-46px) scale(0.72, 0.6)', opacity: 0 },
    { transform: 'translateY(0) scale(1)', opacity: 1 },
  ], SPRINGS.bouncy);
  el.__timer = setTimeout(() => dismiss(el), opts.ms || (level === 'error' ? 6000 : 3800));
  return el;
}

function dismiss(el) {
  if (!el || el.classList.contains('is-leaving')) return;
  el.classList.add('is-leaving');
  clearTimeout(el.__timer);
  el.__lens && el.__lens.animateStrength(0, SPRINGS.sheetClose);
  const a = springAnimate(el, [
    { transform: 'translateY(0) scale(1)', opacity: 1 },
    { transform: 'translateY(-26px) scale(0.86)', opacity: 0 },
  ], SPRINGS.sheetClose);
  whenDone(a, () => {
    flip(() => el.remove());
    el.__lens && el.__lens.destroy();
  });
}

/** FLIP the siblings around a DOM change so the stack slides instead of jumping. */
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
