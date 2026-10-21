# Bridge API — JS <-> Python contract

The UI codes against this file (and mirrors it in `ui/js/mock-api.js`); `app/bridge.py` implements it; `app/core`
produces the payloads. A field not listed here does not exist; add it here first, then in the code.

## 1. Transport

- JS -> Python: `window.pywebview.api.<method>(...args)` returns a Promise. A Python exception rejects the
  promise; the UI shows the error as a toast.
- Python -> JS: the bridge calls `window.__msEvent(type, payload)`. Events produced before the UI called
  `ready()` are buffered and delivered right after `ready()` returns. The bridge coalesces `job` events per job id
  to at most 8 per second.
- The page is served by pywebview's local HTTP server on `http://127.0.0.1:<port>/` (first free port of
  42001-42040, fixed so the origin and `localStorage` stay stable), never from `file://` (Chromium refuses ES
  modules there). The UI uses relative URLs only; fonts and images come from `ui/assets`, never from the network.
- `ready()` is followed by one initial `window` event, so the UI knows the maximized / focused state at start.
- `window.pywebview` appears asynchronously: the UI waits for the `pywebviewready` DOM event. When the page is opened
  in a normal browser (no `window.pywebview`), the UI uses `ui/js/mock-api.js`, which implements every method and
  event below with simulated data.
- All payloads are JSON objects; times are Unix epoch seconds (float); sizes are bytes; durations are seconds.
- Optional arguments may be omitted or passed as `null`; both mean "default".

## 2. Methods

| Method | Args | Returns | Notes |
|---|---|---|---|
| `ready()` | — | `State` | First call of the UI. Starts the engine when the window has not done so yet, enables event delivery. |
| `clean_links(text, platform)` | `text: str`, `platform: Platform \| null` | `CleanResult` | Pure, no side effects. `platform` null = links of every platform are valid; otherwise links of the other platforms get error `other_platform`. |
| `enqueue(text, platform, format)` | `text: str` (any text with links), `platform: Platform \| null`, `format: Format \| null` | `EnqueueResult` | Cleans, de-duplicates, adds jobs. `format` null = the `format` setting. |
| `set_job_format(id, format)` | `id: str`, `format: Format` | `Job \| null` | Per-item override; only while the job is `queued`. `null` when the job is not queued or unknown. Emits `job`. |
| `cancel(id)` | `id: str` | `bool` | |
| `retry(id)` | `id: str` | `bool` | For `error` / `canceled` jobs. |
| `remove(id)` | `id: str` | `bool` | Running job is canceled first. Emits `job_removed`. |
| `clear_finished(platform)` | `platform: Platform \| null` | `int` | Removes `done` / `error` / `canceled` jobs of that platform (null = all) from the list (files stay). |
| `open_file(id)` | `id: str` | `bool` | Opens the file with the default player. |
| `reveal_file(id)` | `id: str` | `bool` | Explorer with the file selected. |
| `open_output_dir()` | — | `bool` | |
| `open_logs()` | — | `bool` | Opens the logs folder. |
| `choose_output_dir()` | — | `str \| null` | Native folder dialog; on success saves `output_dir` and emits `settings`. |
| `get_settings()` | — | `Settings` | |
| `set_settings(patch)` | `patch: object` (subset of Settings) | `Settings` | Invalid values -> rejection with a readable message. |
| `update_engine()` | — | `null` | Forced engine update; progress through `engine` events. |
| `check_updates()` | — | `UpdateStatus` | Starts a check of GitHub Releases for a newer app; result through `update` events. Returns the status at call time. |
| `download_update()` | — | `null` | Downloads the newer `MediaSaver.exe` into the data folder and verifies it; progress through `update` events. No-op unless stage is `available` or `error` after a download. |
| `apply_update()` | — | `null` | Only when `UpdateStatus.can_apply`: starts the swap helper and closes the app; the helper restarts it on the new version. |
| `login()` | — | `null` | Opens the Instagram sign-in window; result through `auth` event. |
| `logout()` | — | `Auth` | |
| `import_cookies_file()` | — | `Auth \| null` | File dialog for a Netscape cookies.txt; `null` when canceled. |
| `read_clipboard()` | — | `str` | Plain text of the clipboard, `""` when empty or not text. |
| `copy_text(text)` | `text: str` | `bool` | |
| `open_url(url)` | `url: str` | `bool` | Default browser; only `https://` URLs. |
| `window_minimize()` | — | `null` | |
| `window_toggle_maximize()` | — | `bool` | Returns the new maximized state. |
| `window_close()` | — | `null` | Graceful: engine shutdown, then exit. |

