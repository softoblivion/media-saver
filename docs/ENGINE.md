# Engine (app/core) — update flow, command lines, error codes, Instagram quirks

Owner: core worker. Contract: `docs/ARCHITECTURE.md` (sections 4–6) and `docs/BRIDGE_API.md` (payloads).
Measured on 2026-10-20 with yt-dlp nightly 2026.10.10.233141, gallery-dl 1.32.17, ffmpeg 9.0.2 (Gyan, PATH) and
the FFmpeg-Builds master build N-127709 (bundled path).

## 1. Modules

| File | Role |
|---|---|
| `paths.py` | `AppPaths` (root = `%REELS_SAVER_HOME%` or `%LOCALAPPDATA%\ReelsSaver`, folders created on access), atomic file writes. |
| `settings.py` | Defaults, per-key validation (ValueError, Russian text), `SettingsStore` (thread-safe, tmp + `os.replace`). |
| `auth.py` | Netscape cookies.txt writer/reader, `read_auth_status`, cookies import validation, sidecar `auth/auth.json` (`source`, `saved_at`, `invalid`). |
| `updater.py` | Engine bootstrap / update (`Updater`), `Net` (urllib with UA, timeouts, retries), SHA-256 verification. |
| `downloader.py` | `ProcessRunner` (hidden subprocess, Job Object tree kill), `BinaryGate`, progress parsing, error classification, format selection, auth ladder, `Downloader` (job table, workers, history). |
| `engine.py` | `Engine` facade of ARCHITECTURE section 6; `logs/app.log` rotating handler on the `reels` logger. |
| `cli.py` | Headless harness: `python -m app.core.cli`. |

## 2. Update flow (every launch, background thread)

`Engine.start()` emits the current `engine` status, then `Updater.start_background()` runs one pass:

1. `checking`: detect binaries (`yt-dlp.exe --version`, `gallery-dl.exe --version`, `ffmpeg -version`).
2. Connectivity: HEAD `https://github.com/` and `https://codeberg.org/`. Both unreachable -> no updates;
   stage `offline` when yt-dlp exists, else `error` ("No internet: the download engine is not installed").
3. yt-dlp (channel from settings, `nightly` default):
   - fetch `<channel base>/SHA2-256SUMS` (plain release asset, no GitHub API quota);
     nightly base `https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/`,
     stable base `https://github.com/yt-dlp/yt-dlp/releases/latest/download/`;
   - missing -> download `yt-dlp.exe` to `bin/yt-dlp.exe.part`, verify SHA-256, `os.replace` (under the gate);
   - present and local SHA == published SHA -> up to date, nothing runs;
   - present and SHA differs -> `yt-dlp.exe --no-config --update-to <channel>` (timeout 90 s, under the gate);
     if it fails or the result still differs from the published SHA (yt-dlp refuses to "update" from a newer
     nightly to an older stable: it prints "is up to date") -> verified fresh download + `os.replace`.
4. ffmpeg: `bin/ffmpeg.exe` + `bin/ffprobe.exe` ("bundled") -> PATH ("system") -> download
   `https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip`
   (~200 MB), verify against `checksums.sha256`, extract only `*/bin/ffmpeg.exe` and `*/bin/ffprobe.exe`,
   delete the zip. A bundled copy is re-checked every 30 days (zip SHA compared with the stored one).
   Env `REELS_SAVER_NO_SYSTEM_FFMPEG=1` (or `Updater(use_system_ffmpeg=False)`) disables PATH detection.
5. gallery-dl (fallback engine, always the stable channel): GitHub releases of mikf/gallery-dl no longer carry
   binaries (2026-10: assets list empty, `latest/download/gallery-dl.exe` is a 404). The exe is published on
   Codeberg: `https://codeberg.org/mikf/gallery-dl/releases/download/latest/gallery-dl.exe` with
   `.../latest/SHA256SUMS`. Same SHA logic; SHA differs -> `gallery-dl.exe --config-ignore -U` (it has
   `-U/--update`, `--update-to stable|dev`, `--update-check`), still differs -> fresh verified download.
6. State in `bin/versions.json` (`yt-dlp.channel`, `ffmpeg.downloaded_at`, `ffmpeg.zip_sha`, `last_check`).
7. Final stage: `ready` (yt-dlp works; message notes a missing ffmpeg), `offline`, or `error`.

