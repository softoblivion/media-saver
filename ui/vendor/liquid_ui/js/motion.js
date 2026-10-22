// Motion core: a damped-spring solver, one shared rAF ticker that runs only while something moves,
// and CSS linear() easings sampled from the same springs for compositor-driven WAAPI animations.
//
// Springs are described the SwiftUI way: `response` (seconds, roughly the period of the undamped
// oscillation) and `damping` (damping fraction, 1 = critically damped, < 1 overshoots).
// stiffness k = (2*pi / response)^2, damping c = 4*pi*damping / response, mass = 1.

export const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

// The motion template (the kit's documentation, "Motion"): these springs, the CSS durations
// --d-1 .. --d-4 (mirrored in DURATIONS) and one rule per element kind. Every app uses these
// names and nothing else.
export const SPRINGS = {
  snappy:      { response: 0.30, damping: 0.80 },  // toggles, droplet lift, switch press
  bouncy:      { response: 0.42, damping: 0.62 },  // droplet travel, toasts, rows entering
  jelly:       { response: 0.38, damping: 0.42 },  // gel wobble after a press
  press:       { response: 0.20, damping: 0.75 },  // the squish while the pointer is down
  smooth:      { response: 0.50, damping: 1.00 },  // layout, content, numbers
  expand:      { response: 0.46, damping: 0.86 },  // heights: drawers, rows collapsing
  sheet:       { response: 0.52, damping: 0.80 },  // sheets and popovers growing out of their button
  sheetClose:  { response: 0.34, damping: 1.00 },  // overlays, menus and rows leaving
  materialize: { response: 0.45, damping: 0.92 },  // lensing ramp in / out
};

/** CSS durations of tokens.css in ms, for JS that times a CSS transition. */
export const DURATIONS = { hover: 120, fade: 180, layout: 300, reveal: 520 };

/** Stagger between rows that enter together (ms). */
export const STAGGER_MS = 40;

/**
 * Minimum time a press stays visibly pressed (ms). A click is often shorter than the press
 * spring, so without this the squish or the droplet lift starts and reverses in the same few
 * frames and reads as a twitch; the release is delayed until the press has been shown this long.
 */
export const PRESS_HOLD_MS = 150;

/** Run `release` once the press that started at `downAt` has been shown for PRESS_HOLD_MS. */
export function afterHold(downAt, release) {
  const wait = Math.max(0, PRESS_HOLD_MS - (performance.now() - downAt));
  if (wait <= 0) release();
  else setTimeout(release, wait);
}

function params(cfg) {
  let { response, damping } = cfg;
  if (reducedMotion.matches) {
    // Reduced motion: no elastic behaviour, quicker settle.
    damping = 1;
    response = Math.min(response, 0.25);
  }
  const w = (2 * Math.PI) / response;
  return { k: w * w, c: 2 * damping * w };
}

// ---------------------------------------------------------------------------------------------
// Ticker

const active = new Set();
let rafId = 0;
let lastT = 0;

function frame(t) {
  rafId = 0;
  let dt = lastT ? (t - lastT) / 1000 : 1 / 60;
  lastT = t;
  if (dt > 1 / 20) dt = 1 / 20;  // a long frame (tab switch, GC) must not explode the integration
  for (const s of Array.from(active)) {
    s._step(dt);
    if (s._resting) {
      active.delete(s);
      s._settled();
    }
  }
  if (active.size && !document.hidden) rafId = requestAnimationFrame(frame);
  else lastT = 0;
}

function wake(s) {
  active.add(s);
  if (!rafId && !document.hidden) {
    lastT = 0;
    rafId = requestAnimationFrame(frame);
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    // Hidden window: stop the loop and jump every spring to its target.
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    lastT = 0;
    for (const s of Array.from(active)) s.snap();
  }
});

/** Number of springs currently animating (exposed for the idle check in the verification). */
export function activeSprings() {
  return active.size;
}

