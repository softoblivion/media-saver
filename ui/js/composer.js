// Input capsule: paste area, live clean preview (chips morph original -> clean), counters, tools,
// Ctrl+V anywhere, drag and drop of text / URLs / .txt files, the format toggle (MP4 / MP3) and the
// primary "Download" button that morphs into a circle while sending and into a progress pill while
// the batch downloads. Everything follows the current mode (js/mode.js): links are cleaned for that
// platform, links of another platform get an inline "Switch to <their platform>" action and one
// counter pill per platform present ("2 TikTok", "1 X").

import { call, on } from './api.js';
import { Lens, gel, shake } from './glass.js';
import { Spring, SPRINGS, clamp, springAnimate, springEasing, whenDone } from './motion.js';
import { icon, kindGlyph } from './icons.js';
import { escapeHtml, nLinks, nItems, plural, itemNoun, speed as fmtSpeed, linkError, shortLink } from './format.js';
import { toast } from './toast.js';
import { topOverlay } from './overlay.js';
import { Segmented } from './controls.js';
import { getMode, getFormat, setMode, setFormat, onPref, PLATFORMS, PLATFORM_NAME } from './mode.js';

const MAX_CHIPS = 80;
const EMPTY = { items: [], clean_text: '', valid: 0, invalid: 0, duplicates: 0 };

// Mode-aware copy (docs/UI_DESIGN.md section 9).
const COPY = {
  instagram: {
    label: 'Instagram links',
    placeholder: 'Paste Instagram Reels or post links, one or many. Other text is ignored.',
    hint: 'Clean links appear here as <code>instagram.com/reel/code</code>',
    empty: 'Paste Instagram links or press Ctrl+V',
    none: 'No Instagram Reels or post links found here',
  },
  youtube: {
    label: 'YouTube links',
    placeholder: 'Paste YouTube video or Shorts links, one or many. Other text is ignored.',
    hint: 'Clean links appear here as <code>youtube.com/watch?v=id</code>',
    empty: 'Paste YouTube links or press Ctrl+V',
    none: 'No YouTube video or Shorts links found here',
  },
  tiktok: {
    label: 'TikTok links',
    placeholder: 'Paste TikTok video links, full or short, one or many. Other text is ignored.',
    hint: 'Clean links appear here as <code>tiktok.com/@user/video/id</code>',
    empty: 'Paste TikTok links or press Ctrl+V',
    none: 'No TikTok video links found here',
  },
  twitter: {
    label: 'X links',
    placeholder: 'Paste links to X posts with a video, one or many. Other text is ignored.',
    hint: 'Clean links appear here as <code>x.com/user/status/id</code>',
    empty: 'Paste X links or press Ctrl+V',
    none: 'No X post links found here',
  },
};

let root, input, preview, counters, btn, lens, formatSeg;
let result = EMPTY;
let seq = 0;
let debounce = 0;
const chips = new Map();          // key -> element
// Jobs added from this composer since the batch began, one batch per platform.
const batches = Object.fromEntries(PLATFORMS.map((p) => [p, new Map()]));
let btnState = 'empty';
let doneMode = null;
let doneTimer = 0;
let widthSpring = null;

const batch = () => batches[getMode()];

export function initComposer() {
  root = document.getElementById('composer');
  input = document.getElementById('links-input');
  preview = document.getElementById('preview');
  counters = document.getElementById('counters');
  // One "other platform" pill per platform ("2 TikTok", "1 X"), cloned from the markup's template;
  // the current mode's own pill never shows.
  const otherTpl = counters.querySelector('[data-c="other"]');
  for (const p of PLATFORMS) {
    const el = otherTpl.cloneNode(true);
    el.dataset.c = `other-${p}`;
    el.querySelector('span').textContent = PLATFORM_NAME[p];
    otherTpl.before(el);
  }
  otherTpl.remove();
  btn = document.getElementById('btn-download');
  lens = new Lens(root, 'panel');

  input.addEventListener('input', () => {
    autoGrow();
    clearTimeout(debounce);
    debounce = setTimeout(refresh, 110);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault();
      download();
    }
  });

  root.querySelectorAll('[data-tool]').forEach((b) => {
    gel(b);
    b.addEventListener('click', () => TOOLS[b.dataset.tool]());
  });

  // "Switch to YouTube" on a chip of the other platform.
  preview.addEventListener('click', (e) => {
    const b = e.target.closest('[data-switch]');
    if (b) setMode(b.dataset.switch);
  });

  formatSeg = new Segmented({
    label: 'Format',
    cls: 'seg-l',
    options: [
      { value: 'mp4', label: 'MP4', title: 'Video with sound (MP4)' },
      { value: 'mp3', label: 'MP3', title: 'Audio only (MP3)' },
    ],
    value: getFormat(),
    onChange: (v) => setFormat(v),
  });
  document.getElementById('format-slot').replaceWith(formatSeg.el);
  formatSeg.el.id = 'format-seg';

  initButton();
  initPasteAnywhere();
  initDrop();
  on('job', onJob);
  on('job_removed', ({ id }) => {
    for (const b of Object.values(batches)) b.delete(id);
    renderButton();
  });
  onPref('mode', onModeChange);
  onPref('format', (f) => {
    formatSeg.set(f);
    renderButton();
  });
  addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey && e.target !== input && !topOverlay()) {
      e.preventDefault();
      download();
    }
  });
  applyCopy();
  autoGrow();
  renderAll();
}

