// Clipboard prompt: on start and whenever the window regains focus (when settings.clipboard_watch is
// on), read the clipboard, clean it for the current mode, and offer links that are neither queued
// nor offered before: a glass pill "N links on the clipboard - Add". When the clipboard holds only
// links of other platforms, the pill offers the links of one of them ("Add to TikTok" switches the
// mode and queues them there).

import { call, on } from './api.js';
import { Lens, gel } from '../vendor/liquid_ui/js/glass.js';
import { SPRINGS, springAnimate, whenDone } from '../vendor/liquid_ui/js/motion.js';
import { icon } from './icons.js';
import { plural, nItems } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';
import { getMode, getFormat, setMode, onPref, PLATFORMS, PLATFORM_NAME } from './mode.js';

let pill, lens, settings, jobsRef;
const offered = new Set();   // "platform:id" already offered (accepted or dismissed)
let current = null;          // { keys: string[], text: string, platform: string }
let checking = false;

const keyOf = (platform, id) => `${platform || 'instagram'}:${id}`;

/**
 * @param {object} initialSettings
 * @param {() => Iterable<{shortcode:string,status:string,platform?:string}>} getJobs current jobs
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
  // A pill for the other platform becomes a normal one after a mode switch (and vice versa).
  onPref('mode', () => {
    if (!pill.hidden && pill.dataset.leaving !== '1') {
      hide(false);
      setTimeout(check, 260);
    }
  });
  check();
}

export async function check() {
  if (!settings || !settings.clipboard_watch || checking) return;
  checking = true;
  try {
    const text = await call('read_clipboard');
    if (!text || !text.trim()) return;
    const mode = getMode();
    const res = await call('clean_links', text, mode);
    const queued = new Set();
    for (const j of jobsRef()) if (j.status !== 'canceled') queued.add(keyOf(j.platform, j.shortcode));
    const typed = (document.getElementById('links-input').value || '');
    const pickFresh = (items) => {
      const out = [];
      const seen = new Set();
      for (const it of items) {
        const k = keyOf(it.platform, it.shortcode);
        if (seen.has(k)) continue;
        seen.add(k);
        if (queued.has(k) || offered.has(k) || typed.includes(it.shortcode)) continue;
        out.push(it);
      }
      return out;
    };
    let platform = mode;
    let fresh = pickFresh(res.items.filter((it) => it.clean && !it.error));
    if (!fresh.length) {
      // Nothing for this mode: offer the links of another platform. With links of several other
      // platforms, the one with the most fresh links wins (ties: the first in the mode order), and
      // only its links are offered, since one enqueue covers one platform.
      const others = pickFresh(res.items.filter((it) => it.error === 'other_platform' && it.clean && it.platform));
      const count = new Map();
      for (const it of others) count.set(it.platform, (count.get(it.platform) || 0) + 1);
      const best = Array.from(count).sort((a, b) => b[1] - a[1] || PLATFORMS.indexOf(a[0]) - PLATFORMS.indexOf(b[0]))[0];
      if (best) {
        platform = best[0];
        fresh = others.filter((it) => it.platform === platform);
      }
    }
    if (!fresh.length) return;
    current = {
      platform,
      keys: fresh.map((i) => keyOf(i.platform, i.shortcode)),
      text: fresh.map((i) => i.clean).join('\n'),
    };
    show(fresh.length, platform !== mode ? platform : null);
  } catch (err) {
    // Clipboard errors are not worth a toast; the prompt is optional.
    console.warn('[ui] clipboard check failed', err);
  } finally {
    checking = false;
  }
}

function show(n, foreign) {
  const name = foreign ? `${PLATFORM_NAME[foreign]} ` : '';
  pill.querySelector('.clip-text').innerHTML = `<b>${n}</b> ${name}${plural(n, ['link', 'links'])} on the clipboard`;
  pill.querySelector('.clip-add span').textContent = foreign ? `Add to ${PLATFORM_NAME[foreign]}` : 'Add';
  if (!pill.hidden && pill.dataset.leaving !== '1') {
    pill.__gel = pill.__gel || gel(pill, { amount: 0.5 });
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
  if (markOffered && current) current.keys.forEach((k) => offered.add(k));
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
  c.keys.forEach((k) => offered.add(k));
  hide(false);
  if (c.platform !== getMode()) setMode(c.platform);
  const format = getFormat();
  try {
    const res = await call('enqueue', c.text, c.platform, format);
    const n = (res.added || []).length;
    if (n) toast('success', `Added from the clipboard: ${nItems(n, c.platform, format)}`);
    else toast('info', 'These links are already in the queue');
  } catch (err) {
    toast('error', err.message);
  }
}
