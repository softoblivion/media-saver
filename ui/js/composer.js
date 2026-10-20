// Input capsule: paste area, live clean preview (chips morph original -> clean), counters, tools,
// Ctrl+V anywhere, drag and drop of text / URLs / .txt files, and the primary "Download" button that
// morphs into a circle while sending and into a progress pill while the batch downloads.

import { call, on } from './api.js';
import { Lens, gel, shake } from './glass.js';
import { Spring, SPRINGS, clamp, springAnimate, springEasing, whenDone } from './motion.js';
import { icon } from './icons.js';
import { escapeHtml, nLinks, plural, speed as fmtSpeed, LINK_ERRORS } from './format.js';
import { toast } from './toast.js';
import { topOverlay } from './overlay.js';

const MAX_CHIPS = 80;
const EMPTY = { items: [], clean_text: '', valid: 0, invalid: 0, duplicates: 0 };

let root, input, preview, counters, btn, lens;
let result = EMPTY;
let seq = 0;
let debounce = 0;
const chips = new Map();          // key -> element
const batch = new Map();          // job id -> job (jobs added from this composer since the batch began)
let btnState = 'empty';
let doneTimer = 0;
let widthSpring = null;

export function initComposer() {
  root = document.getElementById('composer');
  input = document.getElementById('links-input');
  preview = document.getElementById('preview');
  counters = document.getElementById('counters');
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

  initButton();
  initPasteAnywhere();
  initDrop();
  on('job', onJob);
  on('job_removed', ({ id }) => {
    batch.delete(id);
    renderButton();
  });
  addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey && e.target !== input && !topOverlay()) {
      e.preventDefault();
      download();
    }
  });
  autoGrow();
  renderAll();
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
      res = await call('clean_links', text);
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

// ---------------------------------------------------------------------------------------------
// Counters

function renderCounters() {
  const r = result;
  const has = !!input.value.trim();
  const set = (name, n, text) => {
    const el = counters.querySelector(`[data-c="${name}"]`);
    const show = name === 'valid' ? has : n > 0;
    el.hidden = !show;
    const t = el.querySelector('b');
    if (t.textContent !== String(n)) {
      t.textContent = String(n);
      el.querySelector('span').textContent = text;
      if (show) springAnimate(el, [{ transform: 'scale(1.18)' }, { transform: 'scale(1)' }], SPRINGS.jelly, { fill: 'none' });
    }
  };
  set('valid', r.valid, 'ready');
  set('invalid', r.invalid, 'invalid');
  set('dup', r.duplicates, plural(r.duplicates, ['duplicate', 'duplicates']));
}

// ---------------------------------------------------------------------------------------------
// Preview chips

function shortClean(it) {
  return `${it.kind === 'p' ? 'p' : 'reel'}/${it.shortcode}`;
}

