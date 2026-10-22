// Liquid Glass material.
//
// Refracting surfaces ("lenses") use `backdrop-filter: url(#filter)` with an SVG filter chain
//   feGaussianBlur (light frost) -> feDisplacementMap (lensing) -> feColorMatrix (saturation)
// whose displacement map is computed per element size on a canvas: a rounded-rect bezel with a
// convex squircle height profile, the displacement of every bezel pixel traced with Snell's law
// (n_air = 1, n_glass = 1.5) and stored as R = x shift, G = y shift (128 = no shift).
// Maps are rebuilt by a ResizeObserver and cached by (width, height, radius, bezel, thickness).
//
// The specular rim, the press tint and the tint are CSS layers (see css/glass.css); the rim reads
// the light angle --la that this module writes. No layer follows the pointer position.
//
// Only Chromium renders url() filters in backdrop-filter, which is exactly the WebView2 target.

import { Spring, SPRINGS, clamp, afterHold } from './motion.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const IOR = 1.5;
const LUT_SIZE = 128;

let defs = null;
let uid = 0;
const lenses = new Set();
const mapCache = new Map();   // key -> { url, maxDisp }
const MAP_CACHE_LIMIT = 80;

function ensureDefs() {
  if (defs) return defs;
  let svg = document.getElementById('lg-defs');
  if (!svg) {
    svg = document.createElementNS(SVG_NS, 'svg');
    svg.id = 'lg-defs';
    svg.setAttribute('aria-hidden', 'true');
    svg.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none';
    document.body.prepend(svg);
  }
  defs = svg.querySelector('defs') || svg.appendChild(document.createElementNS(SVG_NS, 'defs'));
  return defs;
}

// ---------------------------------------------------------------------------------------------
// Physics: height profile and Snell displacement look-up table

/** Convex squircle bezel profile (kube.io): 0 at the outer edge, 1 where the flat top starts. */
function height(t) {
  t = clamp(t, 0, 1);
  return Math.pow(1 - Math.pow(1 - t, 4), 0.25);
}

const lutCache = new Map();

/**
 * Displacement magnitude (px) across the bezel for a given bezel width and glass thickness.
 * A vertical ray hits the surface at distance t*bezel from the edge, refracts into the glass and
 * travels down to the base plane; the lateral offset is the displacement, directed inward.
 */
function displacementLUT(bezel, thickness) {
  const key = `${bezel.toFixed(1)}|${thickness.toFixed(1)}`;
  const hit = lutCache.get(key);
  if (hit) return hit;
  const lut = new Float32Array(LUT_SIZE);
  let max = 0;
  const d = 0.001;
  for (let i = 0; i < LUT_SIZE; i++) {
    const t = (i + 0.5) / LUT_SIZE;
    const h = height(t);
    const dhdt = (height(t + d) - height(t - d)) / (2 * d);
    const slope = (dhdt * thickness) / bezel;            // dh/dx in px per px
    const theta1 = Math.atan(slope);                     // incidence vs the surface normal
    const theta2 = Math.asin(Math.sin(theta1) / IOR);    // Snell
    const dev = theta1 - theta2;                         // deviation from vertical
    const disp = h * thickness * Math.tan(dev);          // depth of glass under the point * tan(dev)
    lut[i] = disp;
    if (disp > max) max = disp;
  }
  const out = { lut, max };
  lutCache.set(key, out);
  return out;
}

/**
 * Generate the displacement map for a rounded rectangle.
 * Only the top-left quadrant is computed; the other three are mirrored with flipped signs.
 * @returns {{url:string, maxDisp:number}}
 */
