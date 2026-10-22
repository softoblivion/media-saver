// Mock backend for UI work in a normal browser. Mirrors docs/BRIDGE_API.md: every method and event,
// with a realistic simulation (engine update on start, previews arriving after enqueue, jobs moving
// through probing -> downloading -> processing -> done with speed and ETA, MP4 and MP3 jobs, all four
// platforms (Instagram, YouTube, TikTok, X), a login_required job, an "already existed" job, canvas
// thumbnails, link cleaning close to app/core/linkclean.py, and the app self-update: check, download
// (by itself while "Download updates automatically" is on, as the core does), apply).
//
// URL parameters (all optional):
//   ?mock=1             force the mock even inside pywebview
//   &demo=1             preload a queue with every state on every platform (done, transcoded, MP3,
//                       already existed, errors, queued with and without a preview)
//   &jobs=N             preload N finished jobs (list performance check; spread over the four
//                       platforms in turn: Instagram, YouTube, TikTok, X)
//   &mode=youtube       start in that mode (also: tiktok, twitter; default: the persisted setting,
//                       "instagram")
//   &format=mp3         start with MP3 as the default format
//   &update=available   the launch check finds 2.1.0 (also: uptodate, error; default uptodate)
//                       "error" fails the first check and finds 2.1.0 on "Try again"
//   &autodownload=0     start with "Download updates automatically" off
//   &hold=1             queued jobs never start (to look at previews and format toggles)
//   &clip=0             start with an empty clipboard (no clipboard prompt)
//   &fast=1             run the simulation 4x faster
//   &offline=1          engine update ends in "offline"

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rand = (a, b) => a + Math.random() * (b - a);
const now = () => Date.now() / 1000;
const clone = (o) => JSON.parse(JSON.stringify(o));

// ---------------------------------------------------------------------------------------------
// Link cleaning (mirror of the canonical rules in docs/ARCHITECTURE.md 5.3)

const SHORTCODE_RE = /^[A-Za-z0-9_-]{5,64}$/;
const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const IG_HOSTS = new Set(['instagram.com', 'www.instagram.com', 'm.instagram.com', 'instagr.am', 'www.instagr.am']);
const YT_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com']);
const YT_SHORT_HOSTS = new Set(['youtu.be', 'www.youtu.be']);
const REEL_SEGS = new Set(['reel', 'reels', 'tv']);
const TT_HOSTS = new Set(['tiktok.com', 'www.tiktok.com', 'm.tiktok.com']);
const TT_SHORT_HOSTS = new Set(['vm.tiktok.com', 'vt.tiktok.com']);
const TT_ID_RE = /^\d{15,25}$/;
const TT_CODE_RE = /^[A-Za-z0-9]{6,20}$/;
const X_HOSTS = new Set(['x.com', 'www.x.com', 'mobile.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com']);
const X_SHORT_HOSTS = new Set(['t.co']);
const X_ID_RE = /^\d{10,25}$/;

