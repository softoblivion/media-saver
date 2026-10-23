// Form controls with Liquid Glass behaviour.
//  - Segmented: a droplet indicator that stretches with its velocity and lifts into refracting
//    glass while pressed or dragged. Variant `scroll` (chip rows with many options): the track
//    scrolls horizontally, a tap selects, the droplet scrolls with the options.
//  - Switch: the thumb turns into a glass droplet while pressed, then springs across.
//  - Stepper: +/- with a rolling number (critically damped, clipped).
//  - Select: a compact menu button.
//  - Slider: a range input with a live number (quality).
// Every control that owns a Lens has destroy(): panels are built and torn down with the card.

import { Spring, SPRINGS, clamp, springEasing, whenDone, reducedMotion, fadeIn, fadeOut, afterHold } from './motion.js';
import { Lens, gel } from './glass.js';
import { icon } from './icons.js';
import { escapeHtml } from './format.js';

// ---------------------------------------------------------------------------------------------
// Segmented control

export class Segmented {
  /**
   * @param {{label:string, options:{value:string,label:string,title?:string,disabled?:boolean}[],
   *          value:string, onChange:(v)=>void, cls?:string, scroll?:boolean}} o
   */
  constructor(o) {
    this.o = o;
    this.value = o.value;
    this.scroll = !!o.scroll;
    const el = (this.el = document.createElement('div'));
    el.className = `seg${o.cls ? ` ${o.cls}` : ''}${this.scroll ? ' seg-scroll' : ''}`;
    el.setAttribute('role', 'radiogroup');
    el.setAttribute('aria-label', o.label);
    el.innerHTML = `<span class="seg-drop" aria-hidden="true"></span>` +
      o.options.map((op) => `<button type="button" class="seg-opt" role="radio" data-v="${escapeHtml(op.value)}"${op.title ? ` title="${escapeHtml(op.title)}"` : ''}>${escapeHtml(op.label)}</button>`).join('');
    this.drop = el.querySelector('.seg-drop');
    this.opts = Array.from(el.querySelectorAll('.seg-opt'));
    this.lens = new Lens(this.drop, 'droplet', { strength: 0 });
    this.x = new Spring(0, SPRINGS.bouncy, () => this._render());
    this.w = new Spring(0, SPRINGS.bouncy, () => this._render());
    this.lift = new Spring(0, SPRINGS.snappy, (v) => {
      this.lens.setStrength(v);
      this.drop.style.setProperty('--lift', v.toFixed(3));
      this._render();
    });
    this.disabled = false;
    this.off = new Set(o.options.filter((x) => x.disabled).map((x) => x.value));
    this.opts.forEach((b, i) => {
      b.addEventListener('click', (e) => {
        // Pointer selection of the plain variant is resolved in the drag handler (pointer capture
        // retargets the click); the scroll variant and keyboard activation select here.
        if (this.disabled || this.off.has(b.dataset.v)) return;
        if (this.scroll || e.detail === 0) this.set(b.dataset.v, true);
      });
      b.addEventListener('keydown', (e) => this._key(e, i));
    });
    if (this.scroll) this._scrollPress();
    else this._drag();
    this.ro = new ResizeObserver(() => {
      this._place(false);
      this._fades();
    });
    this.ro.observe(el);
    if (this.scroll) el.addEventListener('scroll', () => this._fades(), { passive: true });
    this._sync();
  }

  setDisabled(off, title = '') {
    this.disabled = !!off;
    this.el.classList.toggle('is-disabled', this.disabled);
    this.el.setAttribute('aria-disabled', String(this.disabled));
    if (this.disabled && title) this.el.title = title;
    else this.el.removeAttribute('title');
    this._sync();
  }

  /** Disable one option (e.g. GPU without NVENC); its title explains why. */
  setOptionDisabled(v, off, title = '') {
    const b = this.opts.find((x) => x.dataset.v === v);
    if (!b) return;
    if (off) this.off.add(v);
    else this.off.delete(v);
    b.classList.toggle('is-off', !!off);
    b.setAttribute('aria-disabled', String(!!off));
    if (title) b.title = title;
    else if (!off) b.removeAttribute('title');
  }

