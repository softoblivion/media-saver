# Media Saver — architecture

## 1. Goal

A Windows desktop app, shipped as one portable `MediaSaver.exe`, that:

1. Downloads Instagram Reels (and video posts) and YouTube videos and Shorts in the highest quality the site serves
   at the moment, as MP4, or as MP3 audio.
2. Accepts many links at once (pasted text, drag and drop, clipboard), cleans them to the canonical form and
   downloads them as a queue; one mode (Instagram or YouTube) is active at a time, each with its own queue view.
3. Shows a preview (thumbnail, title, author, duration) of every queued link before it downloads.
4. Updates its download engines on every launch, so extractor breakage on the sites' side is fixed by upstream
   updates without a rebuild of the app, and updates itself from GitHub Releases.
5. Keeps every file it writes next to its own exe (`media-saver_data\`, `Downloads\`): nothing lands in the user profile.
6. Has a Liquid Glass UI (refraction, specular highlights, spring motion) with English UI text and embedded fonts.

## 2. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Window / UI host | pywebview 6 with the `edgechromium` backend (WebView2) | Chromium is the only engine where `backdrop-filter: url(#svg-filter)` works, which real refraction on the web needs. Flutter `liquid_glass_renderer` does not support Windows; WinUI has acrylic only. |
| UI | Plain HTML / CSS / JS (ES modules), no third-party libraries, no build step, no network at runtime; fonts embedded in `ui/assets/fonts` | Fast iteration, works offline, nothing to compile. |
| Backend | Python 3.14 | pywebview, pythonnet and PyInstaller support it; the stdlib covers HTTP, zip and subprocess control. |
| Download engine | Standalone `yt-dlp.exe` (nightly channel by default) run as a subprocess; fallback `gallery-dl.exe` (Instagram only) | Standalone builds carry their own Python and dependencies (incl. curl_cffi) and self-update, so the app never depends on its frozen Python being new enough. |
| Muxing / transcoding | `ffmpeg.exe` + `ffprobe.exe`: system PATH copy if present, otherwise downloaded once from yt-dlp/FFmpeg-Builds into `data\bin` | Merges DASH video + audio, runs the H.264 compatibility pass, encodes MP3. |
| Self-update | GitHub Releases of `softoblivion/media-saver` (asset `MediaSaver.exe` + `SHA256SUMS`), swap by a small `cmd` helper on restart | No installer, no service, no third-party updater; the same verified-download pattern as the engines. |
| Packaging | PyInstaller one-file, windowed, own icon | One exe to double-click. |

## 3. Repository layout

```
media-saver/
  README.md, LICENSE
  docs/ARCHITECTURE.md       this file
  docs/BRIDGE_API.md         JS <-> Python contract (methods, events, payload types)
  docs/ENGINE.md             update flow, engine command lines, error codes, site quirks, formats, preview
  docs/SELF_UPDATE.md        app self-update: release layout, check, verify, swap helper
  docs/UI_DESIGN.md          design system, Liquid Glass material, typography, motion, copy rules
  docs/BUILD.md              dev run, build, release procedure, troubleshooting
  docs/releases/vX.Y.Z.md    release notes
  docs/assets/               README images
  app/__init__.py            __version__ (the only place the version lives)
  app/main.py                entry: windows, single instance, engine lifecycle
  app/bridge.py              pywebview js_api class, event pump
  app/login.py               Instagram sign-in window, cookie capture
  app/winutil.py             clipboard, Explorer, single instance, DWM corners, frameless resize
  app/fake_engine.py         stand-in Engine for UI work without network (`--fake`)
  app/core/paths.py          AppPaths (portable runtime data locations)
  app/core/settings.py       settings schema, defaults, validation, persistence
  app/core/updater.py        bootstrap + update of yt-dlp, gallery-dl, ffmpeg
  app/core/selfupdate.py     check / download / verify / apply of a new MediaSaver.exe
  app/core/downloader.py     job queue, preview fetch, subprocess runner, progress parsing, formats, fallbacks
  app/core/auth.py           cookies.txt read/write, auth status
  app/core/linkclean.py      link extraction and canonicalisation for both platforms
  app/core/engine.py         facade used by the bridge (section 6)
  app/core/cli.py            headless harness: python -m app.core.cli <links>
  ui/                        index.html, css/, js/ (incl. js/mock-api.js), assets/ (icon, fonts)
  tests/                     unittest suite (test_core_*, test_linkclean, test_bridge_*)
  build/                     entry.py, MediaSaver.spec, build.ps1, make_icon.py, icon.ico
  requirements.txt           runtime dependencies (pywebview, pythonnet)
  requirements-dev.txt       build tools (pyinstaller, pillow)
  run_dev.bat                run from source
```

Build output (`release/`, `build/work`, `build/dist`), `.venv/` and the runtime folders created when running from
source (`media-saver_data/`, `Downloads/`) are not tracked.

Rules for all code: comments, docs and user-facing strings in English; `app/core` uses the Python standard library
only; tests use `unittest` (`python -m unittest discover -s tests`). Each module keeps its owning document current.

Module table (owner = the session that may write there during a task; see `_workspace/PARALLEL.md`):

| Module | Path | Owning doc | Owner |
|---|---|---|---|
| Core engine (links, queue, formats, preview, engine updater) | `app/core/linkclean.py`, `downloader.py`, `updater.py`, `cli.py`; `tests/test_linkclean.py`, `tests/test_core_*` except settings and engine | `ENGINE.md` | engine worker |
| Self-update | `app/core/selfupdate.py`; `tests/test_core_selfupdate.py`; `build/`; `app/fake_engine.py`; `tests/test_bridge_*` | `SELF_UPDATE.md`, `BUILD.md` | self-update worker |
| UI | `ui/**` | `UI_DESIGN.md` | ui worker |
| Shell and contracts | `app/main.py`, `bridge.py`, `login.py`, `winutil.py`, `app/core/engine.py`, `paths.py`, `settings.py`, `auth.py`; `tests/test_core_settings.py`, `tests/test_core_engine.py`; `README.md`, `ARCHITECTURE.md`, `BRIDGE_API.md`, release notes | `ARCHITECTURE.md`, `BRIDGE_API.md` | integrator |

## 4. Runtime data (portable)

`AppPaths` (in `app/core/paths.py`) resolves everything from one root:

- app dir = the folder of `MediaSaver.exe` (frozen) or the project root (from source); never the PyInstaller
  extraction folder.
- root = `%MEDIA_SAVER_HOME%` if set (tests set it to a scratch folder), else `<app dir>\media-saver_data`
  (`<project>_data`, workspace rule "Portable projects").
- `bin/` — yt-dlp.exe, gallery-dl.exe, ffmpeg.exe, ffprobe.exe (only when not taken from PATH), `versions.json`.
- `auth/cookies.txt` — Netscape cookie jar of the in-app Instagram sign-in.
- `settings.json`, `history.json` (finished / failed jobs of both platforms survive restarts, last 300).
- `cache/thumbs/`, `cache/jobs/<job_id>/` (info json, per-job cookie copy, temp files).
- `logs/app.log` (rotating, 2 MB x 3), `logs/shell.log`, `logs/jobs/<job_id>.log` (full engine stderr of a failed job).
- `webview/` — WebView2 profile (`storage_path`) of the sign-in window.
- `updates/` — downloaded `MediaSaver-<version>.exe`, the swap helper, the "applied" marker.
- default output dir: `<app dir>\Downloads` (`%MEDIA_SAVER_HOME%\Downloads` under the override).

No path in the app depends on `%LOCALAPPDATA%`, `%USERPROFILE%` or a drive letter (workspace rule "Portable
projects" in `_workspace/CONVENTIONS.md`). Moving the exe together with its `media-saver_data\` folder moves the
whole app.

## 5. Behaviour

### 5.1 Engine update on every launch

Runs in a background thread right after the window opens; the UI shows it in the engine status chip and never
blocks on it (links can be queued; jobs wait until the engine is ready).

1. yt-dlp: missing -> download `yt-dlp.exe` of the configured channel (nightly:
   `https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe`, stable:
   `https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe`), verify against the release
   `SHA2-256SUMS`. Present -> `yt-dlp.exe --update-to <channel>` (timeout 90 s).
2. gallery-dl (fallback engine for Instagram): stable channel only, from Codeberg
   (`https://codeberg.org/mikf/gallery-dl/releases/download/latest/gallery-dl.exe`), updated with its own `-U`.
   "Up to date" is decided for both engines by comparing the SHA-256 of the local exe with the release checksum file,
   so no GitHub API quota is used (details: `docs/ENGINE.md`).
3. ffmpeg: `bin/ffmpeg.exe` -> PATH -> download `ffmpeg-master-latest-win64-gpl.zip` from
   `https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/`, extract `ffmpeg.exe` and `ffprobe.exe` only.
   Own copy refreshed at most every 30 days. ffmpeg is required for merging and for mp3, so a machine without it
   gets the static build on the first launch; a job that needs it while it is missing fails with `ffmpeg_missing`
   and the updater fetches it on the next pass.
4. No network: keep existing binaries, status `offline`. Missing binary and no network: status `error` with retry.
5. Self-heal: a job failing with an extractor-type error (not login, not rate limit) triggers one forced update per
   app session, then the job is retried automatically once.

### 5.2 Download pipeline (per job)

1. Probe: `yt-dlp -J` (metadata, formats, thumbnail) with the auth ladder below; reused from the preview fetch
   (5.4) when that succeeded; thumbnail fetched and cached.
2. Download from the saved info json (`--load-info-json`) so the site is hit once for metadata, then media.
3. mp4: best video + best audio, sorted by resolution, then fps, then bitrate, capped by the platform's quality
   setting (`quality_instagram`: low = short side <= 480, medium <= 720, max = best; `quality_youtube`: 360,
   720, max = best up to 2160p); merge to MP4.
   mp3: best audio only, extracted / encoded to MP3 by ffmpeg (`-x --audio-format mp3 --audio-quality 0`).
4. H.264 compatibility (setting `h264_compat`, default on, mp4 only): if the chosen video codec is not AVC, transcode
   video with libx264 (CRF 16, preset slow, yuv420p), keep AAC audio or encode AAC 256k; prefer an AVC format at
   the same max resolution when one exists so no transcode is needed.
5. Output name from `filename_template` (default `{author}_{shortcode}`), extension by format; an existing file ->
   job done as "already downloaded" when `skip_existing` is on.
6. Auth ladder (Instagram): anonymous -> saved session cookies (`auth/cookies.txt`, per-job copy because yt-dlp
   writes the jar back) -> browser cookies only if a browser is chosen in settings -> gallery-dl with the session
   cookies. Error `login_required` with no session -> the UI offers the in-app sign-in.
   YouTube: anonymous -> browser cookies when a browser is chosen (age-restricted and members-only videos); no
   gallery-dl rung, no in-app sign-in.
7. Rate limit -> wait with backoff (30 s, 90 s) and retry, at most 2 times.

### 5.3 Link cleaning

Both platforms, from any text (`app/core/linkclean.py`, rules and edge cases in its docstring and tests):

- Instagram canonical form: `https://www.instagram.com/reel/<shortcode>` (`/reels/`, `/tv/`, `/<username>/reel/`
  map to `/reel/`; `/p/<shortcode>` stays `/p/`), nothing after the shortcode.
- YouTube canonical form: `https://www.youtube.com/watch?v=<id>` (kind `video`) and
  `https://www.youtube.com/shorts/<id>` (kind `shorts`); accepted inputs include `youtu.be/<id>`, `m.`, `music.`,
  `/embed/`, `/v/`, `/live/`, `youtube-nocookie.com`, `?v=` anywhere in the query; `t=`, `list=`, `si=`,
  `feature=` and fragments are dropped. Playlists, channels, searches are `unsupported_path`.
- The UI passes the current mode; a valid link of the other platform comes back as `other_platform` so the UI can
  offer to switch.

### 5.4 Preview of queued links

Right after `enqueue` a preview worker (one thread, serial, lowest priority) fetches metadata of every queued job
that has none: anonymous `yt-dlp -J` (Instagram) / `yt-dlp -J` (YouTube), stores the info json in the job cache,
fills `title`, `author`, `duration_s`, `thumbnail` and sets `previewed`. The download step reuses the stored info
when it is younger than 10 minutes and the same auth rung applies. A failed preview is silent (the job shows the
link until the download probes it). Preview fetches respect the engine gate and stop while the engine updates.

### 5.5 Modes and formats

- `settings.mode` is the platform shown by the UI; both platforms' jobs live in one job table and one history; the
  UI filters by `job.platform`. Duplicate detection is per platform + id. Concurrency is shared.
- `settings.format` is the default for new jobs; `enqueue(..., format)` and `set_job_format(id, format)` override it
  per job while queued. mp3 ignores the quality settings and `h264_compat`.
- Quality is a per-platform setting shown in the main window next to the mode and format controls (not in the
  settings sheet): `quality_instagram` and `quality_youtube`; a job uses the setting of its platform at start.

### 5.6 Instagram sign-in

`app/login.py` opens a second pywebview window on `https://www.instagram.com/accounts/login/`; the user signs in
there; the window polls cookies until `sessionid` for `.instagram.com` appears, writes all instagram.com cookies to
`auth/cookies.txt` via `app.core.auth.write_netscape_cookies`, calls `engine.reload_auth()` and closes. The app never
sees or stores the password.

### 5.7 App self-update

`app/core/selfupdate.py` (details and the release layout in `docs/SELF_UPDATE.md`):

1. On launch (setting `check_app_updates`) and on "Check for updates": read the latest release of
   `softoblivion/media-saver` (tag `vX.Y.Z`, asset `MediaSaver.exe`, asset `SHA256SUMS`, notes), compare SemVer
   with `app.__version__`.
2. "Download": stream the asset to `data\updates\MediaSaver-<version>.exe.part`, verify the byte size against the
   release asset and the SHA-256 against `SHA256SUMS`, rename to `.exe`; stage `ready`.
3. "Restart to update": write `data\updates\apply-update.cmd` (waits for this process to exit, replaces
   `MediaSaver.exe` keeping the old file as `MediaSaver.exe.old` until the new one started, starts the new exe,
   removes itself), start it detached, close the app. On the next launch the marker in `updates\` produces one
   "Updated to vX.Y.Z" toast and `MediaSaver.exe.old` is deleted.
4. Any failure leaves the current exe untouched; the UI shows the message and offers to retry.

The version lives in `app/__init__.py` only; the build writes it into the exe's version resource.

## 6. Engine facade (contract between `app/core` and `app/bridge.py`)

`app/core/engine.py`:

```python
EmitFn = Callable[[str, dict], None]   # (event_type, payload); called from worker threads

class Engine:
    paths: AppPaths                     # attribute, valid after __init__

    def __init__(self, emit: EmitFn, app_version: str, *, home=None, open_folder=None,
                 runner=None, net=None, use_system_ffmpeg=None, app_updates=None) -> None: ...
                                        # keyword-only extras are optional hooks for the shell, cli and tests
                                        # (app_updates=False: never check GitHub for app releases);
                                        # the shell passes open_folder=winutil.open_path
    def start(self) -> None: ...        # non-blocking: load settings/history, start updater, preview + queue workers,
                                        # the app-update check (when check_app_updates)
    def shutdown(self) -> None: ...     # cancel running jobs, kill process trees, save history; returns within 3 s

    def get_state(self) -> dict: ...    # State object of BRIDGE_API.md (app_version, engine, update, auth, settings, jobs)
    def clean_links(self, text: str, platform: str | None = None) -> dict: ...
    def enqueue(self, text: str, platform: str | None = None, fmt: str | None = None) -> dict: ...
    def set_job_format(self, job_id: str, fmt: str) -> dict | None: ...
    def cancel(self, job_id: str) -> bool: ...
    def retry(self, job_id: str) -> bool: ...
    def remove(self, job_id: str) -> bool: ...
    def clear_finished(self, platform: str | None = None) -> int: ...
    def job_file(self, job_id: str) -> str | None: ...   # final file path of a done job, if it exists

    def get_settings(self) -> dict: ...
    def set_settings(self, patch: dict) -> dict: ...     # validates, saves, emits "settings"; returns full settings
    def update_engine(self) -> None: ...                 # forced engine update in background, progress via "engine"

    def check_updates(self) -> dict: ...                 # start the app-update check; UpdateStatus now, progress via "update"
    def download_update(self) -> None: ...               # background download + verify, progress via "update"
    def apply_update(self) -> bool: ...                  # start the swap helper; True when the app must now exit

    def auth_status(self) -> dict: ...
    def reload_auth(self) -> dict: ...                   # re-read cookies.txt, emit "auth", return status
    def logout(self) -> dict: ...                        # delete cookies.txt, emit "auth", return status
    def import_cookies_file(self, path: str) -> dict: ...  # validate + copy a user cookies.txt, emit "auth"
```

Inside the core the facade delegates to these classes (their public surface is the contract between the
integrator's `engine.py` and the workers' modules):

```python
# app/core/linkclean.py
def clean_text(text: str, platform: str | None = None) -> dict          # CleanResult
def clean_link(raw: str) -> LinkResult                                   # LinkResult has platform and kind

# app/core/downloader.py
class Downloader:
    def add(self, url: str, shortcode: str, platform: str, kind: str, fmt: str) -> dict   # Job dict; starts the preview
    def active_keys(self) -> set[str]                   # "<platform>:<shortcode>" of queued / active jobs
    def set_format(self, job_id: str, fmt: str) -> dict | None
    def clear_finished(self, platform: str | None = None) -> int
    # unchanged: start, shutdown, set_concurrency, jobs, get, cancel, retry, remove, job_file, mark_waiting,
    #            load_history, save_history

# app/core/selfupdate.py
class SelfUpdater:
    def __init__(self, paths: AppPaths, current_version: str, on_status: Callable[[dict], None], *,
                 net: Net | None = None, repo: str = "softoblivion/media-saver",
                 asset_name: str = "MediaSaver.exe", exe_path: str | None = None,
                 on_toast: Callable[[str, str], None] | None = None,
                 spawn=None, urlopen=None) -> None       # spawn / urlopen: test hooks
    def status(self) -> dict                            # UpdateStatus
    def startup(self) -> None                           # delete MediaSaver.exe.old, read + remove the "applied" marker
                                                        # (returns nothing; emits a toast through on_toast when set)
    def check_background(self) -> None
    def check(self) -> dict                             # blocking; UpdateStatus
    def download_background(self) -> None
    def download(self) -> dict                          # blocking; verifies size + SHA-256; UpdateStatus
    def apply(self, launch: bool = True) -> bool        # write + start the helper; True when started (launch=False: tests)
```

Payload shapes (State, Job, EngineStatus, UpdateStatus, Auth, Settings, events) are defined once in
`docs/BRIDGE_API.md`.

## 7. Build and ship

`build/build.ps1` creates `.venv`, installs requirements, runs the unit tests, runs PyInstaller with
`build/MediaSaver.spec`, puts the result at `release/MediaSaver.exe` and writes `release/SHA256SUMS`
(`<sha256>  MediaSaver.exe`). Details and the release procedure in `docs/BUILD.md`.
Subprocesses are always started with `CREATE_NO_WINDOW` (a windowed exe must never flash consoles).
Release notes live in `docs/releases/`; the release assets are `MediaSaver.exe` and `SHA256SUMS`.