function generateMap(w, h, radius, bezel, thickness) {
  const res = w * h > 120000 ? 0.5 : 1;  // large panels: half-resolution map (it is smooth anyway)
  const W = Math.max(2, Math.round(w * res));
  const H = Math.max(2, Math.round(h * res));
  const r = radius * res;
  const b = Math.max(1, bezel * res);
  const { lut, max } = displacementLUT(bezel, thickness);
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(W, H);
  const data = img.data;
  // Neutral fill.
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 128; data[i + 1] = 128; data[i + 2] = 128; data[i + 3] = 255;
  }
  const qw = Math.ceil(W / 2);
  const qh = Math.ceil(H / 2);
  const inv = max > 0 ? 1 / max : 0;
  for (let y = 0; y < qh; y++) {
    const py = y + 0.5;
    for (let x = 0; x < qw; x++) {
      const px = x + 0.5;
      let dist, nx, ny;
      if (px < r && py < r) {
        // Corner arc: distance to the arc and the inward normal toward the corner centre.
        const cx = r - px, cy = r - py;
        const len = Math.hypot(cx, cy) || 1e-6;
        dist = r - len;
        nx = cx / len;
        ny = cy / len;
      } else if (px < py) {
        dist = px; nx = 1; ny = 0;
      } else {
        dist = py; nx = 0; ny = 1;
      }
      if (dist < 0 || dist >= b) continue;
      const t = dist / b;
      const mag = lut[Math.min(LUT_SIZE - 1, Math.floor(t * LUT_SIZE))] * inv;
      // Fade the very outer half pixel so the antialiased edge does not sample garbage.
      const edge = clamp(dist * 2, 0, 1);
      const dx = nx * mag * edge;
      const dy = ny * mag * edge;
      const rx = Math.round(128 + dx * 127);
      const gy = Math.round(128 + dy * 127);
      const rxm = Math.round(128 - dx * 127);
      const gym = Math.round(128 - dy * 127);
      const x2 = W - 1 - x;
      const y2 = H - 1 - y;
      let i = (y * W + x) * 4;  data[i] = rx;  data[i + 1] = gy;
      i = (y * W + x2) * 4;     data[i] = rxm; data[i + 1] = gy;
      i = (y2 * W + x) * 4;     data[i] = rx;  data[i + 1] = gym;
      i = (y2 * W + x2) * 4;    data[i] = rxm; data[i + 1] = gym;
    }
  }
  ctx.putImageData(img, 0, 0);
  return { url: canvas.toDataURL('image/png'), maxDisp: max };
}

function getMap(w, h, radius, bezel, thickness) {
  const key = `${w}x${h}|${radius.toFixed(1)}|${bezel.toFixed(1)}|${thickness.toFixed(1)}`;
  let hit = mapCache.get(key);
  if (hit) {
    // LRU touch
    mapCache.delete(key);
    mapCache.set(key, hit);
    return hit;
  }
  hit = generateMap(w, h, radius, bezel, thickness);
  mapCache.set(key, hit);
  if (mapCache.size > MAP_CACHE_LIMIT) mapCache.delete(mapCache.keys().next().value);
  return hit;
}

// ---------------------------------------------------------------------------------------------
// Presets

/**
 * bezel / thickness: number = px, or a function of the element's short side.
 * refraction: multiplier of the physical displacement; blur: frost stdDeviation (px);
 * saturate: colour boost of the refracted backdrop; css: extra backdrop-filter functions.
 */
export const PRESETS = {
  // Small pill / round controls: the whole surface is curved.
  pill:    { bezel: (s) => s * 0.5, thickness: (s) => s * 0.75, refraction: 1.0, blur: 0.6, saturate: 1.7, css: 'brightness(1.08)' },
  // Medium surfaces: toasts, chips, the clipboard prompt.
  capsule: { bezel: (s) => Math.min(s * 0.5, 22), thickness: (s) => Math.min(s * 0.7, 30), refraction: 1.0, blur: 1.2, saturate: 1.7, css: 'brightness(1.06)' },
  // Large panels: narrower rim relative to the size, more frost for legibility of the content inside.
  panel:   { bezel: 28, thickness: 38, refraction: 1.0, blur: 2.5, saturate: 1.6, css: 'brightness(1.04)' },
  // Sheets and popovers: a strong lens rim, deep frost.
  sheet:   { bezel: 30, thickness: 40, refraction: 1.0, blur: 14, saturate: 1.5, css: 'brightness(0.92)' },
  // The lifted droplet of segmented controls and switch thumbs.
  droplet: { bezel: (s) => s * 0.5, thickness: (s) => s * 0.9, refraction: 1.15, blur: 0, saturate: 1.4, css: 'brightness(1.12)' },
};

const resolve = (v, s) => (typeof v === 'function' ? v(s) : v);

// ---------------------------------------------------------------------------------------------
// Lens

const ro = new ResizeObserver((entries) => {
  for (const e of entries) {
    const lens = e.target.__lens;
    if (lens) lens._schedule();
  }
});

let pending = new Set();
let pendingRaf = 0;
function flushPending() {
  pendingRaf = 0;
  const list = pending;
  pending = new Set();
  for (const l of list) l.update();
}