`auto_update=false` skips step 3–5 updates but still installs missing binaries. `update_engine()` and a change of
`update_channel` run a forced pass. A pass requested while one runs queues one more forced pass.

Gate: jobs hold `BinaryGate` shared while a yt-dlp / gallery-dl subprocess runs; replacing a binary takes it
exclusively (waits up to 300 s for running subprocesses, blocks new ones). `Updater.usable()` is false while
checking or replacing, so queued jobs wait ("Waiting for engine update").

Self-heal: an `extractor` error on a yt-dlp rung calls `Updater.self_heal()` — one forced yt-dlp pass per app
session; when it ran, the same rung is retried once. Later jobs skip the retry and go down the ladder.

## 3. Job pipeline and exact command lines

All subprocesses: `creationflags=CREATE_NO_WINDOW`, stdin `DEVNULL`, env `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`
(and `PYTHONHOME`/`PYTHONPATH` removed), output decoded as UTF-8 with `errors="replace"`, each process assigned to
a Win32 Job Object with `KILL_ON_JOB_CLOSE`; cancel / timeout / shutdown calls `TerminateJobObject` (kills
yt-dlp and its ffmpeg children), fallback `taskkill /T /F`. Working dir and temp files: `cache/jobs/<id>/`,
removed when the job ends.

Common yt-dlp prefix (`Y`):

```
yt-dlp.exe --no-config --encoding utf-8 --color never --socket-timeout 20 --retries 3 --extractor-retries 2 --no-mtime
           [--cookies cache\jobs\<id>\cookies.txt | --cookies-from-browser <browser>]
```

1. Probe: `Y -J <url>` -> stdout JSON saved to `cache/jobs/<id>/info.json` (a `/p/` carousel is a playlist: the
   first entry with video formats is used). Author = `channel` (username), title = first caption line.
2. Thumbnail: `info.thumbnail` fetched with urllib (browser UA), then
   `ffmpeg -hide_banner -nostdin -y -loglevel error -i thumb_src -vf "scale='min(480,iw)':-2" -frames:v 1 -q:v 4 -f image2 -c:v mjpeg cache\thumbs\<shortcode>.jpg`,
   sent as `data:image/jpeg;base64,…` (25–55 KB). History keeps only the path.
3. Target name from `filename_template` (sanitised, see §6); `skip_existing` + existing non-empty file -> job
   `done`, `already_existed=true`, fields from ffprobe of the existing file.
4. Download:
   ```
   Y --load-info-json info.json -f <spec> [-S <sort>] --merge-output-format mp4 --ffmpeg-location <ffmpeg.exe>
     -o cache\jobs\<id>\media.%(ext)s --no-part --force-overwrites
     --newline --progress --progress-delta 0.3
     --progress-template "download:[[IDLP]]{\"s\":%(progress.status)j,\"d\":%(progress.downloaded_bytes)j,\"t\":%(progress.total_bytes)j,\"te\":%(progress.total_bytes_estimate)j,\"sp\":%(progress.speed)j,\"eta\":%(progress.eta)j,\"f\":%(info.format_id)j,\"vc\":%(info.vcodec)j}"
     --progress-template "postprocess:[[IDLPP]]{\"s\":%(progress.status)j,\"pp\":%(progress.postprocessor)j}"
     --print "after_move:[[IDLFILE]]%(filepath)j" --no-simulate
   ```
   Template facts checked on this nightly: `%(field)j` prints JSON; a missing field prints `NA` (not `null`) — the
   parser maps bare `NA` to `null`. `--print` implies quiet, so `--progress` is needed to keep progress lines.
   Download lines go to stdout, postprocess lines to stderr; both streams are parsed. Video vs audio stream is
   told apart by `vc == "none"` or the chosen audio format id.
5. H.264 pass (only when `h264_compat` and ffprobe says the merged video is not h264):
   ```
   ffmpeg -hide_banner -nostdin -y -loglevel error -i media.mp4 -map 0:v:0 -map 0:a:0? -c:v libx264 -crf 16
          -preset slow -pix_fmt yuv420p -profile:v high -movflags +faststart -c:a copy|(-c:a aac -b:a 256k)
          -progress pipe:1 -nostats h264.mp4
   ```
   Audio is copied when it already is AAC (Instagram serves HE-AAC / AAC-LC), else AAC 256k. Progress from
   `out_time_us` / duration. Speed on this machine: 1080x1920 60 s reel ≈ 8 s.
