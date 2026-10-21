"""UpdateScheduler: repeat the update check on a user-set interval while the app runs.

The launch check stays the host app's own `check_background()` call; the scheduler runs the periodic ones. The
interval is a setting key (`update_check_interval`, one of the keys of CHECK_INTERVALS) stored by the host app.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

# (setting key, period in seconds), in the order a settings UI lists them
CHECK_INTERVALS: tuple[tuple[str, int], ...] = (
    ("30m", 1800), ("1h", 3600), ("6h", 21600), ("1d", 86400), ("1w", 604800),
)
DEFAULT_CHECK_INTERVAL = "30m"

_PERIODS = dict(CHECK_INTERVALS)


def interval_seconds(key: str) -> int:
    """Period of a setting key; an unknown key gives the default's period."""
    return _PERIODS.get(key, _PERIODS[DEFAULT_CHECK_INTERVAL])


class UpdateScheduler:
    """Calls `updater.check_background()` once per period in a daemon thread. Never raises.

    The first periodic check runs one period after `start()`; `set_interval()` restarts the wait from now. A check
    skipped because a check or download is already running is not queued: the next period tries again. While the
    updater's stage is `ready` (an update waits for the restart) the periodic check is skipped.
    """

    def __init__(self, updater: Any, interval_key: str, logger_name: str = "selfupdate.schedule") -> None:
        self.updater = updater
        self.log = logging.getLogger(logger_name)
        self._key = interval_key if interval_key in _PERIODS else DEFAULT_CHECK_INTERVAL
        self._period_override: Optional[float] = None  # tests only: seconds that replace the table period
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stopped = False
        self._next: Optional[float] = None

    @property
    def interval_key(self) -> str:
        return self._key

    @property
    def next_check_at(self) -> Optional[float]:
        """Epoch seconds of the next periodic check; None before start() and after stop()."""
        with self._cond:
            return self._next

    def _period(self) -> float:
        return self._period_override if self._period_override is not None else float(interval_seconds(self._key))

    def start(self) -> None:
        with self._cond:
            if self._thread is not None or self._stopped:
                return
            self._next = time.time() + self._period()
            self._thread = threading.Thread(target=self._loop, name="selfupdate-schedule", daemon=True)
            self._thread.start()

    def set_interval(self, key: str) -> None:
        with self._cond:
            self._key = key if key in _PERIODS else DEFAULT_CHECK_INTERVAL
            if self._thread is not None and not self._stopped:
                self._next = time.time() + self._period()
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._next = None
            self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and self._next is not None and time.time() < self._next:
                    self._cond.wait(self._next - time.time())
                if self._stopped:
                    return
                self._next = time.time() + self._period()
            try:
                if not self._update_waiting():
                    self.updater.check_background()
            except Exception:  # noqa: BLE001
                self.log.exception("scheduled update check failed")

    def _update_waiting(self) -> bool:
        """True while a verified update waits for the restart: a check would only re-verify the same file."""
        status = getattr(self.updater, "status", None)
        return callable(status) and (status() or {}).get("stage") == "ready"
