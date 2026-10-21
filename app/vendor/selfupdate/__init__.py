"""Self-update of a portable one-file Windows exe from GitHub Releases (stdlib only).

Public API (docs/SELF_UPDATE.md):

    cfg = UpdateConfig(repo="owner/app", asset_name="App.exe", app_name="App", version="1.2.0",
                       updates_dir=r"...\\app_data\\updates", exe_path=<running exe or None>, on_status=callback)
    updater = SelfUpdater(cfg)
    updater.startup(); updater.check_background(); updater.download_background(); updater.apply()

    # in the app's entry point, before any window or single-instance mutex:
    if argv and argv[0] == APPLY_FLAG:
        sys.exit(run_apply(argv[1:]))

The package imports nothing outside itself, so it can be copied into an app as one folder.
"""
from .applymode import APPLY_FLAG, NO_LAUNCH_FLAG, clean_env, run_apply
from .fileio import atomic_write_bytes, atomic_write_json, atomic_write_text, parse_sums, replace_file, sha256_file
from .net import Net, OfflineError, ReleaseNet
from .updater import Messages, SelfUpdater, UpdateConfig, UpdateStatus, is_newer, parse_version

__version__ = "0.1.1"

__all__ = [
    "APPLY_FLAG", "NO_LAUNCH_FLAG", "Messages", "Net", "OfflineError", "ReleaseNet", "SelfUpdater", "UpdateConfig",
    "UpdateStatus", "__version__", "atomic_write_bytes", "atomic_write_json", "atomic_write_text", "clean_env",
    "is_newer", "parse_sums", "parse_version", "replace_file", "run_apply", "sha256_file",
]
