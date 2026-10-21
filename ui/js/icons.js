// Media Saver's line icons (24 x 24 grid, 1.8 px stroke, round caps), registered into the kit's icon
// set (vendor/liquid_ui/js/icons.js), so app modules and kit components draw from one registry.
// Usage: icon('gear') returns an inline SVG string. Same names as the kit's replace its drawings
// (e.g. `app`: Media Saver's droplet in the update popover).

import { icon, registerIcons } from '../vendor/liquid_ui/js/icons.js';

export { icon };

const P = {
  gear: '<path d="M12 8.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8Z"/><path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l1.8-1.4-1.8-3.1-2.2.8a7.5 7.5 0 0 0-2.6-1.5L14.2 3h-3.6l-.4 2.3a7.5 7.5 0 0 0-2.6 1.5l-2.2-.8-1.8 3.1 1.8 1.4a7.6 7.6 0 0 0 0 3l-1.8 1.4 1.8 3.1 2.2-.8a7.5 7.5 0 0 0 2.6 1.5l.4 2.3h3.6l.4-2.3a7.5 7.5 0 0 0 2.6-1.5l2.2.8 1.8-3.1Z"/>',
  down: '<path d="M12 4v11.5"/><path d="m6.5 10.5 5.5 5.5 5.5-5.5"/><path d="M5 20h14"/>',
  paste: '<rect x="5" y="4.5" width="14" height="16" rx="3"/><path d="M9 4.5V4a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4v.5"/><path d="M9 11h6M9 15h4"/>',
  sparkle: '<path d="M10 3.5 11.6 8a2 2 0 0 0 1.3 1.3l4.6 1.6-4.6 1.6a2 2 0 0 0-1.3 1.3L10 18.4l-1.6-4.6a2 2 0 0 0-1.3-1.3L2.5 10.9l4.6-1.6A2 2 0 0 0 8.4 8Z"/><path d="M18 14.5v5M15.5 17h5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.6"/><path d="M15.5 8.5V6.6A2.1 2.1 0 0 0 13.4 4.5H6.6a2.1 2.1 0 0 0-2.1 2.1v6.8a2.1 2.1 0 0 0 2.1 2.1h1.9"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  retry: '<path d="M4.5 12a7.5 7.5 0 0 1 13-5.1L19.5 9"/><path d="M19.5 4.5V9H15"/><path d="M19.5 12a7.5 7.5 0 0 1-13 5.1L4.5 15"/><path d="M4.5 19.5V15H9"/>',
  play: '<path d="M8 5.8v12.4a1 1 0 0 0 1.5.9l9.7-6.2a1 1 0 0 0 0-1.7L9.5 4.9A1 1 0 0 0 8 5.8Z"/>',
  folder: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.6l2 2.2h7.4a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"/>',
  reveal: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.6l2 2.2h7.4a2 2 0 0 1 2 2v2.3"/><path d="M3.5 7.5V17a2 2 0 0 0 2 2h5"/><circle cx="16.5" cy="16" r="3"/><path d="m18.7 18.2 2.3 2.3"/>',
  trash: '<path d="M4.5 7h15"/><path d="M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7"/><path d="m6.5 7 .8 11.4A1.8 1.8 0 0 0 9.1 20h5.8a1.8 1.8 0 0 0 1.8-1.6L17.5 7"/>',
  min: '<path d="M6 12h12"/>',
  max: '<rect x="6" y="6" width="12" height="12" rx="2.4"/>',
  restore: '<rect x="5.5" y="8.5" width="10" height="10" rx="2.2"/><path d="M9 5.5h7.3a2.2 2.2 0 0 1 2.2 2.2V15"/>',
  close: '<path d="M7 7l10 10M17 7 7 17"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  warn: '<path d="M12 4.5 2.8 19.5h18.4Z"/><path d="M12 10v4.2"/><path d="M12 17.2v.1"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.8v.1"/>',
  user: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 19.5a7.5 7.5 0 0 1 15 0"/>',
  logout: '<path d="M14 4.5h3.5a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h9"/>',
  file: '<path d="M7 3.5h6.5L18 8v11a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z"/><path d="M13 3.5V8.5h5"/>',
  refresh: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5V9h-4.5"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
  minus: '<path d="M5.5 12h13"/>',
  reel: '<rect x="4.5" y="3.5" width="15" height="17" rx="4"/><path d="m10.5 9.5 4 2.5-4 2.5Z"/>',
  post: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M4 15.5 9 11l4 3.5 2.5-2 4.5 3.5"/><circle cx="15" cy="8.8" r="1.4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  logs: '<path d="M6 4.5h12A1.5 1.5 0 0 1 19.5 6v12a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V6A1.5 1.5 0 0 1 6 4.5Z"/><path d="M8 9h8M8 12.5h8M8 16h5"/>',
  clipboard: '<rect x="5" y="4.5" width="14" height="16" rx="3"/><path d="M9 4.5V4a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4v.5"/><path d="m9 13 2.2 2.2L15.5 11"/>',
  import: '<path d="M12 3.5v10"/><path d="m8 10 4 4 4-4"/><path d="M5 15.5v2A2 2 0 0 0 7 19.5h10a2 2 0 0 0 2-2v-2"/>',
  bolt: '<path d="M13 3 5.5 13.2h5.6L10.6 21 18.5 10.6h-5.6Z"/>',
  video: '<rect x="2.5" y="5.5" width="19" height="13" rx="3.5"/><path d="m10.3 9.6 4.2 2.4-4.2 2.4Z"/>',
  shorts: '<rect x="6.5" y="2.5" width="11" height="19" rx="3.5"/><path d="m10.6 9.6 3.6 2.4-3.6 2.4Z"/>',
  audio: '<path d="M9 18V6.2l10-2.2v11.8"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="15.8" r="2.5"/>',
  swap: '<path d="M4.5 8.5h13"/><path d="m14 5 3.5 3.5L14 12"/><path d="M19.5 15.5h-13"/><path d="m10 12-3.5 3.5L10 19"/>',
  folderEdit: '<path d="M20.5 10.6v-.9a2 2 0 0 0-2-2h-7.4l-2-2.2H5.5a2 2 0 0 0-2 2V17a2 2 0 0 0 2 2h5.3"/><path d="m14 20.5.6-2.7 4.9-4.9a1.5 1.5 0 0 1 2.1 2.1l-4.9 4.9Z"/>',
  app: '<path d="M12 3.5S6 10 6 14a6 6 0 0 0 12 0c0-4-6-10.5-6-10.5Z"/><path d="M12 10.5v6M9.5 14.5 12 17l2.5-2.5"/>',
};

