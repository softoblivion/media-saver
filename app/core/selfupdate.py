"""App self-update: Media Saver's configuration of the vendored `selfupdate` package (docs/SELF_UPDATE.md).

The mechanism (check GitHub Releases, download + verify, the `--apply-update` mode of the downloaded exe) lives in
`app/vendor/selfupdate`; this module only supplies the values that make it Media Saver's updater and keeps the
calls of `engine.py` and `main.py` as they were. Statuses are UpdateStatus dicts (docs/BRIDGE_API.md).
"""
from __future__ import annotations

from typing import Any, Callable

from ..vendor import selfupdate as _lib
from ..vendor.selfupdate import APPLY_FLAG, clean_env, run_apply  # noqa: F401  (re-exported for app.main)
from .paths import AppPaths
from .updater import UA

REPO = "softoblivion/media-saver"
ASSET_NAME = "MediaSaver.exe"
APP_NAME = "Media Saver"
LEGACY_FILES = ("apply-update.cmd",)   # cmd helper of 2.0.0 / 2.1.0, removed on startup
LOGGER_NAME = "mediasaver.core.selfupdate"
RELEASE_URL_ENV = "MEDIA_SAVER_RELEASE_URL"   # end-to-end tests: URL of a local "latest release" JSON


def make_config(paths: AppPaths, current_version: str, on_status: Callable[[dict], None], *,
                net: Any = None, exe_path: str | None = None,
                on_toast: Callable[[str, str], None] | None = None) -> _lib.UpdateConfig:
    return _lib.UpdateConfig(
        repo=REPO, asset_name=ASSET_NAME, app_name=APP_NAME, version=current_version,
        updates_dir=paths.updates_dir, exe_path=exe_path if exe_path is not None else paths.exe_path,
        on_status=on_status, on_toast=on_toast, net=net, user_agent=UA,
        legacy_files=LEGACY_FILES, logger_name=LOGGER_NAME, release_url_env=RELEASE_URL_ENV,
    )


class SelfUpdater(_lib.SelfUpdater):
    """The vendored updater configured for Media Saver; the constructor keeps the Engine's call."""

    def __init__(self, paths: AppPaths, current_version: str, on_status: Callable[[dict], None], *,
                 net: Any = None, exe_path: str | None = None, on_toast: Callable[[str, str], None] | None = None,
                 spawn: _lib.applymode.SpawnFn | None = None, urlopen: Callable[..., Any] | None = None) -> None:
        super().__init__(make_config(paths, current_version, on_status, net=net, exe_path=exe_path,
                                     on_toast=on_toast), spawn=spawn, urlopen=urlopen)
        self.paths = paths