export class Lens {
  /**
   * @param {HTMLElement} el
   * @param {keyof PRESETS | object} preset
   * @param {{strength?:number, radius?:number}} opts
   */
  constructor(el, preset = 'capsule', opts = {}) {
    this.el = el;
    this.cfg = typeof preset === 'string' ? { ...PRESETS[preset] } : { ...PRESETS.capsule, ...preset };
    this.opts = opts;
    this.id = `lg${++uid}`;
    this.size = { w: 0, h: 0, r: 0 };
    this.maxDisp = 0;
    this.strength = opts.strength ?? 1;
    this._strengthSpring = null;
    el.__lens = this;
    el.classList.add('lens');
    this._buildLayers();
    this._buildFilter();
    lenses.add(this);
    ro.observe(el);
    this.update();
  }

  _buildLayers() {
    if (!this.el.querySelector(':scope > .lg-rim')) {
      const rim = document.createElement('span');
      rim.className = 'lg-rim';
      rim.setAttribute('aria-hidden', 'true');
      this.el.append(rim);
    }
  }

  _buildFilter() {
    const f = document.createElementNS(SVG_NS, 'filter');
    f.id = this.id;
    f.setAttribute('filterUnits', 'userSpaceOnUse');
    f.setAttribute('primitiveUnits', 'userSpaceOnUse');
    f.setAttribute('color-interpolation-filters', 'sRGB');
    f.setAttribute('x', '0');
    f.setAttribute('y', '0');
    f.innerHTML = `
      <feGaussianBlur in="SourceGraphic" stdDeviation="0" result="frost"/>
      <feImage result="map" x="0" y="0" preserveAspectRatio="none"/>
      <feDisplacementMap in="frost" in2="map" scale="0" xChannelSelector="R" yChannelSelector="G" result="lensed"/>
      <feColorMatrix in="lensed" type="saturate" values="1"/>`;
    this.filter = f;
    this.feBlur = f.children[0];
    this.feImage = f.children[1];
    this.feDisp = f.children[2];
    this.feSat = f.children[3];
    ensureDefs().appendChild(f);
  }

  _schedule() {
    pending.add(this);
    if (!pendingRaf) pendingRaf = requestAnimationFrame(flushPending);
  }

  /** Re-measure and regenerate the map if the size or radius changed. */
  update() {
    const el = this.el;
    if (!el.isConnected) return;
    const w = Math.round(el.offsetWidth);
    const h = Math.round(el.offsetHeight);
    if (w < 2 || h < 2) return;
    let r = this.opts.radius;
    if (r == null) {
      const cs = getComputedStyle(el);
      r = parseFloat(cs.borderTopLeftRadius) || 0;
    }
    r = Math.min(r, w / 2, h / 2);
    if (w === this.size.w && h === this.size.h && Math.abs(r - this.size.r) < 0.5) return;
    this.size = { w, h, r };
    const s = Math.min(w, h);
    const bezel = clamp(resolve(this.cfg.bezel, s), 2, s / 2);
    const thickness = Math.max(2, resolve(this.cfg.thickness, s));
    const map = getMap(w, h, r, bezel, thickness);
    this.maxDisp = map.maxDisp;
    this.feImage.setAttribute('href', map.url);
    this.feImage.setAttribute('width', String(w));
    this.feImage.setAttribute('height', String(h));
    this.filter.setAttribute('width', String(w));
    this.filter.setAttribute('height', String(h));
    this._apply();
  }

  _apply() {
    const k = this.strength;
    const cfg = this.cfg;
    if (k <= 0.001) {
      this.el.style.backdropFilter = 'none';
      this.el.style.setProperty('--lg-k', '0');
      return;
    }
    // feDisplacementMap: P'(x) = P(x + scale * (C(x) - 0.5)); C - 0.5 spans +-0.5 -> scale = 2 * max.
    this.feDisp.setAttribute('scale', (2 * this.maxDisp * cfg.refraction * k).toFixed(2));
    this.feBlur.setAttribute('stdDeviation', (cfg.blur * k).toFixed(2));
    this.feSat.setAttribute('values', (1 + (cfg.saturate - 1) * k).toFixed(3));
    this.el.style.backdropFilter = `url(#${this.id}) ${cfg.css || ''}`;
    this.el.style.setProperty('--lg-k', k.toFixed(3));
  }

  /** Lensing strength 0..1 (materialize in / out modulates bending rather than opacity). */
  setStrength(k) {
    this.strength = Math.max(0, k);
    this._apply();
  }

  animateStrength(target, cfg = SPRINGS.materialize) {
    if (!this._strengthSpring) {
      this._strengthSpring = new Spring(this.strength, cfg, (v) => this.setStrength(v));
    }
    this._strengthSpring.value = this.strength;
    return this._strengthSpring.to(target, cfg);
  }

