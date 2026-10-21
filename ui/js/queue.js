// Queue: header with counts and actions, job cards (cheap glass, content-visibility for long lists),
// liquid progress bars, actions by status, login prompt on Instagram errors, preview placeholders,
// per-item format toggle, and an original empty state.
//
// Every job stays in memory and in the DOM; only the jobs whose `platform` equals the current mode
// are shown (the others carry [hidden]), so switching modes re-renders instantly without a refetch.

import { call, on } from './api.js';
import { Lens, gel } from './glass.js';
import { SPRINGS, springAnimate, springEasing, whenDone, reducedMotion } from './motion.js';
import { icon } from './icons.js';
import { bytes, speed, eta, duration, codec, escapeHtml, timeAgo, shortLink } from './format.js';
import { toast } from './toast.js';
import { setGhosts, activity } from './background.js';
import { formatToggleHtml } from './controls.js';
import { getMode, onPref } from './mode.js';

const ACTIVE = new Set(['queued', 'probing', 'downloading', 'processing']);
const KIND_ICON = { reel: 'reel', p: 'post', video: 'video', shorts: 'shorts' };

const EMPTY_COPY = {
  instagram: {
    title: 'No Reels yet',
    text: 'Paste Reels links on the left, or just press <kbd>Ctrl</kbd>+<kbd>V</kbd> anywhere in the window. Media Saver strips the junk and saves every Reel as MP4 or MP3, in the quality you pick above Download.',
  },
  youtube: {
    title: 'No YouTube videos yet',
    text: 'Paste links to YouTube videos or Shorts on the left, or just press <kbd>Ctrl</kbd>+<kbd>V</kbd> anywhere in the window. Media Saver cleans them and saves them as MP4 or MP3, in the quality you pick above Download.',
  },
};

const jobs = new Map();   // id -> job (both platforms)
const cards = new Map();  // id -> element
let list, scroller, empty, head, counts;
let auth = { logged_in: false };
let progressEase = null;

const platformOf = (j) => j.platform || 'instagram';
const shown = (j) => platformOf(j) === getMode();

export function getJobs() {
  return jobs.values();
}

export function initQueue(state) {
  list = document.getElementById('q-list');
  scroller = document.getElementById('q-scroll');
  empty = document.getElementById('q-empty');
  head = document.getElementById('q-head');
  counts = document.getElementById('q-counts');
  auth = state.auth || auth;
  progressEase = springEasing(SPRINGS.smooth);
  new Lens(head, 'capsule');
  // The split button squishes as one pill; its halves get no gel of their own.
  head.querySelectorAll('.q-actions > .btn, .q-actions > .split').forEach((b) => gel(b));
  document.getElementById('btn-clear').addEventListener('click', clearFinished);
  initFolder(state.settings || {});
  renderEmpty();

  const frag = document.createDocumentFragment();
  for (const j of state.jobs || []) {
    jobs.set(j.id, j);
    const el = makeCard(j);
    cards.set(j.id, el);
    frag.appendChild(el);
  }
  list.appendChild(frag);

  list.addEventListener('click', onAction);
  list.addEventListener('keydown', onToggleKey);
  on('job', upsert);
  on('job_removed', ({ id }) => removeCard(id));
  on('auth', (a) => {
    auth = a;
    for (const j of jobs.values()) if (j.status === 'error') patch(cards.get(j.id), j);
  });
  onPref('mode', onModeChange);
  scroller.addEventListener('scroll', () => head.classList.toggle('is-scrolled', scroller.scrollTop > 4), { passive: true });
  // Relative times refresh once a minute (no animation loop).
  setInterval(() => {
    for (const [id, el] of cards) {
      const t = el.querySelector('.jc-time');
      const j = jobs.get(id);
      if (t && j) t.textContent = timeAgo(j.finished_at || j.created_at);
    }
  }, 60000);
  renderHead();
}

// ---------------------------------------------------------------------------------------------
// Downloads folder: split button "Open folder" + chevron menu (Open folder / Change folder...)

let outDir = '';
let folderBtn, moreBtn, folderMenu;

