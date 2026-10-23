// The title bar (the kit's documentation, "Title bar"): one layout for every app.
//   left    app glyph, app name, the version pill (#ver-pill; the update control mounts on it)
//   centre  the app's own controls, centred on the window
//   aside   optional status chips of the app, right before the window cluster
//   right   one capsule lens with settings | minimize, close
// The drag layer carries `dragClass` (the host's window-drag class); the buttons are siblings of
// it. The kit knows no window API: the app passes the three callbacks.

import { Lens, gel } from './glass.js';
import { icon } from './icons.js';
import { escapeHtml } from './format.js';

/**
 * Build the title bar into `host` (a <header>; it gets the class "titlebar").
 * @param {HTMLElement} host
 * @param {{
 *   name: string,                 // app name
 *   glyph?: string,               // inline SVG of the app glyph (20 px)
 *   version?: string,             // text of the version pill until the update control renders it
 *   pillId?: string,              // id of the version pill (default "ver-pill")
 *   pill?: boolean,               // false: no version pill (default true)
 *   pillLens?: boolean,           // true: the title bar lenses the pill itself (default false: the
 *                                 //   update control does it when it mounts)
 *   settings?: boolean,           // false: no settings button (default true)
 *   dragClass?: string,           // class of the drag layer (default "pywebview-drag-region")
 *   labels?: { settings?: string, minimize?: string, close?: string },
 *   onSettings?: (btn: HTMLElement) => void,
 *   onMinimize?: () => void,
 *   onClose?: () => void,
 * }} o
 * @returns {{ el, left, centre, aside, right, cluster, name, glyph, pill, gear, minimize, close,
 *            lens, setName(text), destroy() }}
 */
export function mountTitlebar(host, o = {}) {
  if (!host) throw new Error('mountTitlebar needs a host element.');
  const labels = { settings: 'Settings', minimize: 'Minimize', close: 'Close', ...(o.labels || {}) };
  const dragClass = o.dragClass === undefined ? 'pywebview-drag-region' : o.dragClass;
  const pillId = o.pillId || 'ver-pill';
  const withPill = o.pill !== false;
  const withGear = o.settings !== false;
  host.classList.add('titlebar');
  host.innerHTML = `
    <div class="tb-drag${dragClass ? ` ${escapeHtml(dragClass)}` : ''}"></div>
    <div class="tb-left">
      <span class="tb-glyph" aria-hidden="true">${o.glyph || icon('app')}</span>
      <span class="tb-name">${escapeHtml(o.name || '')}</span>
      ${withPill ? `<button type="button" id="${escapeHtml(pillId)}" class="ver-pill" aria-haspopup="dialog" aria-label="Version and updates"><span class="ver-dot" aria-hidden="true"></span><span class="ver-text">${escapeHtml(o.version ? `v${o.version}` : 'Version')}</span></button>` : ''}
    </div>
    <div class="tb-centre"></div>
    <div class="tb-right">
      <div class="tb-aside"></div>
      <div class="tb-cluster">
        ${withGear ? `<button type="button" class="tb-btn tb-gear" aria-label="${escapeHtml(labels.settings)}" title="${escapeHtml(labels.settings)}" aria-haspopup="dialog">${icon('gear')}</button><span class="tb-sep" aria-hidden="true"></span>` : ''}
        <button type="button" class="tb-btn tb-min" aria-label="${escapeHtml(labels.minimize)}" title="${escapeHtml(labels.minimize)}">${icon('min')}</button>
        <button type="button" class="tb-btn tb-close" aria-label="${escapeHtml(labels.close)}" title="${escapeHtml(labels.close)}">${icon('close')}</button>
      </div>
    </div>`;
  const q = (sel) => host.querySelector(sel);
  const cluster = q('.tb-cluster');
  const pill = withPill ? q(`#${CSS.escape(pillId)}`) : null;
  const gear = withGear ? q('.tb-gear') : null;
  const minimize = q('.tb-min');
  const close = q('.tb-close');
  const lens = new Lens(cluster, 'capsule');
  let pillLens = null;
  if (pill && o.pillLens) pillLens = new Lens(pill, 'capsule');
  const gels = [];
  for (const b of cluster.querySelectorAll('.tb-btn')) gels.push(gel(b, { amount: 0.7 }));
  if (gear) gear.addEventListener('click', () => o.onSettings && o.onSettings(gear));
  minimize.addEventListener('click', () => o.onMinimize && o.onMinimize());
  close.addEventListener('click', () => o.onClose && o.onClose());
  const api = {
    el: host,
    left: q('.tb-left'),
    centre: q('.tb-centre'),
    aside: q('.tb-aside'),
    right: q('.tb-right'),
    cluster,
    name: q('.tb-name'),
    glyph: q('.tb-glyph'),
    pill,
    gear,
    minimize,
    close,
    lens,
    setName(text) {
      api.name.textContent = text;
    },
    destroy() {
      lens.destroy();
      if (pillLens) pillLens.destroy();
      host.innerHTML = '';
      host.classList.remove('titlebar');
    },
  };
  return api;
}
