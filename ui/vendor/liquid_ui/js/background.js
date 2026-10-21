// Background scene: a layered, slowly living version of the black -> navy gradient.
//
//   1. base gradient (CSS on #scene)
//   2. four large colour fields (cobalt, teal, violet, a faint ember) on 44-78 s CSS drift loops
//   3. faint crisp contour rings and caustic lines (static SVG on a 96 s drift): the lens rims
//      need crisp detail to show their refraction
//   4. ~40 blurred specks rising slowly (one half-resolution canvas, 30 fps cap)
//   5. a light sweep that crosses the window every 26 s (CSS)
//   6. vignette and a static grain tile
//
// Pointer parallax moves three wrappers by a few px (CSS transform transition, eased). Loops and
// the speck canvas pause while the document is hidden, the window is unfocused (`window` event or
// the browser blur), after 90 s without input, or with prefers-reduced-motion. Nothing here
// triggers layout per frame: the drift is compositor work, the canvas draws ~40 sprites.
//
// initBackground(root, options): every colour is an option (docs/LIQUID_UI.md, "Background"); the
// base gradient comes from the --bg-top / --bg-mid / --bg-bottom tokens.

import { reducedMotion } from './motion.js';

/** Default colour fields (cobalt, teal, violet, a faint ember). */
export const DEFAULT_FIELDS = [
  // c: colour, x/y: centre in % of the scene, s: diameter (vmax), a: alpha, d: period (s),
  // anim: one of the drift-a .. drift-d keyframes of css/scene.css
  { c: '#2f62ff', x: 18, y: 38, s: 92, a: 0.46, d: 58, anim: 'drift-a' },
  { c: '#12b8b0', x: 86, y: 78, s: 84, a: 0.3, d: 72, anim: 'drift-b' },
  { c: '#7a48ff', x: 78, y: 26, s: 70, a: 0.3, d: 46, anim: 'drift-c' },
  { c: '#ff8a4a', x: 12, y: 92, s: 58, a: 0.12, d: 78, anim: 'drift-d' },
];

/** Default speck tints ("r,g,b"); the 4th and 5th are the rarer warm and violet specks. */
export const DEFAULT_SPECK_TINTS = ['200,225,255', '150,210,255', '255,255,255', '255,190,140', '175,160,255'];

let FIELDS = DEFAULT_FIELDS;
let LINE_COLOR = '#cfe0ff';
let TINTS = DEFAULT_SPECK_TINTS;
let IDLE_MS = 90000;
let SPECKS = 40;
const FRAME_MS = 1000 / 30;

let scene = null;
let parLayers = [];
let canvas = null;
let ctx = null;
let sprites = [];
let specks = [];
let raf = 0;
let lastDraw = 0;
let lastT = 0;
let focused = true;
let idle = false;
let idleTimer = 0;
let W = 0;
let H = 0;

function grainUrl() {
  const n = 160;
  const c = document.createElement('canvas');
  c.width = c.height = n;
  const g = c.getContext('2d');
  const img = g.createImageData(n, n);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.random() * 255;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 18;
  }
  g.putImageData(img, 0, 0);
  return c.toDataURL('image/png');
}

/** Smooth closed path through points (quadratic segments through the midpoints). */
function closedPath(pts) {
  const mid = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
  let d = '';
  const N = pts.length;
  for (let k = 0; k < N; k++) {
    const p = pts[k];
    const m = mid(p, pts[(k + 1) % N]);
    d += k === 0 ? `M${m[0].toFixed(1)} ${m[1].toFixed(1)}` : `Q${p[0].toFixed(1)} ${p[1].toFixed(1)} ${m[0].toFixed(1)} ${m[1].toFixed(1)}`;
  }
  const p0 = pts[0];
  const m1 = mid(p0, pts[1]);
  return `${d}Q${p0[0].toFixed(1)} ${p0[1].toFixed(1)} ${m1[0].toFixed(1)} ${m1[1].toFixed(1)}Z`;
}

