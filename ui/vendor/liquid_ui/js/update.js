// Update control: the version pill (title bar) with its popover, the update toasts and the
// "Updates" rows of a settings sheet. It renders an UpdateStatus and talks to the app only through
// the bridge functions the app passes in (docs/LIQUID_UI.md, section 7, "Bridge contract"):
//   methods  update_status(), check_updates(), download_update(), apply_update(), open_url(url),
//            get_settings(), set_settings({auto_download_updates})
//   event    update  (UpdateStatus)
// The control never downloads or restarts on its own: the app's updater downloads by itself when
// auto_download_updates is on; the user starts every other step. The first `available` of a
// version raises one toast (not with automatic download), `ready` one sticky toast with "Restart".
// The app's updater checks on launch and then every five minutes on its own; the page has no
// interval control.
//
// Stages: idle / up_to_date -> "Check for updates"; checking -> indeterminate bar; available ->
// latest version, size, date, release notes, "Download"; downloading -> determinate bar; ready ->
// "Restart to update" (apply_update); error -> the message and "Try again".

import { gel, Lens } from './glass.js';
import { Overlay } from './overlay.js';
import { Switch } from './controls.js';
import { icon } from './icons.js';
import { escapeHtml, bytes, shortDate, shortDateTime, timeAgo } from './format.js';
import { toast } from './toast.js';

const IDLE = { stage: 'idle', current: '', latest: null, notes: null, size: null, published_at: null, progress: null, message: '', checked_at: null, can_apply: false };
const CAN_CHECK = new Set(['idle', 'up_to_date', 'error']);

/**
 * Mount the update control.
 * @param {{
 *   pill: HTMLElement,            // <button> with .ver-dot and .ver-text children (created when missing)
 *   popoverHost: HTMLElement,     // the popover surface (class "popover"), positioned fixed by CSS
 *   call: (name:string, ...args) => Promise<any>,
 *   on: (event:string, fn:Function) => any,
 *   openUrl?: (url:string) => Promise<boolean>|boolean,   // default: call('open_url', url)
 *   options?: {
 *     product?: string,           // app name in the popover title, tooltips and toasts
 *     version?: string,           // shown until the first status arrives
 *     status?: object,            // initial UpdateStatus (e.g. State.update)
 *     settings?: object,          // initial settings; else get_settings() is called
 *     hasActiveWork?: () => boolean,
 *     restartWarning?: string,    // toast text when hasActiveWork() is true at restart
 *     onStatus?: (status) => void,
 *     onSettings?: (settings) => void,
 *   }
 * }} args
 */
