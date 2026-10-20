// Queue: header with counts and actions, job cards (cheap glass, content-visibility for long lists),
// liquid progress bars, actions by status, login prompt on errors, and an original empty state.

import { call, on } from './api.js';
import { Lens, gel } from './glass.js';
import { SPRINGS, springAnimate, springEasing, whenDone } from './motion.js';
import { icon } from './icons.js';
import { bytes, speed, eta, duration, codec, escapeHtml, timeAgo, plural } from './format.js';
import { toast } from './toast.js';
import { setGhosts, activity } from './background.js';

const ACTIVE = new Set(['queued', 'probing', 'downloading', 'processing']);
const FINISHED = new Set(['done', 'error', 'canceled']);

const jobs = new Map();   // id -> job
const cards = new Map();  // id -> element
let list, scroller, empty, head, counts;
let auth = { logged_in: false };
let progressEase = null;

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
  head.querySelectorAll('.btn').forEach((b) => gel(b));
  document.getElementById('btn-clear').addEventListener('click', clearFinished);
  document.getElementById('btn-folder').addEventListener('click', () => call('open_output_dir').catch((e) => toast('error', e.message)));
  empty.innerHTML = emptyArt();

  const frag = document.createDocumentFragment();
  for (const j of state.jobs || []) {
    jobs.set(j.id, j);
    const el = makeCard(j);
    cards.set(j.id, el);
    frag.appendChild(el);
  }
  list.appendChild(frag);

  list.addEventListener('click', onAction);
  on('job', upsert);
  on('job_removed', ({ id }) => removeCard(id));
  on('auth', (a) => {
    auth = a;
    for (const j of jobs.values()) if (j.status === 'error') patch(cards.get(j.id), j);
  });
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
// Rendering

