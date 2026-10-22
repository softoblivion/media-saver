"""User settings: schema, defaults, validation, atomic persistence (BRIDGE_API.md `Settings`).

Validation errors are ValueError with an English message (the bridge passes it to the UI as is).
Loading never fails: a missing or broken file gives defaults, bad values fall back per key, unknown keys
are dropped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

from .paths import atomic_write_json, default_output_dir

log = logging.getLogger("mediasaver.core.settings")

QUALITIES_INSTAGRAM = ("low", "medium", "max")   # short side <= 480 / <= 720 / best
QUALITIES_YOUTUBE = ("360", "720", "max")        # short side <= 360 / <= 720 / best (1080p, 1440p, 2160p)
QUALITIES_TIKTOK = ("low", "medium", "max")      # short side <= 480 / <= 720 / best (1080p)
QUALITIES_TWITTER = ("low", "medium", "max")     # short side <= 480 / <= 720 / best (1080p)
PLATFORMS = ("instagram", "youtube", "tiktok", "twitter")
QUALITY_KEYS = {"instagram": "quality_instagram", "youtube": "quality_youtube", "tiktok": "quality_tiktok",
                "twitter": "quality_twitter"}
FORMATS = ("mp4", "mp3")
CHANNELS = ("nightly", "stable")
AUTH_MODES = ("auto", "always", "never")
BROWSERS = ("", "firefox", "edge", "chrome", "brave", "opera")
THEMES = ("dusk", "nova", "frost", "bloom")  # the UI kit's palettes; the UI applies the confirmed value
DEFAULT_THEME = "dusk"
TEMPLATE_TOKENS = ("author", "shortcode", "date", "title")
CONCURRENCY_MIN, CONCURRENCY_MAX = 1, 4
TEMPLATE_MAX_LEN = 100

_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_BAD_TEMPLATE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def defaults() -> dict[str, Any]:
    return {
        "output_dir": default_output_dir(),
        "filename_template": "{author}_{shortcode}",
        "mode": "instagram",
        "format": "mp4",
        "quality_instagram": "max",
        "quality_youtube": "max",
        "quality_tiktok": "max",
        "quality_twitter": "max",
        "h264_compat": True,
        "skip_existing": True,
        "concurrency": 2,
        "update_channel": "nightly",
        "auto_update": True,
        "auto_download_updates": True,
        "auth_mode": "auto",
        "browser_cookies": "",
        "clipboard_watch": True,
        "open_folder_when_done": False,
        "sound_on_done": True,
        "theme": DEFAULT_THEME,
    }


KEYS = tuple(defaults().keys())


def _bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"Setting '{key}' must be true or false")


def _choice(key: str, value: Any, choices: tuple, label: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and key == "quality_youtube":
        value = str(int(value))
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Invalid value for '{label}': {value!r}")
    return value


def validate_value(key: str, value: Any) -> Any:
    """Return the normalised value or raise ValueError (English message)."""
    if key == "output_dir":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Output folder is not set")
        path = os.path.normpath(os.path.expandvars(os.path.expanduser(value.strip())))
        if not os.path.isabs(path):
            raise ValueError("Output folder must be a full path (for example, C:\\Videos)")
        if os.path.exists(path) and not os.path.isdir(path):
            raise ValueError("A file already exists at this path; choose a folder")
        return path
    if key == "filename_template":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("The file name template cannot be empty")
        value = value.strip()
        if len(value) > TEMPLATE_MAX_LEN:
            raise ValueError(f"The file name template is too long (over {TEMPLATE_MAX_LEN} characters)")
        tokens = _TOKEN_RE.findall(value)
        unknown = [t for t in tokens if t not in TEMPLATE_TOKENS]
        if unknown:
            raise ValueError("Unknown placeholder in the template: {" + unknown[0] + "}. "
                             "Available: {author} {shortcode} {date} {title}")
        if "shortcode" not in tokens:
            raise ValueError("The file name template must contain {shortcode}, otherwise files will overwrite each other")
        literal = _TOKEN_RE.sub("", value)
        if "{" in literal or "}" in literal:
            raise ValueError("The template has an unclosed curly brace")
        if _BAD_TEMPLATE_CHARS.search(literal):
            raise ValueError('The template cannot contain the characters < > : " / \\ | ? *')
        return value
    if key == "quality_instagram":
        return _choice(key, value, QUALITIES_INSTAGRAM, "Instagram quality")
    if key == "quality_youtube":
        return _choice(key, value, QUALITIES_YOUTUBE, "YouTube quality")
    if key == "quality_tiktok":
        return _choice(key, value, QUALITIES_TIKTOK, "TikTok quality")
    if key == "quality_twitter":
        return _choice(key, value, QUALITIES_TWITTER, "X quality")
    if key == "mode":
        return _choice(key, value, PLATFORMS, "Mode")
    if key == "format":
        return _choice(key, value, FORMATS, "Format")
    if key in ("h264_compat", "skip_existing", "auto_update", "auto_download_updates", "clipboard_watch",
               "open_folder_when_done", "sound_on_done"):
        return _bool(key, value)
    if key == "concurrency":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError("The number of simultaneous downloads must be a whole number")
        value = int(value)
        if not CONCURRENCY_MIN <= value <= CONCURRENCY_MAX:
            raise ValueError(f"Simultaneous downloads must be between {CONCURRENCY_MIN} and {CONCURRENCY_MAX}")
        return value
    if key == "update_channel":
        return _choice(key, value, CHANNELS, "Update channel")
    if key == "auth_mode":
        return _choice(key, value, AUTH_MODES, "Sign-in mode")
    if key == "browser_cookies":
        if value is None:
            value = ""
        return _choice(key, value, BROWSERS, "Browser for cookies")
    if key == "theme":
        # An unknown name (an older or edited file, a stale UI) falls back to the default rather than failing.
        return value if isinstance(value, str) and value in THEMES else DEFAULT_THEME
    raise KeyError(key)


def apply_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Validate a patch against the current settings; unknown keys are ignored. Returns a new dict."""
    if not isinstance(patch, dict):
        raise ValueError("Settings must be an object")
    result = dict(current)
    for key, value in patch.items():
        if key not in KEYS:
            log.info("ignoring unknown settings key %r", key)
            continue
        result[key] = validate_value(key, value)
    return result


def load(path: str) -> dict[str, Any]:
    """Defaults merged with the valid values of the file; never raises."""
    result = defaults()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return result
    except (OSError, ValueError) as exc:
        log.warning("settings file %s unreadable (%s); using defaults", path, exc)
        return result
    if not isinstance(data, dict):
        log.warning("settings file %s is not an object; using defaults", path)
        return result
    for key in KEYS:
        if key in data:
            try:
                result[key] = validate_value(key, data[key])
            except (ValueError, KeyError) as exc:
                log.warning("settings key %r has a bad value (%s); using default", key, exc)
    return result


def save(path: str, settings: dict[str, Any]) -> None:
    atomic_write_json(path, {k: settings[k] for k in KEYS if k in settings})


class SettingsStore:
    """Thread-safe holder of the current settings, persisted on every change."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data = load(path)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def update(self, patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate, save and return (new_settings, changed_keys_with_old_values)."""
        with self._lock:
            new = apply_patch(self._data, patch)
            changed = {k: self._data[k] for k in KEYS if new[k] != self._data[k]}
            if changed:
                save(self.path, new)
                self._data = new
            return dict(new), changed
