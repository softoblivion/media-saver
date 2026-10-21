"""HTTP through urllib: User-Agent, timeouts, retries, streamed downloads with SHA-256 (docs/SELF_UPDATE.md)."""
from __future__ import annotations

import hashlib
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

DEFAULT_USER_AGENT = "selfupdate (+urllib; Windows)"


class OfflineError(Exception):
    """The network is unreachable (or the host refuses us: GitHub 403/429)."""


class Net:
    """urllib with a User-Agent, timeouts and retries."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 20.0, retries: int = 3) -> None:
        self.user_agent, self.timeout, self.retries = user_agent, timeout, retries

    def _open(self, url: str, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _retry(self, fn, url: str):
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return fn()
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    raise OfflineError(f"{url}: HTTP {exc.code}") from exc
                if exc.code < 500:
                    raise
                last = exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                last = exc
            time.sleep(min(4.0, 1.0 + attempt * 1.5))
        raise OfflineError(f"{url}: {last}")

    def get_bytes(self, url: str, limit: int = 4 << 20) -> bytes:
        def go():
            with self._open(url) as resp:
                return resp.read(limit)
        return self._retry(go, url)

    def get_text(self, url: str) -> str:
        return self.get_bytes(url).decode("utf-8", errors="replace")

    def reachable(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent}),
                                        timeout=8):
                return True
        except urllib.error.HTTPError:
            return True  # the host answered
        except Exception:  # noqa: BLE001
            return False

    def download(self, url: str, dest: str, on_progress: Callable[[int, int | None], None] | None = None) -> str:
        """Stream to dest (normally a .part path); returns its SHA-256 hex."""
        def go():
            sha = hashlib.sha256()
            with self._open(url) as resp, open(dest, "wb") as fh:
                total = resp.headers.get("Content-Length")
                total_i = int(total) if total and total.isdigit() else None
                done = 0
                last = 0.0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if on_progress and now - last > 0.2:
                        last = now
                        on_progress(done, total_i)
                if total_i is not None and done < total_i:
                    raise ConnectionError(f"short read {done}/{total_i}")
            if on_progress:
                on_progress(done, total_i)
            return sha.hexdigest()
        return self._retry(go, url)


class ReleaseNet(Net):
    """Net that sends the GitHub API headers on api.github.com requests (other hosts get the plain User-Agent)."""

    def _open(self, url: str, method: str = "GET"):
        headers = {"User-Agent": self.user_agent}
        if urllib.parse.urlparse(url).hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return urllib.request.urlopen(urllib.request.Request(url, method=method, headers=headers), timeout=self.timeout)
