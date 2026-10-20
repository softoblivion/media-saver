// Mock backend for UI work in a normal browser. Mirrors docs/BRIDGE_API.md: every method and event,
// with a realistic simulation (engine update on start, jobs moving through
// probing -> downloading -> processing -> done with speed and ETA, a login_required job, an
// "already existed" job, canvas thumbnails, link cleaning close to app/core/linkclean.py).
//
// URL parameters (all optional):
//   ?mock=1      force the mock even inside pywebview
//   &demo=1      preload a queue with every state (done, transcoded, already existed, errors)
//   &jobs=N      preload N finished jobs (list performance check)
//   &clip=0      start with an empty clipboard (no clipboard prompt)
//   &fast=1      run the simulation 4x faster
//   &offline=1   engine update ends in "offline"

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const now = () => Date.now() / 1000;
const clone = (o) => JSON.parse(JSON.stringify(o));

// ---------------------------------------------------------------------------------------------
// Link cleaning (mirror of the canonical rules in docs/ARCHITECTURE.md 5.3)

const SHORTCODE_RE = /^[A-Za-z0-9_-]{5,64}$/;
const IG_HOSTS = new Set(['instagram.com', 'www.instagram.com', 'm.instagram.com', 'instagr.am', 'www.instagr.am']);
const REEL_SEGS = new Set(['reel', 'reels', 'tv']);

