# Engine (app/core) — update flow, command lines, formats, preview, error codes, site quirks

Owner: core worker. Contract: `docs/ARCHITECTURE.md` (sections 4–6) and `docs/BRIDGE_API.md` (payloads).
Measured on 2026-10-20 with yt-dlp nightly 2026.10.10.233141, gallery-dl 1.32.17, ffmpeg 9.0.2 (Gyan, PATH) and
the FFmpeg-Builds master build N-127709 (bundled path). The format tables of §4 were probed without a JavaScript
runtime; since 2.1.0 the updater installs deno (2.9.7 on 2026-10-20) and yt-dlp runs its challenge solver with it
(§2 step 5, §8).

## 1. Modules

| File | Role |
|---|---|
| `paths.py` | `AppPaths` (root = `%MEDIA_SAVER_HOME%` or `<app dir>\media-saver_data`, folders created on access), atomic file writes. |
| `settings.py` | Defaults, per-key validation (ValueError, English text), `SettingsStore` (thread-safe, tmp + `os.replace`); `PLATFORMS`, `FORMATS`. |
| `auth.py` | Netscape cookies.txt writer/reader, `read_auth_status`, cookies import validation, sidecar `auth/auth.json` (`source`, `saved_at`, `invalid`). Instagram only. |
| `updater.py` | Engine bootstrap / update (`Updater`), `Net` (urllib with UA, timeouts, retries), SHA-256 verification. |
| `linkclean.py` | Link extraction from free text and canonicalisation for Instagram and YouTube (`clean_text(text, platform)`, `clean_link`); rules in its docstring. |
| `downloader.py` | `ProcessRunner` (hidden subprocess, Job Object tree kill), `BinaryGate`, progress parsing, error classification per platform, format selection, auth ladder per platform, `Downloader` (job table, download workers, preview worker, history). |
| `engine.py` | `Engine` facade of ARCHITECTURE section 6 (integrator); `logs/app.log` rotating handler on the `mediasaver` logger. |
| `cli.py` | Headless harness: `python -m app.core.cli` (§7). |

`Downloader` public surface used by `engine.py`: `add(url, shortcode, platform, kind, fmt)`, `active_keys()`
(`"<platform>:<shortcode>"` of queued / running jobs), `set_format(job_id, fmt)` (only while `queued`, else `None`),
`clear_finished(platform=None)`, plus `start`, `shutdown`, `set_concurrency`, `jobs`, `get`, `cancel`, `retry`,
`remove`, `job_file`, `mark_waiting`, `load_history`, `save_history`.

## 2. Update flow (every launch, background thread)

`Engine.start()` emits the current `engine` status, then `Updater.start_background()` runs one pass:

1. `checking`: detect binaries (`yt-dlp.exe --version`, `gallery-dl.exe --version`, `ffmpeg -version`,
   `deno --version`).
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
   Env `MEDIA_SAVER_NO_SYSTEM_FFMPEG=1` (or `Updater(use_system_ffmpeg=False)`) disables PATH detection (for
   ffmpeg and deno alike).
5. deno (JavaScript runtime for yt-dlp's YouTube challenge solver, needed since yt-dlp 2025.10 deprecated
   extraction without one): `bin/deno.exe` ("bundled") -> PATH ("system") -> download
   `https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip` (~41 MB; the
   `latest/download/` redirect needs no GitHub API quota), verify against the sidecar
   `deno-x86_64-pc-windows-msvc.zip.sha256sum`, extract the single `deno.exe` (~93 MB) to `bin/deno.exe.part`,
   verify it against `deno-x86_64-pc-windows-msvc.sha256sum`, swap under the gate, delete the zip. The sidecars
   are PowerShell `Get-FileHash` output (`Hash      : <UPPER-CASE SHA>`, CRLF), parsed by `parse_hash_sidecar`
   (first 64-hex token). Re-checked every 30 days like ffmpeg (sidecar SHA compared with the stored one). Observed
   on 2026-10-20: release v2.9.7, zip SHA `a0c3101b…f2238` (42 630 221 bytes), deno.exe SHA `e020f3e2…9b33`.
   A missing deno is installed even with `auto_update=false`; a failed install never blocks the engine (the
   `deno` component just stays `ok: false` and YouTube runs on the deprecated path).
6. gallery-dl (fallback engine, Instagram only, always the stable channel): GitHub releases of mikf/gallery-dl no
   longer carry binaries (2026-10: assets list empty, `latest/download/gallery-dl.exe` is a 404). The exe is
   published on Codeberg: `https://codeberg.org/mikf/gallery-dl/releases/download/latest/gallery-dl.exe` with
   `.../latest/SHA256SUMS`. Same SHA logic; SHA differs -> `gallery-dl.exe --config-ignore -U` (it has
   `-U/--update`, `--update-to stable|dev`, `--update-check`), still differs -> fresh verified download.