function applyCopy() {
  const c = COPY[getMode()];
  input.placeholder = c.placeholder;
  input.setAttribute('aria-label', c.label);
  preview.querySelector('.pv-hint-text').innerHTML = c.hint;
}

function onModeChange() {
  applyCopy();
  // The same text is cleaned again for the new platform: chips are rebuilt, not morphed.
  for (const el of chips.values()) el.remove();
  chips.clear();
  if (btnState === 'done' && doneMode !== getMode()) {
    clearTimeout(doneTimer);
    setButton('empty');
  }
  clearTimeout(debounce);
  if (input.value.trim()) refresh();
  else {
    result = EMPTY;
    renderAll();
  }
}

/** Insert text at the caret (when focused) or append it on a new line, then refresh at once. */
export function insertText(text) {
  if (!text) return;
  const v = input.value;
  if (document.activeElement === input) {
    const s = input.selectionStart, e = input.selectionEnd;
    const pre = v.slice(0, s);
    const sep = pre && !/\s$/.test(pre) ? '\n' : '';
    input.setRangeText(sep + text, s, e, 'end');
  } else {
    input.value = v ? `${v.replace(/\s+$/, '')}\n${text}` : text;
    input.focus({ preventScroll: true });
    input.setSelectionRange(input.value.length, input.value.length);
  }
  autoGrow();
  input.scrollTop = input.scrollHeight;
  clearTimeout(debounce);
  refresh();
}

export function focusInput() {
  input.focus();
}

function autoGrow() {
  input.style.height = 'auto';
  const max = Math.max(96, Math.min(220, root.clientHeight * 0.34));
  input.style.height = `${clamp(input.scrollHeight + 2, 84, max)}px`;
}

async function refresh() {
  const text = input.value;
  const my = ++seq;
  let res = EMPTY;
  if (text.trim()) {
    try {
      res = await call('clean_links', text, getMode());
    } catch (err) {
      toast('error', err.message);
      return;
    }
  }
  if (my !== seq) return;
  result = res;
  renderAll();
}

function renderAll() {
  renderCounters();
  renderPreview();
  renderTools();
  renderButton();
}

/**
 * Links of other platforms in the current result (valid links, counted as invalid): distinct clean
 * links per platform, platforms with the most links first. [[platform, n], ...]
 */
function otherCounts() {
  const seen = new Map();
  for (const it of result.items) {
    if (it.error !== 'other_platform' || !it.clean || !it.platform) continue;
    if (!seen.has(it.platform)) seen.set(it.platform, new Set());
    seen.get(it.platform).add(it.clean);
  }
  return Array.from(seen, ([p, s]) => [p, s.size]).sort((a, b) => b[1] - a[1] || PLATFORMS.indexOf(a[0]) - PLATFORMS.indexOf(b[0]));
}

const otherTotal = (counts) => counts.reduce((s, [, n]) => s + n, 0);

// ---------------------------------------------------------------------------------------------
// Counters

