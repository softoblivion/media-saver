// Quality in the main window: one Segmented per platform in the composer's spec row (above the
// format toggle and Download), only the current mode's one visible, so each platform keeps its own
// value and a mode switch swaps options and value at once. Values are Settings `quality_<platform>`
// (instagram, youtube, tiktok, twitter), the same four steps everywhere ("low" | "medium" | "high" |
// "max"), written through js/mode.js (optimistic, reverted with a toast when refused, stale echoes
// ignored). The steps apply to both formats: MP4 caps the resolution, MP3 picks the bitrate; the
// control stays active in MP3 and only the hint and the tooltips switch to the bitrate wording.

import { on } from './api.js';
import { Segmented } from '../vendor/liquid_ui/js/controls.js';
import { SPRINGS, springAnimate, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { getMode, getFormat, getQuality, setQuality, onPref, PLATFORMS, PLATFORM_NAME, QUALITY_KEY, QUALITY_STEPS } from './mode.js';

const LABEL = { low: 'Low', medium: 'Medium', high: 'High', max: 'Max' };

// What the best MP4 of each platform reaches (Max tooltip).
const MP4_BEST = {
  instagram: 'the best the Reel has',
  youtube: 'the best the video has, up to 4K',
  tiktok: 'the best the video has, up to 1080p',
  twitter: 'the best the post has, up to 1080p',
};

// hint: the short line right of the control; title: the option tooltip.
const MP4 = {
  low: { hint: '≤ 480p', title: 'Low: up to 480p, the smallest files' },
  medium: { hint: '≤ 720p', title: 'Medium: up to 720p' },
  high: { hint: '≤ 1080p', title: 'High: up to 1080p' },
  max: { hint: 'Best', title: null },   // per platform, from MP4_BEST
};
const MP3 = {
  low: { hint: '128 kbps', title: 'Low: MP3 at 128 kbps, the smallest files' },
  medium: { hint: '192 kbps', title: 'Medium: MP3 at 192 kbps' },
  high: { hint: '256 kbps', title: 'High: MP3 at 256 kbps' },
  max: { hint: '320 kbps', title: 'Max: MP3 at 320 kbps' },
};
const YT_H264_NOTE = '. With H.264 for editing on, 1440p and 4K are converted to H.264, which takes minutes';

let row, hint;
let h264 = true;
const segs = {};

/** Tooltip and hint of one step for a platform in the current format. */
function spec(platform, value) {
  if (getFormat() === 'mp3') return MP3[value];
  const s = MP4[value];
  if (value !== 'max') return s;
  let title = `Max: ${MP4_BEST[platform]}`;
  if (platform === 'youtube' && h264) title += YT_H264_NOTE;
  return { hint: s.hint, title };
}

export function initQuality(settings) {
  row = document.getElementById('cmp-spec');
  hint = row.querySelector('.spec-hint');
  h264 = !(settings && settings.h264_compat === false);
  const slot = document.getElementById('quality-slot');
  for (const p of PLATFORMS) {
    const seg = new Segmented({
      label: `${PLATFORM_NAME[p]} quality`,
      cls: 'seg-q',
      options: QUALITY_STEPS.map((value) => ({ value, label: LABEL[value] })),
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
  onPref('format', () => {
    renderTitles();
    render(false);
  });
  renderTitles();
  render(false);
}

/** Option tooltips of every platform in the current format. */
function renderTitles() {
  for (const p of PLATFORMS) {
    for (const v of QUALITY_STEPS) {
      // The kit's Segmented sets an option's tooltip through setOptionDisabled (kept enabled here).
      segs[p].setOptionDisabled(v, false, spec(p, v).title);
    }
  }
  renderHint();
}

function renderHint() {
  const mode = getMode();
  const s = spec(mode, getQuality(mode));
  hint.textContent = s.hint;
  hint.title = s.title;
}

function render(swap) {
  const mode = getMode();
  for (const p of PLATFORMS) {
    const seg = segs[p];
    const show = p === mode;
    seg.el.hidden = !show;
    // Transform only: the droplet inside is a lens, so no opacity on its ancestors.
    if (show && swap && !reducedMotion.matches) {
      springAnimate(seg.el, [{ transform: 'translateY(5px) scale(0.96)' }, { transform: 'none' }], SPRINGS.bouncy, { fill: 'none' });
    }
  }
  renderHint();
}