7. State in `bin/versions.json` (`yt-dlp.channel`, `ffmpeg.downloaded_at`, `ffmpeg.zip_sha`, `deno.downloaded_at`,
   `deno.zip_sha`, `last_check`).
8. Final stage: `ready` (yt-dlp works; without ffmpeg the message says merging and MP3 need it and the next engine
   update fetches it), `offline`, or `error`.

`auto_update=false` skips step 3–6 updates but still installs missing binaries. `update_engine()` and a change of
`update_channel` run a forced pass. A pass requested while one runs queues one more forced pass.

Gate: jobs and the preview worker hold `BinaryGate` shared while a yt-dlp / gallery-dl subprocess runs; replacing a
binary takes it exclusively (waits up to 300 s for running subprocesses, blocks new ones). `Updater.usable()` is
false while checking or replacing, so queued jobs wait ("Waiting for engine update") and the preview worker idles.

Self-heal: an `extractor` error on a yt-dlp rung calls `Updater.self_heal()` — one forced yt-dlp pass per app
session; when it ran, the same rung is retried once. Later jobs skip the retry and go down the ladder.

## 3. Job pipeline and exact command lines

All subprocesses: `creationflags=CREATE_NO_WINDOW`, stdin `DEVNULL`, env `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`,
`DENO_DIR=cache\deno` (the deno cache yt-dlp's solver creates stays in the data folder, not in `%LOCALAPPDATA%`)
(and `PYTHONHOME`/`PYTHONPATH` removed), output decoded as UTF-8 with `errors="replace"`, each process assigned to
a Win32 Job Object with `KILL_ON_JOB_CLOSE`; cancel / timeout / shutdown calls `TerminateJobObject` (kills
yt-dlp and its ffmpeg children), fallback `taskkill /T /F`. Working dir and temp files: `cache/jobs/<id>/`,
removed when the job ends (also when a queued job with a preview is canceled or removed).

Job fields added in 2.0: `platform` (`instagram` | `youtube`), `kind` (`reel` | `p` | `video` | `shorts`), `format`
(`mp4` | `mp3`, changeable with `set_format` only while `queued`; a worker that takes the job sets `probing` under
the lock first, so the format is frozen from then on), `previewed`.

Common yt-dlp prefix (`Y`):

```
yt-dlp.exe --no-config --encoding utf-8 --color never --socket-timeout 20 --retries 3 --extractor-retries 2 --no-mtime
           [--no-playlist                                    (YouTube jobs)]
           [--js-runtimes deno:<path to deno.exe>            (when the updater found deno: bin\deno.exe or PATH)]
           [--cookies cache\jobs\<id>\cookies.txt | --cookies-from-browser <browser>]
```

### 3.1 Preview worker (ARCHITECTURE 5.4)

One daemon thread `ms-preview`, started by `Downloader.start()`:

1. Picks the oldest job with `status == "queued"` and `previewed == False`; waits while `usable()` is false or
   the gate is held exclusively (updater replacing binaries). One job, one subprocess at a time.
2. `Y -J <url>` anonymous (never `--cookies` / `--cookies-from-browser`), timeout 60 s, cwd `cache/jobs`, under
   the shared gate, with its own cancel event.
3. Success: info json (carousel -> first video entry) written to `cache/jobs/<id>/preview.json`, `preview_at`
   recorded; `title`, `author`, `duration_s` set (event `job`), then the thumbnail (§3.2 step 2, event `job`),
   then `previewed = true` (event `job`) and a history save.
4. Failure, timeout, unusable json: `previewed = true`, nothing else, INFO line in `logs/app.log`
   ("preview of job <id>: failed after 1.2 s (not_found)"). No toast, no error on the job.
5. The job leaves `queued` while its preview runs: a download worker taking it, `cancel`, `remove` and `shutdown`
   set the preview's cancel event; `ProcessRunner` kills the subprocess tree; no field changes. The download worker
   never waits for a preview: it probes by itself.

Measured: `yt-dlp -J` of a YouTube video 2.1–2.3 s; preview including the thumbnail 2.5 s (Shorts A9Yn8Ad_cH8).

### 3.2 Download pipeline

1. Probe: the anonymous rung takes `preview.json` when it is younger than 10 minutes (`PREVIEW_MAX_AGE`; used
   once, a retried rung probes again; job log line "reused the preview info json (N s old)"); otherwise
   `Y -J <url>` (timeout 180 s) -> stdout JSON saved to `cache/jobs/<id>/info.json` (a `/p/` carousel is a
   playlist: the first entry with video formats is used). `live_status` `is_live` / `is_upcoming` / `post_live`
   -> `not_found` "Live streams and premieres are not supported.". Meta: Instagram author = `channel`
   (username), title = first caption line; YouTube author = `channel` (channel name), title = `title`.
   `previewed` becomes true here at the latest.
2. Thumbnail (only when the job has none yet): `info.thumbnail` fetched with urllib (browser UA) (Instagram jpg,
   YouTube `vi_webp/<id>/maxresdefault.webp` or `vi/<id>/hq720_2.jpg?...` for Shorts), then
   `ffmpeg -hide_banner -nostdin -y -loglevel error -i thumb_src -vf "scale='min(480,iw)':-2" -frames:v 1 -q:v 4 -f image2 -c:v mjpeg cache\thumbs\<key>.jpg`
   (`<key>` = shortcode for Instagram, `youtube_<id>` for YouTube), sent as `data:image/jpeg;base64,…`
   (25–55 KB). Without ffmpeg a raw poster under 400 KB is kept. History keeps only the path.
3. Target name from `filename_template` + `.mp4` / `.mp3` by `job.format` (sanitised, see §6); `skip_existing`
   + that exact file existing and non-empty -> job `done`, `already_existed=true`, fields from ffprobe of the
   existing file (an `.mp4` does not satisfy an mp3 job and vice versa).
4. Download, mp4:
   ```
   Y --load-info-json info.json -f <spec> [-S <sort>] --merge-output-format mp4 --ffmpeg-location <ffmpeg.exe>
     -o cache\jobs\<id>\media.%(ext)s --no-part --force-overwrites
     --newline --progress --progress-delta 0.3
     --progress-template "download:[[IDLP]]{\"s\":%(progress.status)j,\"d\":%(progress.downloaded_bytes)j,\"t\":%(progress.total_bytes)j,\"te\":%(progress.total_bytes_estimate)j,\"sp\":%(progress.speed)j,\"eta\":%(progress.eta)j,\"f\":%(info.format_id)j,\"vc\":%(info.vcodec)j}"
     --progress-template "postprocess:[[IDLPP]]{\"s\":%(progress.status)j,\"pp\":%(progress.postprocessor)j}"
     --print "after_move:[[IDLFILE]]%(filepath)j" --no-simulate
   ```
   Example (YouTube, `quality_youtube` "max" on a video whose top is 1080p): `-f 299+140/299/bv*+ba/b`. Template facts checked on this nightly:
   `%(field)j` prints JSON; a missing field prints `NA` (not `null`) — the parser maps bare `NA` to `null`.
   `--print` implies quiet, so `--progress` is needed to keep progress lines. Download lines go to stdout,
   postprocess lines to stderr; both streams are parsed. Video vs audio stream is told apart by `vc == "none"`
   or the chosen audio format id.
5. Download, mp3 (ffmpeg checked first: missing -> `ffmpeg_missing` before any download subprocess):
   ```
   Y --load-info-json info.json -f "ba/b" -S channels:2 -x --audio-format mp3 --audio-quality 0 --ffmpeg-location <ffmpeg.exe>
     -o cache\jobs\<id>\media.%(ext)s --no-part --force-overwrites <same progress / print arguments>
   ```
   Stages: "Downloading audio", then the `ExtractAudio` postprocessor lines (`[[IDLPP]]{"s":"started",
   "pp":"ExtractAudio"}` / `finished`, confirmed on this nightly) -> "Converting to MP3", then "Saving file". No
   merge, no H.264 pass; `quality_instagram`, `quality_youtube` and `h264_compat` are ignored. `-S channels:2` puts
   stereo / mono tracks before the 5.1 ones YouTube lists with deno (yt-dlp's default order ranks more channels
   higher and would downmix the 6-channel source into the MP3). Final fields: `width/height/fps/vcodec` null,
   `acodec` "mp3", `duration_s` from ffprobe, `filesize`. A job whose info json lists only formats with
   `acodec == "none"` fails early with `not_found` "This video has no sound, so there is nothing to save as MP3."
   (also when ffmpeg reports "unable to obtain file audio codec"). Output: libmp3lame VBR q0, ~240 kbit/s; 48 kHz
   from YouTube (opus 251 is yt-dlp's `ba`), 44.1 kHz from Instagram (HE-AAC).
6. H.264 pass (mp4 only, when `h264_compat` and ffprobe says the merged video is not h264):
   ```
   ffmpeg -hide_banner -nostdin -y -loglevel error -i media.mp4 -map 0:v:0 -map 0:a:0? -c:v libx264 -crf 16
          -preset slow -pix_fmt yuv420p -profile:v high -movflags +faststart -c:a copy|(-c:a aac -b:a 256k)
          -progress pipe:1 -nostats h264.mp4
   ```
   Audio is copied when it already is AAC (Instagram serves HE-AAC / AAC-LC, YouTube 140 is AAC-LC), else AAC 256k.
   Progress from `out_time_us` / duration. Speed on this machine: 1080x1920 26 s reel ≈ 8 s. A YouTube VP9 / AV1
   top at 1440p or 2160p is transcoded at that size, which takes minutes (CPU encode, preset slow).
7. Move to `<output_dir>\<name>.<ext>` (via `.part` + `os.replace`; never overwrites: `name (2).mp4`), then
   `ffprobe -v error -print_format json -show_format -show_streams <file>` fills width/height/fps/vcodec/acodec;
   filesize from the file.

gallery-dl rung (Instagram, session only, per-job copy `cookies-gdl.txt`):
```
gallery-dl.exe --config-ignore --cookies <jar> -o videos=true -j <url>                       (metadata)
gallery-dl.exe --config-ignore --cookies <jar> -o videos=true -D cache\jobs\<id>\gdl
               -f "{shortcode}_{num}.{extension}" --no-mtime --filter "extension == 'mp4'" <url>
```
gallery-dl downloads the progressive `video_url` (≤ 720p baseline H.264, see §4), so it is a last resort; its
first mp4 then goes through steps 6–7. mp3 jobs on this rung: `ffmpeg … -i <mp4> -vn -map 0:a:0 -c:a libmp3lame
-q:a 0 media.mp3`. No progress output (progress jumps).

Overall progress weights (normalised): mp4 — probe 0.06, video 0.62 (0.72 without audio), audio 0.10, merge
0.04, transcode 0.30 (only when needed), finalize 0.02; mp3 — probe 0.06, audio 0.72, convert 0.20, finalize
0.02. Values never go backwards.

## 4. Quality: what the sites serve (anonymous, 2026-10-20) and the format choice

### 4.1 Instagram

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

### 4.2 YouTube

Probed with `yt-dlp --no-playlist -J` without cookies and without a JS runtime (`tbr` kbit/s; "HLS" = m3u8
formats, whose `tbr` is the manifest's peak bandwidth):

| Video | Top | AVC (avc1, https) | VP9 / AV1 (https) | HLS (m3u8) | Audio |
|---|---|---|---|---|---|
| aqz-KE-bpKQ Big Buck Bunny, 635 s, 60 fps | 3840x2160 | 160 144p … 298 720p60 1898, **299 1080p60 3248** | VP9 303 1080p 2127, 308 1440p 5968, **315 2160p 17174**; AV1 399 1080p 1568, 400 1440p, 401 2160p 8982 | avc1 269–312 up to 1080p (312: 7418), vp09 602–628 up to 2160p (628: 27987) | 140 AAC-LC 129, 139 HE-AAC 49, opus 249/250/251 (50/66/129), each also as `-drc` |
| sO_lhW8av3U Blender Conference 2026 Recap, 149 s, 25 fps | 3840x2160 | **137 1080p 2156** | VP9 248 1080p 1357, 271 1440p, 313 2160p 14776; AV1 399/400/401 | avc1 269–270 (270: 4686), vp09 602–625 | same set, with `-drc` |
| dQw4w9WgXcQ (4K remaster), 213 s, 25 fps | 3840x2160 | **137 1080p 3038** | VP9 313 2160p 13466; AV1 401 9025 | yes | 140, 139, 249/250/251 (no `-drc`) |
| A9Yn8Ad_cH8 Shorts, 43 s, 24 fps, vertical | 2160x3840 | **137 1080x1920 2066** | VP9 248 1080x1920 1655, 271 1440x2560, **313 2160x3840 13930**; AV1 399/400/401; extra VP9 779/780, AV1 787/788 at 608x1080 | avc1 to 1080x1920, vp09 to 2160x3840 | 140, 139, opus, `-drc` |

Findings:
- AVC exists up to **1080p** (also 1080p60), never above. 1440p and 2160p are VP9 / AV1 only.
- Every resolution exists as a direct https DASH file and as HLS; the muxed format 18 was not listed (2026-10,
  no JS runtime). Storyboards `sb0…sb3` (mhtml) appear as `vcodec: none, acodec: none`.
- Audio: AAC-LC 140 (~130 kbit/s), HE-AAC 139, opus 249/250/251; many videos also list `-drc` (dynamic range
  compressed) copies with the same bitrate. Audio HLS formats 233/234 have `acodec: null`.
- Downloads ran at full line speed: 256 MB (BBB 1080p60 AVC + AAC) in ~7 s, 40.6 MB in ~6 s, no throttling.

### 4.3 The choice (`select_formats`, done in Python on the info json so it is testable and deterministic)

- Candidates: formats with a `format_id`, without `has_drm`, not mhtml / storyboard. Video = `vcodec` not
  null / "none" and a width or height. Audio = `vcodec == "none"` and `acodec` not null / "none".
- "max": highest pixel area, then fps, then direct https over HLS (HLS peak `tbr` is not comparable and fragment
  downloads are slower), then video-only over muxed, then bitrate (`vbr`/`tbr`).
- Quality is per platform: a job reads the setting of its own platform (`job.platform`) when it starts; mp3 reads
  neither. `select_formats(info, platform, quality, h264_compat)`, caps in `QUALITY_CAPS`:
  Instagram `quality_instagram`: "low" = short side ≤ 480, "medium" = ≤ 720, "max" = best available;
  YouTube `quality_youtube`: "360" = short side ≤ 360, "720" = ≤ 720, "max" = best available (1080p, 1440p, 2160p:
  whatever the video has). A capped choice is the same ranking as "max" limited to short side ≤ cap × 1.02 (keeps
  716x1274 for "medium" and 1088x1936 where a cap is near it; vertical Shorts are capped by their width); when
  nothing fits, the smallest available video.
- `h264_compat`: an AVC format with the same area (±2 %) and fps (±1) wins over the non-AVC best (YouTube 1080p:
  299 / 137 instead of VP9 / AV1); otherwise the best stays and the H.264 pass runs (YouTube 1440p / 2160p, current
  Instagram reels). A lower-resolution AVC is never preferred (quality first).
- Audio: original / default language first (`language_preference`), then non-DRC, then stereo / mono over
  surround (`audio_channels` > 2; with deno YouTube also lists 6-channel 258 AAC 388 kbit/s, 380 AC-3 and 328
  E-AC-3, which would otherwise win on bitrate and put a 5.1 track into the MP4), then AAC when `h264_compat`,
  then direct https, then `abr`, then sample rate. A muxed video (YouTube 18) gets no second audio track.
- Resulting `-f`: `<video_id>+<audio_id>/<video_id>/bv*+ba/b` (fallbacks only matter when the chosen ids vanish).
  When the info json has no usable video metadata: `-f "bv*+ba/b" -S "res,fps,br[,vcodec:avc,acodec:aac]"`
  (`res:<cap>` with the platform cap for capped qualities, e.g. `res:480` for Instagram "low").
- Measured results (taken with the former 1080 cap, which is no longer offered; the AVC picks below are now
  reached with the closest caps: "720" or "max" on a video that tops out at 1080p): BBB -> 299+140, 1920x1080 60 fps h264/aac, 255.9 MB, no transcode; Recap -> 137+140, 1920x1080 25 fps;
  Shorts -> 137+140, 1080x1920 24 fps, 11.4 MB; YouTube "max" on a 2160p Shorts without
  `h264_compat` -> 313+251, 2160x3840 VP9 / opus, 72.7 MB; Instagram DQCkNLtgqEe "max" -> VP9 1080x1920 ->
  H.264 pass, 25.3 MB.
- CRF 16 from VP9 1080p gives ~25–35 MB for 26–60 s (3–4x the source size): visually lossless for editing in
  Premiere / After Effects, which is what `h264_compat` is for.

## 5. Auth ladder and error codes

Ladder (`plan_ladder(auth_mode, has_session, browser, platform)`):
- Instagram: `auto` = anonymous -> session (`auth/cookies.txt` exists and valid) -> browser (`browser_cookies`
  set) -> gallery-dl + session; `always` = the same without anonymous (anonymous when no credentials at all);
  `never` = anonymous only. Each yt-dlp session attempt uses `cache/jobs/<id>/cookies.txt`.
- YouTube: anonymous -> browser (only when `browser_cookies` is set). `auth_mode` and the Instagram session do not
  apply; no session rung, no gallery-dl rung. `can_login` is always false for YouTube jobs.

After a failed attempt (`ladder_action`, both platforms): `network`, `disk`, `ffmpeg_missing`, `engine_missing`,
`not_found`, `canceled` stop; `login_required`, `private`, `unknown` go to the next rung; `rate_limited` on the
anonymous rung goes to a signed-in rung when there is one, otherwise waits 30 s then 90 s and retries the same rung
(max 2; stage text "<Instagram|YouTube> is limiting requests, waiting 30 s"); `extractor` triggers self-heal (§2)
once, then the next rung. Final error (`pick_final_error`): the last error when it is definitive (network, disk,
ffmpeg/engine missing, not_found, private, rate_limited), else the most telling one in the order
private > login_required > rate_limited > not_found > last.
`can_login` (Instagram only): `login_required` always (a dead session means sign in again); `private` /
`rate_limited` only when not signed in.

Session invalid (Instagram): when yt-dlp prints "cookies are no longer valid" or the per-job jar lost `sessionid`,
the core sets `invalid: true` in `auth/auth.json` (status becomes logged out), emits `auth` and a warning toast.

Classification (`classify_error(text, platform)`): regexes run on the `ERROR:` / `[error]` lines first, then on the
whole stderr; first match wins. Instagram uses the table below; YouTube uses the disk and ffmpeg rows, then the
YouTube rows, then the rest of the table, with the YouTube texts (no message of a YouTube job names Instagram).

| Code | Matched (case-insensitive) | Message |
|---|---|---|
| disk | `No space left on device`, `[Errno 28]`, `not enough space on the disk`, `Permission denied`, `[Errno 13]`, `[WinError 5/112]`, `Access is denied`, `unable to open for writing`, `Read-only file system` | Could not write the file: the disk is full or the folder is not accessible. |
| ffmpeg_missing | `ffmpeg is not installed`, `ffprobe and ffmpeg not found`, `ffmpeg not found`, `ffprobe not found` | ffmpeg is missing: it is needed to merge video and audio and to make MP3 files. Update the engine to fetch it. |
| *YouTube* private | `Private video`, `members-only`, `Join this channel`, `available to this channel's members`, `members on level` | This video is private or for channel members only. |
| *YouTube* login_required | `Sign in to confirm you’re not a bot`, `Sign in to confirm your age`, `age-restricted`, `inappropriate for some users`, `requires login` | YouTube wants a signed-in browser for this video. Choose a browser in Settings > Browser cookies. |
| *YouTube* rate_limited | `rate-limited by YouTube`, `has been rate-limited`, `try again later` | YouTube has temporarily limited requests. Wait a while or choose a browser in Settings > Browser cookies. |
| *YouTube* not_found (live) | `live event`, `Premieres in`, `Premiere will begin`, `live stream`, `This live`, `is a live` | Live streams and premieres are not supported. |
| *YouTube* not_found | `Video unavailable`, `This video is not available`, `This video is unavailable`, `has been removed`, `no longer available`, `has been terminated`, `not made this video available`, `not available in your country` | Video not found: it was deleted, is unavailable or the link is wrong. |
| not_found (no video) | `There is no video in this post`, `No video formats found` | This post has no video. (YouTube: This video has nothing to download.) |
| private | `only available for registered users who follow this account`, `This account is private`, `private account` | This account is private: only followers can see the video. |
| rate_limited | `HTTP Error 429`, `Too Many Requests`, `exceeded the rate-limit`, `Please wait a few minutes` | Instagram has temporarily limited requests. Wait a while or sign in. |
| login_required | `rate-limit reached or login required`, `login required`, `redirect(ed) to (the) login page`, `registered users`, `empty media response`, `Restricted Video`, `HTTP Error 401`, `AuthorizationError`, `checkpoint_required`, `challenge_required`, `cookies are no longer valid`, `use --cookies` | Instagram won't show this video without signing in. Sign in to Instagram in the app. |
| not_found | `HTTP Error 404`, `404 Not Found`, `NotFoundError`, `Page Not Found`, `isn't available`, `Media not found`, `post was/has been removed/deleted` | Post not found: it was deleted or the link is wrong. |
| network | `getaddrinfo failed`, `Failed to resolve`, `timed out`, `Connection reset/refused/aborted`, `Failed to connect`, `curl: (5/6/7/28/35/52/56)`, `TransportError`, `urlopen error`, `SSL: `, `HTTP Error 5xx` | Can't reach Instagram / YouTube. Check your internet connection. |
| extractor | `Unable to extract`, `Failed to parse JSON`, `please report this issue`, `Unsupported URL`, `KeyError`/`TypeError`/`IndexError`/`AttributeError`, `Traceback`, `Requested format is not available`, `AbortExtraction` | Instagram / YouTube changed its site and the engine can't read it yet. Try again later. |
| unknown | anything else | Unknown error. See the log for details. |

`engine_missing` / `ffmpeg_missing` are also raised directly when the binary path is missing; a subprocess timeout
(probe 180 s, download/transcode 3600 s) is `network`. A YouTube info json with `live_status` `is_live` /
`is_upcoming` / `post_live` fails as `not_found` with the live message. Full stderr of every attempt (with command
lines) goes to `logs/jobs/<id>.log`, linked as `error.log_path`.

Real samples (yt-dlp nightly 2026.10.10, in `tests/test_core_errors.py` and `tests/test_core_youtube.py`):
- Instagram deleted / non-existent shortcode, anonymous: "Instagram sent an empty media response … use
  --cookies-from-browser or --cookies" -> `login_required` (anonymous access cannot tell "deleted" from "login
  wall"; with a session the API answers and the result becomes `not_found`).
- Instagram long shortcode (private post): "This content is only available for registered users who follow this
  account".
- Instagram broken `sessionid` cookie: "Failed to parse JSON … please report this issue" -> `extractor` (then the
  ladder continues).
- proxy down: "Unable to download webpage: Failed to perform, curl: (7) Failed to connect …".
- gallery-dl anonymous: "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)",
  exit code 4 — gallery-dl needs a session for Instagram in 2026-10.
- YouTube non-existent id (`watch?v=xxxxxxxxxxx`): "ERROR: [youtube] xxxxxxxxxxx: This video is unavailable" ->
  `not_found` (1.2 s).
- YouTube 24/7 stream id `jfKfPfyJRdk` without a JS runtime: "ERROR: [youtube] jfKfPfyJRdk: This live stream
  recording is not available." -> `not_found` (live message).
- Without deno every YouTube call prints "WARNING: [youtube] No supported JavaScript runtime could be found. Only
  deno is enabled by default; … YouTube extraction without a JS runtime has been deprecated, and some formats may
  be missing." (warning only; gone once the updater installed deno, §8).
- Not observed on this machine (patterns from yt-dlp's YouTube extractor texts, covered by tests): the bot check
  "Sign in to confirm you’re not a bot", age gate, private / members-only, rate-limited session, premieres.

## 6. File names, history, settings rules

- `filename_template` tokens `{author}` `{shortcode}` `{date}` (YYYY-MM-DD) `{title}` (≤ 60); must contain
  `{shortcode}` (Instagram shortcode or YouTube video id; else different videos collide). Extension `.mp4` or
  `.mp3` by `job.format`, so the same video can exist in both formats. Author fallback when empty: `instagram` /
  `youtube`. Sanitising: whitespace -> space, `<>:"/\|?*` and control chars -> `_`, repeated separators
  collapsed, trailing dots/spaces stripped, reserved names (`CON`, `NUL`, `COM1`…, `LPT9`) prefixed with `_`,
  ≤ 150 chars, empty -> shortcode. Example: `Blender_aqz-KE-bpKQ.mp3`, `teddysphotos_DQCkNLtgqEe.mp4`.
- `history.json`: `{"version": 2, "jobs": [...]}`, last 300 finished jobs (both platforms) plus active ones,
  written atomically when a job is added, previewed, finishes, is canceled or removed, and on shutdown. Active
  jobs at exit (or found active on load) come back as `canceled` with stage_text "Interrupted when the app
  closed". Thumbnails are stored as files and re-read on load. Every restored job has `previewed = true`.
- Migration: version 1 entries (no `platform` / `kind` / `format`) load as `platform` "instagram", `kind` "p" for
  `/p/` URLs else "reel", `format` "mp4". Unknown values in version 2 entries fall back the same way (YouTube
  kind from the URL: `/shorts/` -> "shorts", else "video"). The next save writes version 2.
- Settings: unknown keys ignored, a bad value on load falls back to its default, a broken file gives defaults.

## 7. How to test

Unit tests (no network): `python -m unittest discover -s tests` with `MEDIA_SAVER_HOME` set to a scratch folder.
Core files: `test_linkclean` (Instagram table, YouTube table, extraction, mixed text with platform filter),
`test_core_formats` (mp3 pipeline, YouTube mp4, format selection, per-platform quality settings, `set_format`, `active_keys`, `clear_finished`,
history v2), `test_core_preview` (preview fetch / reuse / failure / timeout / cancel / remove / gate / order),
`test_core_youtube` (YouTube error samples, ladder, `can_login`, platform texts), `test_core_ladder` (Instagram
ladder + full pipeline with a fake runner), `test_core_history`, `test_core_errors`, `test_core_progress`,
`test_core_names`, `test_core_auth`, `test_core_updater` (install / up to date / 30-day refresh / offline / bad
checksum of every component, deno included; `test_core_youtube` checks that the bundled deno path reaches the yt-dlp
command line). The fakes live in `tests/core_fakes.py`.

Real network (never the user's folders; cmd syntax):
```
set MEDIA_SAVER_HOME=%TEMP%\ms_engine_home
python -m app.core.cli --update-only
python -m app.core.cli --out %TEMP%\ms_engine_out --format mp4 https://www.instagram.com/reel/DQCkNLtgqEe
python -m app.core.cli --out %TEMP%\ms_engine_out --set quality_youtube=720 --set concurrency=1 --platform youtube --format mp4 "https://www.youtube.com/watch?v=sO_lhW8av3U&list=PL1" "https://youtube.com/shorts/A9Yn8Ad_cH8?feature=share"
python -m app.core.cli --out %TEMP%\ms_engine_out --format mp3 https://www.youtube.com/watch?v=aqz-KE-bpKQ https://www.instagram.com/reel/DQCkNLtgqEe
python -m app.core.cli --out %TEMP%\ms_engine_out --set quality_youtube=360 --platform youtube https://www.youtube.com/watch?v=aqz-KE-bpKQ
python -m app.core.cli --out %TEMP%\ms_engine_out2 --set quality_youtube=max --set h264_compat=false --platform youtube https://www.youtube.com/shorts/A9Yn8Ad_cH8
set MEDIA_SAVER_NO_SYSTEM_FFMPEG=1  (forces the bundled ffmpeg download path)
```
`--platform` accepts only links of that platform (the others are printed as `skipped invalid`); without it both
platforms are accepted. `--format` sets the format of the queued links (default: the `format` setting). `--set`
values are JSON when they parse (`quality_youtube=720` arrives as the number 720, which the YouTube quality setting accepts).
With `--set concurrency=1` and several links, the CLI shows the preview of the waiting jobs
(`preview: 'title' by 'author', 43 s, thumb`). Exit code 0 = all jobs done, 1 = some failed, 2 = engine not
usable. `--json` prints raw events. The run also starts the app self-update check (`check_app_updates`), which
reports an error while the repository has no release.

## 8. Site and tool quirks (2026-10)

Instagram:
- Anonymous reels work through yt-dlp's logged-out GraphQL path (needs the standalone exe: it bundles curl_cffi
  for impersonation). The web profile API (`/api/v1/users/web_profile_info/`) answers nothing anonymously.
- New reels are VP9-only at the top resolution -> with `h264_compat` (default) almost every new reel is
  transcoded; old reels are AVC and are not.

YouTube:
- Since yt-dlp 2025.10 YouTube extraction needs a JavaScript runtime for the player challenges; without one
  yt-dlp warns on every call ("No supported JavaScript runtime could be found … deprecated, and some formats may
  be missing"). On 2026-10-20 the warning-only path still listed every resolution up to 2160p; with deno the same
  video lists 61 formats instead of 53 (back: muxed `18`, AVC `136` and VP9 `247` at 720p, surround audio `258`
  AAC 5.1, `380` AC-3, `328` E-AC-3, multichannel HE-AAC `256`, `773`) and the warning is gone.
  yt-dlp only auto-detects deno on PATH, so the engine passes the bundled copy explicitly:
  `--js-runtimes deno:<bin\deno.exe>` (the option takes the exe path or its folder). yt-dlp then runs
  `deno run --ext=js --no-code-cache --no-prompt --no-remote --no-lock --node-modules-dir=none --no-config --no-npm
  --cached-only -` and writes a small cache (`dep_analysis_cache_v2`, `node_analysis_cache_v2`, `npm/`) to
  `DENO_DIR`, which the engine points at `cache\deno`. The official yt-dlp exe bundles the solver scripts
  (`yt_dlp_ejs`), so `--remote-components` stays off (default).
- The bot check ("Sign in to confirm you’re not a bot") did not appear on this machine; when it does, the job
  ends with `login_required` and the browser-cookies hint, and with `browser_cookies` set the browser rung runs.
- Shorts at `quality_youtube` "max" are 2160x3840 VP9 / AV1: with `h264_compat` they go through a 4K H.264 pass
  (slow); "720" gives AVC 136 without a pass (there is no 1080p cap any more).
- `--no-playlist` is always passed; canonical URLs never carry `list=` anyway.
- The thumbnail of a regular video is `maxresdefault.webp` (converted by ffmpeg), of a Shorts video a `hq720` jpg.

Tools:
- yt-dlp `--update-to stable` from a newer nightly says "is up to date" (no implicit downgrade); the updater then
  installs the verified stable copy itself.
- `--progress-template` prints `NA` for missing fields even inside `%(…)j`.
- The `ExtractAudio` postprocessor (`-x`) reports `started` / `finished` through the postprocess progress template
  (`"pp":"ExtractAudio"`), and `--print after_move:%(filepath)j` gives the final `.mp3` path.
- gallery-dl moved its release binaries to Codeberg; GitHub releases are empty. `--update-check` exits 1 when up
  to date (not an error).