function tokens(text) {
  return String(text || '')
    .split(/[\s,;]+/)
    .map((t) => t.replace(/^[<("'\[«]+|[>)"'\]».,!?]+$/g, ''))
    .filter((t) => t && (/^https?:\/\//i.test(t) || /(^|\.)(instagr(am\.com|\.am)|youtube(-nocookie)?\.com|youtu\.be|tiktok\.com|x\.com|twitter\.com|t\.co)\b/i.test(t) || /^www\./i.test(t)));
}

function cleanInstagram(u, item) {
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
  item.kind = kind;
  if (!code || !SHORTCODE_RE.test(code)) {
    item.error = 'no_shortcode';
    return item;
  }
  item.shortcode = code;
  item.clean = `https://www.instagram.com/${kind}/${code}`;
  return item;
}

function cleanYouTube(u, host, item) {
  const seg = u.pathname.split('/').filter(Boolean);
  let kind = 'video';
  let id = null;
  if (YT_SHORT_HOSTS.has(host)) {
    id = seg[0];
  } else if (seg[0] === 'shorts') {
    kind = 'shorts';
    id = seg[1];
  } else if (['embed', 'v', 'live'].includes(seg[0])) {
    id = seg[1];
  } else if (u.searchParams.get('v')) {
    id = u.searchParams.get('v');
  } else {
    item.error = 'unsupported_path';   // playlists, channels, searches, the home page
    return item;
  }
  item.kind = kind;
  if (!id || !YT_ID_RE.test(id)) {
    item.error = 'no_shortcode';
    return item;
  }
  item.shortcode = id;
  item.clean = kind === 'shorts' ? `https://www.youtube.com/shorts/${id}` : `https://www.youtube.com/watch?v=${id}`;
  return item;
}

// TikTok: /@user/video/<id> (kind video) and short links vm. / vt.tiktok.com/<code>, tiktok.com/t/<code>
// (kind share, host kept). Profiles, photo posts and other paths are unsupported_path.
function cleanTikTok(u, host, item) {
  const seg = u.pathname.split('/').filter(Boolean).map((s) => decodeURIComponent(s));
  if (TT_SHORT_HOSTS.has(host) || (seg[0] || '').toLowerCase() === 't') {
    const code = TT_SHORT_HOSTS.has(host) ? seg[0] : seg[1];
    item.kind = 'share';
    if (!code) {
      item.error = 'unsupported_path';
      return item;
    }
    if (!TT_CODE_RE.test(code)) {
      item.error = 'no_shortcode';
      return item;
    }
    item.shortcode = code;
    item.clean = TT_SHORT_HOSTS.has(host) ? `https://${host}/${code}` : `https://www.tiktok.com/t/${code}`;
    return item;
  }
  // /@user/video/<id> or /video/<id> (no user segment)
  const vi = seg.findIndex((s) => s.toLowerCase() === 'video');
  const user = vi === 1 && seg[0].startsWith('@') ? seg[0] : vi === 0 ? '@' : null;
  if (vi < 0 || !user) {
    item.error = 'unsupported_path';
    return item;
  }
  item.kind = 'video';
  const id = seg[vi + 1];
  if (!id || !TT_ID_RE.test(id)) {
    item.error = 'no_shortcode';
    return item;
  }
  item.shortcode = id;
  item.clean = `https://www.tiktok.com/${user}/video/${id}`;
  return item;
}

// X: /<user>/status/<id> (also /statuses/), /i/status/<id>, /i/web/status/<id>; tails like /video/1
// or /photo/1 drop. t.co links are unsupported_path (their target is unknown).
function cleanX(u, host, item) {
  item.platform = 'twitter';
  if (X_SHORT_HOSTS.has(host)) {
    item.error = 'unsupported_path';
    return item;
  }
  const seg = u.pathname.split('/').filter(Boolean).map((s) => decodeURIComponent(s));
  const si = seg.findIndex((s) => /^status(es)?$/i.test(s));
  if (si < 1) {
    item.error = 'unsupported_path';   // profiles, search, home, lists
    return item;
  }
  const head = seg.slice(0, si).map((s) => s.toLowerCase());
  const anon = head[0] === 'i' && (head.length === 1 || (head.length === 2 && head[1] === 'web'));
  if (!anon && si !== 1) {
    item.error = 'unsupported_path';
    return item;
  }
  item.kind = 'post';
  const id = seg[si + 1];
  if (!id || !X_ID_RE.test(id)) {
    item.error = 'no_shortcode';
    return item;
  }
  item.shortcode = id;
  item.clean = anon ? `https://x.com/i/status/${id}` : `https://x.com/${seg[0]}/status/${id}`;
  return item;
}

function cleanOne(original) {
  const item = { original, clean: null, shortcode: null, platform: null, kind: null, error: null };
  let u;
  try {
    u = new URL(/^https?:\/\//i.test(original) ? original : `https://${original}`);
  } catch {
    item.error = 'not_supported';
    return item;
  }
  const host = u.hostname.toLowerCase();
  if (IG_HOSTS.has(host)) {
    item.platform = 'instagram';
    return cleanInstagram(u, item);
  }
  if (YT_HOSTS.has(host) || YT_SHORT_HOSTS.has(host)) {
    item.platform = 'youtube';
    return cleanYouTube(u, host, item);
  }
  if (TT_HOSTS.has(host) || TT_SHORT_HOSTS.has(host)) {
    item.platform = 'tiktok';
    return cleanTikTok(u, host, item);
  }
  if (X_HOSTS.has(host) || X_SHORT_HOSTS.has(host)) {
    return cleanX(u, host, item);
  }
  item.error = 'not_supported';
  return item;
}

export function cleanLinks(text, platform = null) {
  const items = tokens(text).map(cleanOne);
  const seen = new Set();
  let valid = 0, invalid = 0, duplicates = 0;
  const out = [];
  for (const it of items) {
    if (it.clean && platform && it.platform !== platform) it.error = 'other_platform';
    if (!it.clean || it.error) {
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

function makeThumb(seed, wide = false) {
  const W = wide ? 480 : 270;
  const H = wide ? 270 : 480;
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const g = c.getContext('2d');
  const [a, b] = PALETTES[seed % PALETTES.length];
  const grad = g.createLinearGradient(0, 0, W, H);
  grad.addColorStop(0, a);
  grad.addColorStop(1, b);
  g.fillStyle = grad;
  g.fillRect(0, 0, W, H);
  let s = seed * 9301 + 49297;
  const r = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  for (let i = 0; i < 5; i++) {
    g.globalAlpha = 0.25 + r() * 0.3;
    g.fillStyle = i % 2 ? '#ffffff' : '#000000';
    g.beginPath();
    g.arc(r() * W, r() * H, 30 + r() * 110, 0, Math.PI * 2);
    g.fill();
  }
  // Horizon and a simple figure, to read as "a video frame".
  g.globalAlpha = 0.35;
  g.fillStyle = '#0b0b18';
  g.beginPath();
  g.moveTo(0, H * 0.75);
  for (let x = 0; x <= W; x += 30) g.lineTo(x, H * 0.71 + Math.sin(x / 40 + seed) * 18);
  g.lineTo(W, H);
  g.lineTo(0, H);
  g.fill();
  g.globalAlpha = 0.85;
  g.fillStyle = 'rgba(255,255,255,.9)';
  g.beginPath();
  g.arc(W / 2, H * 0.52, 26, 0, Math.PI * 2);
  g.fill();
  g.beginPath();
  g.ellipse(W / 2, H * 0.71, 46, 64, 0, Math.PI, 0);
  g.fill();
  g.globalAlpha = 1;
  return c.toDataURL('image/jpeg', 0.78);
}

// ---------------------------------------------------------------------------------------------
// Sample data

const IG_AUTHORS = ['studio.lumen', 'kitchen_by_mira', 'trail.runner', 'neon_garage', 'dance.lab', 'coffee.atlas',
  'urban.sketch', 'moto.diaries', 'pixel.garden', 'north.waves', 'clay.and.fire', 'tiny.botanist'];
const YT_AUTHORS = ['Lumen Studio', 'Workshop Notes', 'Field Recordings', 'The Long Edit', 'Night Drive Radio',
  'Small Kitchen', 'Practical Engineering Daily', 'Quiet Trails'];
const IG_TITLES = [
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
const YT_TITLES = [
  'Building a walnut desk with hand tools only (full build, no talking)',
  'Why bridges hum in the wind',
  'Lo-fi beats for a rainy evening - 1 hour mix',
  'I cooked every pasta shape in one week',
  'The quietest trail in the Alps, 4K walk',
  'How film cameras meter light, explained in 9 minutes',
  'Synth jam #42: tape delay and a broken drum machine',
  'Restoring a 1970s radio: the whole process',
];

const TT_AUTHORS = ['studio.lumen', 'mira.cooks', 'skate.loops', 'tiny.botanist', 'dance.lab', 'retro.fixes',
  'citybites', 'clay.and.fire'];
const TT_TITLES = [
  'POV: the first coffee of the day #morning',
  'This 15 second pasta trick changed everything',
  'Kickflip, 100 tries later',
  'Watering hack for lazy plant parents',
  'Learn this combo in 30 seconds',
  'Fixing a 1980s Walkman, part 2',
  'Street food tour, stop 4 of 9',
  'Throwing a bowl with my eyes closed',
];
const X_AUTHORS = ['studio_lumen', 'orbit_daily', 'field_notes', 'night_drive', 'harbor_cam', 'quiet_trails'];
const X_TITLES = [
  'The launch from the beach this morning. Sound on.',
  'Timelapse: 6 hours of fog rolling over the bridge',
  'Our new prototype doing its first lap',
  'This is what 120 km/h wind sounds like',
  'Behind the scenes of yesterday\'s shoot',
  'A minute of the harbor at dawn',
];

const DEFAULT_SETTINGS = {
  output_dir: 'C:\\Users\\user\\Videos\\Media-Saver',
  filename_template: '{author}_{shortcode}',
  mode: 'instagram',
  format: 'mp4',
  quality_instagram: 'max',
  quality_youtube: 'max',
  quality_tiktok: 'max',
  quality_twitter: 'max',
  h264_compat: true,
  skip_existing: true,
  concurrency: 2,
  update_channel: 'nightly',
  auto_update: true,
  auto_download_updates: true,
  auth_mode: 'auto',
  browser_cookies: '',
  clipboard_watch: true,
  open_folder_when_done: false,
  sound_on_done: true,
  theme: 'dusk',
};

const APP_VERSION = '2.0.0';
const NEXT_VERSION = '2.1.0';
const NEXT_NOTES = `# Media Saver 2.1.0

## Added
- **Chapters** in YouTube downloads are kept in the MP4 file.
- The queue shows the file size before the download starts.
- New file name token \`{date}\` for the upload date.

## Changed
- MP3 files carry the cover art and the title as tags.
- Faster previews: one request per link instead of two.

## Fixed
- Shorts links with \`?feature=share\` were reported as invalid.
- The window remembers its size on a second monitor.

Full list of changes on [GitHub](https://github.com/softoblivion/media-saver/releases).`;

// ---------------------------------------------------------------------------------------------
// Mock

export function createMock({ params, emit }) {
  const speed = params.get('fast') === '1' ? 4 : 1;
  const hold = params.get('hold') === '1';
  const wait = (ms) => sleep(ms / speed);
  const settings = clone(DEFAULT_SETTINGS);
  if (['youtube', 'tiktok', 'twitter'].includes(params.get('mode'))) settings.mode = params.get('mode');
  if (params.get('format') === 'mp3') settings.format = 'mp3';
  if (params.get('autodownload') === '0') settings.auto_download_updates = false;
  // The theme the page already paints with (index.html reads the same key the app persists).
  try {
    const t = localStorage.getItem('media-saver.theme');
    if (['dusk', 'nova', 'frost', 'bloom'].includes(t)) settings.theme = t;
  } catch {
    // storage unavailable: the default stays
  }
  let auth = { logged_in: false, user_id: null, source: null, saved_at: null };
  let engine = {
    stage: 'starting',
    message: 'Starting the engine…',
    progress: null,
    components: {
      'yt-dlp': { version: '2026.10.12.181023', channel: 'nightly', updated: false, ok: true },
      'gallery-dl': { version: '1.30.9', updated: false, ok: true },
      ffmpeg: { version: '9.0.2', source: 'system', ok: true },
      deno: { version: '2.9.7', source: 'bundled', ok: true },
    },
    last_check: now() - 86400,
  };
  let update = {
    stage: 'idle', current: APP_VERSION, latest: null, notes: null, size: null, published_at: null,
    progress: null, message: '', checked_at: null, can_apply: false,
  };
  const updateFlag = params.get('update') || 'uptodate';
  let updateChecks = 0;
  let updateBusy = false;
  const jobs = new Map();
  let jobCounter = 0;
  let thumbSeed = 1;
  let maximized = false;
  let clipboard = params.get('clip') === '0'
    ? ''
    : 'Look what I found 👉 https://www.instagram.com/reel/DPx7Qm2kLc9/?igsh=MTd4c2FyZ3pxNnB6Ng== and also https://instagram.com/reels/C9vT_aa81Zq?utm_source=ig_web_copy_link and this one https://youtu.be/kJQP7kiw5Fk?si=Qe1x2';
  let updating = false;
  let ready = false;

  const pub = (j) => {
    const c = clone(j);
    delete c._n;
    delete c._token;
    return c;
  };
  const emitJob = (j) => emit('job', pub(j));
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
    set({ stage: 'updating', message: 'Checking gallery-dl, FFmpeg and Deno…', progress: null });
    await wait(900);
    engine.components = {
      'yt-dlp': { version: newVer, channel: settings.update_channel, updated: true, ok: true },
      'gallery-dl': { version: '1.30.10', updated: true, ok: true },
      ffmpeg: { version: '9.0.2', source: 'system', ok: true },
      deno: { version: '2.9.7', source: 'bundled', ok: true },
    };
    set({ stage: 'ready', message: 'Engine ready', progress: null, last_check: now() });
    updating = false;
    ready = true;
    for (const j of jobs.values()) {
      if (j.status === 'queued' && j.stage_text !== 'Queued') {
        j.stage_text = 'Queued';
        emitJob(j);
      }
    }
    if (forced) toast('success', `yt-dlp updated to ${newVer}`);
    pump();
    runPreviews();
  }

  // ---- app self-update simulation -----------------------------------------------------------
  const setUpdate = (patch) => {
    update = { ...update, ...patch };
    emit('update', clone(update));
  };

  async function runCheck() {
    if (updateBusy) return;
    updateBusy = true;
    updateChecks++;
    setUpdate({ stage: 'checking', message: 'Checking for updates…', progress: null });
    await wait(1400);
    updateBusy = false;
    if (updateFlag === 'error' && updateChecks === 1) {
      setUpdate({ stage: 'error', message: 'Could not reach GitHub. Check your internet connection.', checked_at: now() });
      return;
    }
    if (updateFlag === 'available' || updateFlag === 'error') {
      setUpdate({
        stage: 'available', latest: NEXT_VERSION, notes: NEXT_NOTES, size: 12703247, published_at: now() - 2 * 86400,
        message: '', checked_at: now(), progress: null,
      });
      autoDownload();
      return;
    }
    setUpdate({ stage: 'up_to_date', latest: APP_VERSION, notes: null, size: null, published_at: now() - 9 * 86400, message: '', checked_at: now() });
  }

  // Background download (Settings.auto_download_updates), as the core does it: once per `latest`,
  // never again for the same version after an error; turning the setting on starts it.
  let autoDownloadFor = null;
  function autoDownload() {
    if (!settings.auto_download_updates || update.stage !== 'available' || !update.latest) return;
    if (autoDownloadFor === update.latest) return;
    autoDownloadFor = update.latest;
    runDownloadUpdate();
  }

  async function runDownloadUpdate() {
    if (updateBusy || !['available', 'error'].includes(update.stage) || !update.latest) return;
    updateBusy = true;
    setUpdate({ stage: 'downloading', message: '', progress: 0 });
    for (let p = 0; p <= 1.0001; p += 0.05) {
      await wait(180);
      setUpdate({ progress: Math.min(1, p) });
    }
    setUpdate({ stage: 'downloading', progress: null, message: 'Verifying the download…' });
    await wait(700);
    updateBusy = false;
    setUpdate({ stage: 'ready', progress: null, message: '', can_apply: true });
  }

  // ---- jobs -----------------------------------------------------------------------------------
  function newJob(url, shortcode, platform, kind, extra = {}) {
    const id = Math.random().toString(16).slice(2, 10);
    const j = {
      id, url, shortcode, platform, kind, format: settings.format,
      status: 'queued', stage_text: ready ? 'Queued' : 'Waiting for the engine', progress: 0,
      speed_bps: null, eta_s: null, previewed: false, title: null, author: null, duration_s: null, thumbnail: null,
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

  const SAMPLES = {
    instagram: [IG_AUTHORS, IG_TITLES],
    youtube: [YT_AUTHORS, YT_TITLES],
    tiktok: [TT_AUTHORS, TT_TITLES],
    twitter: [X_AUTHORS, X_TITLES],
  };
  // Landscape media: YouTube videos and X posts (the mock's X videos are 16:9).
  const isWide = (j) => (j.platform === 'youtube' && j.kind === 'video') || j.platform === 'twitter';

  function sampleMeta(j) {
    const n = j._n;
    const [authors, titles] = SAMPLES[j.platform] || SAMPLES.instagram;
    const wide = isWide(j);
    const long = j.platform === 'youtube' && j.kind === 'video';
    return {
      author: authors[n % authors.length],
      title: titles[n % titles.length],
      duration_s: long ? Math.round(rand(140, 1400)) : Math.round(rand(8, 58) * 10) / 10,
      thumbnail: makeThumb(thumbSeed++, wide),
    };
  }

  // Preview worker: one job at a time, the first about 0.8 s after enqueue.
  let previewing = false;
  async function runPreviews() {
    if (previewing) return;
    previewing = true;
    await wait(800);
    for (;;) {
      if (!ready) break;   // previews respect the engine gate; runUpdate() restarts the worker
      const next = Array.from(jobs.values())
        .filter((j) => !j.previewed && j.status === 'queued')
        .sort((a, b) => a.created_at - b.created_at)[0];
      if (!next) break;
      const failed = /PRIV|LOGIN|AGE/i.test(next.shortcode || '');
      if (!failed) Object.assign(next, sampleMeta(next));
      next.previewed = true;
      if (jobs.has(next.id)) emitJob(next);
      await wait(380);
    }
    previewing = false;
  }

  const ACTIVE = new Set(['probing', 'downloading', 'processing']);
  const running = () => Array.from(jobs.values()).filter((j) => ACTIVE.has(j.status)).length;

  function pump() {
    if (!ready || hold) return;
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
    if (wasBusy && !busy && settings.open_folder_when_done) toast('info', '(demo) Would open the downloads folder');
    wasBusy = busy;
  }

  const fileName = (j) => {
    const date = new Date(j.created_at * 1000).toISOString().slice(0, 10);
    return settings.filename_template
      .replace('{author}', j.author || 'author')
      .replace('{shortcode}', j.shortcode)
      .replace('{date}', date)
      .replace('{title}', (j.title || '').slice(0, 40).replace(/[\\/:*?"<>|]/g, '')) + (j.format === 'mp3' ? '.mp3' : '.mp4');
  };

  const logPath = (j) => `D:\\Apps\\MediaSaver\\media-saver_data\\logs\\jobs\\${j.id}.log`;

  async function runJob(j) {
    const token = (j._token = Math.random());
    const alive = () => jobs.has(j.id) && j._token === token && j.status !== 'canceled';
    const setJob = (patch) => {
      if (!alive()) return false;
      Object.assign(j, patch);
      emitJob(j);
      return true;
    };
    const n = j._n;
    const yt = j.platform === 'youtube';
    const ig = !j.platform || j.platform === 'instagram';
    const tt = j.platform === 'tiktok';
    const mp3 = j.format === 'mp3';
    const code = j.shortcode || '';
    const wantsLogin = ig && (/LOGIN/i.test(code) || n % 7 === 3);
    const ageGate = yt && (/AGE/i.test(code) || n % 9 === 5);
    const isPrivate = /PRIV/i.test(code);
    const existed = n % 7 === 1 && settings.skip_existing;
    const tall = !isWide(j);
    // Short side from the platform's quality setting, read when the job starts (ARCHITECTURE 5.2):
    // Instagram, TikTok and X low 480 / medium 720 / max 1080; YouTube 360 / 720 / max 2160 for
    // videos, 1080 for Shorts.
    const q = settings[`quality_${j.platform || 'instagram'}`];
    const short = { low: 480, medium: 720, 360: 360, 720: 720 }[q] || (yt && !tall ? 2160 : 1080);
    // YouTube serves AVC up to 1080p only: 1440p / 4K arrive as VP9 / AV1 and need the H.264 pass.
    // TikTok often serves HEVC, which takes the pass too; X serves AVC (no pass).
    const srcCodec = tt ? 'hevc' : j.platform === 'twitter' ? 'avc1' : yt && (n % 5 === 4 || short > 1080) ? 'vp9' : ig && n % 5 === 4 ? 'vp9' : 'avc1';
    const vp9 = srcCodec !== 'avc1';
    const W = tall ? short : Math.round(short * 16 / 9);
    const H = tall ? Math.round(short * 16 / 9) : short;

    if (!setJob({ status: 'probing', stage_text: 'Fetching info', progress: 0.02, error: null, attempt: { engine: 'yt-dlp', auth: ig && auth.logged_in && settings.auth_mode === 'always' ? 'session' : 'anonymous' } })) return;
    await wait(rand(700, 1500));
    if (!alive()) return;

    if (isPrivate) {
      setJob({ status: 'error', stage_text: 'Failed', progress: 0, finished_at: now(),
        error: !ig
          ? { code: 'private', message: 'This video is private. Only people the owner invited can watch it.', can_login: false, log_path: logPath(j) }
          : { code: 'private', message: 'This video is from a private account. You can only download it if you follow the author.', can_login: !auth.logged_in, log_path: logPath(j) } });
      return pump();
    }
    if (wantsLogin && !auth.logged_in) {
      setJob({ stage_text: 'Trying with the saved session', progress: 0.04, attempt: { engine: 'gallery-dl', auth: 'anonymous' } });
      await wait(900);
      setJob({ status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: now(),
        error: { code: 'login_required', message: 'Instagram requires you to sign in to show this video.', can_login: true, log_path: logPath(j) } });
      return pump();
    }
    if (ageGate) {
      setJob({ status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: now(),
        error: { code: 'login_required', message: 'YouTube asks to confirm your age for this video. Choose a browser in Settings > Browser cookies, signed in to YouTube, and retry.', can_login: false, log_path: logPath(j) } });
      return pump();
    }

    const meta = j.previewed && j.title ? {} : sampleMeta(j);
    if (!setJob({
      ...meta,
      previewed: true,
      width: mp3 ? null : W, height: mp3 ? null : H, fps: mp3 ? null : (n % 3 === 2 ? 60 : 30),
      attempt: { engine: 'yt-dlp', auth: wantsLogin ? 'session' : 'anonymous' },
      stage_text: ig || yt ? 'Picking the best format' : 'Fetching info', progress: 0.08,
    })) return;
    await wait(400);

    if (existed) {
      setJob({ status: 'done', stage_text: 'Already downloaded', progress: 1, already_existed: true,
        vcodec: mp3 ? null : 'h264', acodec: mp3 ? 'mp3' : 'aac', filesize: Math.round((mp3 ? rand(0.4, 3) : rand(6, 30)) * 1048576), finished_at: now() });
      j.filepath = `${settings.output_dir}\\${fileName(j)}`;
      emitJob(j);
      return pump();
    }

    let bps = rand(2, 7) * 1048576;
    const aSize = rand(0.3, 0.9) * 1048576 * (yt && !tall ? 6 : 1);
    if (!mp3) {
      // Video then audio, with a jittery speed.
      const vSize = rand(8, 36) * 1048576 * (yt && !tall ? 4 : 1);
      let got = 0;
      while (got < vSize) {
        await wait(250);
        if (!alive()) return;
        bps = Math.max(0.6 * 1048576, bps * rand(0.82, 1.2));
        got = Math.min(vSize, got + bps * 0.25 * speed * (yt && !tall ? 3 : 1));
        const p = 0.08 + 0.7 * (got / vSize);
        setJob({ status: 'downloading', stage_text: 'Downloading video', progress: p, speed_bps: Math.round(bps), eta_s: Math.ceil((vSize - got + aSize) / bps / speed) });
      }
      j._size = vSize;
    }
    let got = 0;
    while (got < aSize) {
      await wait(250);
      if (!alive()) return;
      got = Math.min(aSize, got + bps * 0.25 * speed);
      const p = mp3 ? 0.08 + 0.72 * (got / aSize) : 0.78 + 0.07 * (got / aSize);
      setJob({ status: 'downloading', stage_text: 'Downloading audio', progress: p, speed_bps: Math.round(bps), eta_s: Math.ceil((aSize - got) / bps / speed) + 1 });
    }
    let transcoded = false;
    if (mp3) {
      for (let p = 0; p <= 1; p += 0.125) {
        setJob({ status: 'processing', stage_text: 'Converting to MP3', progress: 0.82 + 0.17 * p, speed_bps: null, eta_s: Math.ceil((1 - p) * 2) || null });
        await wait(260);
        if (!alive()) return;
      }
    } else {
      setJob({ status: 'processing', stage_text: 'Merging tracks', progress: 0.88, speed_bps: null, eta_s: null });
      await wait(800);
      if (!alive()) return;
      if (vp9 && settings.h264_compat) {
        // 1440p / 4K conversions take minutes in the real app; the mock stretches them a little.
        const step = short > 1080 ? 0.04 : 0.1;
        for (let p = 0; p <= 1; p += step) {
          setJob({ stage_text: 'Converting to H.264', progress: 0.9 + 0.09 * p, eta_s: Math.ceil((1 - p) * (short > 1080 ? 240 : 3)) || null });
          await wait(300);
          if (!alive()) return;
        }
        transcoded = true;
      }
    }
    j.filepath = `${settings.output_dir}\\${fileName(j)}`;
    setJob({ status: 'done', stage_text: 'Done', progress: 1, speed_bps: null, eta_s: null,
      vcodec: mp3 ? null : vp9 && !settings.h264_compat ? srcCodec : 'h264', acodec: mp3 ? 'mp3' : 'aac', transcoded,
      filesize: Math.round(mp3 ? aSize * 0.9 : (j._size || 0) + aSize), finished_at: now() });
    delete j._size;
    pump();
  }

  // ---- preloaded queues -----------------------------------------------------------------------
  function preload() {
    const stress = parseInt(params.get('jobs') || '0', 10);
    const t0 = now() - 3600;
    const STRESS = ['instagram', 'youtube', 'tiktok', 'twitter'];
    for (let i = 0; i < stress; i++) {
      const platform = STRESS[i % STRESS.length];
      let code, kind, url;
      if (platform === 'youtube') {
        code = `Yt${(100000 + i).toString(36)}Q${i}`.padEnd(11, 'x').slice(0, 11);
        kind = i % 8 === 1 ? 'video' : 'shorts';
        url = kind === 'video' ? `https://www.youtube.com/watch?v=${code}` : `https://www.youtube.com/shorts/${code}`;
      } else if (platform === 'tiktok') {
        code = `73${String(i).padStart(17, '0')}`;
        kind = 'video';
        url = `https://www.tiktok.com/@${TT_AUTHORS[i % TT_AUTHORS.length]}/video/${code}`;
      } else if (platform === 'twitter') {
        code = `17${String(i).padStart(17, '0')}`;
        kind = 'post';
        url = `https://x.com/${X_AUTHORS[i % X_AUTHORS.length]}/status/${code}`;
      } else {
        code = `Cx${(100000 + i).toString(36)}Q${i}`;
        kind = 'reel';
        url = `https://www.instagram.com/reel/${code}`;
      }
      const [authors, titles] = SAMPLES[platform];
      const j = newJob(url, code, platform, kind, {
        status: 'done', stage_text: 'Done', progress: 1, previewed: true,
        author: authors[i % authors.length],
        title: titles[i % titles.length],
        duration_s: 12 + (i % 40),
        width: 1080, height: 1920, fps: 30, vcodec: 'h264', acodec: 'aac', format: 'mp4',
        filesize: 4e6 + (i % 13) * 1.7e6, transcoded: i % 6 === 0, already_existed: i % 9 === 0, created_at: t0 + i * 10, finished_at: t0 + i * 10 + 8,
      });
      if (isWide(j)) Object.assign(j, { width: 1920, height: 1080 });
      j.thumbnail = makeThumb(i + 100, isWide(j));
      j.filepath = `${settings.output_dir}\\${j.author}_${code}.mp4`;
    }
    if (params.get('demo') !== '1') return;
    const t = now() - 900;
    const ig = (code, extra) => newJob(`https://www.instagram.com/reel/${code}`, code, 'instagram', 'reel', { created_at: t + jobs.size * 30, previewed: true, ...extra });
    const yt = (code, kind, extra) => newJob(kind === 'shorts' ? `https://www.youtube.com/shorts/${code}` : `https://www.youtube.com/watch?v=${code}`,
      code, 'youtube', kind, { created_at: t + jobs.size * 30, previewed: true, ...extra });
    const igBase = (i) => ({ author: IG_AUTHORS[i], title: IG_TITLES[i], duration_s: 14 + i * 3.3, thumbnail: makeThumb(40 + i), width: 1080, height: 1920, fps: 30, acodec: 'aac' });
    const ytBase = (i, wide) => ({ author: YT_AUTHORS[i], title: YT_TITLES[i], duration_s: wide ? 380 + i * 97 : 24 + i * 5, thumbnail: makeThumb(60 + i, wide), width: wide ? 3840 : 1080, height: wide ? 2160 : 1920, fps: wide ? 24 : 30, acodec: 'aac' });
    const fp = (j) => { j.filepath = `${settings.output_dir}\\${fileName(j)}`; return j; };

    // Instagram
    fp(ig('C8kPq1zXmNw', { ...igBase(0), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', filesize: 12.4 * 1048576, finished_at: t + 40 }));
    fp(ig('DA3rT7uYp_2', { ...igBase(1), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', transcoded: true, fps: 60, filesize: 31.9 * 1048576, finished_at: t + 90 }));
    fp(ig('C7mmZ0aQ-Lk', { ...igBase(2), status: 'done', stage_text: 'Already downloaded', progress: 1, vcodec: 'h264', already_existed: true, filesize: 8.1 * 1048576, finished_at: t + 120 }));
    fp(ig('C9audIoTrk1', { ...igBase(6), format: 'mp3', width: null, height: null, fps: null, vcodec: null, acodec: 'mp3', status: 'done', stage_text: 'Done', progress: 1, filesize: 0.9 * 1048576, finished_at: t + 130 }));
    ig('DBLOGINx9Qa', { status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: t + 150, attempt: { engine: 'gallery-dl', auth: 'anonymous' },
      error: { code: 'login_required', message: 'Instagram requires you to sign in to show this video.', can_login: true, log_path: 'D:\\Apps\\MediaSaver\\media-saver_data\\logs\\jobs\\x.log' } });
    ig('C1netw0rkEr', { ...igBase(4), status: 'error', stage_text: 'Network error', progress: 0.31, finished_at: t + 170,
      error: { code: 'network', message: 'The connection dropped. Check your internet and try again.', can_login: false, log_path: 'D:\\Apps\\MediaSaver\\media-saver_data\\logs\\jobs\\y.log' } });
    ig('C2canceled0', { ...igBase(5), status: 'canceled', stage_text: 'Canceled', progress: 0.44, finished_at: t + 180, error: { code: 'canceled', message: 'Canceled', can_login: false, log_path: null } });

    // YouTube
    fp(yt('kJQP7kiw5Fk', 'video', { ...ytBase(0, true), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'vp9', filesize: 486.2 * 1048576, finished_at: t + 60 }));
    fp(yt('Zt9pQe4LmS0', 'shorts', { ...ytBase(1, false), status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', transcoded: true, filesize: 18.7 * 1048576, finished_at: t + 100 }));
    fp(yt('3JZ_D3ELwOQ', 'video', { ...ytBase(2, true), format: 'mp3', width: null, height: null, fps: null, vcodec: null, acodec: 'mp3', status: 'done', stage_text: 'Done', progress: 1, filesize: 56.3 * 1048576, finished_at: t + 140 }));
    yt('AGEgate0001', 'video', { ...ytBase(3, true), status: 'error', stage_text: 'Sign-in required', progress: 0, finished_at: t + 160,
      error: { code: 'login_required', message: 'YouTube asks to confirm your age for this video. Choose a browser in Settings > Browser cookies, signed in to YouTube, and retry.', can_login: false, log_path: 'D:\\Apps\\MediaSaver\\media-saver_data\\logs\\jobs\\z.log' } });

    // A few that start running right away, and some that wait in the queue (previews arrive later).
    jobCounter = 10;
    ['DCrun1aaBb2', 'DCrun2ccDd4', 'DCrun3eeFf6'].forEach((c, i) => newJob(`https://www.instagram.com/reel/${c}`, c, 'instagram', 'reel', { created_at: now() - 3 + i }));
    newJob('https://www.instagram.com/reel/DCqueueA1b2', 'DCqueueA1b2', 'instagram', 'reel', { created_at: now() - 0.5, format: 'mp3' });
    ['M7lc1UVf-VE', 'aqz-KE-bpKQ'].forEach((c, i) => newJob(`https://www.youtube.com/watch?v=${c}`, c, 'youtube', 'video', { created_at: now() - 2 + i }));
    newJob('https://www.youtube.com/shorts/Ks-_Mh1QhMc', 'Ks-_Mh1QhMc', 'youtube', 'shorts', { created_at: now() - 0.2 });

    // TikTok: one done (HEVC source, so the H.264 pass ran), one short link waiting in the queue.
    const ttBase = (i) => ({ author: TT_AUTHORS[i], title: TT_TITLES[i], duration_s: 11 + i * 4.2, thumbnail: makeThumb(80 + i), width: 1080, height: 1920, fps: 30, acodec: 'aac' });
    fp(newJob('https://www.tiktok.com/@studio.lumen/video/7301234567890123456', '7301234567890123456', 'tiktok', 'video',
      { ...ttBase(0), created_at: t + 70, previewed: true, status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', transcoded: true, filesize: 9.6 * 1048576, finished_at: t + 75 }));
    newJob('https://vm.tiktok.com/ZMh7Qx2Lp', 'ZMh7Qx2Lp', 'tiktok', 'share', { created_at: now() - 0.4 });

    // X: one done (AVC, no pass), one the extractor could not read.
    const xBase = (i) => ({ author: X_AUTHORS[i], title: X_TITLES[i], duration_s: 21 + i * 13, thumbnail: makeThumb(90 + i, true), width: 1920, height: 1080, fps: 30, acodec: 'aac' });
    fp(newJob('https://x.com/studio_lumen/status/1712345678901234567', '1712345678901234567', 'twitter', 'post',
      { ...xBase(0), created_at: t + 80, previewed: true, status: 'done', stage_text: 'Done', progress: 1, vcodec: 'h264', filesize: 14.2 * 1048576, finished_at: t + 86 }));
    newJob('https://x.com/i/status/1709876543210987654', '1709876543210987654', 'twitter', 'post', {
      created_at: t + 190, previewed: true, status: 'error', stage_text: 'Failed', progress: 0.06, finished_at: t + 192,
      error: { code: 'extractor', message: 'X changed something and the video could not be read. Try again later.', can_login: false, log_path: 'D:\\Apps\\MediaSaver\\media-saver_data\\logs\\jobs\\w.log' } });
  }
  preload();

  // ---- window focus -> "window" events ------------------------------------------------------
  addEventListener('focus', () => emit('window', { maximized, focused: true }));
  addEventListener('blur', () => emit('window', { maximized, focused: false }));

  const state = () => ({
    app_version: APP_VERSION,
    engine: clone(engine),
    update: clone(update),
    auth: clone(auth),
    settings: clone(settings),
    jobs: Array.from(jobs.values()).sort((a, b) => b.created_at - a.created_at).map(pub),
  });

  const job = (id) => {
    const j = jobs.get(id);
    if (!j) throw new Error('Job not found');
    return j;
  };

  const SETTINGS_RULES = {
    output_dir: (v) => typeof v === 'string' && v.length > 2,
    filename_template: (v) => typeof v === 'string' && /\{shortcode\}/.test(v) && !/[<>:"/\\|?*]/.test(v.replace(/\{[a-z]+\}/g, '')),
    mode: (v) => ['instagram', 'youtube', 'tiktok', 'twitter'].includes(v),
    format: (v) => ['mp4', 'mp3'].includes(v),
    quality_instagram: (v) => ['low', 'medium', 'max'].includes(v),
    quality_youtube: (v) => ['360', '720', 'max'].includes(v),
    quality_tiktok: (v) => ['low', 'medium', 'max'].includes(v),
    quality_twitter: (v) => ['low', 'medium', 'max'].includes(v),
    h264_compat: (v) => typeof v === 'boolean',
    skip_existing: (v) => typeof v === 'boolean',
    concurrency: (v) => Number.isInteger(v) && v >= 1 && v <= 4,
    update_channel: (v) => ['nightly', 'stable'].includes(v),
    auto_update: (v) => typeof v === 'boolean',
    auto_download_updates: (v) => typeof v === 'boolean',
    auth_mode: (v) => ['auto', 'always', 'never'].includes(v),
    browser_cookies: (v) => ['', 'firefox', 'edge', 'chrome', 'brave', 'opera'].includes(v),
    clipboard_watch: (v) => typeof v === 'boolean',
    open_folder_when_done: (v) => typeof v === 'boolean',
    sound_on_done: (v) => typeof v === 'boolean',
    theme: (v) => ['dusk', 'nova', 'frost', 'bloom'].includes(v),
  };

  // ---- the API --------------------------------------------------------------------------------
  return {
    async ready() {
      await sleep(120);
      setTimeout(() => (settings.auto_update ? runUpdate() : (ready = true, engine.stage = 'ready', emit('engine', clone(engine)), pump(), runPreviews())), 500);
      setTimeout(runCheck, 1800 / speed);
      return state();
    },
    async clean_links(text, platform = null) {
      return cleanLinks(text, platform || null);
    },
    async enqueue(text, platform = null, format = null) {
      const res = cleanLinks(text, platform || null);
      const fmt = format || settings.format;
      const added = [];
      const skipped = [];
      const activeKeys = new Set(Array.from(jobs.values())
        .filter((j) => j.status !== 'error' && j.status !== 'canceled')
        .map((j) => `${j.platform}:${j.shortcode}`));
      const seen = new Set();
      for (const it of res.items) {
        if (!it.clean || it.error) {
          skipped.push({ input: it.original, reason: 'invalid' });
          continue;
        }
        const key = `${it.platform}:${it.shortcode}`;
        if (seen.has(key) || activeKeys.has(key)) {
          skipped.push({ input: it.original, reason: 'duplicate' });
          continue;
        }
        seen.add(key);
        added.push(newJob(it.clean, it.shortcode, it.platform, it.kind, { format: fmt }));
      }
      await sleep(180);
      added.forEach((j) => emitJob(j));
      setTimeout(pump, 50);
      if (added.length) runPreviews();
      return { added: added.map(pub), skipped };
    },
    async set_job_format(id, format) {
      if (!['mp4', 'mp3'].includes(format)) throw new Error('Unknown format');
      const j = jobs.get(id);
      if (!j || j.status !== 'queued') return null;
      j.format = format;
      emitJob(j);
      return pub(j);
    },
    async cancel(id) {
      const j = job(id);
      if (!['queued', 'probing', 'downloading', 'processing'].includes(j.status)) return false;
      Object.assign(j, { status: 'canceled', stage_text: 'Canceled', speed_bps: null, eta_s: null, finished_at: now(),
        error: { code: 'canceled', message: 'Canceled', can_login: false, log_path: null } });
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
    async clear_finished(platform = null) {
      let n = 0;
      for (const j of Array.from(jobs.values())) {
        if (platform && j.platform !== platform) continue;
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
    async open_output_dir() { toast('info', `(demo) Would open ${settings.output_dir}`); return true; },
    async open_logs() { toast('info', '(demo) Would open the logs folder'); return true; },
    async choose_output_dir() {
      // The native dialog: alternates between a second plausible folder and the default one, so
      // every try shows a change (toast, "Open folder" title, the settings row).
      await sleep(400);
      const other = 'D:\\Media\\Saved videos';
      settings.output_dir = settings.output_dir === other ? DEFAULT_SETTINGS.output_dir : other;
      emit('settings', clone(settings));
      return settings.output_dir;
    },
    async get_settings() { return clone(settings); },
    async set_settings(patch) {
      await sleep(60);
      for (const [k, v] of Object.entries(patch || {})) {
        if (!(k in SETTINGS_RULES)) throw new Error(`Unknown setting: ${k}`);
        if (!SETTINGS_RULES[k](v)) {
          if (k === 'filename_template') throw new Error('The template must contain {shortcode} and cannot contain the characters < > : " / \\ | ? *');
          throw new Error('Invalid setting value');
        }
      }
      Object.assign(settings, patch);
      emit('settings', clone(settings));
      if ('concurrency' in patch) setTimeout(pump, 30);
      if (patch.auto_download_updates === true) {
        autoDownloadFor = null;
        setTimeout(autoDownload, 0);
      }
      return clone(settings);
    },
    async update_engine() { runUpdate(true); return null; },
    async update_status() { return clone(update); },
    async check_updates() {
      const at = clone(update);
      setTimeout(runCheck, 0);
      return at;
    },
    async download_update() { runDownloadUpdate(); return null; },
    async apply_update() {
      if (!update.can_apply) throw new Error('The update is not ready yet');
      toast('info', `(demo) The app would close now and restart on ${update.latest}`);
      return null;
    },
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
