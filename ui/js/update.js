// App self-update UI: Media Saver's mount of the kit's update control (vendor/liquid_ui/js/update.js,
// the version pill in the title bar, its popover, the update toasts and the "Updates" rows of the
// settings sheet). The control renders UpdateStatus and calls the bridge (docs/BRIDGE_API.md:
// update_status, check_updates, download_update, apply_update, open_url, get_settings,
// set_settings; event `update`). The background download of a found version is the core's job
// (Settings.auto_download_updates, ARCHITECTURE 5.7); the page never downloads or restarts by itself.

import { call, on } from './api.js';
import { mountUpdateControl } from '../vendor/liquid_ui/js/update.js';

let control = null;

/**
 * @param {object} state  State from ready()
 * @param {{activeJobs?: () => boolean}} opts
 */
export function initUpdate(state, opts = {}) {
  control = mountUpdateControl({
    pill: document.getElementById('ver-pill'),
    popoverHost: document.getElementById('update-pop'),
    call,
    on,
    options: {
      product: 'Media Saver',
      version: state.app_version || '',
      status: state.update || null,
      settings: state.settings || null,
      hasActiveWork: opts.activeJobs || (() => false),
      restartWarning: 'Downloads in progress will stop when the app restarts',
    },
  });
  // Confirmed settings from any route (the sheet's other rows, the backend) reach the control.
  on('settings', (s) => s && control.setSettings(s));
  return control;
}

/** The mounted control (pill, popover, mountSettings). */
export function updateControl() {
  return control;
}

/** The running version, as the latest UpdateStatus reports it. */
export function appVersion() {
  return control ? control.status().current || '' : '';
}
