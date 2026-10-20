# Bridge API — JS <-> Python contract

The UI codes against this file (and mirrors it in `ui/js/mock-api.js`); `app/bridge.py` implements it; `app/core`
produces the payloads. A field not listed here does not exist; add it here first, then in the code.

## 1. Transport

- JS -> Python: `window.pywebview.api.<method>(...args)` returns a Promise. A Python exception rejects the
  promise; the UI shows the error as a toast.
- Python -> JS: the bridge calls `window.__reelsEvent(type, payload)`. Events produced before the UI called
  `ready()` are buffered and delivered right after `ready()` returns. The bridge coalesces `job` events per job id
  to at most 8 per second.
- The page is served by pywebview's local HTTP server on `http://127.0.0.1:<port>/` (first free port of
  42001-42040, fixed so the origin and `localStorage` stay stable), never from `file://` (Chromium refuses ES
  modules there). The UI uses relative URLs only.
- `ready()` is followed by one initial `window` event, so the UI knows the maximized / focused state at start.
- `window.pywebview` appears asynchronously: the UI waits for the `pywebviewready` DOM event. When the page is opened
  in a normal browser (no `window.pywebview`), the UI uses `ui/js/mock-api.js`, which implements every method and
  event below with simulated data.
- All payloads are JSON objects; times are Unix epoch seconds (float); sizes are bytes; durations are seconds.

## 2. Methods

| Method | Args | Returns | Notes |
|---|---|---|---|
| `ready()` | — | `State` | First call of the UI. Enables event delivery. |
| `clean_links(text)` | `text: str` | `CleanResult` | Pure, no side effects. |
| `enqueue(text)` | `text: str` (any text with links, or links separated by whitespace) | `EnqueueResult` | Cleans, de-duplicates, adds jobs. |
| `cancel(id)` | `id: str` | `bool` | |
| `retry(id)` | `id: str` | `bool` | For `error` / `canceled` jobs. |
| `remove(id)` | `id: str` | `bool` | Running job is canceled first. Emits `job_removed`. |
| `clear_finished()` | — | `int` | Removes `done` / `error` / `canceled` jobs from the list (files stay). |
| `open_file(id)` | `id: str` | `bool` | Opens the file with the default player. |
| `reveal_file(id)` | `id: str` | `bool` | Explorer with the file selected. |
| `open_output_dir()` | — | `bool` | |
| `open_logs()` | — | `bool` | Opens the logs folder. |
| `choose_output_dir()` | — | `str \| null` | Native folder dialog; on success saves `output_dir` and emits `settings`. |
| `get_settings()` | — | `Settings` | |
| `set_settings(patch)` | `patch: object` (subset of Settings) | `Settings` | Invalid values -> rejection with a readable message. |
| `update_engine()` | — | `null` | Forced update; progress through `engine` events. |
| `login()` | — | `null` | Opens the Instagram sign-in window; result through `auth` event. |
| `logout()` | — | `Auth` | |
| `import_cookies_file()` | — | `Auth \| null` | File dialog for a Netscape cookies.txt; `null` when canceled. |
| `read_clipboard()` | — | `str` | Plain text of the clipboard, `""` when empty or not text. |
| `copy_text(text)` | `text: str` | `bool` | |
| `open_url(url)` | `url: str` | `bool` | Default browser; only `https://` URLs. |
| `window_minimize()` | — | `null` | |
| `window_toggle_maximize()` | — | `bool` | Returns the new maximized state. |
| `window_close()` | — | `null` | Graceful: engine shutdown, then exit. |

## 3. Events (`window.__reelsEvent(type, payload)`)

| type | payload | When |
|---|---|---|
| `engine` | `EngineStatus` | Any change of updater / engine state. |
| `job` | `Job` (full object) | Job created or changed. |
| `job_removed` | `{ "id": str }` | Job removed from the list. |
| `auth` | `Auth` | Login, logout, cookies import, session found invalid. |
| `settings` | `Settings` | Settings changed (by UI or by the app). |
| `toast` | `{ "level": "info" \| "success" \| "warning" \| "error", "text": str }` | Short notification, one sentence. |
| `window` | `{ "maximized": bool, "focused": bool }` | Window state change (focus is used for the clipboard prompt). |