export function mountUpdateControl({ pill, popoverHost, call, on, options = {}, openUrl } = {}) {
  if (!pill || !popoverHost || typeof call !== 'function' || typeof on !== 'function') {
    throw new Error('mountUpdateControl needs pill, popoverHost, call and on.');
  }
  const o = {
    product: 'The app',
    version: '',
    status: null,
    settings: null,
    hasActiveWork: () => false,
    restartWarning: 'Work in progress stops when the app restarts',
    onStatus: null,
    onSettings: null,
    ...options,
  };
  const product = o.product;
  const open = openUrl || ((url) => call('open_url', url));

  let u = { ...IDLE };
  let settings = o.settings ? { ...o.settings } : null;
  let lastBusy = null;
  let availToasted = null;
  let readyToasted = null;
  let popKey = '';
  const blocks = new Set();   // mounted settings blocks

  // ---- pill and popover ---------------------------------------------------------------------
  if (!pill.querySelector('.ver-text')) pill.innerHTML = '<span class="ver-dot" aria-hidden="true"></span><span class="ver-text"></span>';
  pill.classList.add('ver-pill');
  pill.setAttribute('aria-haspopup', 'dialog');
  popoverHost.classList.add('popover');
  popoverHost.setAttribute('role', 'dialog');
  if (!popoverHost.getAttribute('aria-label')) popoverHost.setAttribute('aria-label', `${product} updates`);
  let content = popoverHost.querySelector('.ov-content');
  if (!content) {
    content = document.createElement('div');
    content.className = 'ov-content';
    popoverHost.append(content);
  }
  const pillLens = new Lens(pill, 'capsule');
  const overlay = new Overlay(popoverHost, {
    preset: 'sheet',
    place(surface, origin) {
      const r = (origin || pill).getBoundingClientRect();
      surface.style.top = `${Math.round(r.bottom + 10)}px`;
      const w = surface.offsetWidth || 360;
      surface.style.left = `${Math.round(Math.max(12, Math.min(r.left - 8, innerWidth - w - 12)))}px`;
    },
  });
  gel(pill);
  pill.addEventListener('click', async () => {
    if (overlay.open) return overlay.close();
    renderPop();
    overlay.show(pill);
    try {
      render(await call('update_status'));
    } catch {
      /* the last known status stays */
    }
  });
  popoverHost.addEventListener('click', onPopClick);
  on('update', render);

  if (!settings) {
    Promise.resolve()
      .then(() => call('get_settings'))
      .then((s) => s && setSettings(s))
      .catch(() => {});
  }
  render({ ...IDLE, current: o.version || '', ...(o.status || {}) });

  // ---- actions --------------------------------------------------------------------------------
  function check() {
    return call('check_updates').then((s) => s && render(s)).catch((err) => toast('error', err.message));
  }

  function download() {
    return call('download_update').then((s) => s && render(s)).catch((err) => toast('error', err.message));
  }

  function restart() {
    const go = () => call('apply_update').catch((err) => toast('error', err.message));
    if (o.hasActiveWork()) {
      return toast('warning', o.restartWarning, { ms: 8000, action: { label: 'Restart anyway', run: go } });
    }
    return go();
  }

  /** Open the popover from elsewhere; optionally start a check. */
  function openPopover({ check: doCheck = false } = {}) {
    renderPop();
    overlay.show(pill);
    if (doCheck && CAN_CHECK.has(u.stage)) check();
  }

  /** One line of status, shared by the popover and the settings rows. */
  function statusLine(s = u) {
    if (s.stage === 'error') return s.message || (lastBusy === 'downloading' ? 'The download did not finish' : 'Could not check for updates');
    const d = {
      idle: 'Updates come from GitHub Releases',
      checking: 'Checking for updates…',
      up_to_date: 'You have the latest version',
      available: `Version ${s.latest} is available`,
      downloading: `Downloading version ${s.latest}…`,
      ready: `Version ${s.latest} is ready to install`,
    }[s.stage];
    return d || s.message || '';
  }

  // ---- status -----------------------------------------------------------------------------------
  function render(next) {
    if (!next) return;
    const prev = u;
    u = { ...IDLE, ...next };
    if (u.stage === 'checking' || u.stage === 'downloading') lastBusy = u.stage;
    pill.dataset.stage = u.stage;
    pill.querySelector('.ver-text').textContent = u.current ? `v${u.current}` : 'Version';
    const tip = {
      available: `${product} ${u.latest} is available`,
      downloading: `Downloading ${product} ${u.latest}`,
      ready: `${product} ${u.latest} is ready to install`,
    }[u.stage] || 'Version and updates';
    pill.title = tip;
    pill.setAttribute('aria-label', tip);
    if (u.stage === 'available' && u.latest && availToasted !== u.latest && !(settings && settings.auto_download_updates)) {
      availToasted = u.latest;
      toast('info', `${product} ${u.latest} is available`, { ms: 8000, action: { label: 'View', run: () => openPopover() } });
    }
    if (u.stage === 'ready' && u.latest && readyToasted !== u.latest) {
      readyToasted = u.latest;
      toast('success', `${product} ${u.latest} is ready`, {
        sticky: true,
        action: u.can_apply ? { label: 'Restart', run: restart } : { label: 'View', run: () => openPopover() },
      });
    }
    if (overlay.open || prev.stage !== u.stage) renderPop();
    for (const b of blocks) b.renderStatus();
    o.onStatus && o.onStatus({ ...u });
  }

  function renderPop() {
    const key = [u.stage, u.current, u.latest, u.size, u.published_at, !!u.notes, u.can_apply, u.message, u.checked_at, u.progress == null, !!(settings && settings.auto_download_updates)].join('|');
    if (key === popKey && u.stage === 'downloading') {
      const bar = content.querySelector('.pop-progress');
      if (bar) bar.style.setProperty('--p', String(u.progress ?? 0));
      const pct = content.querySelector('.upd-pct');
      if (pct && u.progress != null) {
        pct.querySelector('b').textContent = `${Math.round(u.progress * 100)}%`;
        pct.querySelector('span').textContent = u.size ? `${bytes(u.size * u.progress)} of ${bytes(u.size)}` : '';
      }
      return;
    }
    popKey = key;
    const newer = !!u.latest && ['available', 'downloading', 'ready'].includes(u.stage);
    const rows = [`<li class="comp">
        <span class="comp-ic">${icon('app')}</span>
        <span class="comp-name">Installed</span>
        <span class="comp-ver">${escapeHtml(u.current ? `v${u.current}` : '—')}</span>
      </li>`];
    if (newer && u.latest !== u.current) {
      const tags = [];
      if (u.size) tags.push(`<em>${escapeHtml(bytes(u.size))}</em>`);
      if (u.published_at) tags.push(`<em>${escapeHtml(shortDate(u.published_at))}</em>`);
      if (u.stage === 'ready') tags.push('<em class="tag-new">downloaded</em>');
      rows.push(`<li class="comp ${u.stage === 'ready' ? 'is-ready' : 'is-new'}">
        <span class="comp-ic">${icon(u.stage === 'ready' ? 'check' : 'down')}</span>
        <span class="comp-name">Latest</span>
        <span class="comp-ver">${escapeHtml(`v${u.latest}`)}</span>
        <span class="comp-tags">${tags.join('')}</span>
      </li>`);
    }
    let progress = '';
    if (u.stage === 'checking') progress = '<div class="pop-progress is-indet"><span></span></div>';
    if (u.stage === 'downloading') {
      const p = u.progress;
      progress = `<div class="pop-progress ${p == null ? 'is-indet' : ''}" style="--p:${p ?? 0}"><span></span></div>`;
      if (p != null) progress += `<div class="upd-pct"><b>${Math.round(p * 100)}%</b><span>${escapeHtml(u.size ? `${bytes(u.size * p)} of ${bytes(u.size)}` : '')}</span></div>`;
    }
    const notes = newer && u.notes ? `<div class="upd-notes" tabindex="0" aria-label="Release notes">${renderMarkdown(u.notes)}</div>` : '';
    const box = u.stage === 'error' && u.message ? `<p class="upd-msg" role="alert">${escapeHtml(u.message)}</p>` : '';
    let action;
    switch (u.stage) {
      case 'checking':
        action = `<button type="button" class="btn ov-focus" disabled>${icon('refresh')}<span>Checking…</span></button>`;
        break;
      case 'available':
        action = `<button type="button" class="btn btn-tinted ov-focus" data-act="download">${icon('down')}<span>Download</span></button>`;
        break;
      case 'downloading':
        action = `<button type="button" class="btn ov-focus" disabled>${icon('down')}<span>Downloading…</span></button>`;
        break;
      case 'ready':
        action = `<button type="button" class="btn btn-tinted ov-focus" data-act="apply" ${u.can_apply ? '' : `disabled title="${escapeHtml(u.message || 'Installs from the packaged app only')}"`}>${icon('refresh')}<span>Restart to update</span></button>`;
        break;
      case 'error':
        action = `<button type="button" class="btn ov-focus" data-act="retry">${icon('retry')}<span>Try again</span></button>`;
        break;
      default:
        action = `<button type="button" class="btn ov-focus" data-act="check">${icon('refresh')}<span>Check for updates</span></button>`;
    }
    const auto = settings && settings.auto_download_updates;
    content.innerHTML = `
      <div class="pop-head">
        <div class="pop-title">${escapeHtml(product)}</div>
        <div class="pop-sub">${escapeHtml(statusLine())}</div>
        ${auto ? `<div class="pop-auto">${icon('down')}<span>New versions download in the background</span></div>` : ''}
      </div>
      ${progress}
      ${box}
      <ul class="comp-list upd-rows">${rows.join('')}</ul>
      ${notes}
      <div class="pop-foot">
        <span class="pop-meta">${u.checked_at ? `Checked ${escapeHtml(timeAgo(u.checked_at))}` : 'Not checked yet'}</span>
        ${action}
      </div>`;
    popoverHost.setAttribute('aria-busy', String(u.stage === 'checking' || u.stage === 'downloading'));
    content.querySelectorAll('.btn').forEach((b) => gel(b));
  }

  function runAct(act) {
    switch (act) {
      case 'check':
        return check();
      case 'download':
        return download();
      case 'retry':
        if (lastBusy === 'downloading' && u.latest) return download();
        return check();
      case 'apply':
        return restart();
      default:
        return undefined;
    }
  }

  function onPopClick(e) {
    const a = e.target.closest('a[data-url]');
    if (a) {
      e.preventDefault();
      Promise.resolve()
        .then(() => open(a.dataset.url))
        .then((ok) => ok || toast('error', 'The link could not be opened.'))
        .catch((err) => toast('error', err.message));
      return;
    }
    const b = e.target.closest('[data-act]');
    if (b) runAct(b.dataset.act);
  }

  // ---- settings -------------------------------------------------------------------------------
  /** Confirmed settings from the app (its `settings` event, get_settings, a refused patch). */
  function setSettings(s) {
    if (!s) return;
    const wasAuto = !!(settings && settings.auto_download_updates);
    settings = { ...(settings || {}), ...s };
    for (const b of blocks) b.refresh();
    if (overlay.open && wasAuto !== !!settings.auto_download_updates) renderPop();
  }

  /**
   * Render the "Updates" rows of a settings sheet into `host` (a `.set-card`): the status line
   * with the next action and "Download updates automatically" (switch). Changes are saved with
   * set_settings; a refusal shows the sentence as a toast and restores the confirmed value.
   * Returns { el, refresh(), destroy() }.
   */
  function mountSettings(host) {
    const inflight = new Map();
    const busyKey = (k) => (inflight.get(k) || 0) > 0;
    host.insertAdjacentHTML('beforeend', `
      <div class="set-row set-row-col" data-row="update">
        <div class="upd-line"><span class="upd-status" data-upd-status></span><span class="upd-acts" data-upd-acts></span></div>
      </div>
      <div class="set-row" data-row="auto_download_updates"><div class="set-label"><span>Download updates automatically</span><small>Checked every 5 minutes; installing still needs a restart</small></div><div class="set-ctl" data-slot="auto_download_updates"></div></div>`);
    const rows = Array.from(host.querySelectorAll('[data-row="update"], [data-row="auto_download_updates"]'));
    const st = host.querySelector('[data-upd-status]');
    const acts = host.querySelector('[data-upd-acts]');
    const s0 = settings || {};

    async function save(key, value, revert) {
      inflight.set(key, (inflight.get(key) || 0) + 1);
      try {
        const s = await call('set_settings', { [key]: value });
        if (s) {
          setSettings(s);
          o.onSettings && o.onSettings(s);
        }
        return s;
      } catch (err) {
        toast('error', err.message);
        revert && revert();
        return null;
      } finally {
        inflight.set(key, inflight.get(key) - 1);
        block.refresh();
      }
    }

    const auto = new Switch({
      label: 'Download updates automatically',
      checked: s0.auto_download_updates,
      onChange: (v) => save('auto_download_updates', v, () => auto.set(settings && settings.auto_download_updates)),
    });
    host.querySelector('[data-slot="auto_download_updates"]').append(auto.el);

    const onActs = (e) => {
      const b = e.target.closest('[data-act]');
      if (b) runAct(b.dataset.act);
    };
    acts.addEventListener('click', onActs);

    const block = {
      el: host,
      renderStatus() {
        st.innerHTML = `<b>${escapeHtml(statusLine(u))}</b>${u.checked_at ? `<small>Checked ${escapeHtml(shortDateTime(u.checked_at))}</small>` : ''}`;
        let html = '';
        if (u.stage === 'available') html = `<button type="button" class="btn btn-s btn-tinted" data-act="download">${icon('down')}<span>Download</span></button>`;
        else if (u.stage === 'ready') html = `<button type="button" class="btn btn-s btn-tinted" data-act="apply" ${u.can_apply ? '' : 'disabled'}>${icon('refresh')}<span>Restart to update</span></button>`;
        else if (u.stage === 'downloading') html = `<button type="button" class="btn btn-s" disabled>${icon('down')}<span>${u.progress != null ? `${Math.round(u.progress * 100)}%` : 'Downloading…'}</span></button>`;
        else html = `<button type="button" class="btn btn-s" data-act="check" ${u.stage === 'checking' ? 'disabled' : ''}>${icon('refresh')}<span>${u.stage === 'checking' ? 'Checking…' : 'Check for updates'}</span></button>`;
        acts.innerHTML = html;
        acts.querySelectorAll('.btn').forEach((b) => gel(b));
      },
      /** Show confirmed settings; a control with a save in flight keeps its local value. */
      refresh() {
        if (!settings) return;
        if (!busyKey('auto_download_updates')) auto.set(settings.auto_download_updates);
      },
      destroy() {
        blocks.delete(block);
        acts.removeEventListener('click', onActs);
        auto.destroy();
        rows.forEach((r) => r.remove());
      },
    };
    blocks.add(block);
    block.renderStatus();
    block.refresh();
    return block;
  }

  return {
    open: openPopover,
    close: () => overlay.close(),
    isOpen: () => overlay.open,
    check,
    download,
    restart,
    status: () => ({ ...u }),
    statusLine,
    setSettings,
    mountSettings,
    pill,
    pillLens,
    overlay,
  };
}