function renderCounters() {
  const r = result;
  const has = !!input.value.trim();
  const others = otherCounts();
  const other = otherTotal(others);
  const set = (name, n, text) => {
    const el = counters.querySelector(`[data-c="${name}"]`);
    const show = name === 'valid' ? has : n > 0;
    el.hidden = !show;
    const t = el.querySelector('b');
    const label = el.querySelector('span');
    if (label.textContent !== text) label.textContent = text;
    if (t.textContent !== String(n)) {
      t.textContent = String(n);
      if (show) springAnimate(el, [{ transform: 'scale(1.18)' }, { transform: 'scale(1)' }], SPRINGS.jelly, { fill: 'none' });
    }
  };
  set('valid', r.valid, 'ready');
  set('invalid', Math.max(0, r.invalid - other), 'invalid');
  set('dup', r.duplicates, plural(r.duplicates, ['duplicate', 'duplicates']));
  const byPlatform = new Map(others);
  for (const p of PLATFORMS) set(`other-${p}`, p === getMode() ? 0 : byPlatform.get(p) || 0, PLATFORM_NAME[p]);
}

// ---------------------------------------------------------------------------------------------
// Preview chips

function chipSig(it) {
  return `${it.clean ? 1 : 0}|${it.error || ''}|${it.platform || ''}`;
}

function renderPreview() {
  const items = result.items;
  const seenOrig = new Map();
  const seenClean = new Set();
  const want = [];
  for (const it of items.slice(0, MAX_CHIPS)) {
    const n = (seenOrig.get(it.original) || 0) + 1;
    seenOrig.set(it.original, n);
    const usable = it.clean && !it.error;
    const dup = !!usable && seenClean.has(it.clean);
    if (usable) seenClean.add(it.clean);
    want.push({ key: `${n}|${it.original}`, it, dup });
  }
  const keep = new Set(want.map((w) => w.key));
  for (const [k, el] of chips) {
    if (!keep.has(k)) {
      chips.delete(k);
      leave(el);
    }
  }
  const list = preview.querySelector('.pv-list');
  let prev = null;
  let fresh = 0;
  for (const w of want) {
    let el = chips.get(w.key);
    if (el && el.__sig !== chipSig(w.it)) {
      el.remove();
      el = null;
    }
    if (!el) {
      el = makeChip(w);
      chips.set(w.key, el);
      enter(el, fresh++);
    } else {
      el.classList.toggle('is-dup', w.dup);
      updateNote(el, w);
    }
    const next = prev ? prev.nextElementSibling : list.firstElementChild;
    if (next !== el) list.insertBefore(el, next);
    prev = el;
  }
  const more = preview.querySelector('.pv-more');
  const rest = items.length - MAX_CHIPS;
  more.hidden = rest <= 0;
  if (rest > 0) more.textContent = `and ${nLinks(rest)} more`;
  preview.classList.toggle('is-empty', items.length === 0);
}

function makeChip({ it, dup }) {
  const el = document.createElement('div');
  const other = it.error === 'other_platform' && !!it.clean;
  const ok = !!it.clean && !it.error;
  el.__sig = chipSig(it);
  el.className = `pv-item ${ok ? 'is-valid' : other ? 'is-other' : 'is-invalid'} ${dup ? 'is-dup' : ''}`;
  el.title = it.clean ? `${it.original}\n→ ${it.clean}` : it.original;
  const orig = it.original.replace(/^https?:\/\/(www\.)?/i, '');
  const ic = it.clean ? kindGlyph(it.platform, it.kind) : 'link';
  if (other) {
    // The action names the chip's own platform (items with `other_platform` carry it).
    const name = PLATFORM_NAME[it.platform] || 'its mode';
    el.innerHTML = `
      <div class="pv-chip">
        <span class="pv-ic">${icon(ic)}</span>
        <span class="pv-text"><span class="pv-orig">${escapeHtml(shortLink(it))}</span></span>
      </div>
      <button type="button" class="btn btn-s pv-switch" data-switch="${escapeHtml(it.platform)}">${icon('swap')}<span>Switch to ${escapeHtml(name)}</span></button>`;
    gel(el.querySelector('.pv-switch'));
    return el;
  }
  el.innerHTML = `
    <div class="pv-chip">
      <span class="pv-ic">${icon(ic)}</span>
      <span class="pv-text">
        <span class="pv-orig">${escapeHtml(orig)}</span>
        ${ok ? `<span class="pv-clean">${escapeHtml(shortLink(it))}</span>` : ''}
      </span>
    </div>
    <span class="pv-note"></span>`;
  updateNote(el, { it, dup });
  return el;
}

function updateNote(el, { it, dup }) {
  const note = el.querySelector('.pv-note');
  if (!note) return;
  let text;
  if (!it.clean || it.error) text = linkError(it);
  else if (dup) text = 'duplicate';
  else {
    const cut = it.original.length - it.clean.length;
    text = cut > 0 ? `−${cut} ${plural(cut, ['char', 'chars'])}` : 'clean';
  }
  if (note.textContent !== text) note.textContent = text;
}

