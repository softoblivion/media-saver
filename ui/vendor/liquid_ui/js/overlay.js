// Sheets and popovers that grow out of the control that opened them (Liquid Glass "morph from
// source"). The surface keeps its final size; a clip-path inset springs from the origin rect to the
// full rect while the lensing strength ramps up (materialize) and the content fades in late.
// Esc and clicks on the scrim close the top-most overlay; focus returns to the origin.

import { Spring, SPRINGS, clamp, lerp, reducedMotion } from './motion.js';
import { Lens } from './glass.js';

const stack = [];

function rectOf(el) {
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return { x: r.left, y: r.top, w: r.width, h: r.height, radius: parseFloat(cs.borderTopLeftRadius) || 0 };
}

export class Overlay {
  /**
   * @param {HTMLElement} surface  the glass surface (positioned fixed by CSS)
   * @param {{modal?:boolean, preset?:string, onClose?:Function, place?:(surface, origin)=>void}} opts
   */
  constructor(surface, opts = {}) {
    this.surface = surface;
    this.opts = opts;
    this.content = surface.querySelector('.ov-content') || surface;
    this.open = false;
    this.lens = new Lens(surface, opts.preset || 'sheet', { strength: 0 });
    this.p = new Spring(0, SPRINGS.sheet, (v) => this._render(v));
    this.p.onRest = () => this._rest();
    this.scrim = null;
    if (opts.modal) {
      this.scrim = document.createElement('div');
      this.scrim.className = 'scrim';
      this.scrim.addEventListener('pointerdown', () => this.close());
      surface.before(this.scrim);
    }
    surface.hidden = true;
  }

  toggle(origin) {
    return this.open ? this.close() : this.show(origin);
  }

  show(origin) {
    if (this.open) return;
    this.open = true;
    this.origin = origin || null;
    this.returnFocus = document.activeElement;
    this.surface.hidden = false;
    if (this.scrim) this.scrim.classList.add('is-on');
    if (this.opts.place) this.opts.place(this.surface, origin);
    this.lens.update();
    this.full = rectOf(this.surface);
    this.from = origin ? rectOf(origin) : { ...this.full, x: this.full.x + this.full.w * 0.1, y: this.full.y + 20, w: this.full.w * 0.8, h: this.full.h * 0.8 };
    origin && origin.classList.add('is-source');
    this._render(this.p.value);
    stack.push(this);
    this.surface.classList.add('is-opening');
    this.p.to(1, SPRINGS.sheet);
    this.lens.animateStrength(1, SPRINGS.materialize);
    setTimeout(() => {
      if (!this.open) return;
      const f = this.surface.querySelector('[autofocus], .ov-focus') || this.surface.querySelector('button, input, [tabindex]');
      f && f.focus({ preventScroll: true });
    }, reducedMotion.matches ? 0 : 160);
  }

  close() {
    if (!this.open) return;
    this.open = false;
    const i = stack.indexOf(this);
    if (i >= 0) stack.splice(i, 1);
    if (this.scrim) this.scrim.classList.remove('is-on');
    this.surface.classList.remove('is-open');
    this.surface.classList.add('is-opening');
    if (this.origin && this.origin.isConnected) this.from = rectOf(this.origin);
    this.p.to(0, SPRINGS.sheetClose);
    this.lens.animateStrength(0, SPRINGS.sheetClose);
    this.opts.onClose && this.opts.onClose();
    if (this.returnFocus && this.returnFocus.isConnected) this.returnFocus.focus({ preventScroll: true });
  }

  _render(p) {
    const s = this.surface;
    const f = this.full;
    const o = this.from;
    if (!f || !o) return;
    const q = clamp(p, 0, 1);
    // Inset of the clip relative to the full rect.
    const top = lerp(o.y - f.y, 0, q);
    const left = lerp(o.x - f.x, 0, q);
    const right = lerp(f.x + f.w - (o.x + o.w), 0, q);
    const bottom = lerp(f.y + f.h - (o.y + o.h), 0, q);
    const radius = lerp(o.radius || 20, parseFloat(getComputedStyle(s).borderTopLeftRadius) || 28, q);
    s.style.clipPath = `inset(${top.toFixed(1)}px ${right.toFixed(1)}px ${bottom.toFixed(1)}px ${left.toFixed(1)}px round ${radius.toFixed(1)}px)`;
    // Overshoot reads as a soft stretch of the whole surface.
    const over = Math.max(0, p - 1);
    s.style.scale = over ? `${(1 + over * 0.12).toFixed(4)} ${(1 + over * 0.08).toFixed(4)}` : '';
    const c = clamp((p - 0.35) / 0.55, 0, 1);
    this.content.style.opacity = c.toFixed(3);
    this.content.style.translate = `0 ${((1 - c) * 10).toFixed(2)}px`;
    if (this.scrim) this.scrim.style.opacity = (q * 1).toFixed(3);
  }

  _rest() {
    const s = this.surface;
    s.classList.remove('is-opening');
    if (this.open) {
      s.style.clipPath = '';
      s.style.scale = '';
      s.classList.add('is-open');
      this.content.style.opacity = '';
      this.content.style.translate = '';
    } else {
      s.hidden = true;
      s.style.clipPath = '';
      if (this.origin) this.origin.classList.remove('is-source');
      if (this.scrim) this.scrim.style.opacity = '';
    }
  }
}

export function topOverlay() {
  return stack[stack.length - 1] || null;
}

addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const top = topOverlay();
    if (top) {
      e.preventDefault();
      top.close();
    }
  }
});

// Clicks outside a non-modal popover close it.
addEventListener('pointerdown', (e) => {
  const top = topOverlay();
  if (!top || top.opts.modal) return;
  if (top.surface.contains(e.target)) return;
  if (top.origin && top.origin.contains(e.target)) return;
  top.close();
}, true);
