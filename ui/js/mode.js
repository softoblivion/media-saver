// The global choices of the main window, all stored in Settings: the mode (platform shown:
// "instagram" | "youtube"), the default format of new jobs ("mp4" | "mp3") and the video quality of
// each platform (`quality_instagram`, `quality_youtube`). This module keeps the current values,
// applies a change locally at once (so the UI re-renders without waiting for the backend), persists
// it through set_settings and reverts it when the backend refuses.

import { call, on } from './api.js';
import { toast } from './toast.js';

export const PLATFORMS = ['instagram', 'youtube'];
export const FORMATS = ['mp4', 'mp3'];
export const PLATFORM_NAME = { instagram: 'Instagram', youtube: 'YouTube' };
/** Allowed quality values per platform (docs/BRIDGE_API.md, Settings). */
export const QUALITIES = { instagram: ['low', 'medium', 'max'], youtube: ['360', '720', 'max'] };
export const QUALITY_KEY = { instagram: 'quality_instagram', youtube: 'quality_youtube' };

const VALID = {
  mode: (v) => PLATFORMS.includes(v),
  format: (v) => FORMATS.includes(v),
  quality_instagram: (v) => QUALITIES.instagram.includes(v),
  quality_youtube: (v) => QUALITIES.youtube.includes(v),
};
const DEFAULTS = { mode: 'instagram', format: 'mp4', quality_instagram: 'max', quality_youtube: 'max' };
// Keys mirrored on <html> as data attributes (CSS hooks).
const DATASET = new Set(['mode', 'format']);

const state = { ...DEFAULTS };
const subs = Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, new Set()]));
const inflight = Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, 0]));   // local changes not yet confirmed

export const getMode = () => state.mode;
export const getFormat = () => state.format;
/** Quality setting of a platform (default: the current mode). */
export const getQuality = (platform = state.mode) => state[QUALITY_KEY[platform]];
export const otherPlatform = (p) => (p === 'youtube' ? 'instagram' : 'youtube');

/** Subscribe to changes of "mode", "format", "quality_instagram" or "quality_youtube". Returns an unsubscribe function. */
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