/** Last segment of a Windows path; a drive root stays as is ("E:\"). */
function folderName(p) {
  const s = String(p || '').replace(/[\\/]+$/, '');
  const name = s.split(/[\\/]/).pop();
  return name && !/^[A-Za-z]:$/.test(s) ? name : (s ? `${s}\\` : '');
}

function renderFolder() {
  folderBtn.title = outDir || 'Open the downloads folder';
  // LRM marks keep the backslashes in place inside the rtl box that ellipsizes the start.
  folderMenu.querySelector('.fm-path').textContent = outDir ? `\u200E${outDir}\u200E` : '—';
  folderMenu.querySelector('.fm-where').title = outDir;
}

function openFolder() {
  call('open_output_dir').catch((e) => toast('error', e.message));
}

async function changeFolder() {
  // The backend saves the choice and emits `settings`; the toast comes from that event.
  try {
    await call('choose_output_dir');
  } catch (e) {
    toast('error', e.message);
  }
}

function folderItems() {
  return Array.from(folderMenu.querySelectorAll('.fm-item'));
}

function onFolderOutside(e) {
  if (!folderMenu.contains(e.target) && !moreBtn.contains(e.target)) closeFolderMenu(false);
}

function openFolderMenu(focusIndex = 0) {
  if (!folderMenu.hidden) return;
  folderMenu.hidden = false;
  moreBtn.setAttribute('aria-expanded', 'true');
  if (!reducedMotion.matches) {
    springAnimate(folderMenu, [
      { opacity: 0, transform: 'translateY(-6px) scale(.96)' },
      { opacity: 1, transform: 'none' },
    ], SPRINGS.snappy, { fill: 'none' });
  }
  const items = folderItems();
  items[(focusIndex + items.length) % items.length].focus();
  addEventListener('pointerdown', onFolderOutside, true);
}

function closeFolderMenu(refocus = true) {
  if (folderMenu.hidden) return;
  folderMenu.hidden = true;
  moreBtn.setAttribute('aria-expanded', 'false');
  removeEventListener('pointerdown', onFolderOutside, true);
  if (refocus) moreBtn.focus();
}

function initFolder(settings) {
  folderBtn = document.getElementById('btn-folder');
  moreBtn = document.getElementById('btn-folder-more');
  folderMenu = document.getElementById('folder-menu');
  outDir = settings.output_dir || '';
  renderFolder();

  folderBtn.addEventListener('click', openFolder);
  moreBtn.addEventListener('click', () => (folderMenu.hidden ? openFolderMenu(0) : closeFolderMenu()));
  moreBtn.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      openFolderMenu(e.key === 'ArrowDown' ? 0 : -1);
    } else if (e.key === 'Escape' && !folderMenu.hidden) {
      e.preventDefault();
      e.stopPropagation();
      closeFolderMenu();
    }
  });
  folderMenu.addEventListener('click', (e) => {
    const b = e.target.closest('.fm-item');
    if (!b) return;
    closeFolderMenu();
    if (b.dataset.act === 'open') openFolder();
    if (b.dataset.act === 'change') changeFolder();
  });
  folderMenu.addEventListener('keydown', (e) => {
    const items = folderItems();
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeFolderMenu(); }
    else if (e.key === 'Tab') closeFolderMenu(false);
  });

  // A new folder (from this menu or from Settings) is confirmed once; the initial state is not.
  on('settings', (s) => {
    const next = (s && s.output_dir) || '';
    if (!next || next === outDir) return;
    const first = !outDir;
    outDir = next;
    renderFolder();
    if (!first) toast('success', `Videos now go to ${folderName(next)}`);
  });
}

// ---------------------------------------------------------------------------------------------
// Mode

function onModeChange() {
  renderEmpty();
  const entering = [];
  for (const [id, el] of cards) {
    const j = jobs.get(id);
    const vis = !!j && shown(j);
    if (el.hidden === vis) {
      el.hidden = !vis;
      if (vis) entering.push(el);
    }
  }
  scroller.scrollTop = 0;
  renderHead();
  if (reducedMotion.matches) return;
  // The first cards of the new mode settle in with a short stagger; the rest are off screen.
  const order = Array.from(list.children).filter((c) => !c.hidden).slice(0, 8);
  order.forEach((el, i) => {
    springAnimate(el, [
      { opacity: 0, transform: 'translateY(10px) scale(0.97)' },
      { opacity: 1, transform: 'none' },
    ], SPRINGS.bouncy, { delay: i * 28, fill: 'backwards' });
  });
}

