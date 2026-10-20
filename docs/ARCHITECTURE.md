# Reels Saver — architecture

## 1. Goal

A Windows desktop app, shipped as one `ReelsSaver.exe`, that:

1. Downloads Instagram Reels (and video posts) in the highest quality Instagram serves at the moment.
2. Accepts many links at once (pasted text, drag and drop, clipboard), cleans them to the canonical form and
   downloads them as a queue.
3. Updates its download engines on every launch, so extractor breakage on Instagram's side is fixed by upstream
   updates without a rebuild of the app.
4. Has a Liquid Glass UI (refraction, specular highlights, spring motion) with English UI text.

## 2. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Window / UI host | pywebview 6 with the `edgechromium` backend (WebView2) | Chromium is the only engine where `backdrop-filter: url(#svg-filter)` works, which real refraction on the web needs. Flutter `liquid_glass_renderer` does not support Windows; WinUI has acrylic only. |
| UI | Plain HTML / CSS / JS (ES modules), no third-party libraries, no build step, no network at runtime | Fast iteration, works offline, nothing to compile. |
| Backend | Python 3.14 | pywebview, pythonnet and PyInstaller support it; the stdlib covers HTTP, zip and subprocess control. |
| Download engine | Standalone `yt-dlp.exe` (nightly channel by default) run as a subprocess; fallback `gallery-dl.exe` | Standalone builds carry their own Python and dependencies (incl. curl_cffi) and self-update, so the app never depends on its frozen Python being new enough. |
| Muxing / transcoding | `ffmpeg.exe` + `ffprobe.exe`: system PATH copy if present, otherwise downloaded once from yt-dlp/FFmpeg-Builds | Merges DASH video + audio and runs the H.264 compatibility pass. |
| Packaging | PyInstaller one-file, windowed, own icon | One exe to double-click. |

## 3. Repository layout

```
reels-saver/
  README.md, LICENSE
  docs/ARCHITECTURE.md       this file
  docs/BRIDGE_API.md         JS <-> Python contract (methods, events, payload types)
  docs/ENGINE.md             update flow, engine command lines, error codes, Instagram quirks
  docs/UI_DESIGN.md          design system, Liquid Glass material, motion, copy rules
  docs/BUILD.md              dev run, build, troubleshooting
  docs/releases/vX.Y.Z.md    release notes
  docs/assets/               README images
  app/__init__.py            __version__
  app/main.py                entry: windows, single instance, engine lifecycle
  app/bridge.py              pywebview js_api class, event pump
  app/login.py               Instagram sign-in window, cookie capture
  app/winutil.py             clipboard, Explorer, single instance, DWM corners, frameless resize
  app/fake_engine.py         stand-in Engine for UI work without network (`--fake`)
  app/core/paths.py          AppPaths (runtime data locations)
  app/core/settings.py       settings schema, defaults, validation, persistence
  app/core/updater.py        bootstrap + update of yt-dlp, gallery-dl, ffmpeg
  app/core/downloader.py     job queue, subprocess runner, progress parsing, fallbacks
  app/core/auth.py           cookies.txt read/write, auth status
  app/core/linkclean.py      link extraction and canonicalisation
  app/core/engine.py         facade used by the bridge (section 6)
  app/core/cli.py            headless harness: python -m app.core.cli <links>
  ui/                        index.html, css/, js/ (incl. js/mock-api.js), assets/
  tests/                     unittest suite (test_core_*, test_linkclean, test_bridge_*)
  build/                     entry.py, ReelsSaver.spec, build.ps1, make_icon.py, icon.ico
  requirements.txt           runtime dependencies (pywebview, pythonnet)
  requirements-dev.txt       build tools (pyinstaller, pillow)
  run_dev.bat                run from source
```

Build output (`release/ReelsSaver.exe`, `build/work`, `build/dist`) and `.venv/` are not tracked.

Rules for all code: comments, docs and user-facing strings in English; `app/core` uses the Python standard library
only; tests use `unittest` (`python -m unittest discover -s tests`). Each module keeps its owning document current:
`ENGINE.md` for `app/core`, `UI_DESIGN.md` for `ui/`, `BUILD.md` for the shell and the build.

## 4. Runtime data

`AppPaths` (in `app/core/paths.py`) resolves everything from one root:

- root = `%REELS_SAVER_HOME%` if set (tests set it to a scratch folder), else `%LOCALAPPDATA%\ReelsSaver`.
- `bin/` — yt-dlp.exe, gallery-dl.exe, ffmpeg.exe, ffprobe.exe (only when not taken from PATH).
- `auth/cookies.txt` — Netscape cookie jar of the in-app Instagram sign-in.
- `settings.json`, `history.json` (finished / failed jobs survive restarts, last 300).
- `cache/thumbs/`, `cache/jobs/<job_id>/` (info json, per-job cookie copy, temp files).
- `logs/app.log` (rotating, 2 MB x 3), `logs/shell.log`, `logs/jobs/<job_id>.log` (full engine stderr of a failed job).
- `webview/` — WebView2 profile (`storage_path`) of the sign-in window.
- default output dir: `%USERPROFILE%\Downloads\Reels Saver`.

## 5. Behaviour

### 5.1 Engine update on every launch

Runs in a background thread right after the window opens; the UI shows it in the engine status chip and never
blocks on it (links can be queued; jobs wait until the engine is ready).

1. yt-dlp: missing -> download `yt-dlp.exe` of the configured channel (nightly:
   `https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe`, stable:
   `https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe`), verify against the release
   `SHA2-256SUMS`. Present -> `yt-dlp.exe --update-to <channel>` (timeout 90 s).