  _key(e, i) {
    if (this.disabled) return;
    const n = this.opts.length;
    let dir = 0;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') dir = 1;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') dir = -1;
    if (!dir) return;
    e.preventDefault();
    let j = i;
    for (let k = 0; k < n; k++) {
      j = (j + dir + n) % n;
      if (!this.off.has(this.opts[j].dataset.v)) break;
    }
    this.opts[j].focus();
    this.set(this.opts[j].dataset.v, true);
  }

  _scrollPress() {
    let downAt = 0;
    const up = () => afterHold(downAt, () => this.lift.to(0, SPRINGS.smooth));
    this.el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || this.disabled) return;
      downAt = performance.now();
      this.lift.to(1, SPRINGS.snappy);
      addEventListener('pointerup', up, { once: true });
      addEventListener('pointercancel', up, { once: true });
    });
    // A vertical wheel scrolls the chip row while it can still move that way; else the page scrolls.
    this.el.addEventListener('wheel', (e) => {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      const max = this.el.scrollWidth - this.el.clientWidth;
      if (max <= 1) return;
      const next = this.el.scrollLeft + e.deltaY;
      if ((e.deltaY < 0 && this.el.scrollLeft <= 0) || (e.deltaY > 0 && this.el.scrollLeft >= max - 1)) return;
      e.preventDefault();
      this.el.scrollLeft = clamp(next, 0, max);
    }, { passive: false });
  }

  _drag() {
    let start = null;
    this.el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || this.disabled) return;
      const r = this.el.getBoundingClientRect();
      start = { x: e.clientX, left: r.left, moved: false, at: performance.now() };
      this.lift.to(1, SPRINGS.snappy);
      this.el.setPointerCapture(e.pointerId);
    });
    this.el.addEventListener('pointermove', (e) => {
      if (!start) return;
      if (Math.abs(e.clientX - start.x) > 4) start.moved = true;
      if (!start.moved) return;
      const b = this._optRect(this.value);
      const target = clamp(e.clientX - start.left - b.w / 2, 3, this.el.clientWidth - b.w - 3);
      this.x.to(target, SPRINGS.press);
    });
    const end = (e) => {
      if (!start) return;
      const moved = start.moved;
      const left = start.left;
      const at = start.at;
      start = null;
      // The lifted droplet settles after the hold, while it travels to the chosen option.
      afterHold(at, () => { if (!start) this.lift.to(0, SPRINGS.smooth); });
      const cx = moved ? this.x.value + this._optRect(this.value).w / 2 : e.clientX - left;
      let best = null;
      for (const b of this.opts) {
        const l = b.offsetLeft;
        if (cx >= l && cx <= l + b.offsetWidth) best = b;
      }
      if (!best) best = cx < this.opts[0].offsetLeft ? this.opts[0] : this.opts[this.opts.length - 1];
      if ((e.type === 'pointercancel' && !moved) || this.off.has(best.dataset.v)) return this._place(true);
      this.set(best.dataset.v, true);
    };
    this.el.addEventListener('pointerup', end);
    this.el.addEventListener('pointercancel', end);
  }

  _optRect(v) {
    const b = this.opts.find((o) => o.dataset.v === v) || this.opts[0];
    return { x: b.offsetLeft, w: b.offsetWidth };
  }

  _render() {
    const v = this.x.velocity;
    const stretch = clamp(Math.abs(v) / 2400, 0, 0.32);
    const lift = this.lift.value;
    const sx = 1 + stretch + lift * 0.08;
    const sy = 1 - stretch * 0.35 + lift * 0.14;
    this.drop.style.width = `${this.w.value.toFixed(2)}px`;
    this.drop.style.transform = `translateX(${this.x.value.toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
  }

  _place(animate) {
    const r = this._optRect(this.value);
    if (!r.w) return;
    const has = this.opts.some((o) => o.dataset.v === this.value);
    this.drop.style.visibility = has ? '' : 'hidden';
    if (animate && !reducedMotion.matches) {
      this.x.to(r.x, SPRINGS.bouncy);
      this.w.to(r.w, SPRINGS.bouncy);
    } else {
      this.x.set(r.x);
      this.w.set(r.w);
    }
    if (this.scroll) this._reveal(animate);
  }

  /** Scroll variant: fade the side that can still scroll. */
  _fades() {
    if (!this.scroll) return;
    const el = this.el;
    const max = el.scrollWidth - el.clientWidth;
    el.classList.toggle('fade-l', max > 1 && el.scrollLeft > 2);
    el.classList.toggle('fade-r', max > 1 && el.scrollLeft < max - 2);
  }

  /** Scroll variant: keep the selected chip in view (horizontal only, never the page). */
  _reveal(smooth) {
    const r = this._optRect(this.value);
    const el = this.el;
    const pad = 24;
    let left = el.scrollLeft;
    if (r.x - pad < left) left = r.x - pad;
    else if (r.x + r.w + pad > left + el.clientWidth) left = r.x + r.w + pad - el.clientWidth;
    if (Math.abs(left - el.scrollLeft) > 1) el.scrollTo({ left: Math.max(0, left), behavior: smooth ? 'smooth' : 'auto' });
  }

  _sync() {
    this.opts.forEach((b) => {
      const on = b.dataset.v === this.value;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on && !this.disabled ? 0 : -1;
      b.classList.toggle('is-on', on);
    });
  }

  /** Set the value; `user` = from an interaction (calls onChange). */
  set(v, user = false) {
    if (v === this.value) {
      this._place(true);
      return;
    }
    this.value = v;
    this._sync();
    this._place(true);
    if (user) this.o.onChange && this.o.onChange(v);
  }

  destroy() {
    this.ro.disconnect();
    this.lens.destroy();
  }
}

// ---------------------------------------------------------------------------------------------
// Switch

export class Switch {
  constructor({ label, checked, onChange }) {
    this.onChange = onChange;
    this.checked = !!checked;
    this.disabled = false;
    const el = (this.el = document.createElement('button'));
    el.type = 'button';
    el.className = 'switch';
    el.setAttribute('role', 'switch');
    el.setAttribute('aria-label', label);
    el.innerHTML = '<span class="switch-fill"></span><span class="switch-thumb"></span>';
    this.thumb = el.querySelector('.switch-thumb');
    this.lens = new Lens(this.thumb, 'droplet', { strength: 0 });
    this.pos = new Spring(this.checked ? 1 : 0, SPRINGS.bouncy, () => this._render());
    this.press = new Spring(0, SPRINGS.snappy, (v) => {
      this.lens.setStrength(v);
      this.thumb.style.setProperty('--lift', v.toFixed(3));
      this._render();
    });
    let downAt = 0;
    el.addEventListener('pointerdown', (e) => {
      if (e.button === 0 && !this.disabled) {
        downAt = performance.now();
        this.press.to(1, SPRINGS.snappy);
      }
    });
    const up = () => afterHold(downAt, () => this.press.to(0, SPRINGS.smooth));
    el.addEventListener('pointerup', up);
    el.addEventListener('pointerleave', up);
    el.addEventListener('click', () => {
      if (!this.disabled) this.set(!this.checked, true);
    });
    this._sync();
    this._render();
  }

  _render() {
    const p = this.pos.value;
    const lift = this.press.value;
    const W = 40, H = 24, pad = 2;   // the track and thumb sizes of css/components.css (.switch)
    const tw = 20 + lift * 8 + Math.min(Math.abs(this.pos.velocity) * 2, 4);
    const th = 20 + lift * 2;
    const x = pad + p * (W - pad * 2 - tw);
    this.thumb.style.width = `${tw.toFixed(2)}px`;
    this.thumb.style.height = `${th.toFixed(2)}px`;
    this.thumb.style.transform = `translate(${x.toFixed(2)}px, ${((H - th) / 2 - pad).toFixed(2)}px)`;
    this.el.style.setProperty('--p', clamp(p, 0, 1).toFixed(3));
  }

  _sync() {
    this.el.setAttribute('aria-checked', String(this.checked));
    this.el.classList.toggle('is-on', this.checked);
  }

  setDisabled(off) {
    this.disabled = !!off;
    this.el.disabled = this.disabled;
  }

  set(v, user = false) {
    v = !!v;
    if (v === this.checked) return;
    this.checked = v;
    this._sync();
    if (reducedMotion.matches) this.pos.set(v ? 1 : 0);
    else this.pos.to(v ? 1 : 0, SPRINGS.bouncy);
    if (user) this.onChange && this.onChange(v);
  }

  destroy() {
    this.lens.destroy();
  }
}

// ---------------------------------------------------------------------------------------------
// Stepper

const ROLL = { response: 0.26, damping: 1 };

export class Stepper {
  constructor({ label, value, min, max, onChange }) {
    this.min = min;
    this.max = max;
    this.value = value;
    this.onChange = onChange;
    const el = (this.el = document.createElement('div'));
    el.className = 'stepper';
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', label);
    el.innerHTML = `
      <button type="button" class="step-btn" data-d="-1" aria-label="Decrease">${icon('minus')}</button>
      <span class="step-val" aria-live="polite"><span class="step-num">${value}</span></span>
      <button type="button" class="step-btn" data-d="1" aria-label="Increase">${icon('plus')}</button>`;
    this.val = el.querySelector('.step-val');
    this.btns = Array.from(el.querySelectorAll('.step-btn'));
    this.btns.forEach((b) => {
      gel(b);
      b.addEventListener('click', () => this.set(this.value + Number(b.dataset.d), true));
    });
    el.addEventListener('keydown', (e) => {
      const k = e.key;
      let v = null;
      if (k === 'ArrowUp' || k === 'ArrowRight' || k === '+' || k === '=') v = this.value + 1;
      else if (k === 'ArrowDown' || k === 'ArrowLeft' || k === '-' || k === '_') v = this.value - 1;
      else if (k === 'Home') v = this.min;
      else if (k === 'End') v = this.max;
      if (v === null) return;
      e.preventDefault();
      this.set(v, true);
    });
    this._sync();
  }

  _sync() {
    const [minus, plus] = this.btns;
    const focused = this.btns.find((b) => b === document.activeElement);
    minus.disabled = this.value <= this.min;
    plus.disabled = this.value >= this.max;
    if (focused && focused.disabled) (focused === minus ? plus : minus).focus();
  }

  set(v, user = false) {
    v = clamp(v, this.min, this.max);
    if (v === this.value) return;
    const dir = v > this.value ? 1 : -1;
    this.value = v;
    this._sync();
    this._roll(v, dir);
    if (user) this.onChange && this.onChange(v);
  }

  _roll(v, dir) {
    const spans = Array.from(this.val.querySelectorAll('.step-num'));
    const old = spans.pop();
    spans.forEach((s) => s.remove());
    let fromY = '0px';
    let fromO = 1;
    const running = old.getAnimations();
    if (running.length) {
      const cs = getComputedStyle(old);
      fromY = `${(cs.transform && cs.transform !== 'none' ? new DOMMatrixReadOnly(cs.transform).m42 : 0).toFixed(2)}px`;
      fromO = Number(cs.opacity);
      running.forEach((a) => a.cancel());
    }
    if (reducedMotion.matches) {
      old.textContent = String(v);
      return;
    }
    old.setAttribute('aria-hidden', 'true');
    const neu = document.createElement('span');
    neu.className = 'step-num';
    neu.textContent = String(v);
    this.val.appendChild(neu);
    const { easing, duration } = springEasing(ROLL);
    old.animate([
      { transform: `translateY(${fromY})`, opacity: fromO },
      { opacity: 0, offset: 0.5 },
      { transform: `translateY(${-dir * 100}%)`, opacity: 0 },
    ], { duration, easing, fill: 'forwards' });
    const anim = neu.animate([
      { transform: `translateY(${dir * 100}%)`, opacity: 0 },
      { transform: 'translateY(0)', opacity: 1 },
    ], { duration, easing });
    whenDone(anim, () => old.remove(), 100);
  }
}

// ---------------------------------------------------------------------------------------------
// Select (menu button). The menu opens above the button (`up`, default) or below it.

export class Select {
  constructor({ label, options, value, onChange, up = true }) {
    this.options = options;
    this.value = value;
    this.onChange = onChange;
    const el = (this.el = document.createElement('div'));
    el.className = `select${up ? ' is-up' : ''}`;
    el.innerHTML = `<button type="button" class="select-btn" aria-haspopup="listbox" aria-label="${escapeHtml(label)}"><span class="select-label"></span>${icon('chevronDown')}</button>
      <div class="select-menu glass-lite" role="listbox" hidden>${options.map((o) => `<button type="button" role="option" class="select-opt" data-v="${escapeHtml(o.value)}">${icon('check', 'select-check')}<span>${escapeHtml(o.label)}</span></button>`).join('')}</div>`;
    this.btn = el.querySelector('.select-btn');
    this.menu = el.querySelector('.select-menu');
    gel(this.btn);
    this.btn.addEventListener('click', () => (this.menu.hidden || this.menu.classList.contains('is-closing') ? this.openMenu() : this.closeMenu()));
    this.menu.addEventListener('click', (e) => {
      const b = e.target.closest('.select-opt');
      if (!b) return;
      this.set(b.dataset.v, true);
      this.closeMenu();
      this.btn.focus();
    });
    this.menu.addEventListener('keydown', (e) => {
      const items = Array.from(this.menu.querySelectorAll('.select-opt'));
      const i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); this.closeMenu(); this.btn.focus(); }
      if (e.key === 'Tab') this.closeMenu();
    });
    this._outside = (e) => { if (!el.contains(e.target)) this.closeMenu(); };
    this._sync();
  }

  openMenu() {
    if (!this.menu.hidden) return;
    this.menu.getAnimations().forEach((a) => a.cancel());
    this.menu.classList.remove('is-closing');
    fadeIn(this.menu);
    this.btn.setAttribute('aria-expanded', 'true');
    const cur = this.menu.querySelector('.is-on') || this.menu.querySelector('.select-opt');
    cur && cur.focus();
    addEventListener('pointerdown', this._outside, true);
  }

  closeMenu() {
    if (this.menu.hidden || this.menu.classList.contains('is-closing')) return;
    this.btn.setAttribute('aria-expanded', 'false');
    removeEventListener('pointerdown', this._outside, true);
    fadeOut(this.menu);
  }

  _sync() {
    const o = this.options.find((x) => x.value === this.value) || this.options[0];
    this.el.querySelector('.select-label').textContent = o ? o.label : '';
    this.menu.querySelectorAll('.select-opt').forEach((b) => {
      const on = b.dataset.v === this.value;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-selected', String(on));
    });
  }

  set(v, user = false) {
    if (v === this.value) return;
    this.value = v;
    this._sync();
    if (user) this.onChange && this.onChange(v);
  }
}

// ---------------------------------------------------------------------------------------------
// Slider: a styled range input with the value beside it. onInput while dragging (live number),
// onChange once on release / key commit.

export class Slider {
  constructor({ label, min = 0, max = 100, step = 1, value, onChange }) {
    this.onChange = onChange;
    this.min = min;
    this.max = max;
    const el = (this.el = document.createElement('div'));
    el.className = 'slider';
    el.innerHTML = `<input type="range" class="slider-in" min="${min}" max="${max}" step="${step}" aria-label="${escapeHtml(label)}"><output class="slider-val tnum"></output>`;
    this.input = el.querySelector('input');
    this.out = el.querySelector('output');
    this.input.addEventListener('input', () => this._paint(Number(this.input.value)));
    this.input.addEventListener('change', () => this.onChange && this.onChange(Number(this.input.value)));
    this.set(value);
  }

  _paint(v) {
    const p = (v - this.min) / (this.max - this.min || 1);
    this.el.style.setProperty('--v', p.toFixed(4));
    this.out.textContent = String(v);
  }

  set(v) {
    if (document.activeElement === this.input && this._dragging) return;
    this.input.value = String(v);
    this._paint(Number(this.input.value));
  }

  setDisabled(off) {
    this.input.disabled = !!off;
    this.el.classList.toggle('is-disabled', !!off);
  }
}
