// The Scale setting (the kit's documentation, "Scale"): a settings row with a segmented control
// of five steps, 100 / 125 / 150 / 175 / 200 %. 100 % is the system's own scaling; a step
// multiplies on top of it. The app applies the value through its bridge (`set_scale(value)`,
// settings key `ui_scale`): the whole window scales, content and window size together, the
// logical layout stays fixed. The control only renders and reports; it scales nothing itself.

import { Segmented } from './controls.js';

export const SCALE_STEPS = [100, 125, 150, 175, 200];
export const DEFAULT_SCALE = 100;

/** The nearest allowed step for any value (a number or a numeric string); 100 for junk. */
export function normalizeScale(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_SCALE;
  let best = SCALE_STEPS[0];
  for (const s of SCALE_STEPS) if (Math.abs(s - n) < Math.abs(best - n)) best = s;
  return best;
}

/**
 * Append the Scale row to `host` (a .set-card). Returns { el, control, set(v), setDisabled(off),
 * destroy() }. `onChange(value)` fires on a user choice with the step as a number.
 */
export function mountScaleControl(host, { value = DEFAULT_SCALE, onChange, label = 'Scale', hint = 'Size of the window and its text' } = {}) {
  const row = document.createElement('div');
  row.className = 'set-row';
  row.dataset.row = 'ui_scale';
  row.innerHTML = `<div class="set-label"><span>${label}</span><small>${hint}</small></div><div class="set-ctl"></div>`;
  const control = new Segmented({
    label,
    cls: 'seg-p seg-scale',
    options: SCALE_STEPS.map((s) => ({ value: String(s), label: `${s} %` })),
    value: String(normalizeScale(value)),
    onChange: (v) => onChange && onChange(Number(v)),
  });
  row.querySelector('.set-ctl').append(control.el);
  host.append(row);
  return {
    el: row,
    control,
    set(v, user = false) {
      control.set(String(normalizeScale(v)), user);
    },
    setDisabled(off, title) {
      control.setDisabled(off, title);
    },
    destroy() {
      control.destroy();
      row.remove();
    },
  };
}