  destroy() {
    ro.unobserve(this.el);
    this.filter.remove();
    lenses.delete(this);
    delete this.el.__lens;
  }
}

export function lensCount() {
  let n = 0;
  for (const l of lenses) if (l.el.isConnected && l.strength > 0.001) n++;
  return n;
}

// ---------------------------------------------------------------------------------------------
// Light: one global light angle for every specular rim that drifts slowly with the pointer across
// the window (the web analogue of highlights that react to device motion). There is no per-surface
// hover light: nothing on a surface follows the pointer.

const LIGHT_BASE = -48;  // degrees, light from the upper left
let pointerRaf = 0;
let lastEvent = null;

function lightFrame() {
  pointerRaf = 0;
  const e = lastEvent;
  if (!e) return;
  const nx = e.clientX / innerWidth - 0.5;
  const ny = e.clientY / innerHeight - 0.5;
  document.documentElement.style.setProperty('--la', `${(LIGHT_BASE + nx * 40 - ny * 16).toFixed(1)}deg`);
}

export function initLight() {
  document.documentElement.style.setProperty('--la', `${LIGHT_BASE}deg`);
  addEventListener('pointermove', (e) => {
    lastEvent = e;
    if (!pointerRaf) pointerRaf = requestAnimationFrame(lightFrame);
  }, { passive: true });
}

// ---------------------------------------------------------------------------------------------
// Gel press: squish with spring overshoot and a jelly release; the CSS lightens the tint while
// `.is-pressed` is on. The surface stays in place under the pointer (no follow).
// Uses the individual `scale` property so it composes with layout transforms.

export function gel(el, { amount = 1 } = {}) {
  if (el.__gel) return el.__gel;
  let down = false;
  let downAt = 0;
  let seq = 0;
  const st = { sx: 1, sy: 1 };
  const render = () => {
    el.style.scale = `${st.sx.toFixed(4)} ${st.sy.toFixed(4)}`;
  };
  const sx = new Spring(1, SPRINGS.press, (v) => { st.sx = v; render(); });
  const sy = new Spring(1, SPRINGS.press, (v) => { st.sy = v; render(); });
  const clear = () => {
    if (sx._resting && sy._resting && !down) el.style.removeProperty('scale');
  };
  [sx, sy].forEach((s) => (s.onRest = clear));

  const size = () => Math.max(24, Math.min(el.offsetWidth, el.offsetHeight * 3));
  const press = (e) => {
    if (e.button !== 0 || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    if (down) {
      // A new press during the hold of the previous one: drop that pending release.
      removeEventListener('pointerup', release);
      removeEventListener('pointercancel', release);
    }
    down = true;
    downAt = performance.now();
    seq += 1;
    // Smaller controls squish more; large surfaces barely move.
    const k = amount * clamp(60 / size(), 0.25, 1);
    sx.to(1 + 0.08 * k, SPRINGS.press);
    sy.to(1 - 0.06 * k, SPRINGS.press);
    el.classList.add('is-pressed');
    addEventListener('pointerup', release, { once: true });
    addEventListener('pointercancel', release, { once: true });
  };
  const release = () => {
    if (!down) return;
    removeEventListener('pointerup', release);
    removeEventListener('pointercancel', release);
    const mine = seq;
    // The press stays visible for PRESS_HOLD_MS (`down` stays true until then, so a spring that
    // comes to rest meanwhile does not clear the scale for a frame), then relaxes: a soft
    // elastic return, no kick against the direction of travel (both read as a twitch).
    afterHold(downAt, () => {
      if (mine !== seq) return;
      down = false;
      el.classList.remove('is-pressed');
      sx.to(1, SPRINGS.jelly);
      sy.to(1, SPRINGS.jelly);
    });
  };
  el.addEventListener('pointerdown', press);
  // Keyboard activation gets a short pulse too.
  el.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) pulse();
  });
  const pulse = () => {
    sx.set(1 + 0.05 * amount);
    sy.set(1 - 0.04 * amount);
    sx.to(1, SPRINGS.jelly);
    sy.to(1, SPRINGS.jelly);
  };
  el.__gel = { pulse };
  return el.__gel;
}

/** Horizontal "no" wobble for invalid input. */
export function shake(el) {
  const s = new Spring(0, SPRINGS.jelly, (v) => (el.style.translate = `${v.toFixed(2)}px 0`));
  s.onRest = () => el.style.removeProperty('translate');
  s.to(0, SPRINGS.jelly, -900);
}
