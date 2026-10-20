// Bridge layer (docs/BRIDGE_API.md). Registers window.__msEvent before ready(), waits for
// pywebview (the `pywebviewready` event, ~1.5 s), falls back to the mock in a normal browser or
// with ?mock=1. Components never touch window.pywebview directly: they use call() and on().

const listeners = new Map();   // type -> Set<fn>
let backend = null;            // object with the bridge methods
let mode = 'pending';          // 'pywebview' | 'mock'

/** Subscribe to a bridge event type ("engine", "job", ...). Returns an unsubscribe function. */
export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type).delete(fn);
}

/** Dispatch an event to subscribers (also used for UI-internal events such as "state"). */
export function emit(type, payload) {
  const set = listeners.get(type);
  if (!set) return;
  for (const fn of Array.from(set)) {
    try {
      fn(payload);
    } catch (err) {
      console.error(`[ui] handler for "${type}" failed`, err);
    }
  }
}

// Events may arrive before the UI finished wiring; queue them until connect() resolves.
let early = [];
window.__msEvent = (type, payload) => {
  if (early) early.push([type, payload]);
  else emit(type, payload);
};

function waitForPywebview(timeoutMs) {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve(true);
    const t = setTimeout(() => {
      window.removeEventListener('pywebviewready', ok);
      resolve(!!(window.pywebview && window.pywebview.api));
    }, timeoutMs);
    function ok() {
      clearTimeout(t);
      resolve(true);
    }
    window.addEventListener('pywebviewready', ok, { once: true });
  });
}

/** Connect to the backend and return the initial State. */
export async function connect() {
  const params = new URLSearchParams(location.search);
  const forceMock = params.get('mock') === '1';
  let real = false;
  if (!forceMock) real = await waitForPywebview(1500);
  if (real) {
    backend = window.pywebview.api;
    mode = 'pywebview';
  } else {
    const mock = await import('./mock-api.js');
    backend = mock.createMock({ params, emit: (t, p) => window.__msEvent(t, p) });
    mode = 'mock';
  }
  const state = await call('ready');
  // Deliver anything that arrived while ready() was in flight, then go live.
  const queued = early;
  early = null;
  for (const [t, p] of queued) emit(t, p);
  return state;
}

export const backendMode = () => mode;

/** Call a bridge method. Rejections carry the Python message (English) when available. */
export async function call(method, ...args) {
  if (!backend) throw new Error('Backend is not connected yet');
  const fn = backend[method];
  if (typeof fn !== 'function') throw new Error(`Method ${method} is unavailable`);
  try {
    return await fn.apply(backend, args);
  } catch (err) {
    throw normalizeError(err);
  }
}

function normalizeError(err) {
  if (err instanceof Error) return err;
  if (err && typeof err === 'object') {
    const msg = err.message || err.error || JSON.stringify(err);
    return new Error(msg);
  }
  return new Error(String(err));
}
