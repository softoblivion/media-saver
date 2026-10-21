// Notification sound when a download finishes (Settings.sound_on_done, default on). Plays on the
// transition of a job to `done` with a new file (not for "Already downloaded", not for jobs restored
// from history), at most once per second so a batch finishing together sounds once. The file is
// local (assets/sounds), so the page still makes no network request.

import { on } from './api.js';

const SRC = './assets/sounds/download-done.wav';
const MIN_GAP_MS = 1000;
const VOLUME = 0.7;

let enabled = true;
let audio = null;
let lastAt = 0;
const status = new Map();   // job id -> last seen status

export function initSound(state) {
  enabled = !(state.settings && state.settings.sound_on_done === false);
  for (const j of state.jobs || []) status.set(j.id, j.status);
  on('settings', (s) => {
    if (s && typeof s.sound_on_done === 'boolean') enabled = s.sound_on_done;
  });
  on('job', (j) => {
    if (!j || !j.id) return;
    const prev = status.get(j.id);
    status.set(j.id, j.status);
    if (j.status === 'done' && prev && prev !== 'done' && !j.already_existed) play();
  });
  on('job_removed', ({ id }) => status.delete(id));
}

function play() {
  if (!enabled) return;
  const now = Date.now();
  if (now - lastAt < MIN_GAP_MS) return;
  lastAt = now;
  try {
    if (!audio) {
      audio = new Audio(SRC);
      audio.volume = VOLUME;
    }
    audio.currentTime = 0;
    const p = audio.play();
    if (p && p.catch) p.catch((err) => console.warn('[ui] notification sound failed', err));
  } catch (err) {
    console.warn('[ui] notification sound failed', err);
  }
}
