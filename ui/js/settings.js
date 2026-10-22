// Settings sheet: every Settings field of BRIDGE_API with English labels, the Appearance group
// (the kit's theme row), the Instagram block (shown in Instagram mode only), engine versions, the
// Updates rows of the kit's update control (js/update.js) and the About block. Grows out of the gear button; changes are saved immediately through set_settings and
// reverted with a toast when the backend rejects them. `mode`, `format` and the `quality_<platform>`
// keys are set from the main window (js/mode.js, js/quality.js), not here.

import { call, on } from './api.js';
import { gel } from '../vendor/liquid_ui/js/glass.js';
import { Overlay } from '../vendor/liquid_ui/js/overlay.js';
import { Segmented, Switch, Stepper, Select } from '../vendor/liquid_ui/js/controls.js';
import { applyTheme, mountThemeControl } from '../vendor/liquid_ui/js/theme.js';
import { icon } from './icons.js';
import { escapeHtml, dateTime } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';
import { getMode, getFormat, onPref } from './mode.js';
import { updateControl, appVersion } from './update.js';

const TOKENS = [
  { t: '{author}', d: 'author' },
  { t: '{shortcode}', d: 'video ID' },
  { t: '{date}', d: 'date' },
  { t: '{title}', d: 'title' },
];
const BROWSERS = [
  { value: '', label: 'Don\'t use' },
  { value: 'firefox', label: 'Firefox' },
  { value: 'edge', label: 'Microsoft Edge' },
  { value: 'chrome', label: 'Google Chrome' },
  { value: 'brave', label: 'Brave' },
  { value: 'opera', label: 'Opera' },
];
const SWITCHES = ['h264_compat', 'skip_existing', 'clipboard_watch', 'open_folder_when_done', 'sound_on_done', 'auto_update'];

let sheet, overlay, body;
let S = null;          // current Settings
let auth = null;
let engine = null;
const ctl = {};        // field -> control

export function initSettings(state) {
  S = state.settings;
  auth = state.auth;
  engine = state.engine;
  sheet = document.getElementById('settings-sheet');
  overlay = new Overlay(sheet, { modal: true, preset: 'sheet' });
  body = sheet.querySelector('.ov-content');
  build();
  const gear = document.getElementById('btn-settings');
  gear.addEventListener('click', () => overlay.toggle(gear));
  on('settings', (s) => {
    S = s;
    sync();
  });
  on('auth', (a) => {
    auth = a;
    renderAuth();
  });
  on('engine', (e) => {
    engine = e;
    renderVersions();
  });
  onPref('mode', renderMode);
  onPref('format', renderExample);
}

export function openSettings() {
  overlay.show(document.getElementById('btn-settings'));
}

// Saves in flight per setting key. While a local change is unconfirmed, the echo of an older value
// (the answer or `settings` event of an earlier save) must not move the control back: rapid stepper
// clicks would otherwise roll the number back and forth.
const inflight = new Map();
const busy = (k) => (inflight.get(k) || 0) > 0;

async function save(patch, revert) {
  const keys = Object.keys(patch);
  keys.forEach((k) => inflight.set(k, (inflight.get(k) || 0) + 1));
  let s = null;
  let failed = null;
  try {
    s = await call('set_settings', patch);
  } catch (err) {
    failed = err;
  } finally {
    keys.forEach((k) => inflight.set(k, inflight.get(k) - 1));
  }
  if (failed) {
    toast('error', failed.message || 'Could not save the setting');
    revert && revert();
  }
  if (s) S = s;
  sync();
}

function row(label, hint, controlHtml = '', cls = '') {
  return `<div class="set-row ${cls}"><div class="set-label"><span>${label}</span>${hint ? `<small>${hint}</small>` : ''}</div><div class="set-ctl">${controlHtml}</div></div>`;
}

