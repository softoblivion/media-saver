// en-US formatting helpers: plurals, sizes, speeds, durations, relative times, item nouns, short
// links and link error notes per platform.

// Used inside functions only, so the import cycle format -> mode -> toast -> format is harmless.
import { PLATFORM_NAME } from './mode.js';

/** plural(5, ['link', 'links']) -> 'links' */
export function plural(n, forms) {
  return Math.abs(n) === 1 ? forms[0] : forms[1];
}

export const nLinks = (n) => `${n} ${plural(n, ['link', 'links'])}`;
export const nFiles = (n) => `${n} ${plural(n, ['file', 'files'])}`;

const num = (v, digits = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function bytes(b) {
  if (b == null || !isFinite(b)) return '';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${num(b / 1024, 0)} KB`;
  if (b < 1024 ** 3) return `${num(b / 1024 ** 2, 1)} MB`;
  return `${num(b / 1024 ** 3, 2)} GB`;
}

export function speed(bps) {
  if (bps == null || !isFinite(bps) || bps <= 0) return '';
  return `${bytes(bps)}/s`;
}

export function eta(s) {
  if (s == null || !isFinite(s) || s < 0) return '';
  s = Math.round(s);
  if (s < 60) return `${Math.max(1, s)} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

export function duration(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

export function timeAgo(epoch) {
  if (!epoch) return '';
  const d = Date.now() / 1000 - epoch;
  if (d < 45) return 'just now';
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 172800) return 'yesterday';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
}

export function shortDate(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function dateTime(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

const CODECS = { h264: 'H.264', avc1: 'H.264', avc: 'H.264', hevc: 'HEVC', h265: 'HEVC', vp9: 'VP9', vp09: 'VP9', av1: 'AV1', av01: 'AV1' };
export function codec(c) {
  if (!c) return '';
  const k = String(c).toLowerCase().split('.')[0];
  return CODECS[k] || String(c).toUpperCase();
}

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

// Chip notes per platform (docs/UI_DESIGN.md section 9). A link without a platform counts as Instagram.
const NOTE_PATH = { instagram: 'not a Reel or post', youtube: 'not a video', tiktok: 'not a video', twitter: 'not a post' };
const NOTE_ID = { instagram: 'no video code', youtube: 'no video ID', tiktok: 'no video ID', twitter: 'no post ID' };

/** Short note next to an invalid link chip (CleanResult item error), by the item's platform. */
export function linkError(item) {
  const p = item.platform || 'instagram';
  switch (item.error) {
    case 'not_supported':
    case 'not_instagram':
      return 'not supported';
    case 'unsupported_path':
      return NOTE_PATH[p] || NOTE_PATH.instagram;
    case 'no_shortcode':
      return NOTE_ID[p] || NOTE_ID.instagram;
    case 'other_platform':
      return PLATFORM_NAME[p] ? `${PLATFORM_NAME[p]} link` : 'other platform';
    default:
      return 'invalid';
  }
}

/** What one item is called on a platform: Instagram "Reel" / "Reels", the others "video" / "videos". */
export function itemNoun(n, platform) {
  return (platform || 'instagram') === 'instagram' ? plural(n, ['Reel', 'Reels']) : plural(n, ['video', 'videos']);
}

/** "3 Reels", "1 video", "2 videos as MP3" (the format is named only when it is MP3). */
export const nItems = (n, platform, format) => `${n} ${itemNoun(n, platform)}${format === 'mp3' ? ' as MP3' : ''}`;

// Short link forms by platform, then kind (the kind `video` exists on YouTube and TikTok).
const SHORT = {
  instagram: { reel: (c) => `reel/${c}`, p: (c) => `p/${c}` },
  youtube: { video: (c) => `watch?v=${c}`, shorts: (c) => `shorts/${c}` },
  tiktok: { video: (c) => `video/${c}`, share: (c) => `t/${c}` },
  twitter: { post: (c) => `status/${c}` },
};

/**
 * Canonical link without the scheme and host: "reel/<code>", "p/<code>", "watch?v=<id>",
 * "shorts/<id>", "video/<id>" and "t/<code>" (TikTok), "status/<id>" (X).
 */
export function shortLink(item) {
  const code = item.shortcode || '';
  const form = (SHORT[item.platform || 'instagram'] || {})[item.kind];
  if (code && form) return form(code);
  const u = String(item.clean || item.url || '');
  return u.replace(/^https?:\/\/(www\.)?/i, '');
}