6. Move to `<output_dir>\<name>.mp4` (via `.part` + `os.replace`; never overwrites: `name (2).mp4`), then
   `ffprobe -v error -print_format json -show_format -show_streams <file>` fills width/height/fps/vcodec/acodec;
   filesize from the file.

gallery-dl rung (session only, per-job copy `cookies-gdl.txt`):
```
gallery-dl.exe --config-ignore --cookies <jar> -o videos=true -j <url>                       (metadata)
gallery-dl.exe --config-ignore --cookies <jar> -o videos=true -D cache\jobs\<id>\gdl
               -f "{shortcode}_{num}.{extension}" --no-mtime --filter "extension == 'mp4'" <url>
```
gallery-dl downloads the progressive `video_url` (≤ 720p baseline H.264, see §5), so it is a last resort; its
first mp4 then goes through steps 5–6. No progress output (progress jumps).

Overall progress weights (normalised): probe 0.06, video 0.62 (0.72 without audio), audio 0.10, merge 0.04,
transcode 0.30 (only when needed), finalize 0.02. Values never go backwards.

## 4. Quality: what Instagram serves (anonymous, 2026-10-20) and the format choice

Probed with `yt-dlp -J` without cookies (`tbr` in kbit/s):

| Reel | Age | Video formats (DASH, video-only) | Audio | Progressive formats `1`/`2`/`3` |
|---|---|---|---|---|
| DQCkNLtgqEe (teddysphotos) | ~1 y | VP9 360p 372, 540p 875, 720p 1464, **1080x1920 1939** | HE-AAC (mp4a.40.5) 73 | one URL, `efg` tag `…C3.720.dash_baseline_1_v1` |
| DPd8KODDA4d (ranveersingh) | ~1 y | VP9 360p 137, 540p 323, 720p 540, **1080x1920 770** | HE-AAC 75 | 720 baseline |
| Da6DPjrsSgB (lotos_moyhizmati) | 94 d | VP9 716x1274 77, **1088x1936 110** | HE-AAC 73 | 716 baseline |
| DZaO__gO0rz (leomessi) | 131 d | VP9 360p 81, **576x1024 301** | HE-AAC 61 | 576 baseline |
| DKcalTzoftf (martingarrix) | 503 d | **AVC 360x640 372** only | HE-AAC 116 | 360 baseline |
| DPL6xC_jJQU (canvaindia) | ~1 y | VP9 360p 466, 540p 1085, 720p 1750, **1080x1920 2232** | HE-AAC 73 | not decoded |
| Chunk8-jurw (instagram, 2022) | 4 y | AVC (avc1.64001F) 472x840 103 … **720x1280 3141** | none (silent reel) | 480 baseline |
| CDUMkliABpa (2020) | 6 y | AVC (avc1.4d401f) 426x240 … **720x1280 1796** | AAC-LC 132 | — |

Findings:
- Everything is DASH (separate video and audio mp4), served as plain https files (`is_dash_periods`), no HLS.
- Current reels: video is **VP9 only** up to 1080x1920 (some 1088x1936); no AVC rendition at the top size.
  Older reels (≤ 2022) are AVC up to 720p. Audio is HE-AAC v1 (mp4a.40.5) 60–75 kbit/s on new reels.