function renderPreview() {
  const items = result.items;
  const seenOrig = new Map();
  const seenClean = new Set();
  const want = [];
  for (const it of items.slice(0, MAX_CHIPS)) {
    const n = (seenOrig.get(it.original) || 0) + 1;
    seenOrig.set(it.original, n);
    const dup = !!it.clean && seenClean.has(it.clean);
    if (it.clean) seenClean.add(it.clean);
    want.push({ key: `${n}|${it.original}`, it, dup });
  }
  const keep = new Set(want.map((w) => w.key));
  for (const [k, el] of chips) {
    if (!keep.has(k)) {
      chips.delete(k);
      leave(el);
    }
  }
  let prev = null;
  let fresh = 0;
  for (const w of want) {
    let el = chips.get(w.key);
    if (!el) {
      el = makeChip(w);
      chips.set(w.key, el);
      enter(el, fresh++);
    } else {
      el.classList.toggle('is-dup', w.dup);
      updateNote(el, w);
    }
    const next = prev ? prev.nextElementSibling : preview.querySelector('.pv-list').firstElementChild;
    if (next !== el) preview.querySelector('.pv-list').insertBefore(el, next);
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
  const ok = !!it.clean;
  el.className = `pv-item ${ok ? 'is-valid' : 'is-invalid'} ${dup ? 'is-dup' : ''}`;
  el.title = ok ? `${it.original}\n→ ${it.clean}` : it.original;
  const orig = it.original.replace(/^https?:\/\/(www\.)?/i, '');
  el.innerHTML = `
    <div class="pv-chip">
      <span class="pv-ic">${icon(ok ? (it.kind === 'p' ? 'post' : 'reel') : 'link')}</span>
      <span class="pv-text">
        <span class="pv-orig">${escapeHtml(orig)}</span>
        ${ok ? `<span class="pv-clean">${escapeHtml(shortClean(it))}</span>` : ''}
      </span>
    </div>
    <span class="pv-note"></span>`;
  updateNote(el, { it, dup });
  return el;
}

function updateNote(el, { it, dup }) {
  const note = el.querySelector('.pv-note');
  let text;
  if (!it.clean) text = LINK_ERRORS[it.error] || 'invalid';
  else if (dup) text = 'duplicate';
  else {
    const cut = it.original.length - it.clean.length;
    text = cut > 0 ? `−${cut} chars` : 'clean';
  }
  if (note.textContent !== text) note.textContent = text;
}

function enter(el, i) {
  const delay = Math.min(i, 10) * 34;
  const valid = el.classList.contains('is-valid');
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
      if (!valid) setTimeout(() => shake(el.querySelector('.pv-chip')), delay + 140);
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
  if (!input.value.trim()) {
    focusInput();
    shake(root);
    return toast('info', 'Paste Reels links. Ctrl+V works anywhere in the window');
  }
  if (!result.valid) {
    // Clean result may be stale: refresh once before giving up.
    clearTimeout(debounce);
    await refresh();
    if (!result.valid) {
      shake(root);
      return toast('warning', 'No Instagram Reels or post links found here');
    }
  }
  sending = true;
  setButton('sending');
  try {
    const text = input.value;
    const res = await call('enqueue', text);
    const added = res.added || [];
    const dups = (res.skipped || []).filter((s) => s.reason === 'duplicate').length;
    if (!batchActive()) batch.clear();
    for (const j of added) batch.set(j.id, j);
    flyOut();
    input.value = '';
    autoGrow();
    result = EMPTY;
    setTimeout(renderAll, 260);
    if (added.length) {
      const more = dups ? ` · ${dups} already in the queue` : '';
      toast('success', `Queued ${added.length} ${plural(added.length, ['video', 'videos'])}${more}`);
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

function batchActive() {
  for (const j of batch.values()) if (ACTIVE.has(j.status)) return true;
  return false;
}

function onJob(job) {
  if (!batch.has(job.id)) return;
  const was = batchActive();
  batch.set(job.id, job);
  const now = batchActive();
  if (was && !now) {
    // The whole batch finished.
    const ok = Array.from(batch.values()).filter((j) => j.status === 'done').length;
    btn.querySelector('.pm-done-text').textContent = ok === batch.size ? 'Done' : `Done ${ok} of ${batch.size}`;
    setButton('done');
    clearTimeout(doneTimer);
    doneTimer = setTimeout(() => {
      batch.clear();
      if (btnState === 'done') setButton('empty');
      renderButton();
    }, 2200);
    return;
  }
  renderButton();
}

function initButton() {
  btn.innerHTML = `
    <span class="pm-fill" aria-hidden="true"></span>
    <span class="pm-layer pm-label">${icon('down')}<span class="pm-text">Download</span><kbd>Ctrl+Enter</kbd></span>
    <span class="pm-layer pm-spin" aria-hidden="true"><span class="spinner"></span></span>
    <span class="pm-layer pm-progress"><span class="pm-ptext"></span><span class="pm-pmeta"></span></span>
    <span class="pm-layer pm-done">${icon('check')}<span class="pm-done-text">Done</span></span>`;
  btn.classList.add('hl');
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

function renderButton() {
  if (sending) return;
  const valid = result.valid;
  if (valid > 0) {
    btn.querySelector('.pm-text').textContent = valid === 1 ? 'Download video' : `Download ${valid} videos`;
    btn.disabled = false;
    if (btnState !== 'ready') setButton('ready');
    return;
  }
  if (btnState === 'done') return;
  if (batchActive()) {
    const jobs = Array.from(batch.values());
    const doneN = jobs.filter((j) => !ACTIVE.has(j.status)).length;
    const p = jobs.reduce((s, j) => s + (ACTIVE.has(j.status) ? j.progress || 0 : 1), 0) / jobs.length;
    const bps = jobs.reduce((s, j) => s + (j.status === 'downloading' && j.speed_bps ? j.speed_bps : 0), 0);
    btn.querySelector('.pm-ptext').textContent = jobs.length === 1 ? 'Downloading video' : `Downloading ${Math.min(doneN + 1, jobs.length)} of ${jobs.length}`;
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