## 4. Types

### State
```json
{
  "app_version": "1.0.0",
  "engine": EngineStatus,
  "auth": Auth,
  "settings": Settings,
  "jobs": [Job]          // newest first
}
```

### CleanResult
```json
{
  "items": [
    {
      "original": "https://www.instagram.com/reel/DAbC123xYz_/?igsh=MWx0cG9...",
      "clean": "https://www.instagram.com/reel/DAbC123xYz_",   // null when invalid
      "shortcode": "DAbC123xYz_",                              // null when invalid
      "kind": "reel",                                          // "reel" | "p" | null
      "error": null                                            // null | "not_instagram" | "unsupported_path" | "no_shortcode"
    }
  ],
  "clean_text": "https://www.instagram.com/reel/DAbC123xYz_",  // valid clean links joined by "\n", duplicates removed
  "valid": 1,
  "invalid": 0,
  "duplicates": 0
}
```

### EnqueueResult
```json
{
  "added": [Job],
  "skipped": [ { "input": "…", "reason": "invalid" | "duplicate" } ]   // duplicate = same shortcode already queued/active
}
```

### Job
```json
{
  "id": "a1b2c3d4",
  "url": "https://www.instagram.com/reel/DAbC123xYz_",
  "shortcode": "DAbC123xYz_",
  "status": "queued" | "probing" | "downloading" | "processing" | "done" | "error" | "canceled",
  "stage_text": "Downloading video",       // short line for the current stage
  "progress": 0.42,                         // overall 0..1 across probe, video, audio, merge, transcode
  "speed_bps": 3145728,                     // null when unknown
  "eta_s": 4,                               // null when unknown
  "title": "…", "author": "username",       // null until probed
  "duration_s": 23.4,
  "thumbnail": "data:image/jpeg;base64,…",  // null until probed; small (<= 480 px wide)
  "width": 1080, "height": 1920, "fps": 30,
  "vcodec": "h264", "acodec": "aac",        // codecs of the final file
  "filesize": 12345678,                     // final file size, null until done
  "filepath": "C:\\Users\\…\\username_DAbC123xYz_.mp4",
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

### EngineStatus
```json
{
  "stage": "starting" | "checking" | "downloading" | "updating" | "ready" | "offline" | "error",
  "message": "Updating yt-dlp…",           // one line
  "progress": 0.3,                          // null when indeterminate
  "components": {
    "yt-dlp":     { "version": "2026.10.18.234512", "channel": "nightly", "updated": true, "ok": true },
    "gallery-dl": { "version": "1.30.10", "updated": false, "ok": true },
    "ffmpeg":     { "version": "9.0.2", "source": "system" | "bundled", "ok": true }   // bundled builds report e.g. "N-127709-g5993347a1b-20261019"
  },
  "last_check": 1760950000.0
}
```
`ready` means jobs can run. `offline` also lets jobs run when the binaries exist.

### Auth
```json
{ "logged_in": true, "user_id": "1234567", "source": "session" | "import" | null, "saved_at": 1760950000.0 }
```

### Settings
```json
{
  "output_dir": "C:\\Users\\<user>\\Downloads\\Reels Saver",
  "filename_template": "{author}_{shortcode}",   // tokens: {author} {shortcode} {date} {title}; must contain {shortcode}
  "quality": "max",                              // "max" | "1080" | "720"
  "h264_compat": true,
  "skip_existing": true,
  "concurrency": 2,                              // 1..4
  "update_channel": "nightly",                   // "nightly" | "stable"
  "auto_update": true,
  "auth_mode": "auto",                           // "auto" (anonymous first, then session) | "always" | "never"
  "browser_cookies": "",                         // "" | "firefox" | "edge" | "chrome" | "brave" | "opera"
  "clipboard_watch": true,                       // UI offers links found in the clipboard on window focus
  "open_folder_when_done": false                 // open output dir when the whole queue finishes
}
```