/** Topographic rings around two centres plus long caustic lines across the portrait frame. */
function linesSvg() {
  let paths = '';
  const centres = [
    { x: 250, y: 360, n: 10, r0: 54, step: 40, seed: 1.7 },
    { x: 760, y: 1010, n: 11, r0: 50, step: 44, seed: 3.9 },
  ];
  for (const c of centres) {
    for (let i = 0; i < c.n; i++) {
      const r = c.r0 + i * c.step;
      const pts = [];
      for (let k = 0; k < 96; k++) {
        const a = (k / 96) * Math.PI * 2;
        const wob = 1 + 0.09 * Math.sin(3 * a + c.seed + i * 0.35) + 0.05 * Math.sin(5 * a - c.seed * 2 + i * 0.2) + 0.03 * Math.sin(8 * a + i);
        pts.push([c.x + Math.cos(a) * r * wob, c.y + Math.sin(a) * r * wob * 0.86]);
      }
      const o = (0.035 + 0.06 * (1 - i / c.n)).toFixed(3);
      paths += `<path d="${closedPath(pts)}" stroke-opacity="${o}"/>`;
    }
  }
  // Caustic lines: long, slightly braided curves, brighter where they cross.
  for (let i = 0; i < 9; i++) {
    const y0 = 140 + i * 128;
    const ph = i * 0.9;
    let d = `M-40 ${y0}`;
    for (let x = 0; x <= 1000; x += 40) {
      const y = y0 + 34 * Math.sin(x / 150 + ph) + 16 * Math.sin(x / 61 - ph * 1.7) + 7 * Math.sin(x / 23 + ph * 3);
      d += ` L${x} ${y.toFixed(1)}`;
    }
    const o = (0.028 + 0.03 * ((i * 7) % 3) / 2).toFixed(3);
    paths += `<path d="${d}" stroke-opacity="${o}" stroke-width="0.9"/>`;
  }
  return `<svg viewBox="0 0 960 1280" preserveAspectRatio="xMidYMid slice" fill="none" stroke="${LINE_COLOR}" stroke-width="1.15">${paths}</svg>`;
}

/** Soft round sprites in a few tints; drawn scaled, so a speck costs one drawImage. */
function makeSprites() {
  return TINTS.map((rgb) => {
    const s = 32;
    const c = document.createElement('canvas');
    c.width = c.height = s;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    grd.addColorStop(0, `rgba(${rgb},1)`);
    grd.addColorStop(0.35, `rgba(${rgb},0.55)`);
    grd.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = grd;
    g.fillRect(0, 0, s, s);
    return c;
  });
}

function spawn(s, anywhere) {
  s.x = Math.random() * W;
  s.y = anywhere ? Math.random() * H : H + 10 + Math.random() * 40;
  s.r = 1.5 + Math.random() * Math.random() * 6;           // half-res px: 3-15 CSS px glow
  s.v = 3 + Math.random() * 8 + s.r * 0.6;                 // rise speed, half-res px / s
  s.a = 0.12 + Math.random() * 0.36;
  s.sw = 4 + Math.random() * 14;                           // sway amplitude
  s.sf = 0.05 + Math.random() * 0.12;                      // sway frequency (Hz)
  s.ph = Math.random() * Math.PI * 2;
  s.sp = Math.random() < 0.12 ? 3 : Math.random() < 0.18 ? 4 : Math.floor(Math.random() * 3);
  return s;
}

function resize() {
  if (!canvas) return;
  const r = canvas.getBoundingClientRect();
  const w = Math.max(2, Math.round(r.width / 2));
  const h = Math.max(2, Math.round(r.height / 2));
  if (w === W && h === H) return;
  const first = !W;
  W = w;
  H = h;
  canvas.width = W;
  canvas.height = H;
  if (first) specks = Array.from({ length: SPECKS }, () => spawn({}, true));
  draw(0, performance.now() / 1000);
}

function draw(dt, t) {
  ctx.clearRect(0, 0, W, H);
  ctx.globalCompositeOperation = 'lighter';
  for (const s of specks) {
    s.y -= s.v * dt;
    if (s.y < -20) spawn(s, false);
    const x = s.x + Math.sin(t * s.sf * Math.PI * 2 + s.ph) * s.sw;
    // Fade in from the bottom, out towards the top: no speck pops in or out.
    const fy = s.y / H;
    const fade = Math.min(1, (1 - fy) * 5, fy * 2.2);
    if (fade <= 0) continue;
    ctx.globalAlpha = s.a * fade;
    const d = s.r * 2;
    ctx.drawImage(sprites[s.sp % sprites.length], x - s.r, s.y - s.r, d, d);
  }
  ctx.globalAlpha = 1;
}

function loop(now) {
  raf = 0;
  if (!running()) return;
  raf = requestAnimationFrame(loop);
  if (now - lastDraw < FRAME_MS) return;
  const dt = lastT ? Math.min(0.1, (now - lastT) / 1000) : 0;
  lastT = now;
  lastDraw = now;
  draw(dt, now / 1000);
}