function build() {
  body.innerHTML = `
    <header class="sheet-head">
      <h2 id="settings-title">Settings</h2>
      <button type="button" class="icon-btn sheet-close" aria-label="Close settings">${icon('x')}</button>
    </header>
    <div class="sheet-scroll">
      <section class="set-group">
        <h3>Appearance</h3>
        <div class="set-card" data-slot="appearance"></div>
      </section>

      <section class="set-group">
        <h3>Downloads</h3>
        <div class="set-card">
          <div class="set-row set-row-col">
            <div class="set-label"><span>Downloads folder</span></div>
            <div class="set-path">
              <span class="path-text" data-f="output_dir"></span>
              <button type="button" class="btn btn-s" data-act="choose">${icon('folder')}<span>Change…</span></button>
            </div>
          </div>
          <div class="set-row set-row-col">
            <div class="set-label"><span>File name</span><small>Example: <b class="tpl-example"></b></small></div>
            <div class="tpl">
              <input class="field" type="text" data-f="filename_template" spellcheck="false" autocomplete="off" aria-label="File name template">
              <div class="tpl-tokens">${TOKENS.map((x) => `<button type="button" class="token" data-token="${x.t}"><code>${x.t}</code><span>${x.d}</span></button>`).join('')}</div>
            </div>
          </div>
          ${row('H.264 for editing', 'Convert VP9 / AV1 so the file opens right away in Premiere and After Effects; 1440p and 4K take minutes. MP4 only', '<span data-slot="h264_compat"></span>')}
          ${row('Skip downloaded', 'If the file is already in the folder, don\'t download it again', '<span data-slot="skip_existing"></span>')}
          ${row('Simultaneous downloads', 'How many items download in parallel, for both modes', '<span data-slot="concurrency"></span>')}
          ${row('Browser cookies', 'A fallback when a site asks to sign in (private or age-restricted videos)', '<span data-slot="browser_cookies"></span>')}
        </div>
      </section>

      <section class="set-group">
        <h3>Behavior</h3>
        <div class="set-card">
          ${row('Watch the clipboard', 'Offer links from the clipboard when the window is focused', '<span data-slot="clipboard_watch"></span>')}
          ${row('Open folder when done', 'When the whole queue has finished', '<span data-slot="open_folder_when_done"></span>')}
          ${row('Sound when a download finishes', 'A short sound for each finished file', '<span data-slot="sound_on_done"></span>')}
        </div>
      </section>

      <section class="set-group" data-platform="instagram">
        <h3>Instagram</h3>
        <div class="set-card">
          <div class="set-row set-row-col auth-row" data-slot="auth"></div>
          ${row('When to sign in', 'Auto tries without signing in first, then signs in if refused', '<span data-slot="auth_mode"></span>')}
        </div>
      </section>

      <section class="set-group">
        <h3>Engine</h3>
        <div class="set-card">
          ${row('Auto-update engine', 'Check yt-dlp, gallery-dl and FFmpeg on every launch', '<span data-slot="auto_update"></span>')}
          ${row('Update channel', 'Nightly fixes site breakage faster', '<span data-slot="update_channel"></span>')}
          <div class="set-row set-row-col">
            <div class="versions" data-slot="versions"></div>
          </div>
        </div>
      </section>

      <section class="set-group">
        <h3>Updates</h3>
        <div class="set-card" data-slot="updates"></div>
      </section>

      <section class="set-group">
        <h3>About</h3>
        <div class="set-card">
          <div class="set-row">
            <div class="set-label"><span>Media Saver <b class="app-ver"></b></span><small>Downloads Instagram Reels, YouTube, TikTok and X videos as MP4 or MP3</small></div>
          </div>
          ${row('Logs', 'Engine and download logs, for troubleshooting', `<button type="button" class="btn btn-s" data-act="logs">${icon('logs')}<span>Open logs</span></button>`)}
        </div>
      </section>
    </div>`;

  const slot = (n) => body.querySelector(`[data-slot="${n}"]`);
  // Theme: applied at once, saved like every other key; a refusal restores the confirmed theme.
  ctl.theme = mountThemeControl(slot('appearance'), {
    value: S.theme,
    onChange: (name) => {
      applyTheme(name);
      save({ theme: name }, () => {
        ctl.theme.set(S.theme);
        applyTheme(S.theme);
      });
    },
  });
  const mkSwitch = (f, label) => {
    ctl[f] = new Switch({ label, checked: !!S[f], onChange: (v) => save({ [f]: v }, () => ctl[f].set(!v)) });
    slot(f).replaceWith(ctl[f].el);
  };
  mkSwitch('h264_compat', 'H.264 for editing');
  mkSwitch('skip_existing', 'Skip downloaded');
  ctl.concurrency = new Stepper({ label: 'Simultaneous downloads', value: S.concurrency, min: 1, max: 4, onChange: (v) => save({ concurrency: v }) });
  slot('concurrency').replaceWith(ctl.concurrency.el);
  ctl.browser_cookies = new Select({ label: 'Browser cookies', options: BROWSERS, value: S.browser_cookies || '', onChange: (v) => save({ browser_cookies: v }) });
  slot('browser_cookies').replaceWith(ctl.browser_cookies.el);
  mkSwitch('clipboard_watch', 'Watch the clipboard');
  mkSwitch('open_folder_when_done', 'Open folder when done');
  mkSwitch('sound_on_done', 'Sound when a download finishes');
  ctl.auth_mode = new Segmented({
    label: 'When to sign in',
    options: [{ value: 'auto', label: 'Auto' }, { value: 'always', label: 'Always' }, { value: 'never', label: 'Never' }],
    value: S.auth_mode,
    onChange: (v) => save({ auth_mode: v }),
  });
  slot('auth_mode').replaceWith(ctl.auth_mode.el);
  mkSwitch('auto_update', 'Auto-update engine');
  ctl.update_channel = new Segmented({
    label: 'Update channel',
    options: [{ value: 'nightly', label: 'Nightly' }, { value: 'stable', label: 'Stable' }],
    value: S.update_channel,
    onChange: (v) => save({ update_channel: v }),
  });
  slot('update_channel').replaceWith(ctl.update_channel.el);
  // Updates: the kit's rows (status + next action, automatic download); they save their own keys and
  // follow the confirmed settings through the update control.
  updateControl().mountSettings(slot('updates'));

  // Filename template: save on blur / Enter, tokens insert at the caret.
  const tpl = body.querySelector('[data-f="filename_template"]');
  tpl.addEventListener('input', renderExample);
  tpl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') tpl.blur();
  });
  tpl.addEventListener('change', () => {
    const v = tpl.value.trim();
    if (v !== S.filename_template) save({ filename_template: v });
  });
  body.querySelectorAll('.token').forEach((b) => {
    gel(b);
    b.addEventListener('pointerdown', (e) => e.preventDefault()); // keep the caret in the input
    b.addEventListener('click', () => {
      tpl.focus();
      const s = tpl.selectionStart ?? tpl.value.length, en = tpl.selectionEnd ?? s;
      tpl.setRangeText(b.dataset.token, s, en, 'end');
      renderExample();
      save({ filename_template: tpl.value.trim() });
    });
  });

  body.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    try {
      if (act === 'choose') await call('choose_output_dir');
      if (act === 'logs') await call('open_logs');
      if (act === 'login') await call('login');
      if (act === 'logout') {
        await call('logout');
        toast('info', 'Signed out of Instagram, session deleted');
      }
      if (act === 'import') {
        const a = await call('import_cookies_file');
        if (a && a.logged_in) toast('success', 'Cookies imported, signed in');
        else if (a) toast('warning', 'The file has no valid Instagram session');
      }
      if (act === 'update') await call('update_engine');
    } catch (err) {
      toast('error', err.message);
    }
  });
  body.querySelector('.sheet-close').addEventListener('click', () => overlay.close());
  body.querySelectorAll('.btn, .icon-btn').forEach((b) => gel(b));
  sync();
  renderAuth();
  renderVersions();
  renderMode();
}

