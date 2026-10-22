// Media Saver's background: the kit's scene (vendor/liquid_ui/js/background.js) in the colours of
// the active theme (the `deep` treatment reads the theme tokens; applyTheme() retints it), plus soft
// "ghosts" of recent job thumbnails. The ghosts are an app layer inside #scene, above the colour
// fields and lines, below the vignette and grain; they drift on a CSS loop that holds with the rest
// of the scene (#scene.is-paused).

import { initBackground, activity, setFocused } from '../vendor/liquid_ui/js/background.js';

export { activity, setFocused };

let ghosts = null;

export function initScene() {
  const root = document.getElementById('scene');
  initBackground(root, { idleMs: 60000 });
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