function renderEmpty() {
  const c = EMPTY_COPY[getMode()];
  if (!empty.querySelector('.empty-art')) empty.innerHTML = `${emptyArt()}<h3 class="empty-title"></h3><p class="empty-text"></p>`;
  empty.querySelector('.empty-title').textContent = c.title;
  empty.querySelector('.empty-text').innerHTML = c.text;
}

// ---------------------------------------------------------------------------------------------
// Rendering

function makeCard(j) {
  const el = document.createElement('article');
  el.className = 'jcard glass-lite';
  el.dataset.id = j.id;
  el.setAttribute('role', 'listitem');
  el.innerHTML = `
    <div class="jc-thumb"><img alt="" decoding="async"><span class="jc-thumb-ph"></span><span class="jc-dur"></span></div>
    <div class="jc-body">
      <div class="jc-top">
        <span class="jc-title"></span>
        <span class="jc-actions"></span>
      </div>
      <div class="jc-sub"><span class="jc-author"></span><span class="jc-dot" aria-hidden="true">·</span><span class="jc-time"></span></div>
      <div class="jc-badges"><span class="jc-fmt"></span><span class="jc-badge-list"></span></div>
      <div class="jc-status">
        <div class="jc-bar"><span class="jc-fill"></span></div>
        <div class="jc-line"><span class="jc-stage"></span><span class="jc-meta"></span></div>
      </div>
      <div class="jc-error" hidden>
        <span class="jc-err-text"></span>
        <span class="jc-err-actions"></span>
      </div>
    </div>
    <span class="lg-rim" aria-hidden="true"></span>`;
  el.__state = {};
  el.hidden = !shown(j);
  patch(el, j);
  return el;
}

const set = (el, sel, text) => {
  const n = el.querySelector(sel);
  if (n.textContent !== text) n.textContent = text;
  return n;
};

function actionsFor(j) {
  const a = [];
  if (ACTIVE.has(j.status)) a.push(['cancel', 'x', 'Cancel']);
  if (j.status === 'done') a.push(['open', 'play', j.format === 'mp3' ? 'Play audio' : 'Open video'], ['reveal', 'reveal', 'Show in folder']);
  if (j.status === 'error' || j.status === 'canceled') a.push(['retry', 'retry', 'Retry']);
  if (!ACTIVE.has(j.status)) a.push(['remove', 'trash', 'Remove from list']);
  return a;
}