function renderMode() {
  const mode = getMode();
  body.querySelectorAll('.set-group[data-platform]').forEach((g) => {
    g.hidden = g.dataset.platform !== mode;
  });
  renderExample();
}

// File name example per mode: a platform-typical author and media id.
const EXAMPLE = {
  instagram: ['studio.lumen', 'DAbC123xYz_'],
  youtube: ['Lumen Studio', 'dQ4w9WgXcQ0'],
  tiktok: ['studio.lumen', '7106594312292453675'],
  twitter: ['studio_lumen', '1577855540407197696'],
};

function renderExample() {
  const input = body.querySelector('[data-f="filename_template"]');
  const tpl = input.value || '{author}_{shortcode}';
  const [author, code] = EXAMPLE[getMode()] || EXAMPLE.instagram;
  const ex = tpl
    .replaceAll('{author}', author)
    .replaceAll('{shortcode}', code)
    .replaceAll('{date}', new Date().toISOString().slice(0, 10))
    .replaceAll('{title}', 'Morning light');
  body.querySelector('.tpl-example').textContent = `${ex}.${getFormat() === 'mp3' ? 'mp3' : 'mp4'}`;
}

function sync() {
  if (!S) return;
  body.querySelector('[data-f="output_dir"]').textContent = S.output_dir || '—';
  body.querySelector('[data-f="output_dir"]').title = S.output_dir || '';
  const tpl = body.querySelector('[data-f="filename_template"]');
  if (document.activeElement !== tpl) tpl.value = S.filename_template || '';
  renderExample();
  for (const f of SWITCHES) if (!busy(f)) ctl[f].set(!!S[f]);
  if (!busy('theme') && S.theme) ctl.theme.set(S.theme);
  if (!busy('update_channel')) ctl.update_channel.set(S.update_channel);
  if (!busy('auth_mode')) ctl.auth_mode.set(S.auth_mode || 'auto');
  if (!busy('concurrency')) ctl.concurrency.set(S.concurrency);
  if (!busy('browser_cookies')) ctl.browser_cookies.set(S.browser_cookies || '');
}