- Progressive formats `1`/`2`/`3` (from `video_versions`) carry no codec/size metadata in the info json; the
  `efg` query parameter (base64 JSON) says `xpv_progressive … <height>.dash_baseline_N` = baseline H.264 at
  ≤ 720p. They are never the best choice. Direct ffprobe of these URLs gets 403 (needs yt-dlp's headers).
- Bitrates are low (VP9 1080p 0.8–2 Mbit/s). Some reels top out at 576p or 360p anonymously (Messi, Garrix);
  whether a logged-in session gets more could not be tested (no login in this worker). To check at integration.
- `fps` is missing in the info json of VP9 formats; ffprobe of the file gives it (23.976 / 25 / 30, even 2 fps on
  a photo-slideshow reel).

Choice (`select_formats`, done in Python on the info json so it is testable and deterministic):
- "max": among formats with a video codec and size, highest pixel area, then fps, then bitrate (`vbr`/`tbr`).
- "1080" / "720": same, limited to short side ≤ cap × 1.02 (keeps 1088x1936 for "1080"); when nothing fits,
  the smallest available video.
- `h264_compat`: an AVC format with the same area (±2 %) and fps wins over the non-AVC best; otherwise the best
  stays and the H.264 pass runs. A lower-resolution AVC is never preferred (quality first).
- Audio: best `abr` (AAC first when `h264_compat`).
- Resulting `-f`: `<video_id>+<audio_id>/<video_id>/bv*+ba/b` (fallbacks only matter when the chosen ids vanish).
  When the info json has no usable video metadata: `-f "bv*+ba/b" -S "res,fps,br[,vcodec:avc,acodec:aac]"`
  (`res:<cap>` for capped qualities).
- CRF 16 from VP9 1080p gives ~25–35 MB for 26–60 s (3–4x the source size): visually lossless for editing in
  Premiere / After Effects, which is what `h264_compat` is for.

## 5. Auth ladder and error codes

Ladder (`plan_ladder`): `auto` = anonymous -> session (`auth/cookies.txt` exists and valid) -> browser
(`browser_cookies` set) -> gallery-dl + session; `always` = the same without anonymous (anonymous when no
credentials at all); `never` = anonymous only. Each yt-dlp session attempt uses `cache/jobs/<id>/cookies.txt`.

After a failed attempt (`ladder_action`): `network`, `disk`, `ffmpeg_missing`, `engine_missing`, `not_found`,
`canceled` stop; `login_required`, `private`, `unknown` go to the next rung; `rate_limited` on the anonymous rung
goes to a signed-in rung when there is one, otherwise waits 30 s then 90 s and retries the same rung (max 2);
`extractor` triggers self-heal (§2) once, then the next rung. Final error (`pick_final_error`): the last error
when it is definitive (network, disk, ffmpeg/engine missing, not_found, private, rate_limited), else the most
telling one in the order private > login_required > rate_limited > not_found > last.
`can_login`: `login_required` always (a dead session means sign in again); `private` / `rate_limited` only when
not signed in.

Session invalid: when yt-dlp prints "cookies are no longer valid" or the per-job jar lost `sessionid`, the core
sets `invalid: true` in `auth/auth.json` (status becomes logged out), emits `auth` and a warning toast.

Classification (`classify_error`): regexes run on the `ERROR:` / `[error]` lines first, then on the whole
stderr; first match wins in this order:

| Code | Matched (case-insensitive) | Message (ru) |
|---|---|---|
| disk | `No space left on device`, `[Errno 28]`, `not enough space on the disk`, `Permission denied`, `[Errno 13]`, `[WinError 5/112]`, `Access is denied`, `unable to open for writing`, `Read-only file system` | Could not write the file: the disk is full or the folder is not accessible. |
| ffmpeg_missing | `ffmpeg is not installed`, `ffprobe and ffmpeg not found`, `ffmpeg not found`, `ffprobe not found` | ffmpeg is missing, so video and audio can't be merged. Update the engine. |
| not_found (no video) | `There is no video in this post`, `No video formats found` | This post has no video. |
| private | `only available for registered users who follow this account`, `This account is private`, `private account` | This account is private: only followers can see the video. |
| rate_limited | `HTTP Error 429`, `Too Many Requests`, `exceeded the rate-limit`, `Please wait a few minutes` | Instagram has temporarily limited requests. Wait a while or sign in. |
| login_required | `rate-limit reached or login required`, `login required`, `redirect(ed) to (the) login page`, `registered users`, `empty media response`, `Restricted Video`, `HTTP Error 401`, `AuthorizationError`, `checkpoint_required`, `challenge_required`, `cookies are no longer valid`, `use --cookies` | Instagram won't show this video without signing in. Sign in to Instagram in the app. |
| not_found | `HTTP Error 404`, `404 Not Found`, `NotFoundError`, `Page Not Found`, `isn't available`, `Media not found`, `post was/has been removed/deleted` | Post not found: it was deleted or the link is wrong. |
| network | `getaddrinfo failed`, `Failed to resolve`, `timed out`, `Connection reset/refused/aborted`, `Failed to connect`, `curl: (5/6/7/28/35/52/56)`, `TransportError`, `urlopen error`, `SSL: `, `HTTP Error 5xx` | Can't reach Instagram. Check your internet connection. |
| extractor | `Unable to extract`, `Failed to parse JSON`, `please report this issue`, `Unsupported URL`, `KeyError`/`TypeError`/`IndexError`/`AttributeError`, `Traceback`, `Requested format is not available`, `AbortExtraction` | Instagram changed its site and the engine can't read it yet. Try again later. |
| unknown | anything else | Unknown error. See the log for details. |

`engine_missing` / `ffmpeg_missing` are also raised directly when the binary path is missing; a subprocess timeout
(probe 180 s, download/transcode 3600 s) is `network`. Full stderr of every attempt (with command lines) goes to
`logs/jobs/<id>.log`, linked as `error.log_path`.

Real samples (yt-dlp nightly 2026.10.10, in `tests/test_core_errors.py`):
- deleted / non-existent shortcode, anonymous: "Instagram sent an empty media response … use --cookies-from-browser
  or --cookies" -> `login_required` (anonymous access cannot tell "deleted" from "login wall"; with a session the
  API answers and the result becomes `not_found`).
