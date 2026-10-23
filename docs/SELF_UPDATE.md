# App self-update

Scope: how the app finds, downloads, verifies and installs its own new releases. Code: `app/core/selfupdate.py` (Media Saver's configuration, an adapter) on top of the
vendored package `app/vendor/selfupdate/` (the mechanism); tests: `tests/test_core_selfupdate.py`. The facade calls
(`check_updates`, `download_update`, `apply_update`, the `update` event) are defined in `ARCHITECTURE.md` section 6
and `BRIDGE_API.md`; the payload is `UpdateStatus` there. This file owns the Media Saver side: the release layout,
the configuration values, the messages as the user sees them, the history of the update path and how to test it.

## 1. The vendored package

`app/vendor/selfupdate/` is a copy of the `selfupdate` package (version in its `VENDORED.txt`), maintained outside
this repository together with its own test suite and shared with other apps. Never edit the copy here: changes
are made in the package and the copy is replaced as a whole with the maintainer's sync script,

```
vendor-sync.ps1 -Lib selfupdate -Project media-saver [-Check]
```

then this project's full tests and build run. `-Check` only reports whether the copy differs. The package's tests
run where it is maintained; `tests/test_core_selfupdate.py` here pins Media Saver's configuration and the calls the
app relies on. The package imports nothing outside itself and only the Python standard library.

Besides the updater, the package provides the helpers the rest of `app/core` uses: `Net`, `OfflineError`,
`parse_sums`, `sha256_file`, `replace_file` (re-exported by `app/core/updater.py`) and `atomic_write_bytes`,
`atomic_write_text`, `atomic_write_json` (re-exported by `app/core/paths.py`).

| Module | Contents |
|---|---|
| `updater.py` | `UpdateConfig`, `SelfUpdater`, `UpdateStatus`, `Messages`, version parsing |
| `applymode.py` | `run_apply` (the `--apply-update` mode), `clean_env`, the swap and the wait for the old process |
| `net.py` | `Net`, `ReleaseNet` (GitHub API headers), `OfflineError` |
| `fileio.py` | atomic writes, retried replace, SHA-256, checksum file parsing |

## 2. Configuration (`app/core/selfupdate.py`)

| `UpdateConfig` field | Media Saver value |
|---|---|
| `repo` | `softoblivion/media-saver` |
| `asset_name` | `MediaSaver.exe` (downloads: `updates\MediaSaver-<version>.exe`) |
| `app_name` | `Media Saver` |
| `version` | `app.__version__` |
| `updates_dir` | `AppPaths.updates_dir` = `media-saver_data\updates` |
| `exe_path` | `AppPaths.exe_path` (the frozen exe; `None` from source) |
| `on_status` / `on_toast` | `Engine._on_update_status` (the `update` event) / a `toast` event |
| `net` | the Engine's `net` argument (tests); otherwise `ReleaseNet` |
| `user_agent` | `app.core.updater.UA`, the same as the engine downloads |
| `legacy_files` | `apply-update.cmd` (helper of 2.0.0 / 2.1.0, deleted on startup) |
| `logger_name` | `mediasaver.core.selfupdate` (lands in `logs\app.log`) |
| `release_url_env` | `MEDIA_SAVER_RELEASE_URL`: when set, its value (the URL of a JSON document in the GitHub API "latest release" format) replaces the GitHub lookup; the redirect fallback is skipped then. For end-to-end tests only |

`SelfUpdater(paths, current_version, on_status, *, net, exe_path, on_toast, spawn, urlopen)` keeps the constructor
the Engine has always called and builds the configuration from it. `app/main.py` routes
`--apply-update <pid> <target>` to `run_apply` before the single-instance mutex and before any window.

## 3. Release layout (what the updater expects on GitHub)

The newest **published, non-draft, non-prerelease** release is the one `GET /releases/latest` returns, so a
prerelease is never offered.

| Item | Rule |
|---|---|
| Tag | `vX.Y.Z` (SemVer, leading `v` stripped for display and comparison). A tag that does not start with digits is an error ("Could not read the release version"). |
| Asset `MediaSaver.exe` | the one-file build, exactly this name |
| Asset `SHA256SUMS` | text file, line `<sha256 lowercase>  MediaSaver.exe` (two spaces; `build\build.ps1` writes it) |
| Release body | Markdown release notes, shown in the update dialog as they are (`docs/releases/vX.Y.Z.md`) |

Both asset names are looked up by name. Rename either and every installed copy reports "The release has no
MediaSaver.exe" / "The release has no checksum file". v1.0.0 (asset `ReelsSaver.exe`, no `SHA256SUMS`) predates the
updater, so 1.x installs cannot update themselves; the first self-updating version is 2.0.0, and the first update
that can be offered is 2.0.1 or later.

## 4. States and messages

`idle` -> `checking` -> `up_to_date` | `available` | `error`; `available` -> `downloading` -> `ready` | `error`.
`ready` -> the app exits and the downloaded exe takes over.

| Stage | Message | Notes |
|---|---|---|
| `idle` | Updates are not checked yet | initial |
| `checking` | Checking for updates… | |
| `up_to_date` | Media Saver is up to date | `latest <= current` |
| `available` | Version X.Y.Z is available | `latest`, `notes`, `size`, `published_at` filled |
| `downloading` | Downloading Media Saver X.Y.Z… 42% | `progress` 0..1 |
| `ready` | Version X.Y.Z is ready to install | `can_apply` true when the app runs as the packaged exe; from source the text gets " (applies to the packaged MediaSaver.exe only)" and `can_apply` stays false |
| `error` | No internet connection / GitHub limited the request, try again later / The release has no MediaSaver.exe / The release has no checksum file / The downloaded file is damaged (checksum mismatch) / Could not read the release version / No release has been published yet / Could not save the update file / Could not start the update helper / The update is not ready to install | `check_updates()` or `download_update()` may be called again |

After a finished update the next launch shows the toast "Media Saver updated to vX.Y.Z" once.

## 5. How it works (summary; the code of the package is the reference)

- Check: GitHub API `releases/latest`; on 403 / 429 or no answer, the `github.com/<repo>/releases/latest` redirect
  plus a `HEAD` for the size. SemVer compare against `app.__version__`. A verified file already in `updates\` goes
  straight to `ready`.
- When: on every launch (`Engine.start`; only the headless CLI and tests pass `app_updates=False`), on the "Check
  for updates" button, and every 5 minutes while the app runs (the vendored `UpdateScheduler`, `Engine.update_scheduler`; fixed
  period, not a setting; a failed check is retried later with a wait that doubles up to one hour; no check without
  a network connection). The background download is the engine's job
  (`Engine._maybe_auto_download`), setting `auto_download_updates` (default on); the page only shows the status;
  the restart is always a click: ARCHITECTURE.md 5.7, items 5-6.
- Download: `SHA256SUMS` first (nothing is downloaded without a checksum), then the exe to
  `updates\MediaSaver-<v>.exe.part`; size and SHA-256 verified, then renamed.
- Apply: `updates\applied.json` is written, `updates\MediaSaver-<v>.exe --apply-update <pid> <MediaSaver.exe>`
  starts with a clean PyInstaller environment and no console, the app exits. The new exe waits for the pid (at
  most 120 s), renames `MediaSaver.exe` to `MediaSaver.exe.old`, copies itself over it, starts it; every step goes
  to `updates\apply.log`. A failed copy restores the old exe. The next launch deletes `.old` (retried in the
  background for about 10 s while the exiting process still holds it), the marker, the legacy helper and every download not newer than the running version.

Why not a script: 2.0.0 and 2.1.0 used a `cmd` helper started with `DETACHED_PROCESS`; a detached `cmd` has no
console, so every console tool it ran (`tasklist`, `find`, `ping`) opened a visible console of its own and the
`tasklist | find` pipe hung waiting for keyboard input (seen on a friend's PC: a console titled
`find.exe " <pid> "` after "Restart to update"). The helper could not be fixed from the new side: it is the old exe
that starts it, so 2.0.0 and 2.1.0 installs need one manual download of a newer `MediaSaver.exe`.

## 6. Failure handling

| Failure | Result |
|---|---|
| Offline / GitHub limit | status `error` with the message; nothing on disk changes |
| Asset or checksum file missing | status `error`; nothing downloaded |
| Interrupted download, size or checksum mismatch | `.part` deleted; status `error`; the next `download_update()` starts over |
| Disk full / folder not writable | "Could not save the update file" |
| File changed between download and apply | file deleted, "The downloaded file is damaged (checksum mismatch)", `apply()` returns `False`, the app keeps running |
| The new exe cannot be started in apply mode | marker removed, "Could not start the update helper", app keeps running |
| Exe locked for 20 s or the copy fails | old exe stays (or is restored); the next launch finds a marker with another version, logs "did not apply", and the update is offered again |
| Running from source | `can_apply` false; `apply()` refuses |

## 7. Testing

`tests/test_core_selfupdate.py` runs without network or a real exe: the configuration values, the default net
and User-Agent, Media Saver's messages and file names through a check / download / apply with a fake net and an
injected `spawn`, the startup toast and the legacy helper cleanup, logging to `mediasaver.core.selfupdate`, the
shared helpers being the vendored ones, `VENDORED.txt`, and `main()` routing `--apply-update` to `run_apply`.
`MEDIA_SAVER_HOME` is a temp folder.

`tests/test_bridge_fake_engine.py` and `app/fake_engine.py` simulate the whole flow for the UI: `run_dev.bat --fake`
starts with an update offered (next minor version, a sample changelog, 13 MB), "Download" runs three seconds,
"Restart to update" closes the window. `MEDIA_SAVER_FAKE_UPDATE=uptodate` or `=error` simulates the other outcomes.

Frozen build check (no release needed), from a scratch folder with `MEDIA_SAVER_HOME` set: start the built exe and
open the version panel: it shows "Media Saver is up to date" (or the newer version) and "Checked … ago". Then copy
the exe to `updates\MediaSaver-9.9.9.exe` and run
`MediaSaver-9.9.9.exe --apply-update 1 "<scratch>\Target Dir (x86)\MediaSaver.exe" --no-launch` against a dummy
target: exit code 0, the target holds the new exe, `MediaSaver.exe.old` the dummy, `apply.log` ends with
"swap done".

Read-only smoke test against the real API (never downloads): from the project root with `MEDIA_SAVER_HOME` set to a
scratch folder,
`.venv\Scripts\python -c "from app.core.paths import AppPaths; from app.core.selfupdate import SelfUpdater; print(SelfUpdater(AppPaths(), '0.0.1', print).check())"`.
With a version at or above the newest tag it reports "Media Saver is up to date".

End-to-end against a local server (no release needed): build the version under test (N) and a build of N+1 (version
bumped locally, not committed); put the N+1 exe as `MediaSaver.exe`, its `SHA256SUMS` and a `latest.json`
(`{"tag_name": "vN+1", "body": "...", "assets": [{"name": "MediaSaver.exe", "size": <bytes>,
"browser_download_url": "http://127.0.0.1:8765/MediaSaver.exe"}, {"name": "SHA256SUMS", ...}]}`) in a folder and
serve it with `python -m http.server 8765`; start N from a scratch folder with `MEDIA_SAVER_HOME` and
`MEDIA_SAVER_RELEASE_URL=http://127.0.0.1:8765/latest.json`; the update downloads in the background; press
"Restart". Assert: N+1 runs from the same path, no console window, `MediaSaver.exe.old` gone within ~15 s,
settings and history in the data folder unchanged.

End-to-end by hand (needs two releases): build version N, publish N+1 as described in `BUILD.md` section 7, start
the exe of version N from a scratch folder with `MEDIA_SAVER_HOME` set, press "Check for updates", "Download",
"Restart to update"; the window reopens on N+1, shows the toast, and `MediaSaver.exe.old` is gone.

Not covered by automated tests: a real GitHub release, the PyInstaller one-file restart in a frozen build,
antivirus locking the exe, SmartScreen.
