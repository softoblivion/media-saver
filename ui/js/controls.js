// Form controls with Liquid Glass behaviour:
//  - Segmented: a droplet indicator that stretches with its velocity and lifts into refracting
//    glass while pressed or dragged.
//  - Switch: the thumb turns into a glass droplet while pressed, then springs across.
//  - Stepper: +/- with a rolling number.
//  - Select: a compact menu (used for the browser cookies choice).

import { Spring, SPRINGS, clamp } from './motion.js';
import { Lens, gel } from './glass.js';
import { icon } from './icons.js';
import { escapeHtml } from './format.js';

// ---------------------------------------------------------------------------------------------
// Segmented control

export class Segmented {
  /**
   * @param {{label:string, options:{value:string,label:string,title?:string}[], value:string,
   *          onChange:(v)=>void, cls?:string}} o  cls: size variant ("seg-tb", "seg-l", "seg-q")
   */
  constructor(o) {
    this.o = o;
    this.value = o.value;
    const el = (this.el = document.createElement('div'));
    el.className = o.cls ? `seg ${o.cls}` : 'seg';
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
    this.opts.forEach((b, i) => {
      // Pointer selection is resolved in the drag handler (pointer capture retargets the click to
      // the track); this listener only serves keyboard activation (detail === 0).
      b.addEventListener('click', (e) => {
        if (e.detail === 0 && !this.disabled) this.set(b.dataset.v, true);
      });
      b.addEventListener('keydown', (e) => this._key(e, i));
    });
    this.disabled = false;
    this._drag();
    new ResizeObserver(() => this._place(false)).observe(el);
    this._sync();
  }

  /**
   * Disabled: dimmed, out of the tab order, ignores pointer and keys; the value is kept. `title`
   * goes on the whole control (the options take no pointer events while disabled, so it shows).
   */
  setDisabled(off, title = '') {
    this.disabled = !!off;
    this.el.classList.toggle('is-disabled', this.disabled);
    this.el.setAttribute('aria-disabled', String(this.disabled));
    if (this.disabled && title) this.el.title = title;
    else this.el.removeAttribute('title');
    this._sync();
  }

  /** Change the tooltip of one option. */
  setOptionTitle(v, title) {
    const b = this.opts.find((o) => o.dataset.v === v);
    if (!b) return;
    if (title) b.title = title;
    else b.removeAttribute('title');
  }

