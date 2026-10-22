// Themes: four named palettes shared by every app (the kit's documentation, "Themes"). A theme is
// a hue family (the scene) and a contrast accent (the primary action, highlights); css/themes.css
// holds the generated tokens of each one under `:root[data-theme="<name>"]`, so the first paint
// needs no JS. applyTheme() switches the attribute and retints the scene; mountThemeControl()
// renders the settings row with the four swatches. The app persists the choice in its settings.

import { retint } from './background.js';
import { gel } from './glass.js';

export const THEMES = [
  { name: 'dusk', label: 'Dusk', hue: '#2a5bd8', accent: '#ff7a59' },
  { name: 'nova', label: 'Nova', hue: '#6a3df0', accent: '#ff8a4a' },
  { name: 'frost', label: 'Frost', hue: '#2d7fc9', accent: '#b58cff' },
  { name: 'bloom', label: 'Bloom', hue: '#d2409a', accent: '#3ee6b8' },
];

export const DEFAULT_THEME = 'dusk';

export function themeByName(name) {
  return THEMES.find((t) => t.name === name) || null;
}

// ---------------------------------------------------------------------------------------------
// Token derivation (the generator of css/themes.css; also used by the gallery for ad-hoc pairs)

const rgb = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const hex = (a) => `#${a.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')}`;
const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
const rgba = (a, al) => `rgba(${a.map(Math.round).join(', ')}, ${al})`;

/** Identity and scene tokens for a hue / accent pair. */
export function derive({ hue, accent }) {
  const h = rgb(hue);
  const a = rgb(accent);
  const black = [0, 0, 0];
  const white = [255, 255, 255];
  const light = mix(a, white, 0.22);
  const deep = mix(a, h, 0.42);
  return {
    '--accent': accent,
    '--accent-2': hex(deep),
    '--accent-ink': hex(mix(a, white, 0.56)),
    '--accent-grad': `linear-gradient(135deg, ${hex(light)} 0%, ${accent} 48%, ${hex(deep)} 100%)`,
    '--accent-grad-a': `linear-gradient(135deg, ${rgba(light, 0.74)} 0%, ${rgba(a, 0.74)} 48%, ${rgba(deep, 0.78)} 100%)`,
    '--btn-glow': rgba(a, 0.55),
    '--focus-ring': rgba(mix(a, white, 0.45), 0.95),
    '--glyph-glow': rgba(mix(h, white, 0.2), 0.55),
    '--bg-top': hex(mix(h, black, 0.95)),
    '--bg-mid': hex(mix(h, black, 0.86)),
    '--bg-bottom': hex(mix(h, black, 0.74)),
    '--scene-hue': hue,
    '--scene-accent': accent,
    '--scene-line': hex(mix(h, white, 0.74)),
    '--tint-regular': rgba(mix(h, black, 0.82), 0.3),
    '--tint-sheet': rgba(mix(h, black, 0.88), 0.52),
    '--well': rgba(mix(h, black, 0.92), 0.4),
  };
}

/** The CSS of css/themes.css (run by the kit's tools/gen-themes.mjs). */
export function themesCss() {
  const block = (sel, t) => `${sel} {\n${Object.entries(derive(t)).map(([k, v]) => `  ${k}: ${v};`).join('\n')}\n}`;
  return THEMES.map((t) => `/* ${t.label}: hue ${t.hue}, accent ${t.accent} */\n${block(`:root[data-theme="${t.name}"]`, t)}`).join('\n\n');
}

// ---------------------------------------------------------------------------------------------
// Applying a theme

let current = '';
const listeners = new Set();

/** The active theme name (from the root attribute, else the default). */
export function currentTheme() {
  return current || document.documentElement.dataset.theme || DEFAULT_THEME;
}

/**
 * Switch the theme: sets `data-theme` on the root and retints the scene. Unknown names fall
 * back to the default. Returns the name applied.
 */
export function applyTheme(name) {
  const t = themeByName(name) || themeByName(DEFAULT_THEME);
  if (document.documentElement.dataset.theme === t.name && current === t.name) return t.name;
  document.documentElement.dataset.theme = t.name;
  current = t.name;
  retint();
  for (const fn of listeners) fn(t.name);
  return t.name;
}

/** Called after every applyTheme(); returns an unsubscribe function. */
export function onTheme(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------------------------------------------------------------------------------------------
// Settings row

/**
 * Append a "Theme" row with the four swatches to `host` (a `.set-card`). `onChange(name)` is
 * called on a click (the app saves it and calls set() with the confirmed value, or applies it
 * at once). Returns { el, set(name), destroy() }.
 */
export function mountThemeControl(host, { value = currentTheme(), label = 'Theme', hint = 'Background colour and accent of the window', onChange } = {}) {
  const row = document.createElement('div');
  row.className = 'set-row set-row-col';
  row.innerHTML = `<div class="set-label"><span>${label}</span><small>${hint}</small></div><div class="theme-row" role="radiogroup" aria-label="${label}"></div>`;
  const group = row.querySelector('.theme-row');
  const chips = THEMES.map((t) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'theme-chip';
    b.dataset.theme = t.name;
    b.setAttribute('role', 'radio');
    b.innerHTML = `<span class="theme-sw" aria-hidden="true" style="--h:${t.hue};--a:${t.accent}"><i></i></span><span class="theme-name">${t.label}</span>`;
    b.addEventListener('click', () => {
      if (b.getAttribute('aria-checked') === 'true') return;
      set(t.name);
      onChange && onChange(t.name);
    });
    gel(b);
    group.appendChild(b);
    return b;
  });
  const set = (name) => {
    chips.forEach((b) => {
      const on = b.dataset.theme === name;
      b.setAttribute('aria-checked', String(on));
      b.classList.toggle('is-on', on);
      b.tabIndex = on ? 0 : -1;
    });
  };
  group.addEventListener('keydown', (e) => {
    const i = chips.findIndex((b) => b.getAttribute('aria-checked') === 'true');
    let j = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % chips.length;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + chips.length) % chips.length;
    if (j < 0) return;
    e.preventDefault();
    chips[j].focus();
    chips[j].click();
  });
  set(value);
  host.appendChild(row);
  return { el: row, set, destroy: () => row.remove() };
}