function running() {
  return !!scene && !document.hidden && focused && !idle && !reducedMotion.matches;
}

function sync() {
  if (!scene) return;
  const go = running();
  scene.classList.toggle('is-paused', !go);
  // Decorative CSS loops inside the app follow the same rule.
  document.documentElement.classList.toggle('is-still', !go);
  if (go && !raf) {
    lastT = 0;
    raf = requestAnimationFrame(loop);
  } else if (!go && raf) {
    cancelAnimationFrame(raf);
    raf = 0;
  }
}

// Pointer parallax: depth per layer (px at the window edge), eased by the CSS transition.
let DEPTH = [10, 5, 16];
let parRaf = 0;
let lastPointer = null;

function parallaxFrame() {
  parRaf = 0;
  if (!lastPointer || reducedMotion.matches) return;
  const nx = lastPointer.x / innerWidth - 0.5;
  const ny = lastPointer.y / innerHeight - 0.5;
  parLayers.forEach((el, i) => {
    const k = DEPTH[i] || 0;
    el.style.transform = `translate3d(${(-nx * k).toFixed(2)}px, ${(-ny * k).toFixed(2)}px, 0)`;
  });
}

/**
 * Build the scene into `root` (default `#scene`). Call once.
 * @param {HTMLElement} root
 * @param {{fields?:{c:string,x:number,y:number,s:number,a:number,d:number,anim:string}[],
 *          lineColor?:string, speckTints?:string[], specks?:number, idleMs?:number,
 *          parallax?:number[]}} options
 */
export function initBackground(root = document.getElementById('scene'), options = {}) {
  if (options.fields) FIELDS = options.fields;
  if (options.lineColor) LINE_COLOR = options.lineColor;
  if (options.speckTints && options.speckTints.length) TINTS = options.speckTints;
  if (options.specks != null) SPECKS = Math.max(0, options.specks);
  if (options.idleMs) IDLE_MS = options.idleMs;
  if (options.parallax) DEPTH = options.parallax;
  scene = root;
  scene.innerHTML = '';

  const fields = document.createElement('div');
  fields.className = 'sc-par sc-fields';
  FIELDS.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'sc-field';
    el.style.cssText = `--x:${f.x}%;--y:${f.y}%;--s:${f.s}vmax;--c:${f.c};--a:${f.a};--d:${f.d}s;--anim:${f.anim};--delay:${-i * 9}s`;
    fields.appendChild(el);
  });

  const lines = document.createElement('div');
  lines.className = 'sc-par sc-lines';
  lines.innerHTML = `<div class="sc-lines-in">${linesSvg()}</div>`;

  const specksWrap = document.createElement('div');
  specksWrap.className = 'sc-par';
  canvas = document.createElement('canvas');
  canvas.className = 'sc-specks';
  specksWrap.appendChild(canvas);
  ctx = canvas.getContext('2d');
  sprites = makeSprites();

  const sweep = document.createElement('div');
  sweep.className = 'sc-sweep';
  const vignette = document.createElement('div');
  vignette.className = 'sc-vignette';
  const grain = document.createElement('div');
  grain.className = 'sc-grain';
  grain.style.backgroundImage = `url(${grainUrl()})`;

  scene.append(fields, lines, specksWrap, sweep, vignette, grain);
  parLayers = [fields, lines, specksWrap];

  new ResizeObserver(resize).observe(canvas);
  resize();

  addEventListener('pointermove', (e) => {
    lastPointer = { x: e.clientX, y: e.clientY };
    if (!parRaf) parRaf = requestAnimationFrame(parallaxFrame);
    activity();
  }, { passive: true });
  addEventListener('keydown', activity);
  addEventListener('wheel', activity, { passive: true });
  addEventListener('pointerdown', activity, { passive: true });
  addEventListener('focus', () => setFocused(true));
  addEventListener('blur', () => setFocused(false));
  document.addEventListener('visibilitychange', sync);
  reducedMotion.addEventListener('change', sync);
  activity();
}

/** Any user input: wake the scene and restart the idle timer. */
export function activity() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    idle = true;
    sync();
  }, IDLE_MS);
  if (idle) {
    idle = false;
  }
  sync();
}

/** Window focus from the shell (`window` event) or the browser. */
export function setFocused(f) {
  focused = !!f;
  if (focused) activity();
  else sync();
}

/** For the verification: whether the speck loop is running. */
export function sceneRunning() {
  return !!raf;
}