  _key(e, i) {
    if (this.disabled) return;
    const n = this.opts.length;
    let j = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % n;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + n) % n;
    if (j === null) return;
    e.preventDefault();
    this.opts[j].focus();
    this.set(this.opts[j].dataset.v, true);
  }

  _drag() {
    let start = null;
    this.el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || this.disabled) return;
      const r = this.el.getBoundingClientRect();
      start = { x: e.clientX, left: r.left, moved: false, id: e.pointerId };
      this.lift.to(1, SPRINGS.snappy);
      this.el.setPointerCapture(e.pointerId);
    });
    this.el.addEventListener('pointermove', (e) => {
      if (!start) return;
      if (Math.abs(e.clientX - start.x) > 4) start.moved = true;
      if (!start.moved) return;
      // The droplet follows the finger, centred under it, clamped to the track.
      const b = this._optRect(this.value);
      const target = clamp(e.clientX - start.left - b.w / 2, 3, this.el.clientWidth - b.w - 3);
      this.x.to(target, SPRINGS.press);
    });
    const end = (e) => {
      if (!start) return;
      const moved = start.moved;
      const left = start.left;
      start = null;
      this.lift.to(0, SPRINGS.bouncy);
      // Drag: the option under the droplet centre; tap: the option under the pointer.
      const cx = moved ? this.x.value + this._optRect(this.value).w / 2 : e.clientX - left;
      let best = null;
      for (const b of this.opts) {
        const l = b.offsetLeft;
        if (cx >= l && cx <= l + b.offsetWidth) best = b;
      }
      if (!best) best = cx < this.opts[0].offsetLeft ? this.opts[0] : this.opts[this.opts.length - 1];
      if (e.type === 'pointercancel' && !moved) return this._place(true);
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
    // Stretch along the motion, thinner across it: the droplet "flows".
    const stretch = clamp(Math.abs(v) / 2400, 0, 0.32);
    const lift = this.lift.value;
    const sx = 1 + stretch + lift * 0.1;
    const sy = 1 - stretch * 0.35 + lift * 0.16;
    this.drop.style.width = `${this.w.value.toFixed(2)}px`;
    this.drop.style.transform = `translateX(${this.x.value.toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
  }

  _place(animate) {
    const r = this._optRect(this.value);
    if (!r.w) return;
    if (animate) {
      this.x.to(r.x, SPRINGS.bouncy);
      this.w.to(r.w, SPRINGS.bouncy);
    } else {
      this.x.set(r.x);
      this.w.set(r.w);
    }
  }

  _sync() {
    this.opts.forEach((b) => {
      const on = b.dataset.v === this.value;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on && !this.disabled ? 0 : -1;
      b.classList.toggle('is-on', on);
    });
  }

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
}

// ---------------------------------------------------------------------------------------------
// Format toggle for job cards (MP4 / MP3). Plain markup with a CSS thumb: a queue may hold
// hundreds of cards, so it has no lens and no springs; the owner delegates clicks on
// [data-act="fmt"] and reads data-v.

export function formatToggleHtml(value) {
  const opt = (v, label) =>
    `<button type="button" role="radio" data-act="fmt" data-v="${v}" aria-checked="${value === v}" tabindex="${value === v ? 0 : -1}">${label}</button>`;
  return `<span class="fmt-toggle" role="radiogroup" aria-label="Format" data-v="${value}"><i class="fmt-thumb" aria-hidden="true"></i>${opt('mp4', 'MP4')}${opt('mp3', 'MP3')}</span>`;
}

// ---------------------------------------------------------------------------------------------
// Switch

export class Switch {
  constructor({ label, checked, onChange }) {
    this.onChange = onChange;
    this.checked = !!checked;
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
    el.addEventListener('pointerdown', (e) => {
      if (e.button === 0) this.press.to(1, SPRINGS.snappy);
    });
    const up = () => this.press.to(0, SPRINGS.bouncy);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointerleave', up);
    el.addEventListener('click', () => this.set(!this.checked, true));
    this._sync();
    this._render();
  }

  _render() {
    const p = this.pos.value;
    const lift = this.press.value;
    const W = 52, H = 32, pad = 2;
    const tw = 28 + lift * 12 + Math.min(Math.abs(this.pos.velocity) * 2, 6);
    const th = 28 + lift * 2;
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

  set(v, user = false) {
    v = !!v;
    if (v === this.checked) return;
    this.checked = v;
    this._sync();
    this.pos.to(v ? 1 : 0, SPRINGS.bouncy);
    if (user) this.onChange && this.onChange(v);
  }
}

// ---------------------------------------------------------------------------------------------
// Stepper

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
    el.querySelectorAll('.step-btn').forEach((b) => {
      gel(b);
      b.addEventListener('click', () => this.set(this.value + Number(b.dataset.d), true));
    });
    this._sync();
  }

  _sync() {
    const [minus, plus] = this.el.querySelectorAll('.step-btn');
    minus.disabled = this.value <= this.min;
    plus.disabled = this.value >= this.max;
  }

  set(v, user = false) {
    v = clamp(v, this.min, this.max);
    if (v === this.value) return;
    const dir = v > this.value ? 1 : -1;
    this.value = v;
    this._sync();
    // Rolling digits: the old number leaves upward (or downward), the new one rolls in.
    const old = this.val.querySelector('.step-num');
    const neu = document.createElement('span');
    neu.className = 'step-num';
    neu.textContent = String(v);
    this.val.appendChild(neu);
    const sp = new Spring(0, SPRINGS.bouncy, (p) => {
      old.style.transform = `translateY(${(-dir * p * 100).toFixed(1)}%)`;
      old.style.opacity = String(clamp(1 - p * 1.4, 0, 1));
      neu.style.transform = `translateY(${(dir * (1 - p) * 100).toFixed(1)}%)`;
      neu.style.opacity = String(clamp(p * 1.4, 0, 1));
    });
    sp.onRest = () => old.remove();
    sp.to(1);
    if (user) this.onChange && this.onChange(v);
  }
}

// ---------------------------------------------------------------------------------------------
// Select (menu button)

export class Select {
  constructor({ label, options, value, onChange }) {
    this.options = options;
    this.value = value;
    this.onChange = onChange;
    const el = (this.el = document.createElement('div'));
    el.className = 'select';
    el.innerHTML = `<button type="button" class="select-btn" aria-haspopup="listbox" aria-label="${escapeHtml(label)}"><span class="select-label"></span>${icon('chevronDown')}</button>
      <div class="select-menu glass-lite" role="listbox" hidden>${options.map((o) => `<button type="button" role="option" class="select-opt" data-v="${escapeHtml(o.value)}">${icon('check', 'select-check')}<span>${escapeHtml(o.label)}</span></button>`).join('')}</div>`;
    this.btn = el.querySelector('.select-btn');
    this.menu = el.querySelector('.select-menu');
    gel(this.btn);
    this.btn.addEventListener('click', () => (this.menu.hidden ? this.openMenu() : this.closeMenu()));
    this.menu.addEventListener('click', (e) => {
      const b = e.target.closest('.select-opt');
      if (!b) return;
      this.set(b.dataset.v, true);
      this.closeMenu();
    });
    this.menu.addEventListener('keydown', (e) => {
      const items = Array.from(this.menu.querySelectorAll('.select-opt'));
      const i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      if (e.key === 'Escape') { e.stopPropagation(); this.closeMenu(); this.btn.focus(); }
    });
    this._outside = (e) => { if (!el.contains(e.target)) this.closeMenu(); };
    this._sync();
  }

  openMenu() {
    this.menu.hidden = false;
    this.menu.animate([{ opacity: 0, transform: 'translateY(-6px) scale(.96)' }, { opacity: 1, transform: 'none' }], { duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)' });
    const cur = this.menu.querySelector('.is-on') || this.menu.querySelector('.select-opt');
    cur && cur.focus();
    addEventListener('pointerdown', this._outside, true);
  }

  closeMenu() {
    this.menu.hidden = true;
    removeEventListener('pointerdown', this._outside, true);
  }

  _sync() {
    const o = this.options.find((x) => x.value === this.value) || this.options[0];
    this.el.querySelector('.select-label').textContent = o.label;
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