function patch(el, j) {
  if (!el) return;
  const st = el.__state;
  const prevStatus = st.status;
  const platform = platformOf(j);
  const mp3 = j.format === 'mp3';
  // `previewed` false = the preview fetch is still pending; a missing field counts as finished.
  const pending = j.status === 'queued' && j.previewed === false;
  st.status = j.status;
  el.dataset.status = j.status;
  el.dataset.platform = platform;
  el.dataset.format = j.format || 'mp4';
  el.dataset.shape = platform === 'youtube' && j.kind === 'video' ? 'wide' : 'tall';
  el.classList.toggle('is-active', ACTIVE.has(j.status));
  el.classList.toggle('is-pending', pending);

  // Thumbnail (or the kind / audio glyph while there is none)
  const phIcon = mp3 ? 'audio' : KIND_ICON[j.kind] || (platform === 'youtube' ? 'video' : 'reel');
  if (st.ph !== phIcon) {
    st.ph = phIcon;
    el.querySelector('.jc-thumb-ph').innerHTML = icon(phIcon);
  }
  const img = el.querySelector('.jc-thumb img');
  if (j.thumbnail && st.thumb !== j.thumbnail) {
    st.thumb = j.thumbnail;
    img.src = j.thumbnail;
    el.classList.add('has-thumb');
    if (prevStatus && !el.hidden) img.animate([{ opacity: 0, transform: 'scale(1.08)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)' });
  }
  set(el, '.jc-dur', j.duration_s ? duration(j.duration_s) : '');

  // Title and author: the preview fields, a skeleton while pending, the clean link otherwise.
  const link = shortLink(j);
  const titleEl = el.querySelector('.jc-title');
  const titleMode = j.title ? 'text' : pending ? 'skeleton' : 'link';
  const titleText = j.title || (pending ? '' : link);
  if (st.titleMode !== titleMode || titleEl.textContent !== titleText) {
    const wasSkeleton = st.titleMode === 'skeleton';
    st.titleMode = titleMode;
    titleEl.textContent = titleText;
    titleEl.classList.toggle('is-skeleton', titleMode === 'skeleton');
    titleEl.classList.toggle('is-link', titleMode === 'link');
    if (wasSkeleton && !el.hidden) titleEl.animate([{ opacity: 0, filter: 'blur(3px)' }, { opacity: 1, filter: 'none' }], { duration: 320, easing: 'ease-out' });
  }
  titleEl.title = j.title || j.url || '';
  const authorEl = el.querySelector('.jc-author');
  const authorText = j.author ? (platform === 'instagram' ? `@${j.author}` : j.author) : titleMode === 'text' ? '' : titleMode === 'skeleton' ? link : '';
  if (authorEl.textContent !== authorText) authorEl.textContent = authorText;
  authorEl.classList.toggle('is-link', !j.author && !!authorText);
  set(el, '.jc-time', timeAgo(j.finished_at || j.created_at));

  // Format: a toggle while queued, a static badge afterwards.
  const queued = j.status === 'queued';
  const fmtKey = `${queued}|${j.format || 'mp4'}`;
  if (st.fmtKey !== fmtKey) {
    st.fmtKey = fmtKey;
    el.querySelector('.jc-fmt').innerHTML = queued
      ? formatToggleHtml(j.format || 'mp4')
      : `<span class="badge badge-fmt" data-v="${mp3 ? 'mp3' : 'mp4'}">${mp3 ? 'MP3' : 'MP4'}</span>`;
  }

  // Badges
  const badges = [];
  if (!mp3) {
    if (j.width && j.height) badges.push(`<span class="badge">${j.width}×${j.height}</span>`);
    if (j.fps) badges.push(`<span class="badge">${Math.round(j.fps)} fps</span>`);
    if (j.vcodec && j.status === 'done') badges.push(`<span class="badge">${escapeHtml(codec(j.vcodec))}</span>`);
  }
  if (j.filesize) badges.push(`<span class="badge">${bytes(j.filesize)}</span>`);
  if (j.transcoded && !mp3) badges.push('<span class="badge badge-mint">converted</span>');
  if (j.already_existed) badges.push('<span class="badge badge-amber">already saved</span>');
  if (j.attempt && j.attempt.engine === 'gallery-dl' && j.status === 'done') badges.push('<span class="badge">gallery-dl</span>');
  if (j.attempt && j.attempt.auth && j.attempt.auth !== 'anonymous' && j.status === 'done') badges.push('<span class="badge">signed in</span>');
  const bh = badges.join('');
  if (st.badges !== bh) {
    st.badges = bh;
    el.querySelector('.jc-badge-list').innerHTML = bh;
  }

  // Progress
  const fill = el.querySelector('.jc-fill');
  const p = j.status === 'done' ? 1 : Math.max(0, Math.min(1, j.progress || 0));
  if (st.p !== p) {
    st.p = p;
    fill.style.transition = `transform ${progressEase.duration}ms ${progressEase.easing}`;
    fill.style.transform = `translateX(${((p - 1) * 100).toFixed(2)}%)`;
  }
  let stage = j.stage_text || '';
  if (j.status === 'done') stage = j.already_existed ? 'Already downloaded' : 'Done';
  if (pending && !stage) stage = 'Fetching preview';
  set(el, '.jc-stage', stage);
  const meta = [];
  if (j.status === 'downloading' || j.status === 'processing') {
    if (j.speed_bps) meta.push(speed(j.speed_bps));
    if (j.eta_s != null) meta.push(`≈ ${eta(j.eta_s)}`);
    if (!meta.length) meta.push(`${Math.round(p * 100)}%`);
  }
  if (queued) meta.push('waiting for its turn');
  set(el, '.jc-meta', meta.join(' · '));

  // Error
  const errBox = el.querySelector('.jc-error');
  const showErr = j.status === 'error' && j.error;
  errBox.hidden = !showErr;
  el.classList.toggle('has-error', !!showErr);
  if (showErr) {
    set(el, '.jc-err-text', j.error.message || 'Download failed');
    // Sign-in exists for Instagram only; YouTube errors show the core's message as is.
    const igLogin = platform === 'instagram' && j.error.can_login;
    const canLogin = igLogin && !auth.logged_in;
    const key = `${canLogin}|${igLogin && auth.logged_in}|${!!j.error.log_path}`;
    if (st.errKey !== key) {
      st.errKey = key;
      const btns = [];
      if (canLogin) btns.push(`<button type="button" class="btn btn-tinted btn-s" data-act="login">${icon('user')}<span>Sign in to Instagram</span></button>`);
      else if (igLogin && auth.logged_in) btns.push(`<button type="button" class="btn btn-s" data-act="retry">${icon('retry')}<span>Retry signed in</span></button>`);
      if (j.error.log_path) btns.push(`<button type="button" class="btn btn-ghost btn-s" data-act="logs">${icon('logs')}<span>Log</span></button>`);
      const box = el.querySelector('.jc-err-actions');
      box.innerHTML = btns.join('');
      box.querySelectorAll('.btn').forEach((b) => gel(b));
    }
  }

  // Actions
  const acts = actionsFor(j);
  const ak = acts.map((a) => a.join(':')).join(',');
  if (st.acts !== ak) {
    st.acts = ak;
    const box = el.querySelector('.jc-actions');
    box.innerHTML = acts.map(([act, ic, label]) => `<button type="button" class="icon-btn jc-act" data-act="${act}" aria-label="${label}" title="${label}">${icon(ic)}</button>`).join('');
    box.querySelectorAll('.icon-btn').forEach((b) => gel(b));
  }

  // Status transitions get a little celebration or wobble.
  if (prevStatus && prevStatus !== j.status && !el.hidden) {
    if (j.status === 'done') springAnimate(el, [{ transform: 'scale(1.025)' }, { transform: 'scale(1)' }], SPRINGS.jelly, { fill: 'none' });
    if (j.status === 'error') springAnimate(el, [{ transform: 'translateX(-10px)' }, { transform: 'none' }], SPRINGS.jelly, { fill: 'none' });
  }
}

function upsert(j) {
  activity();
  const existed = jobs.has(j.id);
  jobs.set(j.id, j);
  if (existed) {
    patch(cards.get(j.id), j);
  } else {
    const el = makeCard(j);
    cards.set(j.id, el);
    insertSorted(el, j);
  }
  renderHead();
}

function insertSorted(el, j) {
  // Newest first. New jobs nearly always go to the top; find the first older card otherwise.
  let before = null;
  for (const c of list.children) {
    const cj = jobs.get(c.dataset.id);
    if (cj && cj.created_at < j.created_at) {
      before = c;
      break;
    }
  }
  if (el.hidden) {
    list.insertBefore(el, before);
    return;
  }
  flipList(() => list.insertBefore(el, before));
  springAnimate(el, [
    { opacity: 0, transform: 'translateY(-14px) scale(0.94)' },
    { opacity: 1, transform: 'none' },
  ], SPRINGS.bouncy, { fill: 'backwards' });
}

function removeCard(id) {
  const el = cards.get(id);
  jobs.delete(id);
  cards.delete(id);
  renderHead();
  if (!el) return;
  if (el.hidden) {
    el.remove();
    return;
  }
  el.classList.add('is-leaving');
  const h = el.offsetHeight;
  const a = springAnimate(el, [
    { opacity: 1, transform: 'none' },
    { opacity: 0, transform: 'scale(0.92)' },
  ], SPRINGS.sheetClose);
  whenDone(a, () => {
    // Collapse the gap so the cards below slide up.
    const c = el.animate([{ height: `${h}px`, marginBottom: getComputedStyle(el).marginBottom }, { height: '0px', marginBottom: '0px' }],
      { ...springEasing(SPRINGS.smooth), fill: 'forwards' });
    whenDone(c, () => el.remove());
  });
}

/** FLIP visible cards around a DOM change. */
function flipList(change) {
  const view = scroller.getBoundingClientRect();
  const visible = Array.from(list.children).filter((c) => {
    if (c.hidden) return false;
    const r = c.getBoundingClientRect();
    return r.bottom > view.top && r.top < view.bottom;
  }).slice(0, 14);
  const before = new Map(visible.map((c) => [c, c.getBoundingClientRect().top]));
  change();
  for (const [c, top] of before) {
    const dy = top - c.getBoundingClientRect().top;
    if (Math.abs(dy) > 0.5) springAnimate(c, [{ translate: `0 ${dy}px` }, { translate: '0 0' }], SPRINGS.bouncy, { fill: 'none' });
  }
}

function renderHead() {
  let active = 0, done = 0, failed = 0, total = 0;
  const mine = [];
  for (const j of jobs.values()) {
    if (!shown(j)) continue;
    total++;
    mine.push(j);
    if (ACTIVE.has(j.status)) active++;
    else if (j.status === 'done') done++;
    else failed++;
  }
  const parts = [];
  if (active) parts.push(`<span class="qc qc-active"><i></i>${active} active</span>`);
  if (done) parts.push(`<span class="qc qc-done"><i></i>${done} done</span>`);
  if (failed) parts.push(`<span class="qc qc-err"><i></i>${failed} failed</span>`);
  if (!parts.length) parts.push('<span class="qc qc-none">empty</span>');
  const html = parts.join('');
  if (counts.innerHTML !== html) counts.innerHTML = html;
  document.getElementById('btn-clear').disabled = !(done + failed);
  const isEmpty = total === 0;
  if (empty.hidden === isEmpty) {
    empty.hidden = !isEmpty;
    if (isEmpty) springAnimate(empty, [{ opacity: 0, transform: 'scale(.94)' }, { opacity: 1, transform: 'none' }], SPRINGS.smooth);
  }
  scroller.classList.toggle('is-empty', isEmpty);
  // Recent thumbnails of this mode drift into the background scene.
  const thumbs = mine.filter((j) => j.thumbnail).sort((a, b) => b.created_at - a.created_at).slice(0, 4).map((j) => j.thumbnail);
  setGhosts(thumbs);
}

// ---------------------------------------------------------------------------------------------
// Actions

async function onAction(e) {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const card = b.closest('.jcard');
  const id = card && card.dataset.id;
  const act = b.dataset.act;
  const run = (m, ...a) => call(m, ...a).catch((err) => toast('error', err.message));
  switch (act) {
    case 'fmt': return setJobFormat(card, id, b.dataset.v);
    case 'cancel': return run('cancel', id);
    case 'retry': return run('retry', id);
    case 'remove': return run('remove', id);
    case 'open': return run('open_file', id);
    case 'reveal': return run('reveal_file', id);
    case 'logs': return run('open_logs');
    case 'login': return run('login');
  }
}

/** Arrow keys move the per-item format toggle like a radio group. */
function onToggleKey(e) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
  const b = e.target.closest('.fmt-toggle [data-act="fmt"]');
  if (!b) return;
  e.preventDefault();
  const other = b.dataset.v === 'mp4' ? 'mp3' : 'mp4';
  const card = b.closest('.jcard');
  setJobFormat(card, card.dataset.id, other).then(() => {
    const next = card.querySelector(`.fmt-toggle [data-v="${other}"]`);
    next && next.focus();
  });
}