function makeCard(j) {
  const el = document.createElement('article');
  el.className = 'jcard glass-lite';
  el.dataset.id = j.id;
  el.innerHTML = `
    <span class="lg-glow" aria-hidden="true"></span>
    <div class="jc-thumb"><img alt="" decoding="async"><span class="jc-thumb-ph">${icon('reel')}</span><span class="jc-dur"></span></div>
    <div class="jc-body">
      <div class="jc-top">
        <span class="jc-author"></span>
        <span class="jc-time"></span>
        <span class="jc-actions"></span>
      </div>
      <div class="jc-title"></div>
      <div class="jc-badges"></div>
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
  if (j.status === 'done') a.push(['open', 'play', 'Open video'], ['reveal', 'reveal', 'Show in folder']);
  if (j.status === 'error' || j.status === 'canceled') a.push(['retry', 'retry', 'Retry']);
  if (!ACTIVE.has(j.status)) a.push(['remove', 'trash', 'Remove from list']);
  return a;
}

function patch(el, j) {
  if (!el) return;
  const st = el.__state;
  const prevStatus = st.status;
  st.status = j.status;
  el.dataset.status = j.status;
  el.classList.toggle('is-active', ACTIVE.has(j.status));

  // Thumbnail
  const img = el.querySelector('.jc-thumb img');
  if (j.thumbnail && st.thumb !== j.thumbnail) {
    st.thumb = j.thumbnail;
    img.src = j.thumbnail;
    el.classList.add('has-thumb');
    if (prevStatus) img.animate([{ opacity: 0, transform: 'scale(1.08)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)' });
  }
  set(el, '.jc-dur', j.duration_s ? duration(j.duration_s) : '');

  set(el, '.jc-author', j.author ? `@${j.author}` : j.shortcode || 'Video');
  set(el, '.jc-time', timeAgo(j.finished_at || j.created_at));
  set(el, '.jc-title', j.title || j.url);

  // Badges
  const badges = [];
  if (j.width && j.height) badges.push(`<span class="badge">${j.width}×${j.height}</span>`);
  if (j.fps) badges.push(`<span class="badge">${Math.round(j.fps)} fps</span>`);
  if (j.vcodec && j.status === 'done') badges.push(`<span class="badge">${escapeHtml(codec(j.vcodec))}</span>`);
  if (j.filesize) badges.push(`<span class="badge">${bytes(j.filesize)}</span>`);
  if (j.transcoded) badges.push('<span class="badge badge-mint">converted</span>');
  if (j.already_existed) badges.push('<span class="badge badge-amber">already saved</span>');
  if (j.attempt && j.attempt.engine === 'gallery-dl' && j.status === 'done') badges.push('<span class="badge">gallery-dl</span>');
  if (j.attempt && j.attempt.auth && j.attempt.auth !== 'anonymous' && j.status === 'done') badges.push('<span class="badge">signed in</span>');
  const bh = badges.join('');
  if (st.badges !== bh) {
    st.badges = bh;
    el.querySelector('.jc-badges').innerHTML = bh;
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
  set(el, '.jc-stage', stage);
  const meta = [];
  if (j.status === 'downloading' || j.status === 'processing') {
    if (j.speed_bps) meta.push(speed(j.speed_bps));
    if (j.eta_s != null) meta.push(`≈ ${eta(j.eta_s)}`);
    if (!meta.length) meta.push(`${Math.round(p * 100)}%`);
  }
  if (j.status === 'queued') meta.push('waiting for its turn');
  set(el, '.jc-meta', meta.join(' · '));

  // Error
  const errBox = el.querySelector('.jc-error');
  const showErr = j.status === 'error' && j.error;
  errBox.hidden = !showErr;
  el.classList.toggle('has-error', !!showErr);
  if (showErr) {
    set(el, '.jc-err-text', j.error.message || 'Download failed');
    const canLogin = j.error.can_login && !auth.logged_in;
    const key = `${canLogin}|${!!j.error.log_path}`;
    if (st.errKey !== key) {
      st.errKey = key;
      const btns = [];
      if (canLogin) btns.push(`<button type="button" class="btn btn-tinted btn-s" data-act="login">${icon('user')}<span>Sign in to Instagram</span></button>`);
      else if (j.error.can_login && auth.logged_in) btns.push(`<button type="button" class="btn btn-s" data-act="retry">${icon('retry')}<span>Retry signed in</span></button>`);
      if (j.error.log_path) btns.push(`<button type="button" class="btn btn-ghost btn-s" data-act="logs">${icon('logs')}<span>Log</span></button>`);
      const box = el.querySelector('.jc-err-actions');
      box.innerHTML = btns.join('');
      box.querySelectorAll('.btn').forEach((b) => gel(b));
    }
  }

  // Actions
  const acts = actionsFor(j);
  const ak = acts.map((a) => a[0]).join(',');
  if (st.acts !== ak) {
    st.acts = ak;
    const box = el.querySelector('.jc-actions');
    box.innerHTML = acts.map(([act, ic, label]) => `<button type="button" class="icon-btn jc-act" data-act="${act}" aria-label="${label}" title="${label}">${icon(ic)}</button>`).join('');
    box.querySelectorAll('.icon-btn').forEach((b) => gel(b));
  }

  // Status transitions get a little celebration or wobble.
  if (prevStatus && prevStatus !== j.status) {
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
  let active = 0, done = 0, failed = 0;
  for (const j of jobs.values()) {
    if (ACTIVE.has(j.status)) active++;
    else if (j.status === 'done') done++;
    else failed++;
  }
  const parts = [];
  if (active) parts.push(`<span class="qc qc-active"><i></i>${active} active</span>`);
  if (done) parts.push(`<span class="qc qc-done"><i></i>${done} done</span>`);
  if (failed) parts.push(`<span class="qc qc-err"><i></i>${failed} ${plural(failed, ['failed', 'failed'])}</span>`);
  if (!parts.length) parts.push('<span class="qc qc-none">empty</span>');
  counts.innerHTML = parts.join('');
  document.getElementById('btn-clear').disabled = !(done + failed);
  const isEmpty = jobs.size === 0;
  if (empty.hidden === isEmpty) {
    empty.hidden = !isEmpty;
    if (isEmpty) springAnimate(empty, [{ opacity: 0, transform: 'scale(.94)' }, { opacity: 1, transform: 'none' }], SPRINGS.smooth);
  }
  scroller.classList.toggle('is-empty', isEmpty);
  // Recent thumbnails drift into the background scene.
  const thumbs = Array.from(jobs.values()).filter((j) => j.thumbnail).sort((a, b) => b.created_at - a.created_at).slice(0, 4).map((j) => j.thumbnail);
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
    case 'cancel': return run('cancel', id);
    case 'retry': return run('retry', id);
    case 'remove': return run('remove', id);
    case 'open': return run('open_file', id);
    case 'reveal': return run('reveal_file', id);
    case 'logs': return run('open_logs');
    case 'login': return run('login');
  }
}

async function clearFinished() {
  try {
    const n = await call('clear_finished');
    if (n) toast('info', `Removed ${n} from the list. Files stay on disk`);
  } catch (err) {
    toast('error', err.message);
  }
}

/** After a successful login, retry the jobs that failed only because a login was required. */
export function retryLoginFailures() {
  let n = 0;
  for (const j of jobs.values()) {
    if (j.status === 'error' && j.error && j.error.code === 'login_required') {
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
  </div>
  <h3 class="empty-title">The queue is empty</h3>
  <p class="empty-text">Paste Reels links on the left, or just press <kbd>Ctrl</kbd>+<kbd>V</kbd> anywhere in the window. Reels Saver strips the junk and downloads the videos in the best quality.</p>`;
}
