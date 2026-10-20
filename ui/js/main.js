// Entry point: scene, material, bridge, components, entrance choreography.

import { connect, call, on, backendMode } from './api.js';
import { initBackground, setFocused, activity } from './background.js';
import { initLight, gel, Lens } from './glass.js';
import { SPRINGS, springAnimate, reducedMotion } from './motion.js';
import { icon, appGlyph } from './icons.js';
import { initToasts, toast } from './toast.js';
import { initEngine } from './engine.js';
import { initComposer, focusInput } from './composer.js';
import { initQueue, getJobs, retryLoginFailures } from './queue.js';
import { initSettings } from './settings.js';
import { initClipboard } from './clipboard.js';

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

  initEngine(state.engine);
  initComposer();
  initQueue(state);
  initSettings(state);
  initClipboard(state.settings, getJobs);

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
      if (n) toast('info', `Retrying after sign-in: ${n} ${n === 1 ? 'video' : 'videos'}`);
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