async function setJobFormat(card, id, format) {
  const j = jobs.get(id);
  if (!j || j.format === format) return;
  const tog = card.querySelector('.fmt-toggle');
  // Optimistic: the thumb moves at once; the `job` event confirms it.
  if (tog) {
    tog.dataset.v = format;
    tog.querySelectorAll('[data-act="fmt"]').forEach((x) => {
      const on = x.dataset.v === format;
      x.setAttribute('aria-checked', String(on));
      x.tabIndex = on ? 0 : -1;
    });
  }
  try {
    const res = await call('set_job_format', id, format);
    if (res) {
      upsert(res);
    } else {
      toast('info', 'This item has already started, its format is fixed');
      const cur = jobs.get(id);
      if (cur) {
        card.__state.fmtKey = null;
        patch(card, cur);
      }
    }
  } catch (err) {
    toast('error', err.message);
    const cur = jobs.get(id);
    if (cur) {
      card.__state.fmtKey = null;
      patch(card, cur);
    }
  }
}

async function clearFinished() {
  try {
    const n = await call('clear_finished', getMode());
    if (n) toast('info', `Removed ${n} from the list. Files stay on disk`);
  } catch (err) {
    toast('error', err.message);
  }
}

/** After a successful Instagram sign-in, retry the Instagram jobs that failed only for lack of it. */
export function retryLoginFailures() {
  let n = 0;
  for (const j of jobs.values()) {
    if (platformOf(j) === 'instagram' && j.status === 'error' && j.error && j.error.code === 'login_required') {
      call('retry', j.id).catch(() => {});
      n++;
    }
  }
  return n;
}