registerIcons(P, { filled: ['play'] });

// Kind glyph per platform: by platform first, because the kind `video` exists on YouTube (landscape)
// and TikTok (vertical). Original line glyphs only; no platform logos or marks.
const KIND_GLYPH = {
  instagram: { reel: 'reel', p: 'post', _: 'reel' },
  youtube: { video: 'video', shorts: 'shorts', _: 'video' },
  tiktok: { video: 'shorts', share: 'shorts', _: 'shorts' },
  twitter: { post: 'video', _: 'video' },
};

/** Glyph name for a link or job: kindGlyph('tiktok', 'video') -> 'shorts'; unknown kind -> the platform's default. */
export function kindGlyph(platform, kind) {
  const g = KIND_GLYPH[platform || 'instagram'] || KIND_GLYPH.instagram;
  return g[kind] || g._;
}

/** The app glyph: a glass droplet with a down arrow (same drawing as assets/icon.svg, simplified). */
export function appGlyph(size = 20) {
  return `<svg class="app-glyph" width="${size}" height="${size}" viewBox="0 0 64 64" aria-hidden="true">
    <defs>
      <linearGradient id="ag-fill" x1="12" y1="6" x2="54" y2="60" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#5fb4ff"/><stop offset=".55" stop-color="#6c7cff"/><stop offset="1" stop-color="#a45dff"/>
      </linearGradient>
    </defs>
    <path d="M32 4C32 4 10 27.5 10 41a22 22 0 0 0 44 0C54 27.5 32 4 32 4Z" fill="url(#ag-fill)"/>
    <path d="M32 4C32 4 10 27.5 10 41a22 22 0 0 0 44 0C54 27.5 32 4 32 4Z" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="1.5"/>
    <path d="M32 24v20M24 37l8 8 8-8" fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}
