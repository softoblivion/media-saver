// Entry point: scene, material, bridge, components, entrance choreography.

import { connect, call, on, backendMode } from './api.js';
import { initScene, setFocused, activity } from './scene.js';
import { initLight, gel, Lens } from '../vendor/liquid_ui/js/glass.js';
import { SPRINGS, springAnimate, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { icon, appGlyph, SERVICE_GLYPH } from './icons.js';
import { nItems } from './format.js';
import { initToasts, toast } from '../vendor/liquid_ui/js/toast.js';
import { initEngine } from './engine.js';
import { initComposer, focusInput } from './composer.js';
import { initQueue, getJobs, retryLoginFailures } from './queue.js';
import { initSettings } from './settings.js';
import { initClipboard } from './clipboard.js';
import { initUpdate } from './update.js';
import { initSound } from './sound.js';
import { initPrefs, getMode, setMode, onPref, PLATFORMS, PLATFORM_NAME } from './mode.js';
import { Segmented } from '../vendor/liquid_ui/js/controls.js';
import { applyTheme } from '../vendor/liquid_ui/js/theme.js';
import { initQuality } from './quality.js';

// The theme the page paints with before the bridge answers: the inline script of index.html reads
// this key, so a restart shows the saved theme without a flash of the default one.
const THEME_KEY = 'media-saver.theme';

/** Apply a confirmed settings object's theme and remember it for the next launch. */
function syncTheme(settings) {
  if (!settings || !settings.theme) return;
  const name = applyTheme(settings.theme);
  try {
    localStorage.setItem(THEME_KEY, name);
  } catch {
    // storage unavailable: the next launch starts from the default and the bridge corrects it
  }
}

function initTitlebar() {
  document.querySelectorAll('.ic-slot[data-icon]').forEach((s) => (s.outerHTML = icon(s.dataset.icon)));
  document.getElementById('tb-glyph').innerHTML = appGlyph(20);
  const gear = document.getElementById('btn-settings');
  gear.innerHTML = icon('gear');
  new Lens(gear, 'pill');
  gel(gear);
  // The window has a fixed size: only Minimize and Close (no maximize, no double-click on the drag area).
  const win = {
    'win-min': ['min', 'window_minimize'],
    'win-close': ['close', 'window_close'],
  };
  for (const [id, [ic, method]] of Object.entries(win)) {
    const b = document.getElementById(id);
    b.innerHTML = icon(ic);
    b.addEventListener('click', async () => {
      try {
        await call(method);
      } catch (err) {
        toast('error', err.message);
      }
    });
  }
}

/** Mode switch in the title bar: it scopes the whole window (composer and queue). One service
 *  glyph per option (js/icons.js `svc*`); the name stays in aria-label and the tooltip. */
function initModeSwitch() {
  const seg = new Segmented({
    label: 'Mode',
    cls: 'seg-tb seg-svc',
    options: PLATFORMS.map((p) => ({ value: p, label: PLATFORM_NAME[p], title: `${PLATFORM_NAME[p]} (Alt+${PLATFORMS.indexOf(p) + 1})` })),
    value: getMode(),
    onChange: (v) => setMode(v),
  });
  seg.el.id = 'mode-switch';
  // The kit escapes option labels as text: swap each label for its glyph after construction.
  for (const b of seg.opts) {
    b.setAttribute('aria-label', PLATFORM_NAME[b.dataset.v]);
    b.innerHTML = icon(SERVICE_GLYPH[b.dataset.v]);
  }
  // The droplet is placed by the control's ResizeObserver, whose first callback runs once the
  // switch is laid out in the title bar, so it measures the icon-sized options, not the text labels.
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
  initScene();
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

  syncTheme(state.settings);
  on('settings', syncTheme);
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
