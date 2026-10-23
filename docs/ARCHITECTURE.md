# Media Saver — architecture

## 1. Goal

A Windows desktop app, shipped as one portable `MediaSaver.exe`, that:

1. Downloads Instagram Reels (and video posts), YouTube videos and Shorts, TikTok videos and X (Twitter) posts in
   the highest quality the site serves at the moment, as MP4, or as MP3 audio.
2. Accepts many links at once (pasted text, drag and drop, clipboard), cleans them to the canonical form and
   downloads them as a queue; one mode (Instagram, YouTube, TikTok or X) is active at a time, each with its own
   queue view.
3. Shows a preview (thumbnail, title, author, duration) of every queued link before it downloads.
4. Updates its download engines on every launch, so extractor breakage on the sites' side is fixed by upstream
   updates without a rebuild of the app, and updates itself from GitHub Releases.
5. Keeps every file it writes next to its own exe (`media-saver_data\`): nothing of the app's state lands in the
   user profile. Saved videos go to the user's `Videos\Media-Saver` by default (the user picks another folder
   in Settings).
6. Has a Liquid Glass UI (refraction, specular highlights, spring motion) with English UI text and embedded fonts.

## 2. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Window / UI host | pywebview 6 with the `edgechromium` backend (WebView2) | Chromium is the only engine where `backdrop-filter: url(#svg-filter)` works, which real refraction on the web needs. Flutter `liquid_glass_renderer` does not support Windows; WinUI has acrylic only. |
| UI | Plain HTML / CSS / JS (ES modules), no third-party libraries, no build step, no network at runtime; the shared Liquid UI design kit vendored in `ui/vendor/liquid_ui` (material, controls, overlays, toasts, scene, update control, embedded fonts) | Fast iteration, works offline, nothing to compile; one design shared with the other desktop apps. |
| Backend | Python 3.14 | pywebview, pythonnet and PyInstaller support it; the stdlib covers HTTP, zip and subprocess control. |
| Download engine | Standalone `yt-dlp.exe` (nightly channel by default) run as a subprocess; fallback `gallery-dl.exe` (Instagram only) | Standalone builds carry their own Python and dependencies (incl. curl_cffi) and self-update, so the app never depends on its frozen Python being new enough. |
| Muxing / transcoding | `ffmpeg.exe` + `ffprobe.exe`: system PATH copy if present, otherwise downloaded once from yt-dlp/FFmpeg-Builds into `data\bin` | Merges DASH video + audio, runs the H.264 compatibility pass, encodes MP3. |
| Self-update | GitHub Releases of `softoblivion/media-saver` (asset `MediaSaver.exe` + `SHA256SUMS`); the downloaded exe swaps itself in (`--apply-update`) on restart | No installer, no service, no third-party updater; the same verified-download pattern as the engines. |
| Packaging | PyInstaller one-file, windowed, own icon | One exe to double-click. |

## 3. Repository layout

```
media-saver/
  README.md, LICENSE
  docs/ARCHITECTURE.md       this file
  docs/BRIDGE_API.md         JS <-> Python contract (methods, events, payload types)
  docs/ENGINE.md             update flow, engine command lines, error codes, site quirks, formats, preview
  docs/SELF_UPDATE.md        app self-update: release layout, check, verify, apply mode
  docs/UI_DESIGN.md          design system, Liquid Glass material, typography, motion, copy rules
  docs/BUILD.md              dev run, build, release procedure, troubleshooting
  docs/releases/vX.Y.Z.md    release notes
  docs/assets/               README images
  app/__init__.py            __version__ (the only place the version lives)
  app/main.py                entry: windows, single instance, engine lifecycle
  app/bridge.py              pywebview js_api class, event pump
  app/login.py               Instagram sign-in window, cookie capture
  app/winutil.py             clipboard, Explorer, single instance, DWM corners and border colour, fixed-size window hook
  app/fake_engine.py         stand-in Engine for UI work without network (`--fake`)
  app/core/paths.py          AppPaths (portable runtime data locations)
  app/core/settings.py       settings schema, defaults, validation, persistence
  app/core/updater.py        bootstrap + update of yt-dlp, gallery-dl, ffmpeg
  app/core/selfupdate.py     Media Saver's configuration of the vendored self-updater (adapter)
  app/core/downloader.py     job queue, preview fetch, subprocess runner, progress parsing, formats, fallbacks
  app/core/auth.py           cookies.txt read/write, auth status
  app/core/linkclean.py      link extraction and canonicalisation for the four platforms
  app/core/engine.py         facade used by the bridge (section 6)
  app/core/cli.py            headless harness: python -m app.core.cli <links>
  app/vendor/selfupdate/     vendored self-update package (check, download, verify, apply mode; shared HTTP and
                             file helpers); a synced copy, never edited here; VENDORED.txt names its version
  ui/                        index.html, css/ (theme, parts, app), js/ (incl. js/mock-api.js), assets/ (icon, sound)
  ui/vendor/liquid_ui/       vendored Liquid UI design kit (tokens, material, controls, overlays, toasts, scene,
                             update control, fonts); a synced copy, never edited here; VENDORED.txt names its version
  tests/                     unittest suite (test_core_*, test_linkclean, test_bridge_*)
  build/                     entry.py, MediaSaver.spec, build.ps1, make_icon.py, icon.ico
  requirements.txt           runtime dependencies (pywebview, pythonnet)
  requirements-dev.txt       build tools (pyinstaller, pillow)
  run_dev.bat                run from source
```

Build output (`release/`, `build/work`, `build/dist`), `.venv/` and the runtime folders created when running from
source (`media-saver_data/`, `Downloads/`) are not tracked.

Rules for all code: comments, docs and user-facing strings in English; `app/core` uses the Python standard library
and `app/vendor` only (the vendored packages are stdlib-only too); tests use `unittest` (`python -m unittest discover -s tests`). Each module keeps its owning document current.

Module table (each module is described by its owning document):

| Module | Path | Owning doc |
|---|---|---|
| Core engine (links, queue, formats, preview, engine updater) | `app/core/linkclean.py`, `downloader.py`, `updater.py`, `cli.py`; `tests/test_linkclean.py`, `tests/test_core_*` except settings and engine | `ENGINE.md` |
| Self-update | `app/core/selfupdate.py` (adapter); `app/vendor/selfupdate/` (vendored, replaced only by the sync described in `SELF_UPDATE.md`); `tests/test_core_selfupdate.py`; `build/`; `app/fake_engine.py`; `tests/test_bridge_*` | `SELF_UPDATE.md`, `BUILD.md` |
| UI | `ui/**` except `ui/vendor/` | `UI_DESIGN.md` |
| Liquid UI kit (vendored) | `ui/vendor/liquid_ui/` (replaced only by the sync described in `UI_DESIGN.md`, section 0) | `UI_DESIGN.md` |
| Shell and contracts | `app/main.py`, `bridge.py`, `login.py`, `winutil.py`, `app/core/engine.py`, `paths.py`, `settings.py`, `auth.py`; `tests/test_core_settings.py`, `tests/test_core_engine.py`; `README.md`, `ARCHITECTURE.md`, `BRIDGE_API.md`, release notes | `ARCHITECTURE.md`, `BRIDGE_API.md` |

## 4. Runtime data (portable)

`AppPaths` (in `app/core/paths.py`) resolves everything from one root:

- app dir = the folder of `MediaSaver.exe` (frozen) or the project root (from source); never the PyInstaller
  extraction folder.
- root = `%MEDIA_SAVER_HOME%` if set (tests set it to a scratch folder), else `<app dir>\media-saver_data`
  (the data folder sits beside the executable).
- `bin/` — yt-dlp.exe, gallery-dl.exe, ffmpeg.exe, ffprobe.exe (only when not taken from PATH), `versions.json`.
- `auth/cookies.txt` — Netscape cookie jar of the in-app Instagram sign-in.
- `settings.json`, `history.json` (finished / failed jobs of every platform survive restarts, last 300).
- `cache/thumbs/`, `cache/jobs/<job_id>/` (info json, per-job cookie copy, temp files).
- `logs/app.log` (rotating, 2 MB x 3), `logs/shell.log`, `logs/jobs/<job_id>.log` (full engine stderr of a failed job).
- `webview/` — WebView2 profile (`storage_path`) of the sign-in window.
- `updates/` — downloaded `MediaSaver-<version>.exe`, `apply.log`, the "applied" marker.
- default output dir: `<Videos>\Media-Saver` (the Windows known folder Videos, so a redirected or localized
  library is followed; `%MEDIA_SAVER_HOME%\Downloads` under the override). The output dir is the one
  path outside the data folder: the app's state is portable, the user's videos belong with the user's videos.

No path in the app depends on `%LOCALAPPDATA%`, `%USERPROFILE%` or a drive letter. Moving the exe together with its `media-saver_data\` folder moves the
whole app's state; the saved videos stay where the user chose.

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
3. Quality: one four-step ladder for every platform and both formats, read from the job's own platform setting
   (`quality_instagram`, `quality_youtube`, `quality_tiktok`, `quality_twitter`) when the job starts:
   low = short side <= 480 / MP3 128 kbps, medium = <= 720 / 192 kbps, high = <= 1080 / 256 kbps, max = best
   available (YouTube up to 2160p) / 320 kbps; default max. Settings files of earlier versions are mapped on load
   ("360" -> low, "720" -> medium, any other value outside the ladder -> high; details in `docs/ENGINE.md` 4.5).
   mp4: best video + best audio, sorted by resolution, then fps, then bitrate, capped by that short side; merge to
   MP4.
   mp3: best audio only, extracted / encoded to MP3 by ffmpeg at the ladder bitrate
   (`-x --audio-format mp3 --audio-quality <128K|192K|256K|320K>`).
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
8. Queue order: downloads start from the top of the list the UI shows (newest first). The links of one `enqueue`
   call are added so that the first pasted link ends up on top (the batch is added in reverse; `added` of the
   result stays in paste order), and a free download worker starts the queued job shown topmost, then the next one
   below it. A pasted batch therefore reads and runs top to bottom in paste order; a link pasted later appears above
   the older queued ones and starts next. `created_at` is strictly increasing per added job, so the UI order (by
   `created_at`) and the engine order agree. A retried job moves to the top of the engine order and starts next;
   its `created_at` stays, so a running UI keeps its card in place (details in `docs/ENGINE.md` 3).

### 5.3 Link cleaning

All four platforms, from any text (`app/core/linkclean.py`, rules and edge cases in its docstring and tests):

- Instagram canonical form: `https://www.instagram.com/reel/<shortcode>` (`/reels/`, `/tv/`, `/<username>/reel/`
  map to `/reel/`; `/p/<shortcode>` stays `/p/`), nothing after the shortcode.
- YouTube canonical form: `https://www.youtube.com/watch?v=<id>` (kind `video`) and
  `https://www.youtube.com/shorts/<id>` (kind `shorts`); accepted inputs include `youtu.be/<id>`, `m.`, `music.`,
  `/embed/`, `/v/`, `/live/`, `youtube-nocookie.com`, `?v=` anywhere in the query; `t=`, `list=`, `si=`,
  `feature=` and fragments are dropped. Playlists, channels, searches are `unsupported_path`.
- TikTok canonical form: `https://www.tiktok.com/@<user>/video/<id>` (kind `video`; `@/video/<id>` when the input
  carries no user) and the short links `https://vm.tiktok.com/<code>`, `https://vt.tiktok.com/<code>`,
  `https://www.tiktok.com/t/<code>` (kind `share`, resolved by yt-dlp at download time, so the preview of a short
  link arrives only after the engine followed it). Photo posts, profiles, sounds, tags, LIVE are `unsupported_path`.
- X canonical form: `https://x.com/<user>/status/<id>` (kind `post`; `https://x.com/i/status/<id>` without a user);
  `twitter.com`, `mobile.`, `/i/web/status/`, `/statuses/`, `/video/1`, `/photo/1`, `?s=20&t=` all fold into it.
  Profiles, Spaces, broadcasts, lists and `t.co` short links are `unsupported_path`. A post with several videos is
  a playlist in yt-dlp's terms; the first entry with video formats is taken (`_pick_entry`), as for carousels.
- The UI passes the current mode; a valid link of another platform comes back as `other_platform` (with its
  `platform` filled) so the UI can offer to switch.

### 5.4 Preview of queued links

Right after `enqueue` a pool of three preview workers (`PREVIEW_WORKERS`) fetches metadata of the queued jobs
that have none, in parallel and top of the list first (the download start order of 5.2): anonymous `yt-dlp -J` on
every platform, stores the info json in the job cache, fills `title`, `author`, `duration_s`, `thumbnail` and sets
`previewed` (false while a fetch is pending, true when it finished or gave up); the card shows a placeholder until
then. The download step reuses the stored info when it is younger than 10 minutes and the same auth rung applies.
A job that starts downloading never waits for its preview: a fetch that has not started its subprocess is dropped,
a running one is let finish, and whichever answer arrives first (preview or the download's own probe) sets the
fields; a later preview never replaces them and an empty value never replaces a field. The thumbnail of a
downloading job is made right after its probe, before the download. A failed preview is silent (the job shows the
link until the download probes it). Preview fetches respect the engine gate and stop while the engine updates;
cancel, remove and shutdown kill the fetch of that job, and shutdown stops the pool.

### 5.5 Modes and formats

- `settings.mode` is the platform shown by the UI; the jobs of every platform live in one job table and one history; the
  UI filters by `job.platform`. Duplicate detection is per platform + id. Concurrency is shared.
- `settings.format` is the default for new jobs; `enqueue(..., format)` and `set_job_format(id, format)` override it
  per job while queued. mp3 ignores `h264_compat`; the quality setting gives its bitrate (5.2 item 3).
- Quality is a per-platform setting shown in the main window next to the mode and format controls (not in the
  settings sheet): `quality_instagram`, `quality_youtube`, `quality_tiktok`, `quality_twitter`, each "low" |
  "medium" | "high" | "max"; a job uses the setting of its platform at start, for mp4 (resolution cap) and mp3
  (bitrate) alike.

### 5.6 Instagram sign-in

`app/login.py` opens a second pywebview window on `https://www.instagram.com/accounts/login/`; the user signs in
there; the window polls cookies until `sessionid` for `.instagram.com` appears, writes all instagram.com cookies to
`auth/cookies.txt` via `app.core.auth.write_netscape_cookies`, calls `engine.reload_auth()` and closes. The app never
sees or stores the password.

### 5.7 App self-update

`app/core/selfupdate.py` configures the vendored package `app/vendor/selfupdate` (details, the release layout and
the configuration values in `docs/SELF_UPDATE.md`):

1. On every launch (always; no setting switches the check off) and on "Check for updates": read the latest release of
   `softoblivion/media-saver` (tag `vX.Y.Z`, asset `MediaSaver.exe`, asset `SHA256SUMS`, notes), compare SemVer
   with `app.__version__`.
2. "Download": stream the asset to `data\updates\MediaSaver-<version>.exe.part`, verify the byte size against the
   release asset and the SHA-256 against `SHA256SUMS`, rename to `.exe`; stage `ready`.
3. "Restart to update": write the marker in `updates\`, start `updates\MediaSaver-<version>.exe --apply-update
   <pid> <MediaSaver.exe>` and close the app. That exe waits for the pid to end, renames the installed exe to
   `MediaSaver.exe.old`, copies itself over it and starts it. No script, no console. On the next launch the marker
   produces one "Updated to vX.Y.Z" toast and `MediaSaver.exe.old` is deleted (retried in the background for about
   10 s while the previous process is still exiting).
4. Any failure leaves the current exe untouched; the UI shows the message and offers to retry.
5. `auto_download_updates` (default on; replaces the 2.1.2 `auto_install_updates`, which is ignored when an older
   settings file has it): when on, the engine (`Engine._maybe_auto_download`) starts the download as soon as a check
   reports `available`, once per `latest` (a repeated `available` of the same version or an `error` after the
   automatic download never starts another one; turning the setting on starts it for a version already found). The
   restart is never automatic: a `ready` file raises one sticky toast "Media Saver X.Y.Z is ready" with "Restart",
   and the version pill and the panel keep "Restart to update". When off, nothing is downloaded without the
   user's click. While the app runs, the engine checks again every 5 minutes (a fixed period, no setting) through the
   vendored `UpdateScheduler` (`Engine.update_scheduler`: a daemon thread started with the launch check, first
   periodic check one period later, each wait offset by a random few seconds; after a failed check the wait doubles
   up to one hour and returns to 5 minutes after the first check that succeeds; no check is made without a network
   connection; the round is skipped while a download runs or a file is `ready`; `Engine.shutdown` stops it). A
   settings file written by 2.4.x - 2.6.x still carries `update_check_interval`; loading drops it.
6. Closing the window ends the app and everything it started: the shell calls `Engine.shutdown` (the periodic
   check stops, download workers stop, every engine subprocess tree is killed through its Job Object) and exits
   the process with `os._exit(0)`; a half-downloaded update stays as a `.part` file and is downloaded again on the
   next run. Nothing keeps running after the window is closed.

Apply: instead of the running app renaming itself and relaunching, Media Saver lets the downloaded exe do the
swap after the app exited (verified with real builds). Both variants avoid helper scripts and consoles; the swap-by-the-new-exe variant keeps
the shutdown path of the app unchanged and tolerates the PyInstaller bootloader still holding the old file
for a moment.

The version lives in `app/__init__.py` only; the build writes it into the exe's version resource.

Update notice: the kit's update sheet (`ui/vendor/liquid_ui/js/update.js`): the first time in a run that a newer version is reported,
a modal sheet opens with the new version, the installed version with size and date, the release notes and the
buttons Install / Later; Install downloads when needed and calls `apply_update` as soon as the file is verified
(through the restart warning while downloads run); Later closes it until the version pill is clicked. The page
still never downloads by itself: the background download stays the engine's job (item 5 above).

### 5.8 Window

The window is frameless and has a fixed size: 1180 x 780 logical pixels, scaled down in steps (1120 x 745,
1060 x 710, 1000 x 675, 940 x 640) to fit the work area of a small monitor, never below 940 x 640. It cannot be
resized or maximized: no sizing border, no maximize button, no maximize by system command or double click on the
title bar. The page draws its own title bar with minimize and close (`window_minimize`, `window_close`), covers the
whole client area, and DWM gives it rounded corners and a border colour equal to the page background. The size
lock is `FixedSizeHook` in `app/winutil.py`; details in `BUILD.md`, section 5.

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
                                        # the app-update check (always; app_updates=False only for cli / tests)
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

    def update_status(self) -> dict: ...                 # current UpdateStatus (the update popover refreshes on open)
    def check_updates(self) -> dict: ...                 # start the app-update check; UpdateStatus now, progress via "update"
    def download_update(self) -> None: ...               # background download + verify, progress via "update"
    def apply_update(self) -> bool: ...                  # start the new exe in apply mode; True when the app must now exit

    def auth_status(self) -> dict: ...
    def reload_auth(self) -> dict: ...                   # re-read cookies.txt, emit "auth", return status
    def logout(self) -> dict: ...                        # delete cookies.txt, emit "auth", return status
    def import_cookies_file(self, path: str) -> dict: ...  # validate + copy a user cookies.txt, emit "auth"
```

Inside the core the facade delegates to these classes (their public surface is the contract between
`engine.py` and the core modules):

```python
# app/core/linkclean.py
def clean_text(text: str, platform: str | None = None) -> dict          # CleanResult
def clean_link(raw: str) -> LinkResult                                   # LinkResult has platform and kind

# app/core/downloader.py
class Downloader:
    def add(self, url: str, shortcode: str, platform: str, kind: str, fmt: str) -> dict   # Job dict; starts the preview
    def add_batch(self, items: list[tuple[str, str, str, str, str]]) -> list[dict]
                                                        # one pasted batch (url, shortcode, platform, kind, fmt) in
                                                        # paste order; first link on top (5.2 item 8); Job dicts in
                                                        # paste order; Engine.enqueue uses it
    def active_keys(self) -> set[str]                   # "<platform>:<shortcode>" of queued / active jobs
    def set_format(self, job_id: str, fmt: str) -> dict | None
    def clear_finished(self, platform: str | None = None) -> int
    # unchanged: start, shutdown, set_concurrency, jobs, get, cancel, retry, remove, job_file, mark_waiting,
    #            load_history, save_history

# app/core/selfupdate.py (adapter; the methods come from app/vendor/selfupdate.SelfUpdater)
class SelfUpdater(app.vendor.selfupdate.SelfUpdater):
    def __init__(self, paths: AppPaths, current_version: str, on_status: Callable[[dict], None], *,
                 net: Net | None = None, exe_path: str | None = None,
                 on_toast: Callable[[str, str], None] | None = None,
                 spawn=None, urlopen=None) -> None       # spawn / urlopen: test hooks
    def status(self) -> dict                            # UpdateStatus
    def startup(self) -> None                           # delete MediaSaver.exe.old, read + remove the "applied" marker
                                                        # (returns nothing; emits a toast through on_toast when set)
    def check_background(self) -> None
    def check(self) -> dict                             # blocking; UpdateStatus
    def download_background(self) -> None
    def download(self) -> dict                          # blocking; verifies size + SHA-256; UpdateStatus
    def apply(self, launch: bool = True) -> bool        # marker + start `<new exe> --apply-update`; True when started
```

Payload shapes (State, Job, EngineStatus, UpdateStatus, Auth, Settings, events) are defined once in
`docs/BRIDGE_API.md`.

## 7. Build and ship

`build/build.ps1` creates `.venv`, installs requirements, runs the unit tests, runs PyInstaller with
`build/MediaSaver.spec`, puts the result at `release/MediaSaver.exe` and writes `release/SHA256SUMS`
(`<sha256>  MediaSaver.exe`). Details and the release procedure in `docs/BUILD.md`.
Subprocesses are always started with `CREATE_NO_WINDOW` (a windowed exe must never flash consoles).
Release notes live in `docs/releases/`; the release assets are `MediaSaver.exe` and `SHA256SUMS`.
