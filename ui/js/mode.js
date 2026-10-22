// The global choices of the main window, all stored in Settings: the mode (platform shown:
// "instagram" | "youtube" | "tiktok" | "twitter"), the default format of new jobs ("mp4" | "mp3")
// and the video quality of each platform (`quality_<platform>`). This module keeps the current values,
// applies a change locally at once (so the UI re-renders without waiting for the backend), persists
// it through set_settings and reverts it when the backend refuses.

import { call, on } from './api.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';

/** Platform ids in the order of the mode switch (Alt+1..Alt+4). The id `twitter` is shown as "X". */
export const PLATFORMS = ['instagram', 'youtube', 'tiktok', 'twitter'];
export const FORMATS = ['mp4', 'mp3'];
export const PLATFORM_NAME = { instagram: 'Instagram', youtube: 'YouTube', tiktok: 'TikTok', twitter: 'X' };
/** Allowed quality values, the same four steps for every platform and both formats
 *  (docs/BRIDGE_API.md, Settings): MP4 by resolution, MP3 by bitrate. */
export const QUALITY_STEPS = ['low', 'medium', 'high', 'max'];
export const QUALITIES = Object.fromEntries(PLATFORMS.map((p) => [p, QUALITY_STEPS]));
export const QUALITY_KEY = Object.fromEntries(PLATFORMS.map((p) => [p, `quality_${p}`]));

const VALID = {
  mode: (v) => PLATFORMS.includes(v),
  format: (v) => FORMATS.includes(v),
  ...Object.fromEntries(PLATFORMS.map((p) => [QUALITY_KEY[p], (v) => QUALITIES[p].includes(v)])),
};
const DEFAULTS = {
  mode: 'instagram',
  format: 'mp4',
  ...Object.fromEntries(PLATFORMS.map((p) => [QUALITY_KEY[p], 'max'])),
};
// Keys mirrored on <html> as data attributes (CSS hooks).
const DATASET = new Set(['mode', 'format']);

const state = { ...DEFAULTS };
const subs = Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, new Set()]));
const inflight = Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, 0]));   // local changes not yet confirmed

export const getMode = () => state.mode;
export const getFormat = () => state.format;
/** Quality setting of a platform (default: the current mode). */
export const getQuality = (platform = state.mode) => state[QUALITY_KEY[platform]];

/** Subscribe to changes of "mode", "format" or a "quality_<platform>" key. Returns an unsubscribe function. */
export function onPref(key, fn) {
  subs[key].add(fn);
  return () => subs[key].delete(fn);
}

function apply(key, value) {
  if (state[key] === value) return false;
  state[key] = value;
  if (DATASET.has(key)) document.documentElement.dataset[key] = value;
  for (const fn of Array.from(subs[key])) {
    try {
      fn(value);
    } catch (err) {
      console.error(`[ui] ${key} subscriber failed`, err);
    }
  }
  return true;
}

export function initPrefs(settings) {
  const s = settings || {};
  for (const k of Object.keys(DEFAULTS)) state[k] = VALID[k](s[k]) ? s[k] : DEFAULTS[k];
  for (const k of DATASET) document.documentElement.dataset[k] = state[k];
  on('settings', (next) => {
    if (!next) return;
    // While a local change is in flight, the echo of an older value must not flip the UI back.
    for (const k of Object.keys(DEFAULTS)) {
      if (!inflight[k] && VALID[k](next[k])) apply(k, next[k]);
    }
  });
}

async function persist(key, value, prev) {
  inflight[key]++;
  try {
    await call('set_settings', { [key]: value });
  } catch (err) {
    if (state[key] === value) apply(key, prev);
    toast('error', err.message || 'Could not save the setting');
  } finally {
    inflight[key]--;
  }
}

function change(key, value) {
  if (!VALID[key](value)) return;
  const prev = state[key];
  if (apply(key, value)) persist(key, value, prev);
}

export const setMode = (mode) => change('mode', mode);
export const setFormat = (format) => change('format', format);
export const setQuality = (platform, value) => QUALITY_KEY[platform] && change(QUALITY_KEY[platform], value);