## 3. Events (`window.__msEvent(type, payload)`)

| type | payload | When |
|---|---|---|
| `engine` | `EngineStatus` | Any change of updater / engine state. |
| `update` | `UpdateStatus` | Any change of the app self-update state. |
| `job` | `Job` (full object) | Job created or changed (including the preview fields arriving). |
| `job_removed` | `{ "id": str }` | Job removed from the list. |
| `auth` | `Auth` | Login, logout, cookies import, session found invalid. |
| `settings` | `Settings` | Settings changed (by UI or by the app). |
| `toast` | `{ "level": "info" \| "success" \| "warning" \| "error", "text": str }` | Short notification, one sentence. |
| `window` | `{ "maximized": bool, "focused": bool }` | Window state change (focus is used for the clipboard prompt). |

## 4. Types

Platform: `"instagram" | "youtube" | "tiktok" | "twitter"`. Format: `"mp4" | "mp3"`.

Platform ids and display names: `instagram` Instagram, `youtube` YouTube, `tiktok` TikTok, `twitter` X (the service
formerly called Twitter; the id stays `twitter` because yt-dlp's extractor and the historic URLs use it, the UI
shows "X"). Kinds per platform: Instagram `reel` | `p`; YouTube `video` | `shorts`; TikTok `video` (a full URL with
the numeric id) | `share` (a short link `vm.tiktok.com/<code>`, `vt.tiktok.com/<code>`, `tiktok.com/t/<code>` whose
target id is unknown until the engine follows it); X `post`.

### State
```json
{
  "app_version": "2.0.0",
  "engine": EngineStatus,
  "update": UpdateStatus,
  "auth": Auth,
  "settings": Settings,
  "jobs": [Job]          // newest first, every platform; the UI shows the jobs of the current mode
}
```

### CleanResult
```json
{
  "items": [
    {
      "original": "https://www.instagram.com/reel/DAbC123xYz_/?igsh=MWx0cG9...",
      "clean": "https://www.instagram.com/reel/DAbC123xYz_",   // null when invalid
      "shortcode": "DAbC123xYz_",                              // platform media id; null when invalid
      "platform": "instagram",                                 // Platform | null
      "kind": "reel",                                          // "reel" | "p" (Instagram) | "video" | "shorts" (YouTube) | "video" | "share" (TikTok) | "post" (X) | null
      "error": null                                            // null | "not_supported" | "unsupported_path" | "no_shortcode" | "other_platform"
    }
  ],
  "clean_text": "https://www.instagram.com/reel/DAbC123xYz_",  // valid clean links joined by "\n", duplicates removed
  "valid": 1,
  "invalid": 0,
  "duplicates": 0
}
```
- `not_supported`: a URL whose host belongs to none of the four platforms (was `not_instagram` in 1.x).
- `other_platform`: a valid link of the platform that was not requested; `clean`, `shortcode`, `platform`, `kind`
  are filled so the UI can offer to switch modes. Counted as invalid.