- long shortcode (private post): "This content is only available for registered users who follow this account".
- broken `sessionid` cookie: "Failed to parse JSON … please report this issue" -> `extractor` (then the ladder
  continues).
- proxy down: "Unable to download webpage: Failed to perform, curl: (7) Failed to connect …".
- gallery-dl anonymous: "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)",
  exit code 4 — gallery-dl needs a session for Instagram in 2026-10.

## 6. File names, history, settings rules

- `filename_template` tokens `{author}` `{shortcode}` `{date}` (YYYY-MM-DD) `{title}` (first caption line, ≤ 60);
  must contain `{shortcode}` (else different reels collide). Sanitising: whitespace -> space, `<>:"/\|?*` and
  control chars -> `_`, repeated separators collapsed, trailing dots/spaces stripped, reserved names (`CON`,
  `NUL`, `COM1`…, `LPT9`) prefixed with `_`, ≤ 150 chars, empty -> shortcode.
- `history.json`: `{"version": 1, "jobs": [...]}`, last 300 finished jobs plus active ones, written atomically
  when a job is added, finishes, is canceled or removed, and on shutdown. Active jobs at exit (or found active on load) come back as `canceled` with
  stage_text "Interrupted when the app closed". Thumbnails are stored as files and re-read on load.
- Settings: unknown keys ignored, a bad value on load falls back to its default, a broken file gives defaults.

## 7. How to test

Unit tests (no network): `python -m unittest discover -s tests` (core files `tests/test_core_*.py`: settings,
error samples, progress parsing, names, cookies, ladder + full pipeline with a fake runner, history, updater
with fake net, engine facade).

Real network (never the user's folders):
```
set REELS_SAVER_HOME=%TEMP%\idl_home
python -m app.core.cli --update-only
python -m app.core.cli --out %TEMP%\idl_out https://www.instagram.com/reel/DQCkNLtgqEe https://www.instagram.com/reel/DPd8KODDA4d
python -m app.core.cli --out %TEMP%\idl_out --set quality=\"720\" --set h264_compat=false <links>
set REELS_SAVER_NO_SYSTEM_FFMPEG=1  (forces the bundled ffmpeg download path)
```
Exit code 0 = all jobs done, 1 = some failed, 2 = engine not usable. `--json` prints raw events.

## 8. Instagram / tool quirks (2026-10)

- Anonymous reels work through yt-dlp's logged-out GraphQL path (needs the standalone exe: it bundles curl_cffi
  for impersonation). The web profile API (`/api/v1/users/web_profile_info/`) answers nothing anonymously.
- New reels are VP9-only at the top resolution -> with `h264_compat` (default) almost every new reel is
  transcoded; old reels are AVC and are not.
- yt-dlp `--update-to stable` from a newer nightly says "is up to date" (no implicit downgrade); the updater then
  installs the verified stable copy itself.
- `--progress-template` prints `NA` for missing fields even inside `%(…)j`.
- gallery-dl moved its release binaries to Codeberg; GitHub releases are empty. `--update-check` exits 1 when up
  to date (not an error).
