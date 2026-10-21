// Entry point: scene, material, bridge, components, entrance choreography.

import { connect, call, on, backendMode } from './api.js';
import { initBackground, setFocused, activity } from './background.js';
import { initLight, gel, Lens } from './glass.js';
import { SPRINGS, springAnimate, reducedMotion } from './motion.js';
import { icon, appGlyph } from './icons.js';
import { nItems } from './format.js';
import { initToasts, toast } from './toast.js';
import { initEngine } from './engine.js';
import { initComposer, focusInput } from './composer.js';
import { initQueue, getJobs, retryLoginFailures } from './queue.js';
import { initSettings } from './settings.js';
import { initClipboard } from './clipboard.js';
import { initUpdate } from './update.js';
import { initSound } from './sound.js';
import { initPrefs, getMode, setMode, onPref, PLATFORMS, PLATFORM_NAME } from './mode.js';
import { Segmented } from './controls.js';
import { initQuality } from './quality.js';

function initTitlebar() {
  document.querySelectorAll('.ic-slot[data-icon]').forEach((s) => (s.outerHTML = icon(s.dataset.icon)));
  document.getElementById('tb-glyph').innerHTML = appGlyph(20);
  const gear = document.getElementById('btn-settings');
  gear.innerHTML = icon('gear');
  new Lens(gear, 'pill');
  gel(gear);
  const win = {
    'win-min': ['min', 'window_minimize'],
    'win-max': ['max', 'window_toggle_maximize'],
    'win-close': ['close', 'window_close'],
  };
  for (const [id, [ic, method]] of Object.entries(win)) {
    const b = document.getElementById(id);
    b.innerHTML = icon(ic);
    b.addEventListener('click', async () => {
      try {
        const r = await call(method);
        if (method === 'window_toggle_maximize') setMaximized(!!r);
      } catch (err) {
        toast('error', err.message);
      }
    });
  }
  // The frameless window has no native caption: double-click on the drag area toggles maximize.
  document.querySelectorAll('.pywebview-drag-region').forEach((d) =>
    d.addEventListener('dblclick', () => call('window_toggle_maximize').then((r) => setMaximized(!!r)).catch(() => {})));
}

/** Mode switch in the title bar: it scopes the whole window (composer and queue). */
function initModeSwitch() {
  const seg = new Segmented({
    label: 'Mode',
    cls: 'seg-tb',
    options: PLATFORMS.map((p) => ({ value: p, label: PLATFORM_NAME[p], title: `${PLATFORM_NAME[p]} links and queue (Alt+${PLATFORMS.indexOf(p) + 1})` })),
    value: getMode(),
    onChange: (v) => setMode(v),
  });
  seg.el.id = 'mode-switch';
  document.getElementById('mode-slot').appendChild(seg.el);
  onPref('mode', (m) => seg.set(m));
  // Alt+1..Alt+4 switch modes from anywhere (in the order of PLATFORMS). e.code also catches layouts
  // where Alt+digit produces another character.
  addEventListener('keydown', (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const m = /^Digit([1-9])$/.exec(e.code || '') || /^([1-9])$/.exec(e.key || '');
    const i = m ? Number(m[1]) - 1 : -1;
    if (i < 0 || i >= PLATFORMS.length) return;
    e.preventDefault();
    setMode(PLATFORMS[i]);
  });
}

function setMaximized(m) {
  document.documentElement.classList.toggle('is-maximized', m);
  const b = document.getElementById('win-max');
  b.innerHTML = icon(m ? 'restore' : 'max');
  b.setAttribute('aria-label', m ? 'Restore' : 'Maximize');
}

function entrance() {
  if (reducedMotion.matches) return;
  const parts = [
    [document.getElementById('titlebar'), 0],
    [document.getElementById('composer'), 60],
    [document.getElementById('q-head'), 130],
    [document.getElementById('q-scroll'), 190],
  ];
  for (const [el, delay] of parts) {
    // Opacity only on surfaces that are lenses themselves: opacity < 1 on a container would make
    // it a backdrop root and its glass children would briefly lose the scene behind them.
    const own = !!el.__lens;
    springAnimate(el, [
      { opacity: own ? 0 : 1, transform: 'translateY(14px) scale(0.97)' },
      { opacity: 1, transform: 'none' },
    ], SPRINGS.bouncy, { delay, fill: 'backwards' });
    if (el.__lens) {
      el.__lens.setStrength(0);
      setTimeout(() => el.__lens.animateStrength(1), delay);
    }
  }
}

async function boot() {
  initBackground();
  initLight();
  initToasts();
  initTitlebar();

  let state;
  try {
    state = await connect();
  } catch (err) {
    console.error(err);
    toast('error', `Could not connect to the app: ${err.message}`);
    return;
  }
  document.documentElement.dataset.backend = backendMode();

  initPrefs(state.settings);
  initModeSwitch();
  initEngine(state.engine);
  initUpdate(state, {
    activeJobs: () => Array.from(getJobs()).some((j) => ['probing', 'downloading', 'processing'].includes(j.status)),
  });
  initComposer();
  initQuality(state.settings);
  initQueue(state);
  initSettings(state);
  initClipboard(state.settings, getJobs);
  initSound(state);

  on('toast', (t) => t && toast(t.level || 'info', t.text));
  on('window', (w) => {
    if (!w) return;
    setMaximized(!!w.maximized);
    setFocused(!!w.focused);
    if (w.focused) activity();
  });
  let loggedIn = !!(state.auth && state.auth.logged_in);
  on('auth', (a) => {
    const now = !!(a && a.logged_in);
    if (now && !loggedIn) {
      const n = retryLoginFailures();
      if (n) toast('info', `Retrying after sign-in: ${nItems(n, 'instagram')}`);
    }
    loggedIn = now;
  });

  document.body.classList.add('is-ready');
  entrance();
  if (backendMode() === 'mock') console.info('[ui] running against mock-api.js');
  setTimeout(focusInput, 400);
}

// Unhandled promise rejections from bridge calls become toasts instead of silent failures.
addEventListener('unhandledrejection', (e) => {
  const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
  toast('error', msg);
});

boot();
