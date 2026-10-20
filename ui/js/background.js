// Background scene: slowly drifting colour fields, fine grain and soft "ghosts" of recent thumbnails.
// Everything moves through CSS animations on transforms (compositor), never through a rAF loop.
// The drift pauses when the window is unfocused / hidden or after a minute without user activity,
// so an idle app costs nothing.

import { reducedMotion } from './motion.js';

const BLOBS = [
  { c: '#3c6dff', x: 8, y: 12, s: 64, a: 0.75, d: 46 },
  { c: '#8a4dff', x: 62, y: -6, s: 58, a: 0.62, d: 58 },
  { c: '#14c7b4', x: 78, y: 62, s: 56, a: 0.5, d: 52 },
  { c: '#ff6a8a', x: 30, y: 78, s: 40, a: 0.38, d: 64 },
  { c: '#ffb648', x: 92, y: 28, s: 30, a: 0.3, d: 70 },
  { c: '#2a9bff', x: 44, y: 42, s: 46, a: 0.36, d: 76 },
];

let scene = null;
let ghosts = null;
let idleTimer = 0;
let focused = true;
const IDLE_MS = 60000;

function grainUrl() {
  const n = 180;
  const c = document.createElement('canvas');
  c.width = c.height = n;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(n, n);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.random() * 255;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 22;
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL('image/png');
}

/**
 * Topographic contour lines: thin, faint, crisp. The colour fields alone are too smooth for the
 * lensing to read; crisp lines bend visibly in every glass rim.
 */
function contoursSvg() {
  const centers = [
    { x: 380, y: 300, n: 11, r0: 70, step: 46, seed: 1.3 },
    { x: 1260, y: 760, n: 12, r0: 60, step: 50, seed: 4.1 },
  ];
  let paths = '';
  for (const c of centers) {
    for (let i = 0; i < c.n; i++) {
      const r = c.r0 + i * c.step;
      const pts = [];
      const N = 120;
      for (let k = 0; k < N; k++) {
        const a = (k / N) * Math.PI * 2;
        const wob = 1 + 0.09 * Math.sin(3 * a + c.seed + i * 0.35) + 0.05 * Math.sin(5 * a - c.seed * 2 + i * 0.2) + 0.03 * Math.sin(8 * a + i);
        pts.push([c.x + Math.cos(a) * r * wob, c.y + Math.sin(a) * r * wob * 0.82]);
      }
      // Smooth closed curve through midpoints (quadratic segments).
      const mid = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      let d = '';
      for (let k = 0; k < N; k++) {
        const p = pts[k], q = pts[(k + 1) % N];
        const m = mid(p, q);
        d += k === 0 ? `M${m[0].toFixed(1)} ${m[1].toFixed(1)}` : `Q${p[0].toFixed(1)} ${p[1].toFixed(1)} ${m[0].toFixed(1)} ${m[1].toFixed(1)}`;
      }
      const p0 = pts[0], m1 = mid(p0, pts[1]);
      d += `Q${p0[0].toFixed(1)} ${p0[1].toFixed(1)} ${m1[0].toFixed(1)} ${m1[1].toFixed(1)}Z`;
      const o = (0.05 + 0.07 * (1 - i / c.n)).toFixed(3);
      paths += `<path d="${d}" stroke-opacity="${o}"/>`;
    }
  }
  return `<svg viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" fill="none" stroke="#fff" stroke-width="1.3">${paths}</svg>`;
}

export function initBackground(root = document.getElementById('scene')) {
  scene = root;
  scene.innerHTML = '';
  const field = document.createElement('div');
  field.className = 'scene-field';
  BLOBS.forEach((b, i) => {
    const el = document.createElement('div');
    el.className = `blob blob-${i}`;
    el.style.cssText = `--x:${b.x}%;--y:${b.y}%;--s:${b.s}vmax;--c:${b.c};--a:${b.a};animation-duration:${b.d}s;animation-delay:${-i * 7}s`;
    field.appendChild(el);
  });
  const contours = document.createElement('div');
  contours.className = 'scene-contours';
  contours.innerHTML = contoursSvg();
  ghosts = document.createElement('div');
  ghosts.className = 'scene-ghosts';
  const grain = document.createElement('div');
  grain.className = 'scene-grain';
  grain.style.backgroundImage = `url(${grainUrl()})`;
  const vignette = document.createElement('div');
  vignette.className = 'scene-vignette';
  scene.append(field, contours, ghosts, vignette, grain);

  const bump = () => activity();
  addEventListener('pointermove', bump, { passive: true });
  addEventListener('keydown', bump);
  addEventListener('wheel', bump, { passive: true });
  addEventListener('focus', () => setFocused(true));
  addEventListener('blur', () => setFocused(false));
  reducedMotion.addEventListener('change', sync);
  activity();
}

let idle = false;
export function activity() {
  idle = false;
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    idle = true;
    sync();
  }, IDLE_MS);
  sync();
}

export function setFocused(f) {
  focused = f;
  sync();
}

function sync() {
  if (!scene) return;
  const paused = idle || !focused || reducedMotion.matches;
  scene.classList.toggle('is-paused', paused);
  // Decorative CSS loops inside the app (empty-state art, sheens) follow the same rule.
  document.documentElement.classList.toggle('is-still', idle || !focused);
}

const ghostCache = new Map();

/** Downscale a thumbnail to a tiny image: upscaled by the compositor it reads as a soft blur. */
function ghostUrl(src) {
  if (ghostCache.has(src)) return Promise.resolve(ghostCache.get(src));
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = 18;
      c.height = 32;
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, c.width, c.height);
      let url = null;
      try {
        url = c.toDataURL('image/png');   // throws for a cross-origin (tainted) thumbnail
      } catch {
        url = null;
      }
      ghostCache.set(src, url);
      resolve(url);
    };
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

const GHOST_SLOTS = [
  { x: 58, y: 16, r: -8 },
  { x: 82, y: 50, r: 7 },
  { x: 66, y: 70, r: -4 },
  { x: 90, y: 6, r: 10 },
];
let ghostKeys = [];

/** Show up to four recent thumbnails drifting softly in the background. */
export async function setGhosts(thumbs) {
  if (!ghosts) return;
  const list = thumbs.filter(Boolean).slice(0, GHOST_SLOTS.length);
  const key = list.join('|');
  if (key === ghostKeys.join('|')) return;
  ghostKeys = list;
  const urls = await Promise.all(list.map(ghostUrl));
  const existing = Array.from(ghosts.children);
  urls.forEach((u, i) => {
    let el = existing[i];
    if (!el) {
      el = document.createElement('div');
      el.className = 'ghost';
      const slot = GHOST_SLOTS[i];
      el.style.cssText = `--x:${slot.x}%;--y:${slot.y}%;--r:${slot.r}deg;animation-delay:${-i * 9}s`;
      ghosts.appendChild(el);
    }
    if (u && el.dataset.src !== u) {
      el.dataset.src = u;
      el.classList.remove('is-in');
      el.style.backgroundImage = `url(${u})`;
      requestAnimationFrame(() => el.classList.add('is-in'));
    }
  });
  existing.slice(urls.length).forEach((el) => {
    el.classList.remove('is-in');
    setTimeout(() => el.remove(), 1200);
  });
}