function tokens(text) {
  return String(text || '')
    .split(/[\s,;]+/)
    .map((t) => t.replace(/^[<("'\[«]+|[>)"'\]».,!?]+$/g, ''))
    .filter((t) => t && (/^https?:\/\//i.test(t) || /(^|\.)instagr(am\.com|\.am)\b/i.test(t) || /^www\./i.test(t)));
}

function cleanOne(original) {
  const item = { original, clean: null, shortcode: null, kind: null, error: null };
  let u;
  try {
    u = new URL(/^https?:\/\//i.test(original) ? original : `https://${original}`);
  } catch {
    item.error = 'not_instagram';
    return item;
  }
  const host = u.hostname.toLowerCase();
  if (!IG_HOSTS.has(host)) {
    item.error = 'not_instagram';
    return item;
  }
  const seg = u.pathname.split('/').filter(Boolean).map((s) => decodeURIComponent(s));
  let kind = null;
  let code = null;
  if (seg.length && REEL_SEGS.has(seg[0].toLowerCase())) {
    kind = 'reel';
    code = seg[1];
  } else if (seg.length && seg[0].toLowerCase() === 'p') {
    kind = 'p';
    code = seg[1];
  } else if (seg.length >= 2 && (REEL_SEGS.has(seg[1].toLowerCase()) || seg[1].toLowerCase() === 'p')) {
    // /<username>/reel/<code> and /<username>/p/<code>
    kind = seg[1].toLowerCase() === 'p' ? 'p' : 'reel';
    code = seg[2];
  } else {
    item.error = 'unsupported_path';
    return item;
  }
  if (!code || !SHORTCODE_RE.test(code)) {
    item.error = 'no_shortcode';
    item.kind = kind;
    return item;
  }
  item.kind = kind;
  item.shortcode = code;
  item.clean = `https://www.instagram.com/${kind}/${code}`;
  return item;
}

export function cleanLinks(text) {
  const items = tokens(text).map(cleanOne);
  const seen = new Set();
  let valid = 0, invalid = 0, duplicates = 0;
  const out = [];
  for (const it of items) {
    if (!it.clean) {
      invalid++;
      continue;
    }
    if (seen.has(it.clean)) {
      duplicates++;
      continue;
    }
    seen.add(it.clean);
    valid++;
    out.push(it.clean);
  }
  return { items, clean_text: out.join('\n'), valid, invalid, duplicates };
}

// ---------------------------------------------------------------------------------------------
// Thumbnails

const PALETTES = [
  ['#ff9a62', '#7a3cff'], ['#30d5c8', '#1d3b8f'], ['#ffd25e', '#ff5f6d'], ['#8ee6ff', '#5a5cff'],
  ['#b8ff8a', '#0f8f7a'], ['#ff7eb6', '#4b2a9a'], ['#ffe3b0', '#c2593f'], ['#7af0d0', '#253a7a'],
];

function makeThumb(seed) {
  const c = document.createElement('canvas');
  c.width = 270;
  c.height = 480;
  const g = c.getContext('2d');
  const [a, b] = PALETTES[seed % PALETTES.length];
  const grad = g.createLinearGradient(0, 0, 270, 480);
  grad.addColorStop(0, a);
  grad.addColorStop(1, b);
  g.fillStyle = grad;
  g.fillRect(0, 0, 270, 480);
  let s = seed * 9301 + 49297;
  const r = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  for (let i = 0; i < 5; i++) {
    g.globalAlpha = 0.25 + r() * 0.3;
    g.fillStyle = i % 2 ? '#ffffff' : '#000000';
    g.beginPath();
    g.arc(r() * 270, r() * 480, 30 + r() * 110, 0, Math.PI * 2);
    g.fill();
  }
  // Horizon and a simple figure, to read as "a video frame".
  g.globalAlpha = 0.35;
  g.fillStyle = '#0b0b18';
  g.beginPath();
  g.moveTo(0, 360);
  for (let x = 0; x <= 270; x += 30) g.lineTo(x, 340 + Math.sin(x / 40 + seed) * 18);
  g.lineTo(270, 480);
  g.lineTo(0, 480);
  g.fill();
  g.globalAlpha = 0.85;
  g.fillStyle = 'rgba(255,255,255,.9)';
  g.beginPath();
  g.arc(135, 250, 26, 0, Math.PI * 2);
  g.fill();
  g.beginPath();
  g.ellipse(135, 340, 46, 64, 0, Math.PI, 0);
  g.fill();
  g.globalAlpha = 1;
  return c.toDataURL('image/jpeg', 0.78);
}

// ---------------------------------------------------------------------------------------------
// Mock

const AUTHORS = ['studio.lumen', 'kitchen_by_mira', 'trail.runner', 'neon_garage', 'dance.lab', 'coffee.atlas',
  'urban.sketch', 'moto.diaries', 'pixel.garden', 'north.waves', 'clay.and.fire', 'tiny.botanist'];
const TITLES = [
  'Morning light over the bay, shot on a phone',
  'Recipe: perfect pasta in 12 minutes',
  'How we built a motorcycle from scratch, part 3',
  'New combo, breakdown on the count of 1-2-3-4',
  'Latte art: a swan on the first try',
  'City sketch in 60 seconds',
  'Highway sunset, 4K timelapse',
  'Repairing an old camera by hand',
  'Barista challenge: 5 drinks in a row',
  'Monstera repotting without the stress',
];

const DEFAULT_SETTINGS = {
  output_dir: 'C:\\Users\\user\\Downloads\\Reels Saver',
  filename_template: '{author}_{shortcode}',
  quality: 'max',
  h264_compat: true,
  skip_existing: true,
  concurrency: 2,
  update_channel: 'nightly',
  auto_update: true,
  auth_mode: 'auto',
  browser_cookies: '',
  clipboard_watch: true,
  open_folder_when_done: false,
};

export function createMock({ params, emit }) {
  const speed = params.get('fast') === '1' ? 4 : 1;
  const wait = (ms) => sleep(ms / speed);
  const settings = clone(DEFAULT_SETTINGS);
  let auth = { logged_in: false, user_id: null, source: null, saved_at: null };
  let engine = {
    stage: 'starting',
    message: 'Starting the engine…',
    progress: null,
    components: {
      'yt-dlp': { version: '2026.10.12.181023', channel: 'nightly', updated: false, ok: true },
      'gallery-dl': { version: '1.30.9', updated: false, ok: true },
      ffmpeg: { version: '9.0.2', source: 'system', ok: true },
    },
    last_check: now() - 86400,
  };
  const jobs = new Map();
  let jobCounter = 0;
  let thumbSeed = 1;
  let maximized = false;
  let clipboard = params.get('clip') === '0'
    ? ''
    : 'Look what I found 👉 https://www.instagram.com/reel/DPx7Qm2kLc9/?igsh=MTd4c2FyZ3pxNnB6Ng== and also https://instagram.com/reels/C9vT_aa81Zq?utm_source=ig_web_copy_link';
  let updating = false;
  let ready = false;

  const emitJob = (j) => emit('job', clone(j));
  const toast = (level, text) => emit('toast', { level, text });

  // ---- engine update simulation -------------------------------------------------------------
  async function runUpdate(forced = false) {
    if (updating) return;
    updating = true;
    const set = (patch) => {
      engine = { ...engine, ...patch };
      emit('engine', clone(engine));
    };
    set({ stage: 'checking', message: 'Checking for updates…', progress: null });
    await wait(1100);
    if (params.get('offline') === '1') {
      set({ stage: 'offline', message: 'No network, using the saved versions', progress: null, last_check: now() });
      updating = false;
      ready = true;
      pump();
      return;
    }
    const newVer = forced ? '2026.10.19.002117' : '2026.10.18.234512';
    for (let p = 0; p <= 1.0001; p += 0.08) {
      set({ stage: 'downloading', message: `Downloading yt-dlp ${settings.update_channel}…`, progress: Math.min(1, p) });
      await wait(140);
    }
    set({ stage: 'updating', message: 'Checking gallery-dl and ffmpeg…', progress: null });
    await wait(900);
    engine.components = {
      'yt-dlp': { version: newVer, channel: settings.update_channel, updated: true, ok: true },
      'gallery-dl': { version: '1.30.10', updated: true, ok: true },
      ffmpeg: { version: '9.0.2', source: 'system', ok: true },
    };
    set({ stage: 'ready', message: 'Engine ready', progress: null, last_check: now() });
    updating = false;
    ready = true;
    if (forced) toast('success', `yt-dlp updated to ${newVer}`);
    pump();
  }

  // ---- jobs -----------------------------------------------------------------------------------
  function newJob(url, shortcode, extra = {}) {
    const id = Math.random().toString(16).slice(2, 10);
    const j = {
      id, url, shortcode,
      status: 'queued', stage_text: ready ? 'Queued' : 'Waiting for the engine', progress: 0,
      speed_bps: null, eta_s: null, title: null, author: null, duration_s: null, thumbnail: null,
      width: null, height: null, fps: null, vcodec: null, acodec: null, filesize: null, filepath: null,
      already_existed: false, transcoded: false,
      attempt: { engine: 'yt-dlp', auth: 'anonymous' },
      error: null, created_at: now(), finished_at: null,
      _n: jobCounter++,
      ...extra,
    };
    jobs.set(id, j);
    return j;
  }

  const ACTIVE = new Set(['probing', 'downloading', 'processing']);
  const running = () => Array.from(jobs.values()).filter((j) => ACTIVE.has(j.status)).length;

  function pump() {
    if (!ready) return;
    const queued = Array.from(jobs.values())
      .filter((j) => j.status === 'queued')
      .sort((a, b) => a.created_at - b.created_at);
    for (const j of queued) {
      if (running() >= settings.concurrency) break;
      runJob(j);
    }
    maybeFinishedAll();
  }

  let wasBusy = false;
  function maybeFinishedAll() {
    const busy = Array.from(jobs.values()).some((j) => j.status === 'queued' || ACTIVE.has(j.status));
    if (wasBusy && !busy && settings.open_folder_when_done) toast('info', '(demo) Would open the videos folder');
    wasBusy = busy;
  }

  const fileName = (j) => {
    const date = new Date(j.created_at * 1000).toISOString().slice(0, 10);
    return settings.filename_template
      .replace('{author}', j.author || 'author')
      .replace('{shortcode}', j.shortcode)
      .replace('{date}', date)
      .replace('{title}', (j.title || '').slice(0, 40).replace(/[\\/:*?"<>|]/g, '')) + '.mp4';
  };

  async function runJob(j) {
    const token = (j._token = Math.random());
    const alive = () => jobs.has(j.id) && j._token === token && j.status !== 'canceled';
    const update = (patch) => {
      if (!alive()) return false;
      Object.assign(j, patch);
      emitJob(j);
      return true;
    };
    const n = j._n;
    const code = j.shortcode || '';
    const wantsLogin = /LOGIN/i.test(code) || n % 7 === 3;
    const isPrivate = /PRIV/i.test(code);
    const existed = n % 7 === 1 && settings.skip_existing;
    const vp9 = n % 5 === 4;
    const H = settings.quality === '720' ? 1280 : 1920;
    const W = settings.quality === '720' ? 720 : 1080;

    if (!update({ status: 'probing', stage_text: 'Fetching info', progress: 0.02, error: null, attempt: { engine: 'yt-dlp', auth: auth.logged_in && settings.auth_mode === 'always' ? 'session' : 'anonymous' } })) return;
    await wait(rand(700, 1500));
    if (!alive()) return;

    if (isPrivate) {
      update({ status: 'error', stage_text: 'Failed', progress: 0, finished_at: now(),
        error: { code: 'private', message: 'This video is from a private account. You can only download it if you follow the author.', can_login: !auth.logged_in, log_path: 'C:\\Users\\user\\AppData\\Local\\ReelsSaver\\logs\\jobs\\' + j.id + '.log' } });
      return pump();
    }
    if (wantsLogin && !auth.logged_in) {
      update({ stage_text: 'Trying with the saved session', progress: 0.04, attempt: { engine: 'gallery-dl', auth: 'anonymous' } });
      await wait(900);
      update({ status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: now(),
        error: { code: 'login_required', message: 'Instagram requires you to sign in to show this video.', can_login: true, log_path: 'C:\\Users\\user\\AppData\\Local\\ReelsSaver\\logs\\jobs\\' + j.id + '.log' } });
      return pump();
    }

    const meta = {
      author: AUTHORS[n % AUTHORS.length],
      title: TITLES[n % TITLES.length],
      duration_s: Math.round(rand(8, 58) * 10) / 10,
      thumbnail: makeThumb(thumbSeed++),
      width: W, height: H, fps: n % 3 === 2 ? 60 : 30,
      attempt: { engine: 'yt-dlp', auth: wantsLogin ? 'session' : 'anonymous' },
    };
    if (!update({ ...meta, stage_text: 'Picking the best format', progress: 0.08 })) return;
    await wait(400);

    if (existed) {
      update({ status: 'done', stage_text: 'Already downloaded', progress: 1, already_existed: true,
        vcodec: 'h264', acodec: 'aac', filesize: Math.round(rand(6, 30) * 1048576), finished_at: now() });
      j.filepath = `${settings.output_dir}\\${fileName(j)}`;
      emitJob(j);
      return pump();
    }

    // Video then audio, with a jittery speed.
    const vSize = rand(8, 36) * 1048576;
    const aSize = rand(0.3, 0.9) * 1048576;
    let got = 0;
    let bps = rand(2, 7) * 1048576;
    while (got < vSize) {
      await wait(250);
      if (!alive()) return;
      bps = Math.max(0.6 * 1048576, bps * rand(0.82, 1.2));
      got = Math.min(vSize, got + (bps * 0.25) * speed);
      const p = 0.08 + 0.7 * (got / vSize);
      update({ status: 'downloading', stage_text: 'Downloading video', progress: p, speed_bps: Math.round(bps), eta_s: Math.ceil((vSize - got + aSize) / bps / speed) });
    }
    got = 0;
    while (got < aSize) {
      await wait(250);
      if (!alive()) return;
      got = Math.min(aSize, got + bps * 0.25 * speed);
      update({ stage_text: 'Downloading audio', progress: 0.78 + 0.07 * (got / aSize), speed_bps: Math.round(bps), eta_s: Math.ceil((aSize - got) / bps / speed) + 1 });
    }
    update({ status: 'processing', stage_text: 'Merging tracks', progress: 0.88, speed_bps: null, eta_s: null });
    await wait(800);
    if (!alive()) return;
    let transcoded = false;
    if (vp9 && settings.h264_compat) {
      for (let p = 0; p <= 1; p += 0.1) {
        update({ stage_text: 'Converting to H.264', progress: 0.9 + 0.09 * p, eta_s: Math.ceil((1 - p) * 3) || null });
        await wait(300);
        if (!alive()) return;
      }
      transcoded = true;
    }
    j.filepath = `${settings.output_dir}\\${fileName(j)}`;
    update({ status: 'done', stage_text: 'Done', progress: 1, speed_bps: null, eta_s: null,
      vcodec: vp9 && !settings.h264_compat ? 'vp9' : 'h264', acodec: 'aac', transcoded,
      filesize: Math.round(vSize + aSize), finished_at: now() });
    pump();
  }

  // ---- preloaded queues -----------------------------------------------------------------------
  function preload() {
    const stress = parseInt(params.get('jobs') || '0', 10);
    const t0 = now() - 3600;
    for (let i = 0; i < stress; i++) {
      const code = `Cx${(100000 + i).toString(36)}Q${i}`;
      const j = newJob(`https://www.instagram.com/reel/${code}`, code, {
        status: 'done', stage_text: 'Done', progress: 1, author: AUTHORS[i % AUTHORS.length], title: TITLES[i % TITLES.length],
        duration_s: 12 + (i % 40), thumbnail: makeThumb(i + 100), width: 1080, height: 1920, fps: 30, vcodec: 'h264', acodec: 'aac',
        filesize: 4e6 + (i % 13) * 1.7e6, transcoded: i % 6 === 0, already_existed: i % 9 === 0, created_at: t0 + i * 10, finished_at: t0 + i * 10 + 8,
      });
      j.filepath = `${settings.output_dir}\\${j.author}_${code}.mp4`;
    }
    if (params.get('demo') !== '1') return;
    const t = now() - 900;
    const mk = (code, extra) => newJob(`https://www.instagram.com/reel/${code}`, code, { created_at: t + jobs.size * 30, ...extra });
    const base = (i) => ({ author: AUTHORS[i], title: TITLES[i], duration_s: 14 + i * 3.3, thumbnail: makeThumb(40 + i), width: 1080, height: 1920, fps: 30, acodec: 'aac' });
    let a = mk('C8kPq1zXmNw', { ...base(0), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', filesize: 12.4 * 1048576, finished_at: t + 40 });
    a.filepath = `${settings.output_dir}\\${a.author}_${a.shortcode}.mp4`;
    a = mk('DA3rT7uYp_2', { ...base(1), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', transcoded: true, fps: 60, filesize: 31.9 * 1048576, finished_at: t + 90 });
    a.filepath = `${settings.output_dir}\\${a.author}_${a.shortcode}.mp4`;
    a = mk('C7mmZ0aQ-Lk', { ...base(2), status: 'done', stage_text: 'Already downloaded', progress: 1, vcodec: 'h264', already_existed: true, filesize: 8.1 * 1048576, finished_at: t + 120 });
    a.filepath = `${settings.output_dir}\\${a.author}_${a.shortcode}.mp4`;
    mk('DBLOGINx9Qa', { status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: t + 150, attempt: { engine: 'gallery-dl', auth: 'anonymous' },
      error: { code: 'login_required', message: 'Instagram requires you to sign in to show this video.', can_login: true, log_path: 'C:\\logs\\jobs\\x.log' } });
    mk('C1netw0rkEr', { ...base(4), status: 'error', stage_text: 'Network error', progress: 0.31, finished_at: t + 170,
      error: { code: 'network', message: 'The connection dropped. Check your internet and try again.', can_login: false, log_path: 'C:\\logs\\jobs\\y.log' } });
    mk('C2canceled0', { ...base(5), status: 'canceled', stage_text: 'Canceled', progress: 0.44, finished_at: t + 180, error: null });
    // A few that start running right away.
    jobCounter = 10;
    ['DCrun1aaBb2', 'DCrun2ccDd4', 'DCrun3eeFf6'].forEach((c, i) => newJob(`https://www.instagram.com/reel/${c}`, c, { created_at: now() - 3 + i }));
  }
  preload();

  // ---- window focus -> "window" events ------------------------------------------------------
  addEventListener('focus', () => emit('window', { maximized, focused: true }));
  addEventListener('blur', () => emit('window', { maximized, focused: false }));

  const state = () => ({
    app_version: '1.0.0',
    engine: clone(engine),
    auth: clone(auth),
    settings: clone(settings),
    jobs: Array.from(jobs.values()).sort((a, b) => b.created_at - a.created_at).map((j) => {
      const c = clone(j);
      delete c._n;
      delete c._token;
      return c;
    }),
  });

  const job = (id) => {
    const j = jobs.get(id);
    if (!j) throw new Error('Job not found');
    return j;
  };

  const SETTINGS_RULES = {
    output_dir: (v) => typeof v === 'string' && v.length > 2,
    filename_template: (v) => typeof v === 'string' && /\{(author|shortcode|date|title)\}/.test(v) && !/[<>:"/\\|?*]/.test(v.replace(/\{[a-z]+\}/g, '')),
    quality: (v) => ['max', '1080', '720'].includes(v),
    h264_compat: (v) => typeof v === 'boolean',
    skip_existing: (v) => typeof v === 'boolean',
    concurrency: (v) => Number.isInteger(v) && v >= 1 && v <= 4,
    update_channel: (v) => ['nightly', 'stable'].includes(v),
    auto_update: (v) => typeof v === 'boolean',
    auth_mode: (v) => ['auto', 'always', 'never'].includes(v),
    browser_cookies: (v) => ['', 'firefox', 'edge', 'chrome', 'brave', 'opera'].includes(v),
    clipboard_watch: (v) => typeof v === 'boolean',
    open_folder_when_done: (v) => typeof v === 'boolean',
  };

  // ---- the API --------------------------------------------------------------------------------
  return {
    async ready() {
      await sleep(120);
      setTimeout(() => (settings.auto_update ? runUpdate() : (ready = true, engine.stage = 'ready', emit('engine', clone(engine)), pump())), 500);
      return state();
    },
    async clean_links(text) {
      return cleanLinks(text);
    },
    async enqueue(text) {
      const res = cleanLinks(text);
      const added = [];
      const skipped = [];
      const activeCodes = new Set(Array.from(jobs.values()).filter((j) => j.status !== 'error' && j.status !== 'canceled').map((j) => j.shortcode));
      const seen = new Set();
      for (const it of res.items) {
        if (!it.clean) {
          skipped.push({ input: it.original, reason: 'invalid' });
          continue;
        }
        if (seen.has(it.shortcode) || activeCodes.has(it.shortcode)) {
          skipped.push({ input: it.original, reason: 'duplicate' });
          continue;
        }
        seen.add(it.shortcode);
        const j = newJob(it.clean, it.shortcode);
        added.push(j);
      }
      await sleep(180);
      added.forEach((j) => emitJob(j));
      setTimeout(pump, 50);
      return { added: added.map((j) => { const c = clone(j); delete c._n; return c; }), skipped };
    },
    async cancel(id) {
      const j = job(id);
      if (!['queued', 'probing', 'downloading', 'processing'].includes(j.status)) return false;
      Object.assign(j, { status: 'canceled', stage_text: 'Canceled', speed_bps: null, eta_s: null, finished_at: now(), error: null });
      emitJob(j);
      setTimeout(pump, 30);
      return true;
    },
    async retry(id) {
      const j = job(id);
      if (!['error', 'canceled'].includes(j.status)) return false;
      Object.assign(j, { status: 'queued', stage_text: 'Queued', progress: 0, error: null, finished_at: null, speed_bps: null, eta_s: null });
      emitJob(j);
      setTimeout(pump, 30);
      return true;
    },
    async remove(id) {
      const j = job(id);
      j.status = 'canceled';
      jobs.delete(id);
      emit('job_removed', { id });
      setTimeout(pump, 30);
      return true;
    },
    async clear_finished() {
      let n = 0;
      for (const j of Array.from(jobs.values())) {
        if (['done', 'error', 'canceled'].includes(j.status)) {
          jobs.delete(j.id);
          emit('job_removed', { id: j.id });
          n++;
        }
      }
      return n;
    },
    async open_file(id) { const j = job(id); toast('info', `(demo) Would open ${j.filepath?.split('\\').pop()}`); return true; },
    async reveal_file(id) { job(id); toast('info', '(demo) Would show the file in Explorer'); return true; },
    async open_output_dir() { toast('info', '(demo) Would open the videos folder'); return true; },
    async open_logs() { toast('info', '(demo) Would open the logs folder'); return true; },
    async choose_output_dir() {
      await sleep(400);
      settings.output_dir = 'D:\\Video\\Reels';
      emit('settings', clone(settings));
      return settings.output_dir;
    },
    async get_settings() { return clone(settings); },
    async set_settings(patch) {
      await sleep(60);
      for (const [k, v] of Object.entries(patch || {})) {
        if (!(k in SETTINGS_RULES)) throw new Error(`Unknown setting: ${k}`);
        if (!SETTINGS_RULES[k](v)) {
          if (k === 'filename_template') throw new Error('The template needs at least one token ({author}, {shortcode}, {date}, {title}) and cannot contain the characters < > : " / \\ | ? *');
          throw new Error('Invalid setting value');
        }
      }
      Object.assign(settings, patch);
      emit('settings', clone(settings));
      if ('concurrency' in patch) setTimeout(pump, 30);
      return clone(settings);
    },
    async update_engine() { runUpdate(true); return null; },
    async login() {
      toast('info', '(demo) Opening the Instagram sign-in window…');
      setTimeout(() => {
        auth = { logged_in: true, user_id: '5839201744', source: 'session', saved_at: now() };
        emit('auth', clone(auth));
        toast('success', 'Signed in, session saved');
      }, 2200 / speed);
      return null;
    },
    async logout() {
      auth = { logged_in: false, user_id: null, source: null, saved_at: null };
      emit('auth', clone(auth));
      return clone(auth);
    },
    async import_cookies_file() {
      await sleep(500);
      auth = { logged_in: true, user_id: '5839201744', source: 'import', saved_at: now() };
      emit('auth', clone(auth));
      return clone(auth);
    },
    async read_clipboard() { return clipboard; },
    async copy_text(text) {
      clipboard = String(text);
      try { await navigator.clipboard.writeText(clipboard); } catch { /* the browser may refuse; the mock clipboard still holds it */ }
      return true;
    },
    async open_url(url) {
      if (!/^https:\/\//.test(url)) return false;
      toast('info', `(demo) Would open ${url}`);
      return true;
    },
    async window_minimize() { toast('info', '(demo) The window would minimize'); return null; },
    async window_toggle_maximize() {
      maximized = !maximized;
      emit('window', { maximized, focused: true });
      return maximized;
    },
    async window_close() { toast('warning', '(demo) The app would close here'); return null; },

    // Test hook for the verification (not part of the bridge): put text into the mock clipboard.
    __setClipboard(text) { clipboard = text; },
  };
}
