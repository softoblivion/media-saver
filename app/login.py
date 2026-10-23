"""Instagram login window (docs/ARCHITECTURE.md section 5.4).

A second pywebview window shows the real Instagram login page; the user types the credentials there. This module
only polls the window's cookie jar until a `sessionid` cookie of instagram.com appears, then hands all instagram.com
cookies to `app.core.auth.write_netscape_cookies`, tells the engine to reload the session and closes the window.
It never sees or stores the password.

HttpOnly finding (verified on pywebview 6.2.1, edgechromium): `window.get_cookies()` goes through the WebView2
CookieManager and does return HttpOnly cookies (`sessionid` is HttpOnly), so no extra native access is needed.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("mediasaver.login")

LOGIN_URL = "https://www.instagram.com/accounts/login/"
WINDOW_TITLE = "Sign in to Instagram"
POLL_INTERVAL = 1.5
POLL_TIMEOUT = 20 * 60  # give up after 20 minutes without a session


def _is_instagram_domain(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return d == "instagram.com" or d.endswith(".instagram.com")


def _parse_expires(raw: Any) -> Optional[float]:
    """Epoch seconds, or None for session cookies (WebView2 reports year 0001 for those)."""
    if not raw:
        return None
    text = str(raw)
    # email.utils reads the year "0001" as 2001, so reject the .NET "not set" date before parsing.
    year = re.search(r"(\d{4})\s+\d\d:\d\d:\d\d", text)
    if year and int(year.group(1)) < 1971:
        return None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.year < 1971:
        return None
    return moment.timestamp()


def cookies_to_dicts(jar: Iterable[Any]) -> list[dict]:
    """Convert pywebview's `get_cookies()` result (SimpleCookie objects) to the dicts auth.write_netscape_cookies takes.

    Only instagram.com cookies are kept. Dict keys: name, value, domain, path, secure, http_only, expires.
    """
    out: list[dict] = []
    for item in jar:
        if not isinstance(item, SimpleCookie):
            continue
        for name, morsel in item.items():
            domain = morsel["domain"] or ""
            if not _is_instagram_domain(domain):
                continue
            out.append(
                {
                    "name": name,
                    "value": morsel.value,
                    "domain": domain,
                    "path": morsel["path"] or "/",
                    "secure": bool(morsel["secure"]),
                    "http_only": bool(morsel["httponly"]),
                    "expires": _parse_expires(morsel["expires"]),
                }
            )
    return out


def has_session(cookies: Iterable[dict]) -> bool:
    return any(c["name"] == "sessionid" and c["value"] and _is_instagram_domain(c["domain"]) for c in cookies)


def default_cookie_writer(cookies: list[dict], path: Any) -> None:
    from app.core.auth import write_netscape_cookies  # imported late on purpose

    write_netscape_cookies(cookies, path)


class LoginController:
    """Owns at most one login window at a time."""

    def __init__(
        self,
        engine: Any,
        toast: Callable[[str, str], None],
        create_window: Optional[Callable[..., Any]] = None,
        cookie_writer: Callable[[list[dict], Any], None] = default_cookie_writer,
        poll_interval: float = POLL_INTERVAL,
        background_color: str = "#0b0d14",
    ) -> None:
        self._engine = engine
        self._toast = toast
        self._create_window = create_window
        self._write = cookie_writer
        self._interval = poll_interval
        self._bg = background_color
        self._lock = threading.Lock()
        self._window: Any = None
        self._closed = threading.Event()
        self._poller: Optional[threading.Thread] = None

    # -- public entry points
    def open(self) -> None:
        """Open the login window, or bring the existing one to front."""
        with self._lock:
            window = self._window
            if window is not None and not self._closed.is_set():
                try:
                    window.restore()
                    window.show()
                except Exception:  # noqa: BLE001
                    log.debug("cannot raise login window", exc_info=True)
                return
            self._closed = threading.Event()
            factory = self._create_window
            if factory is None:
                import webview

                factory = webview.create_window
            window = factory(
                WINDOW_TITLE,
                LOGIN_URL,
                width=480,
                height=760,
                min_size=(420, 560),
                resizable=True,
                background_color=self._bg,
                text_select=True,
            )
            self._window = window
            closed = self._closed
            window.events.closed += lambda *_: closed.set()
            window.events.loaded += self._on_loaded

    def clear_session(self, main_window: Any) -> None:
        """Forget the Instagram page session of the WebView2 profile (called on app logout)."""
        try:
            main_window.clear_cookies()
        except Exception:  # noqa: BLE001
            log.debug("clear_cookies failed", exc_info=True)

    # -- polling
    def _on_loaded(self, *_: Any) -> None:
        with self._lock:
            if self._poller is not None and self._poller.is_alive():
                return
            self._poller = threading.Thread(target=self._poll, args=(self._window, self._closed), name="login-poll", daemon=True)
            self._poller.start()

    def _poll(self, window: Any, closed: threading.Event) -> None:
        deadline = time.monotonic() + POLL_TIMEOUT
        while not closed.is_set() and time.monotonic() < deadline:
            time.sleep(self._interval)
            if closed.is_set():
                return
            try:
                cookies = cookies_to_dicts(window.get_cookies())
            except Exception:  # noqa: BLE001 - window might be navigating or closing
                log.debug("get_cookies failed", exc_info=True)
                continue
            if has_session(cookies):
                self._finish(window, cookies, closed)
                return

    def _finish(self, window: Any, cookies: list[dict], closed: threading.Event) -> None:
        try:
            self._write(cookies, self._engine.paths.cookies_file)
            self._engine.reload_auth()
        except Exception:  # noqa: BLE001
            log.exception("saving the Instagram session failed")
            self._toast("error", "Could not save the Instagram session. See the log for details.")
            return
        self._toast("success", "Signed in. Instagram session saved.")
        closed.set()
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            log.debug("destroy login window failed", exc_info=True)