// ---------------------------------------------------------------------------------------------
// Spring

export class Spring {
  /**
   * @param {number} value initial value
   * @param {{response:number,damping:number}} cfg
   * @param {(v:number, s:Spring)=>void} onUpdate called every frame while moving and once on settle
   */
  constructor(value, cfg = SPRINGS.smooth, onUpdate = null) {
    this.value = value;
    this.target = value;
    this.velocity = 0;
    this.precision = 0.001;
    this.onUpdate = onUpdate;
    this.onRest = null;
    this.setConfig(cfg);
    this._resting = true;
  }

  setConfig(cfg) {
    this.cfg = cfg;
    const p = params(cfg);
    this.k = p.k;
    this.c = p.c;
    return this;
  }

  /** Animate to a target. Optional config switch and initial velocity kick. */
  to(target, cfg = null, velocity = null) {
    if (cfg) this.setConfig(cfg);
    if (velocity !== null) this.velocity = velocity;
    this.target = target;
    if (this.value === target && Math.abs(this.velocity) < 1e-6) {
      this.onUpdate && this.onUpdate(this.value, this);
      return this;
    }
    this._resting = false;
    wake(this);
    return this;
  }

  /** Jump to a value without animation. */
  set(value) {
    this.value = this.target = value;
    this.velocity = 0;
    this._resting = true;
    active.delete(this);
    this.onUpdate && this.onUpdate(value, this);
    return this;
  }

  snap() {
    this.set(this.target);
    this.onRest && this.onRest(this);
  }

  _step(dt) {
    // Semi-implicit Euler with fixed sub-steps: stable for the stiff, lightly damped springs used here.
    const h = 1 / 240;
    let n = Math.max(1, Math.round(dt / h));
    const sub = dt / n;
    let x = this.value;
    let v = this.velocity;
    while (n--) {
      const a = -this.k * (x - this.target) - this.c * v;
      v += a * sub;
      x += v * sub;
    }
    this.value = x;
    this.velocity = v;
    const scale = Math.max(1, Math.abs(this.target));
    if (Math.abs(x - this.target) < this.precision * scale && Math.abs(v) < this.precision * 10 * scale) {
      this.value = this.target;
      this.velocity = 0;
      this._resting = true;
    }
    this.onUpdate && this.onUpdate(this.value, this);
  }

  _settled() {
    this.onRest && this.onRest(this);
  }
}

// ---------------------------------------------------------------------------------------------
// CSS easing from a spring

const easingCache = new Map();

/**
 * Sample a spring from 0 to 1 (zero initial velocity) into a CSS linear() easing.
 * Returns { easing, duration } with duration in ms, the time until the spring settles.
 */
export function springEasing(cfg = SPRINGS.bouncy) {
  const key = `${cfg.response}|${cfg.damping}|${reducedMotion.matches}`;
  const hit = easingCache.get(key);
  if (hit) return hit;
  const { k, c } = params(cfg);
  const dt = 1 / 600;
  let x = 0, v = 0, t = 0;
  const samples = [];
  // Settle when the oscillation stays inside 0.1 % for the rest of the motion.
  for (; t < 3; t += dt) {
    const a = -k * (x - 1) - c * v;
    v += a * dt;
    x += v * dt;
    samples.push([t, x]);
    if (Math.abs(x - 1) < 0.001 && Math.abs(v) < 0.02) break;
  }
  const duration = Math.max(t, 0.05);
  const points = [];
  const count = 48;
  for (let i = 0; i <= count; i++) {
    const tt = (duration * i) / count;
    const idx = Math.min(samples.length - 1, Math.round(tt / dt));
    const value = i === count ? 1 : samples[idx][1];
    points.push(`${value.toFixed(4)} ${((i / count) * 100).toFixed(2)}%`);
  }
  const out = { easing: `linear(${points.join(', ')})`, duration: Math.round(duration * 1000) };
  easingCache.set(key, out);
  return out;
}