// ---------------------------------------------------------------------------------------------
// Empty state art: a droplet over a film strip, original drawing.

function emptyArt() {
  return `
  <div class="empty-art" aria-hidden="true">
    <svg viewBox="0 0 240 200" width="240" height="200">
      <defs>
        <linearGradient id="ea-drop" x1="80" y1="20" x2="170" y2="170" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#7cc4ff" stop-opacity=".95"/>
          <stop offset=".5" stop-color="#7a7dff" stop-opacity=".85"/>
          <stop offset="1" stop-color="#b06bff" stop-opacity=".9"/>
        </linearGradient>
        <radialGradient id="ea-spec" cx="100" cy="70" r="46" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#fff" stop-opacity=".9"/>
          <stop offset="1" stop-color="#fff" stop-opacity="0"/>
        </radialGradient>
        <linearGradient id="ea-film" x1="0" y1="0" x2="240" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#fff" stop-opacity="0"/>
          <stop offset=".25" stop-color="#fff" stop-opacity=".14"/>
          <stop offset=".75" stop-color="#fff" stop-opacity=".14"/>
          <stop offset="1" stop-color="#fff" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <g class="ea-film">
        <rect x="0" y="128" width="240" height="44" rx="10" fill="url(#ea-film)"/>
        ${Array.from({ length: 11 }, (_, i) => `<rect x="${8 + i * 22}" y="133" width="10" height="6" rx="2" fill="#fff" fill-opacity=".18"/><rect x="${8 + i * 22}" y="161" width="10" height="6" rx="2" fill="#fff" fill-opacity=".18"/>`).join('')}
        ${[0, 1, 2, 3, 4].map((i) => `<rect x="${22 + i * 42}" y="142" width="32" height="16" rx="4" fill="#fff" fill-opacity="${0.06 + (i === 2 ? 0.1 : 0)}"/>`).join('')}
      </g>
      <g class="ea-drop">
        <path d="M120 22c0 0-46 50-46 82a46 46 0 0 0 92 0c0-32-46-82-46-82Z" fill="url(#ea-drop)"/>
        <path d="M120 22c0 0-46 50-46 82a46 46 0 0 0 92 0c0-32-46-82-46-82Z" fill="none" stroke="#fff" stroke-opacity=".5" stroke-width="1.5"/>
        <ellipse cx="102" cy="78" rx="16" ry="24" fill="url(#ea-spec)" transform="rotate(24 102 78)"/>
        <path d="M120 70v42M104 98l16 16 16-16" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
      <g class="ea-bubbles" fill="#fff">
        <circle cx="54" cy="64" r="5" fill-opacity=".22"/>
        <circle cx="190" cy="48" r="7" fill-opacity=".16"/>
        <circle cx="200" cy="96" r="3.5" fill-opacity=".28"/>
        <circle cx="38" cy="108" r="3" fill-opacity=".24"/>
      </g>
    </svg>
  </div>`;
}
