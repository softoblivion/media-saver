// en-US formatting shared by the kit and its apps: plurals, sizes, percentages, durations, ETA,
// relative and short dates, HTML escaping, the last segment of a Windows path. App-specific nouns
// (kinds, codecs, sample rates) stay in the app and may build on num() and plural().

/** Fixed-point number in en-US notation: num(1234.5, 1) -> "1,234.5". */
export const num = (v, digits = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function plural(n, forms) {
  return Math.abs(n) === 1 ? forms[0] : forms[1];
}

/** Byte count in B / KB / MB / GB (1024 based): 9_542_041 -> "9.1 MB". */
export function bytes(b) {
  if (b == null || !isFinite(b)) return '';
  const a = Math.abs(b);
  if (a < 1024) return `${Math.round(b)} B`;
  if (a < 1024 * 1024) return `${num(b / 1024, 0)} KB`;
  if (a < 1024 ** 3) return `${num(b / 1024 ** 2, 1)} MB`;
  return `${num(b / 1024 ** 3, 2)} GB`;
}

/** Ratio as a percentage with a no-break space: 0.19 -> "19 %", 0.042 -> "4.2 %". */
export function percent(r) {
  if (r == null || !isFinite(r)) return '';
  const p = r * 100;
  return `${p < 10 && p > 0 ? num(p, 1) : Math.round(p)}\u00a0%`;
}

/** Remaining time: 72 -> "1 min 12 s left". */
export function eta(s) {
  if (s == null || !isFinite(s) || s < 0) return '';
  s = Math.round(s);
  if (s < 60) return `${Math.max(1, s)} s left`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s left` : `${m} min left`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min left`;
}

/** Media duration: 72 -> "1:12", 3725 -> "1:02:05". */
export function duration(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`;
}

/** Relative time of an epoch (seconds): "just now", "5 min ago", "3 h ago", "yesterday", "Oct 3". */
export function timeAgo(epoch) {
  if (!epoch) return '';
  const d = Date.now() / 1000 - epoch;
  if (d < 45) return 'just now';
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 172800) return 'yesterday';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
}

/** Date of an epoch (seconds): "Oct 21, 2026". */
export function shortDate(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** Date and time of an epoch (seconds): "Oct 21, 4:05 PM". */
export function shortDateTime(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

/** Last path segment of a Windows path ("D:\\clips\\a.mp4" -> "a.mp4"; "E:\\" stays). */
export function baseName(p) {
  if (!p) return '';
  const s = String(p).replace(/[\\/]+$/, '');
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
  return i >= 0 ? s.slice(i + 1) || s : s;
}