function renderAuth() {
  const box = body.querySelector('[data-slot="auth"]');
  const a = auth || { logged_in: false };
  const src = a.source === 'import' ? 'from cookies.txt' : a.source === 'session' ? 'via the sign-in window' : '';
  box.innerHTML = a.logged_in
    ? `<div class="auth-status is-on"><span class="auth-ic">${icon('user')}</span><div><b>Signed in</b><small>${[a.user_id ? `ID ${escapeHtml(a.user_id)}` : '', src, a.saved_at ? `saved ${escapeHtml(dateTime(a.saved_at))}` : ''].filter(Boolean).join(' · ')}</small></div></div>
       <div class="auth-actions"><button type="button" class="btn btn-s" data-act="import">${icon('import')}<span>Import cookies.txt</span></button><button type="button" class="btn btn-s btn-danger" data-act="logout">${icon('logout')}<span>Sign out</span></button></div>`
    : `<div class="auth-status"><span class="auth-ic">${icon('user')}</span><div><b>Not signed in</b><small>Public Reels download without signing in. Sign in if Instagram asks for it.</small></div></div>
       <div class="auth-actions"><button type="button" class="btn btn-tinted btn-s" data-act="login">${icon('user')}<span>Sign in to Instagram</span></button><button type="button" class="btn btn-s" data-act="import">${icon('import')}<span>Import cookies.txt</span></button></div>`;
  box.querySelectorAll('.btn').forEach((b) => gel(b));
}

function renderVersions() {
  const box = body.querySelector('[data-slot="versions"]');
  const v = appVersion();
  body.querySelector('.app-ver').textContent = v ? `v${v}` : '';
  const c = (engine && engine.components) || {};
  const item = (name, x) => x
    ? `<div class="ver ${x.ok ? '' : 'is-bad'}"><span>${name}</span><b>${escapeHtml(x.version || '—')}</b>${x.updated ? '<em>updated</em>' : ''}</div>`
    : `<div class="ver"><span>${name}</span><b>—</b></div>`;
  const busy = engine && ['starting', 'checking', 'downloading', 'updating'].includes(engine.stage);
  box.innerHTML = `${item('yt-dlp', c['yt-dlp'])}${item('gallery-dl', c['gallery-dl'])}${item('FFmpeg', c.ffmpeg)}${item('Deno', c.deno)}
    <div class="ver-foot"><small>${engine && engine.last_check ? `Checked ${escapeHtml(dateTime(engine.last_check))}` : ''}</small>
    <button type="button" class="btn btn-s" data-act="update" ${busy ? 'disabled' : ''}>${icon('refresh')}<span>${busy ? 'Updating…' : 'Update now'}</span></button></div>`;
  box.querySelectorAll('.btn').forEach((b) => gel(b));
}