/** WAAPI helper driven by a spring easing. Returns the Animation. */
export function springAnimate(el, keyframes, cfg = SPRINGS.bouncy, extra = {}) {
  const { easing, duration } = springEasing(cfg);
  return el.animate(keyframes, { duration, easing, fill: 'both', ...extra });
}

/**
 * Run fn once when the animation finishes, or after a timeout if it never does (a hidden window
 * pauses the animation timeline; DOM clean-up must not depend on it).
 */
export function whenDone(anim, fn, extraMs = 400) {
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    fn();
  };
  anim.onfinish = run;
  const t = anim.effect && anim.effect.getComputedTiming ? anim.effect.getComputedTiming().endTime : 1000;
  setTimeout(run, (Number(t) || 1000) + extraMs);
}

// ---------------------------------------------------------------------------------------------
// Small helpers

export const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

// ---------------------------------------------------------------------------------------------
// Element entrances and exits shared by every app (the motion template).

/**
 * A list row (card, queue item, chip) entering: drops in from 16 px below with `bouncy`,
 * staggered by STAGGER_MS per index. Returns the Animation (null with reduced motion).
 */
export function enterRow(el, index = 0, { from = 16 } = {}) {
  if (reducedMotion.matches) return null;
  return springAnimate(el, [
    { opacity: 0, transform: `translateY(${from}px) scale(0.98)` },
    { opacity: 1, transform: 'none' },
  ], SPRINGS.bouncy, { delay: index * STAGGER_MS, fill: 'backwards' });
}

/**
 * A list row leaving: fades and shrinks (`sheetClose`), then its height collapses (`expand`)
 * so the rows below move up, then `el` is removed. Resolves when the row is gone.
 */
export function leaveRow(el) {
  return new Promise((resolve) => {
    if (!el.isConnected) return resolve();
    if (reducedMotion.matches) {
      el.remove();
      return resolve();
    }
    const h = el.offsetHeight;
    const cs = getComputedStyle(el);
    const mt = parseFloat(cs.marginTop) || 0;
    const mb = parseFloat(cs.marginBottom) || 0;
    el.style.pointerEvents = 'none';
    const fade = springAnimate(el, [
      { opacity: 1, transform: 'none' },
      { opacity: 0, transform: 'scale(0.96)' },
    ], SPRINGS.sheetClose);
    whenDone(fade, () => {
      el.style.overflow = 'hidden';
      const collapse = springAnimate(el, [
        { height: `${h}px`, marginTop: `${mt}px`, marginBottom: `${mb}px` },
        { height: '0px', marginTop: '0px', marginBottom: '0px' },
      ], SPRINGS.expand);
      whenDone(collapse, () => {
        el.remove();
        resolve();
      });
    });
  });
}

/**
 * A surface fading out in place (menus, hints, chips that vanish without a layout change):
 * `sheetClose` to 0 opacity and 0.96 scale, then `el.hidden = true`. Resolves when hidden.
 */
export function fadeOut(el) {
  return new Promise((resolve) => {
    if (reducedMotion.matches || el.hidden) {
      el.hidden = true;
      return resolve();
    }
    el.classList.add('is-closing');
    const a = springAnimate(el, [
      { opacity: 1, transform: 'none' },
      { opacity: 0, transform: 'translateY(4px) scale(0.96)' },
    ], SPRINGS.sheetClose, { fill: 'none' });
    whenDone(a, () => {
      el.hidden = true;
      el.classList.remove('is-closing');
      resolve();
    });
  });
}

/** A surface appearing in place (menus): from 6 px below at 0.96 scale with `snappy`. */
export function fadeIn(el) {
  el.hidden = false;
  if (reducedMotion.matches) return null;
  return springAnimate(el, [
    { opacity: 0, transform: 'translateY(6px) scale(0.96)' },
    { opacity: 1, transform: 'none' },
  ], SPRINGS.snappy, { fill: 'none' });
}