// ---------------------------------------------------------------------------------------------
// Minimal, safe Markdown for release notes: headings, bullets, paragraphs, **bold**, `code` and
// [text](https://...) links (rendered as <a data-url>, opened through openUrl). Everything is
// HTML-escaped first; nothing else is parsed.

export function renderMarkdown(md) {
  const lines = String(md || '').replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let para = [];
  let inList = false;
  const flush = () => {
    if (para.length) html += `<p>${inline(para.join(' '))}</p>`;
    para = [];
  };
  const closeList = () => {
    if (inList) html += '</ul>';
    inList = false;
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    let m;
    if (!line.trim()) {
      flush();
      closeList();
      continue;
    }
    if ((m = line.match(/^ {0,3}(#{1,6})\s+(.*?)\s*#*$/))) {
      flush();
      closeList();
      html += `<h4${m[1].length <= 2 ? ' class="h-top"' : ''}>${inline(m[2])}</h4>`;
      continue;
    }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flush();
      if (!inList) html += '<ul>';
      inList = true;
      html += `<li>${inline(m[1])}</li>`;
      continue;
    }
    if (inList && /^\s{2,}\S/.test(line)) {
      html = html.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`);
      continue;
    }
    closeList();
    para.push(line.trim());
  }
  flush();
  closeList();
  return html;
}

function inline(s) {
  const codes = [];
  let t = escapeHtml(s).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0001${codes.length - 1}\u0001`;
  });
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/__(.+?)__/g, '<strong>$1</strong>');
  t = t.replace(/\[([^\]]+)\]\((https:\/\/[^\s)"]+)\)/g, (_, text, url) => `<a href="#" data-url="${url}" title="${url}">${text}</a>`);
  return t.replace(/\u0001(\d+)\u0001/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}