function enter(el, i) {
  const delay = Math.min(i, 10) * 34;
  const valid = el.classList.contains('is-valid');
  const invalid = el.classList.contains('is-invalid');
  const text = el.querySelector('.pv-text');
  const orig = el.querySelector('.pv-orig');
  const clean = el.querySelector('.pv-clean');
  el.style.opacity = '0';
  requestAnimationFrame(() => {
    springAnimate(el, [
      { opacity: 0, transform: 'translateY(8px) scale(0.92)' },
      { opacity: 1, transform: 'none' },
    ], SPRINGS.bouncy, { delay, fill: 'backwards' });
    el.style.opacity = '';
    if (!valid || !clean) {
      if (invalid) setTimeout(() => shake(el.querySelector('.pv-chip')), delay + 140);
      return;
    }
    // Morph: the chip shows the original link first, then flows into the clean short form.
    const wo = orig.offsetWidth;
    const wc = clean.offsetWidth;
    text.style.width = `${wo}px`;
    el.classList.add('is-raw');
    const sp = new Spring(0, SPRINGS.bouncy, (p) => {
      text.style.width = `${(wo + (wc - wo) * p).toFixed(1)}px`;
      const q = clamp(p, 0, 1);
      orig.style.opacity = String(clamp(1 - q * 1.8, 0, 1));
      orig.style.transform = `translateY(${(-q * 7).toFixed(2)}px)`;
      orig.style.filter = `blur(${(q * 3).toFixed(2)}px)`;
      clean.style.opacity = String(clamp((q - 0.25) * 1.6, 0, 1));
      clean.style.transform = `translateY(${((1 - q) * 7).toFixed(2)}px)`;
    });
    sp.onRest = () => {
      text.style.width = '';
      orig.style.filter = '';
      el.classList.remove('is-raw');
      el.classList.add('is-clean');
    };
    setTimeout(() => sp.to(1), delay + 360);
  });
}

function leave(el) {
  const a = springAnimate(el, [
    { opacity: 1, transform: 'none' },
    { opacity: 0, transform: 'scale(0.9)' },
  ], SPRINGS.sheetClose);
  whenDone(a, () => el.remove());
}

// ---------------------------------------------------------------------------------------------
// Tools

const TOOLS = {
  async paste() {
    try {
      const text = await call('read_clipboard');
      if (!text || !text.trim()) return toast('info', 'The clipboard has no text');
      insertText(text.trim());
    } catch (err) {
      toast('error', err.message);
    }
  },
  async clean() {
    if (!result.valid) return toast('info', 'No links to clean');
    const before = input.value;
    input.value = result.clean_text;
    autoGrow();
    input.animate([{ filter: 'blur(4px)', opacity: 0.2 }, { filter: 'none', opacity: 1 }], springEasing(SPRINGS.smooth));
    refresh();
    const removed = result.invalid + result.duplicates;
    if (before !== input.value) toast('success', removed ? `Kept ${nLinks(result.valid)}, removed the rest` : `Links cleaned: ${nLinks(result.valid)}`);
  },
  async copy() {
    if (!result.valid) return toast('info', 'Nothing to copy: no valid links');
    try {
      await call('copy_text', result.clean_text);
      toast('success', `Copied ${nLinks(result.valid)}`);
    } catch (err) {
      toast('error', err.message);
    }
  },
};

function renderTools() {
  const has = !!input.value.trim();
  root.querySelector('[data-tool="clean"]').disabled = !result.valid || !has;
  root.querySelector('[data-tool="copy"]').disabled = !result.valid;
}

// ---------------------------------------------------------------------------------------------
// Download

let sending = false;