2. gallery-dl (fallback engine): stable channel only, from Codeberg
   (`https://codeberg.org/mikf/gallery-dl/releases/download/latest/gallery-dl.exe`; the GitHub releases no longer
   carry binaries), updated with its own `-U`.
   "Up to date" is decided for both engines by comparing the SHA-256 of the local exe with the release checksum file,
   so no GitHub API quota is used (details: `docs/ENGINE.md`).
3. ffmpeg: `bin/ffmpeg.exe` -> PATH -> download `ffmpeg-master-latest-win64-gpl.zip` from
   `https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/`, extract `ffmpeg.exe` and `ffprobe.exe` only.
   Own copy refreshed at most every 30 days.
4. No network: keep existing binaries, status `offline`. Missing binary and no network: status `error` with retry.
5. Self-heal: a job failing with an extractor-type error (not login, not rate limit) triggers one forced update per
   app session, then the job is retried automatically once.

### 5.2 Download pipeline (per job)

1. Probe: `yt-dlp -J` (metadata, formats, thumbnail) with the auth ladder below; thumbnail fetched and cached.
2. Download from the saved info json (`--load-info-json`) so Instagram is hit once for metadata, then media.
3. Quality "max": best video + best audio, sorted by resolution, then fps, then bitrate; merge to MP4.
4. H.264 compatibility (setting `h264_compat`, default on): if the chosen video codec is not AVC, transcode video
   with libx264 (CRF 16, preset slow, yuv420p), keep AAC audio or encode AAC 256k; prefer an AVC format at the same
   max resolution when one exists so no transcode is needed.
5. Output name from `filename_template` (default `{author}_{shortcode}`); an existing file -> job done as "already
   downloaded" when `skip_existing` is on.
6. Auth ladder: anonymous -> saved session cookies (`auth/cookies.txt`, per-job copy because yt-dlp writes the jar
   back) -> browser cookies only if a browser is chosen in settings -> gallery-dl with the session cookies.
   Error `login_required` with no session -> the UI offers the in-app sign-in.
7. Rate limit -> wait with backoff (30 s, 90 s) and retry, at most 2 times.

### 5.3 Link cleaning

Canonical form: `https://www.instagram.com/reel/<shortcode>` — scheme, host, `/reel/`, shortcode, nothing after it
(no trailing slash, no query such as `?igsh=`, no fragment). `/reels/`, `/tv/`, `/<username>/reel/` map to `/reel/`;
`/p/<shortcode>` stays `/p/<shortcode>` (posts can be carousels). Details and edge cases: `app/core/linkclean.py`
docstring and its tests.

### 5.4 Instagram sign-in

`app/login.py` opens a second pywebview window on `https://www.instagram.com/accounts/login/`; the user signs in
there; the window polls cookies until `sessionid` for `.instagram.com` appears, writes all instagram.com cookies to
`auth/cookies.txt` via `app.core.auth.write_netscape_cookies`, calls `engine.reload_auth()` and closes. The app never
sees or stores the password.

## 6. Engine facade (contract between `app/core` and `app/bridge.py`)

`app/core/engine.py`:

```python
EmitFn = Callable[[str, dict], None]   # (event_type, payload); called from worker threads

class Engine:
    paths: AppPaths                     # attribute, valid after __init__

    def __init__(self, emit: EmitFn, app_version: str, *, home=None, open_folder=None,
                 runner=None, net=None, use_system_ffmpeg=None) -> None: ...
                                        # keyword-only extras are optional hooks for the shell, cli and tests;
                                        # the shell passes open_folder=winutil.open_path
    def start(self) -> None: ...        # non-blocking: load settings/history, start updater + queue workers
    def shutdown(self) -> None: ...     # cancel running jobs, kill process trees, save history; returns within 3 s

    def get_state(self) -> dict: ...    # State object of BRIDGE_API.md (app_version, engine, auth, settings, jobs)
    def clean_links(self, text: str) -> dict: ...
    def enqueue(self, text: str) -> dict: ...
    def cancel(self, job_id: str) -> bool: ...
    def retry(self, job_id: str) -> bool: ...
    def remove(self, job_id: str) -> bool: ...
    def clear_finished(self) -> int: ...
    def job_file(self, job_id: str) -> str | None: ...   # final file path of a done job, if it exists

    def get_settings(self) -> dict: ...
    def set_settings(self, patch: dict) -> dict: ...     # validates, saves, emits "settings"; returns full settings
    def update_engine(self) -> None: ...                 # forced update in background, progress via "engine"

    def auth_status(self) -> dict: ...
    def reload_auth(self) -> dict: ...                   # re-read cookies.txt, emit "auth", return status
    def logout(self) -> dict: ...                        # delete cookies.txt, emit "auth", return status
    def import_cookies_file(self, path: str) -> dict: ...  # validate + copy a user cookies.txt, emit "auth"
```

Payload shapes (State, Job, EngineStatus, Auth, Settings, events) are defined once in `docs/BRIDGE_API.md`.

## 7. Build and ship

`build/build.ps1` creates `.venv`, installs requirements, runs the unit tests, runs PyInstaller with
`build/ReelsSaver.spec`, and puts the result at `release/ReelsSaver.exe`. Details in `docs/BUILD.md`.
Subprocesses are always started with `CREATE_NO_WINDOW` (a windowed exe must never flash consoles).
Release notes live in `docs/releases/`; the release asset is `ReelsSaver.exe`.
