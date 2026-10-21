// Media Saver's background: the kit's scene (vendor/liquid_ui/js/background.js) in Media Saver's
// colours, plus soft "ghosts" of recent job thumbnails. The ghosts are an app layer inside #scene,
// above the colour fields and lines, below the vignette and grain; they drift on a CSS loop that
// holds with the rest of the scene (#scene.is-paused).

import { initBackground, activity, setFocused } from '../vendor/liquid_ui/js/background.js';

export { activity, setFocused };

// Six colour fields: cobalt, violet, teal, rose, amber, sky ("lagoon" identity, docs/UI_DESIGN.md 5).
const FIELDS = [
  { c: '#3c6dff', x: 8, y: 12, s: 64, a: 0.75, d: 46, anim: 'drift-a' },
  { c: '#8a4dff', x: 62, y: -6, s: 58, a: 0.62, d: 58, anim: 'drift-b' },
  { c: '#14c7b4', x: 78, y: 62, s: 56, a: 0.5, d: 52, anim: 'drift-c' },
  { c: '#ff6a8a', x: 30, y: 78, s: 40, a: 0.38, d: 64, anim: 'drift-b' },
  { c: '#ffb648', x: 92, y: 28, s: 30, a: 0.3, d: 70, anim: 'drift-a' },
  { c: '#2a9bff', x: 44, y: 42, s: 46, a: 0.36, d: 76, anim: 'drift-c' },
];

let ghosts = null;

export function initScene() {
  const root = document.getElementById('scene');
  initBackground(root, { fields: FIELDS, idleMs: 60000 });
  ghosts = document.createElement('div');
  ghosts.className = 'scene-ghosts';
  root.insertBefore(ghosts, root.querySelector('.sc-sweep') || null);
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