export async function download() {
  if (sending) return;
  const mode = getMode();
  if (!input.value.trim()) {
    focusInput();
    shake(root);
    return toast('info', COPY[mode].empty);
  }
  if (!result.valid) {
    // Clean result may be stale: refresh once before giving up.
    clearTimeout(debounce);
    await refresh();
    if (!result.valid) {
      shake(root);
      const others = otherCounts();
      const n = otherTotal(others);
      if (n) {
        // One platform: "These are TikTok links"; several: "These are TikTok and X links". The
        // action switches to the platform with the most links.
        const o = others[0][0];
        const names = others.map(([p]) => PLATFORM_NAME[p]);
        const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0];
        const lead = n === 1 ? (/^[AEIOUX]/.test(list) ? 'This is an' : 'This is a') : 'These are';
        return toast('warning', `${lead} ${list} ${plural(n, ['link', 'links'])}`,
          { action: { label: `Switch to ${PLATFORM_NAME[o]}`, run: () => setMode(o) }, ms: 6000 });
      }
      return toast('warning', COPY[mode].none);
    }
  }
  sending = true;
  setButton('sending');
  const format = getFormat();
  try {
    const text = input.value;
    const res = await call('enqueue', text, mode, format);
    const added = res.added || [];
    const dups = (res.skipped || []).filter((s) => s.reason === 'duplicate').length;
    const b = batches[mode];
    if (!batchActive(b)) b.clear();
    for (const j of added) b.set(j.id, j);
    flyOut();
    input.value = '';
    autoGrow();
    result = EMPTY;
    setTimeout(renderAll, 260);
    if (added.length) {
      const more = dups ? ` · ${dups} already in the queue` : '';
      toast('success', `Queued ${nItems(added.length, mode, format)}${more}`);
    } else if (dups) {
      toast('info', dups === 1 ? 'This link is already in the queue' : 'These links are already in the queue');
    }
  } catch (err) {
    toast('error', err.message);
  } finally {
    sending = false;
    renderButton();
  }
}

function flyOut() {
  const items = Array.from(preview.querySelectorAll('.pv-item'));
  chips.clear();
  items.forEach((el, i) => {
    const a = springAnimate(el, [
      { opacity: 1, transform: 'none' },
      { opacity: 0, transform: 'translateX(90px) scale(0.7)' },
    ], SPRINGS.sheetClose, { delay: Math.min(i, 12) * 22 });
    whenDone(a, () => el.remove());
  });
}

// ---------------------------------------------------------------------------------------------
// Primary button morph

const ACTIVE = new Set(['queued', 'probing', 'downloading', 'processing']);

function batchActive(b = batch()) {
  for (const j of b.values()) if (ACTIVE.has(j.status)) return true;
  return false;
}

function onJob(job) {
  const b = batches[job.platform] || Object.values(batches).find((m) => m.has(job.id));
  if (!b || !b.has(job.id)) return;
  const was = batchActive(b);
  b.set(job.id, job);
  const now = batchActive(b);
  if (b !== batch()) return;
  if (was && !now) {
    // The whole batch of this mode finished.
    const ok = Array.from(b.values()).filter((j) => j.status === 'done').length;
    btn.querySelector('.pm-done-text').textContent = ok === b.size ? 'Done' : `Done ${ok} of ${b.size}`;
    doneMode = getMode();
    setButton('done');
    clearTimeout(doneTimer);
    doneTimer = setTimeout(() => {
      b.clear();
      if (btnState === 'done') setButton('empty');
      renderButton();
    }, 2200);
    return;
  }
  renderButton();
}

function initButton() {
  btn.innerHTML = `
    <span class="pm-fill" aria-hidden="true"><span class="pm-liquid"></span></span>
    <span class="pm-layer pm-label">${icon('down')}<span class="pm-text">Download</span><kbd>Ctrl+Enter</kbd></span>
    <span class="pm-layer pm-spin" aria-hidden="true"><span class="spinner"></span></span>
    <span class="pm-layer pm-progress"><span class="pm-ptext"></span><span class="pm-pmeta"></span></span>
    <span class="pm-layer pm-done">${icon('check')}<span class="pm-done-text">Done</span></span>`;
  gel(btn, { amount: 0.8 });
  btn.addEventListener('click', () => {
    if (btnState === 'ready' || btnState === 'empty') download();
  });
  widthSpring = new Spring(0, SPRINGS.bouncy, (w) => {
    btn.style.width = `${w.toFixed(1)}px`;
  });
  widthSpring.onRest = () => {
    if (btnState !== 'sending') btn.style.width = '';
  };
}

function setButton(state) {
  const prev = btnState;
  btnState = state;
  btn.dataset.state = state;
  if (state !== 'empty') btn.removeAttribute('aria-disabled');
  btn.setAttribute('aria-busy', String(state === 'sending' || state === 'progress'));
  if (state === 'progress' && prev !== 'progress') {
    // The level of a new batch appears where it is; it never drains from the previous batch's value.
    btn.classList.add('pm-instant');
    void btn.offsetWidth;
    btn.classList.remove('pm-instant');
  }
  const full = btn.parentElement.clientWidth;
  const narrow = btn.offsetHeight || 52;
  if (state === 'sending' && prev !== 'sending') {
    widthSpring.set(btn.offsetWidth || full);
    widthSpring.to(narrow, SPRINGS.snappy);
  } else if (prev === 'sending' && state !== 'sending') {
    widthSpring.set(btn.offsetWidth || narrow);
    widthSpring.to(full, SPRINGS.bouncy);
  }
}

