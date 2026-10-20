// Clipboard prompt: on start and whenever the window regains focus (when settings.clipboard_watch is
// on), read the clipboard, clean it, and offer links that are neither queued nor offered before:
// a glass pill "N links on the clipboard - Add".

import { call, on } from './api.js';
import { Lens, gel } from './glass.js';
import { SPRINGS, springAnimate, whenDone } from './motion.js';
import { icon } from './icons.js';
import { plural } from './format.js';
import { toast } from './toast.js';

let pill, lens, settings, jobsRef;
const offered = new Set();   // shortcodes already offered (accepted or dismissed)
let current = null;          // { codes: string[], text: string }
let checking = false;

/**
 * @param {object} initialSettings
 * @param {() => Iterable<{shortcode:string,status:string}>} getJobs current jobs
 */
export function initClipboard(initialSettings, getJobs) {
  settings = initialSettings;
  jobsRef = getJobs;
  pill = document.getElementById('clip-pill');
  pill.innerHTML = `
    <span class="clip-ic">${icon('clipboard')}</span>
    <span class="clip-text"></span>
    <button type="button" class="clip-add btn btn-tinted btn-s">${icon('plus')}<span>Add</span></button>
    <button type="button" class="clip-x icon-btn" aria-label="Dismiss">${icon('x')}</button>`;
  lens = new Lens(pill, 'capsule', { strength: 0 });
  pill.hidden = true;
  gel(pill.querySelector('.clip-add'));
  gel(pill.querySelector('.clip-x'));
  pill.querySelector('.clip-add').addEventListener('click', accept);
  pill.querySelector('.clip-x').addEventListener('click', () => hide(true));

  on('settings', (s) => {
    settings = s;
    if (!s.clipboard_watch) hide(false);
  });
  on('window', (w) => {
    if (w && w.focused) check();
  });
  check();
}

export async function check() {
  if (!settings || !settings.clipboard_watch || checking) return;
  checking = true;
  try {
    const text = await call('read_clipboard');
    if (!text || !text.trim()) return;
    const res = await call('clean_links', text);
    if (!res.valid) return;
    const queued = new Set();
    for (const j of jobsRef()) if (j.status !== 'canceled') queued.add(j.shortcode);
    const typed = (document.getElementById('links-input').value || '');
    const fresh = [];
    const seen = new Set();
    for (const it of res.items) {
      if (!it.clean || seen.has(it.shortcode)) continue;
      seen.add(it.shortcode);
      if (queued.has(it.shortcode) || offered.has(it.shortcode) || typed.includes(it.shortcode)) continue;
      fresh.push(it);
    }
    if (!fresh.length) return;
    current = { codes: fresh.map((i) => i.shortcode), text: fresh.map((i) => i.clean).join('\n') };
    show(fresh.length);
  } catch (err) {
    // Clipboard errors are not worth a toast; the prompt is optional.
    console.warn('[ui] clipboard check failed', err);
  } finally {
    checking = false;
  }
}

function show(n) {
  pill.querySelector('.clip-text').innerHTML = `<b>${n}</b> ${plural(n, ['link', 'links'])} on the clipboard`;
  if (!pill.hidden && pill.dataset.leaving !== '1') {
    pill.__gel = pill.__gel || gel(pill, { amount: 0.5, stretch: false });
    pill.__gel.pulse();
    return;
  }
  delete pill.dataset.leaving;
  pill.hidden = false;
  lens.update();
  lens.animateStrength(1);
  springAnimate(pill, [
    { transform: 'translate(-50%, 70px) scale(0.6, 0.5)', opacity: 0 },
    { transform: 'translate(-50%, 0) scale(1)', opacity: 1 },
  ], SPRINGS.bouncy);
}

function hide(markOffered) {
  if (pill.hidden || pill.dataset.leaving === '1') return;
  if (markOffered && current) current.codes.forEach((c) => offered.add(c));
  lens.animateStrength(0, SPRINGS.sheetClose);
  const a = springAnimate(pill, [
    { transform: 'translate(-50%, 0) scale(1)', opacity: 1 },
    { transform: 'translate(-50%, 40px) scale(0.8)', opacity: 0 },
  ], SPRINGS.sheetClose);
  pill.dataset.leaving = '1';
  whenDone(a, () => {
    if (pill.dataset.leaving === '1') pill.hidden = true;
  });
}

async function accept() {
  if (!current) return;
  const c = current;
  c.codes.forEach((x) => offered.add(x));
  hide(false);
  try {
    const res = await call('enqueue', c.text);
    const n = (res.added || []).length;
    if (n) toast('success', `Added from clipboard: ${n} ${plural(n, ['video', 'videos'])}`);
    else toast('info', 'These links are already in the queue');
  } catch (err) {
    toast('error', err.message);
  }
}