- Canonical forms: Instagram `https://www.instagram.com/reel/<shortcode>` and `/p/<shortcode>`; YouTube
  `https://www.youtube.com/watch?v=<id>` (kind `video`) and `https://www.youtube.com/shorts/<id>` (kind `shorts`),
  `<id>` = 11 characters `[A-Za-z0-9_-]`. Nothing after the id: no `?t=`, `&list=`, `&feature=`, `#`, `/`.
  TikTok `https://www.tiktok.com/@<user>/video/<id>` (kind `video`, `<id>` = 15..25 digits, the `@<user>` segment
  kept as given when the input has it, `https://www.tiktok.com/@/video/<id>` when it does not: yt-dlp accepts both)
  and short links `https://vm.tiktok.com/<code>`, `https://vt.tiktok.com/<code>`, `https://www.tiktok.com/t/<code>`
  (kind `share`, `shortcode` = `<code>`, 6..20 characters `[A-Za-z0-9]`, host kept). X
  `https://x.com/<user>/status/<id>` (kind `post`, `<id>` = 10..25 digits, `<user>` kept when the input has it,
  `https://x.com/i/status/<id>` otherwise; `twitter.com`, `mobile.twitter.com`, `/i/web/status/`, `/statuses/`,
  `/video/1` and `/photo/1` tails all map to it). `shortcode` is the numeric id or the short-link code.

### EnqueueResult
```json
{
  "added": [Job],
  "skipped": [ { "input": "…", "reason": "invalid" | "duplicate" } ]   // duplicate = same platform + id already queued/active
}
```

### Job
```json
{
  "id": "a1b2c3d4",
  "url": "https://www.instagram.com/reel/DAbC123xYz_",
  "shortcode": "DAbC123xYz_",               // platform media id
  "platform": "instagram",                  // Platform
  "kind": "reel",                           // "reel" | "p" | "video" | "shorts" | "share" | "post" (see Types)
  "format": "mp4",                          // Format; fixed once the job leaves "queued"
  "status": "queued" | "probing" | "downloading" | "processing" | "done" | "error" | "canceled",
  "stage_text": "Downloading video",       // short line for the current stage
  "progress": 0.42,                         // overall 0..1 across probe, video, audio, merge, transcode
  "speed_bps": 3145728,                     // null when unknown
  "eta_s": 4,                               // null when unknown
  "previewed": true,                        // the preview fetch (title, author, duration, thumbnail) has finished, found or not
  "title": "…", "author": "username",       // null until previewed / probed
  "duration_s": 23.4,
  "thumbnail": "data:image/jpeg;base64,…",  // null until previewed; small (<= 480 px wide)
  "width": 1080, "height": 1920, "fps": 30, // null for mp3
  "vcodec": "h264", "acodec": "aac",        // codecs of the final file; vcodec null for mp3
  "filesize": 12345678,                     // final file size, null until done
  "filepath": "D:\\Apps\\MediaSaver\\Downloads\\username_DAbC123xYz_.mp4",
  "already_existed": false,                 // true when skip_existing found the file
  "transcoded": false,                      // true when the H.264 pass ran
  "attempt": { "engine": "yt-dlp" | "gallery-dl", "auth": "anonymous" | "session" | "browser" },
  "error": null | { "code": ErrorCode, "message": "Readable text", "can_login": bool, "log_path": "…" },
  "created_at": 1760950000.0,
  "finished_at": null
}
```

ErrorCode: `login_required`, `private`, `not_found`, `rate_limited`, `network`, `engine_missing`, `ffmpeg_missing`,
`extractor`, `disk`, `canceled`, `unknown`.

- A job with status `canceled` always carries `error.code == "canceled"` (the UI shows no error row for it).
- `fps` can be fractional (23.976).
- Preview: right after `enqueue` the core fetches title, author, duration and thumbnail of every queued job
  (anonymous metadata request, one job at a time) and emits `job`; `previewed` turns true when that finished. The
  download reuses the fetched metadata, so the site is not asked twice.
- `can_login` is only ever true for Instagram jobs.