/** "Download 3 Reels", "Download video", "Download 2 as MP3". */
function readyLabel(n, mode, format) {
  if (format === 'mp3') return n === 1 ? 'Download as MP3' : `Download ${n} as MP3`;
  return n === 1 ? `Download ${itemNoun(1, mode)}` : `Download ${n} ${itemNoun(n, mode)}`;
}

function renderButton() {
  if (sending) return;
  const mode = getMode();
  const valid = result.valid;
  if (valid > 0) {
    btn.querySelector('.pm-text').textContent = readyLabel(valid, mode, getFormat());
    btn.disabled = false;
    if (btnState !== 'ready') setButton('ready');
    return;
  }
  if (btnState === 'done') return;
  if (batchActive()) {
    const jobs = Array.from(batch().values());
    const doneN = jobs.filter((j) => !ACTIVE.has(j.status)).length;
    const p = jobs.reduce((s, j) => s + (ACTIVE.has(j.status) ? j.progress || 0 : 1), 0) / jobs.length;
    const bps = jobs.reduce((s, j) => s + (j.status === 'downloading' && j.speed_bps ? j.speed_bps : 0), 0);
    const one = jobs[0] && jobs[0].format === 'mp3' ? 'Downloading audio' : `Downloading ${itemNoun(1, mode)}`;
    btn.querySelector('.pm-ptext').textContent = jobs.length === 1 ? one : `Downloading ${Math.min(doneN + 1, jobs.length)} of ${jobs.length}`;
    btn.querySelector('.pm-pmeta').textContent = bps ? fmtSpeed(bps) : `${Math.round(p * 100)}%`;
    btn.style.setProperty('--p', p.toFixed(4));
    btn.disabled = false;
    if (btnState !== 'progress') setButton('progress');
    return;
  }
  btn.querySelector('.pm-text').textContent = 'Download';
  btn.disabled = false;
  btn.setAttribute('aria-disabled', 'true');
  if (btnState !== 'empty') setButton('empty');
}

// ---------------------------------------------------------------------------------------------
// Paste anywhere and drag and drop

function isEditable(t) {
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
}

function initPasteAnywhere() {
  document.addEventListener('paste', (e) => {
    if (isEditable(e.target)) return;
    if (topOverlay()) return;
    const text = e.clipboardData && e.clipboardData.getData('text');
    if (!text) return;
    e.preventDefault();
    insertText(text.trim());
    lens.animateStrength(1.6, SPRINGS.jelly).onRest = () => lens.animateStrength(1);
  });
}

function initDrop() {
  let depth = 0;
  const onState = (v) => {
    root.classList.toggle('is-drop', v);
    lens.animateStrength(v ? 1.7 : 1, SPRINGS.bouncy);
  };
  addEventListener('dragenter', (e) => {
    e.preventDefault();
    if (depth++ === 0) onState(true);
  });
  addEventListener('dragover', (e) => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  });
  addEventListener('dragleave', () => {
    if (--depth <= 0) {
      depth = 0;
      onState(false);
    }
  });
  addEventListener('drop', async (e) => {
    e.preventDefault();
    depth = 0;
    onState(false);
    const dt = e.dataTransfer;
    if (!dt) return;
    const files = Array.from(dt.files || []);
    if (files.length) {
      const texts = [];
      for (const f of files) {
        if (f.size > 4 * 1024 * 1024) {
          toast('warning', `${f.name} is too large`);
          continue;
        }
        if (!/\.(txt|csv|md|url|html?)$/i.test(f.name) && f.type && !f.type.startsWith('text/')) {
          toast('warning', `${f.name}: a text file with links is required`);
          continue;
        }
        texts.push(await readFile(f));
      }
      if (texts.length) insertText(texts.join('\n').trim());
      return;
    }
    const text = dt.getData('text/uri-list') || dt.getData('text/plain') || dt.getData('text');
    if (text) insertText(text.split(/\r?\n/).filter((l) => !l.startsWith('#')).join('\n').trim());
  });
}

function readFile(f) {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ''));
    r.onerror = () => resolve('');
    r.readAsText(f);
  });
}
