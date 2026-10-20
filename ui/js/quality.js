// Video quality in the main window: one Segmented per platform in the composer's spec row (above
// the format toggle and Download), only the current mode's one visible, so each platform keeps its
// own value and a mode switch swaps options and value at once. Values are Settings
// `quality_instagram` / `quality_youtube`, written through js/mode.js (optimistic, reverted with a
// toast when refused, stale echoes ignored). MP3 ignores quality: the control stays in place,
// dimmed and disabled, so the layout does not jump.

import { on } from './api.js';
import { Segmented } from './controls.js';
import { SPRINGS, springAnimate, reducedMotion } from './motion.js';
import { getMode, getFormat, getQuality, setQuality, onPref, PLATFORMS, PLATFORM_NAME, QUALITY_KEY } from './mode.js';

const MP4_ONLY = 'MP4 only';

// hint: the short line right of the control; title: the option tooltip.
const OPTIONS = {
  instagram: [
    { value: 'low', label: 'Low', hint: '≤ 480p', title: 'Low: up to 480p, the smallest files' },
    { value: 'medium', label: 'Medium', hint: '≤ 720p', title: 'Medium: up to 720p' },
    { value: 'max', label: 'Max', hint: 'Best', title: 'Max: the best the Reel has' },
  ],
  youtube: [
    { value: '360', label: '360p', hint: '≤ 360p', title: '360p: up to 360p, the smallest files' },
    { value: '720', label: '720p', hint: '≤ 720p', title: '720p: up to 720p' },
    { value: 'max', label: 'Max', hint: 'Best, up to 4K', title: 'Max: the best the video has, up to 4K' },
  ],
};
const YT_MAX_H264 = 'Max: the best the video has, up to 4K. With H.264 for editing on, 1440p and 4K are converted to H.264, which takes minutes';

let row, hint;
let h264 = true;
const segs = {};

export function initQuality(settings) {
  row = document.getElementById('cmp-spec');
  hint = row.querySelector('.spec-hint');
  h264 = !(settings && settings.h264_compat === false);
  const slot = document.getElementById('quality-slot');
  for (const p of PLATFORMS) {
    const seg = new Segmented({
      label: `${PLATFORM_NAME[p]} video quality`,
      cls: 'seg-q',
      options: OPTIONS[p].map(({ value, label, title }) => ({ value, label, title })),
      value: getQuality(p),
      onChange: (v) => {
        setQuality(p, v);
        renderHint();
      },
    });
    seg.el.dataset.platform = p;
    segs[p] = seg;
    slot.before(seg.el);
    onPref(QUALITY_KEY[p], (v) => {
      seg.set(v);
      renderHint();
    });
  }
  slot.remove();
  on('settings', (s) => {
    if (!s || typeof s.h264_compat !== 'boolean' || s.h264_compat === h264) return;
    h264 = s.h264_compat;
    renderTitles();
  });
  onPref('mode', () => render(true));
  onPref('format', () => render(false));
  renderTitles();
  render(false);
}

function renderTitles() {
  segs.youtube.setOptionTitle('max', h264 ? YT_MAX_H264 : OPTIONS.youtube[2].title);
  renderHint();
}

function renderHint() {
  const mode = getMode();
  const mp3 = getFormat() === 'mp3';
  const op = OPTIONS[mode].find((o) => o.value === getQuality(mode)) || OPTIONS[mode][2];
  hint.textContent = mp3 ? MP4_ONLY : op.hint;
  hint.title = mp3 ? 'MP3 is audio only; quality applies to MP4' : (mode === 'youtube' && op.value === 'max' && h264 ? YT_MAX_H264 : op.title);
}

function render(swap) {
  const mode = getMode();
  const mp3 = getFormat() === 'mp3';
  row.classList.toggle('is-off', mp3);
  for (const p of PLATFORMS) {
    const seg = segs[p];
    const show = p === mode;
    seg.el.hidden = !show;
    seg.setDisabled(mp3, MP4_ONLY);
    // Transform only: the droplet inside is a lens, so no opacity on its ancestors.
    if (show && swap && !reducedMotion.matches) {
      springAnimate(seg.el, [{ transform: 'translateY(5px) scale(0.96)' }, { transform: 'none' }], SPRINGS.bouncy, { fill: 'none' });
    }
  }
  renderHint();
}