### EngineStatus
```json
{
  "stage": "starting" | "checking" | "downloading" | "updating" | "ready" | "offline" | "error",
  "message": "Updating yt-dlp…",           // one line
  "progress": 0.3,                          // null when indeterminate
  "components": {
    "yt-dlp":     { "version": "2026.10.18.234512", "channel": "nightly", "updated": true, "ok": true },
    "gallery-dl": { "version": "1.30.10", "updated": false, "ok": true },
    "ffmpeg":     { "version": "9.0.2", "source": "system" | "bundled", "ok": true },  // bundled builds report e.g. "N-127709-g5993347a1b-20261019"
    "deno":       { "version": "2.9.7", "source": "system" | "bundled", "ok": true }   // JavaScript runtime for yt-dlp's YouTube challenge solver
  },
  "last_check": 1760950000.0
}
```
`ready` means jobs can run. `offline` also lets jobs run when the binaries exist. mp3 jobs and merging need
ffmpeg; when `components.ffmpeg.ok` is false the updater fetches a static build on the next pass (`update_engine()`).
`deno` is optional for the job itself: YouTube jobs run without it, but yt-dlp then reports the deprecated path and
may miss formats; when `components.deno.ok` is false the updater fetches the runtime on the next pass.

### UpdateStatus
```json
{
  "stage": "idle" | "checking" | "up_to_date" | "available" | "downloading" | "ready" | "error",
  "current": "2.0.0",                       // running version
  "latest": "2.1.0",                        // null until a check found a release
  "notes": "## Added\n- …",                 // release notes (Markdown) of `latest`, null when unavailable
  "size": 12703247,                         // asset size of `latest`, null when unknown
  "published_at": 1760950000.0,             // null when unknown
  "progress": 0.4,                          // download progress 0..1, null when indeterminate / not downloading
  "message": "A new version is available",  // one line, shown as is
  "checked_at": 1760950000.0,               // null before the first check of this run
  "can_apply": false                        // true when a verified file is ready and the app runs as MediaSaver.exe
}
```
- `idle` before the first check; `up_to_date` when `latest <= current`. The check runs on every launch.
- `ready`: the file is downloaded, its size and SHA-256 match the release; `apply_update()` is allowed.
- `error`: `message` says what failed (offline, checksum mismatch, GitHub limit); `check_updates()` or
  `download_update()` may be called again.
- Running from source `can_apply` stays false (`message` says that the update applies to the packaged exe only).

### Auth
```json
{ "logged_in": true, "user_id": "1234567", "source": "session" | "import" | null, "saved_at": 1760950000.0 }
```

### Settings
```json
{
  "output_dir": "C:\\Users\\<user>\\Videos\\Media-Saver",   // default: the user's Videos folder + Media-Saver
  "filename_template": "{author}_{shortcode}",   // tokens: {author} {shortcode} {date} {title}; must contain {shortcode}
  "mode": "instagram",                           // Platform shown in the UI (persisted; each mode keeps its own queue view)
  "format": "mp4",                               // Format default for new jobs
  "quality_instagram": "max",                    // "low" | "medium" | "max": short side <= 480 / <= 720 / best (mp4 only)
  "quality_youtube": "max",                      // "360" | "720" | "max": short side <= 360 / <= 720 / best, up to 2160p (mp4 only)
  "quality_tiktok": "max",                       // "low" | "medium" | "max": short side <= 480 / <= 720 / best, up to 1080p (mp4 only)
  "quality_twitter": "max",                      // "low" | "medium" | "max": short side <= 480 / <= 720 / best, up to 1080p (mp4 only)
  "h264_compat": true,                           // mp4 only
  "skip_existing": true,
  "concurrency": 2,                              // 1..4, shared by all modes
  "update_channel": "nightly",                   // "nightly" | "stable"
  "auto_update": true,                           // engines on every launch
  "auto_download_updates": true,                 // download a found update in the background; installing is always the user's "Restart" (ARCHITECTURE 5.7)
  "update_check_interval": "30m",                // "30m" | "1h" | "6h" | "1d" | "1w": app-update check period while running; missing in an older file -> "30m"
  "auth_mode": "auto",                           // "auto" (anonymous first, then session) | "always" | "never"; Instagram only
  "browser_cookies": "",                         // "" | "firefox" | "edge" | "chrome" | "brave" | "opera"; used by every platform
  "clipboard_watch": true,                       // UI offers links found in the clipboard on window focus
  "open_folder_when_done": false,                // open output dir when the whole queue finishes
  "sound_on_done": true                          // the UI plays assets/sounds/download-done.wav when a job finishes with a new file
}
```
