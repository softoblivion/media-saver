"""UpdateScheduler: repeat the update check every few minutes while the app runs.

The launch check stays the host app's own `check_background()` call; the scheduler runs the periodic ones. The
period is fixed by the library (no setting): CHECK_PERIOD seconds with a small random offset, longer after a
failed check, and no check at all while Windows reports no network connection.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable, Optional

from .net import network_available

CHECK_PERIOD = 300          # seconds between periodic checks
CHECK_JITTER = 30           # each wait is CHECK_PERIOD +/- up to this many seconds
ERROR_BACKOFF_MAX = 3600    # the wait doubles after every failed check, up to this


class UpdateScheduler:
    """Calls `updater.check_background()` about every CHECK_PERIOD seconds in a daemon thread. Never raises.

    The first periodic check runs one period after `start()`. Each wait is the period plus a random offset of up
    to CHECK_JITTER seconds either way, so several apps started together do not call GitHub in the same second.
    While the updater's stage is `error` the wait doubles with every further check (CHECK_PERIOD, 2x, 4x ... up
    to ERROR_BACKOFF_MAX) and returns to CHECK_PERIOD after the first check that ends in another stage. When
    `online()` reports no network the check is skipped without counting as a failure. While the stage is `ready`
    (an update waits for the restart) the periodic check is skipped. A check skipped because a check or download
    is already running is not queued: the next period tries again.
    """

    def __init__(self, updater: Any, logger_name: str = "selfupdate.schedule",
                 online: Callable[[], bool] = network_available) -> None:
        self.updater = updater
        self.log = logging.getLogger(logger_name)
        self._online = online
        self._period_override: Optional[float] = None  # tests only: seconds that replace CHECK_PERIOD
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stopped = False
        self._next: Optional[float] = None
        self._failures = 0

    @property
    def next_check_at(self) -> Optional[float]:
        """Epoch seconds of the next periodic check; None before start() and after stop()."""
        with self._cond:
            return self._next

    @property
    def failures(self) -> int:
        """Consecutive periodic checks that found the updater in stage `error` (drives the back-off)."""
        with self._cond:
            return self._failures

    def _base(self) -> float:
        return self._period_override if self._period_override is not None else float(CHECK_PERIOD)

    def _wait(self) -> float:
        """The next wait in seconds: the base period, doubled per failure up to the cap, plus the jitter."""
        base = self._base()
        cap = ERROR_BACKOFF_MAX * base / CHECK_PERIOD
        period = min(base * (2 ** self._failures), cap)
        jitter = min(float(CHECK_JITTER), period * 0.1)
        return max(0.0, period + random.uniform(-jitter, jitter))

    def start(self) -> None:
        with self._cond:
            if self._thread is not None or self._stopped:
                return
            self._next = time.time() + self._wait()
            self._thread = threading.Thread(target=self._loop, name="selfupdate-schedule", daemon=True)
            self._thread.start()

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
                stage = self._stage()
                if stage == "error":
                    self._failures += 1
                else:
                    self._failures = 0
                self._next = time.time() + self._wait()
            try:
                if stage == "ready":
                    continue
                if not self._online():
                    self.log.debug("no network connection, update check skipped")
                    continue
                self.updater.check_background()
            except Exception:  # noqa: BLE001
                self.log.exception("scheduled update check failed")

    def _stage(self) -> str:
        status = getattr(self.updater, "status", None)
        if not callable(status):
            return ""
        try:
            return str((status() or {}).get("stage") or "")
        except Exception:  # noqa: BLE001
            return ""
