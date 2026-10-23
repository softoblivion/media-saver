# Media Saver user guide

Everything about using the app. Installation and the first steps are in the [README](../README.md).

## Usage

1. Choose the mode: **Instagram**, **YouTube**, **TikTok** or **X** (`Alt+1` … `Alt+4`).
2. Paste one or more links into the input panel, or press `Ctrl+V` anywhere in the window. A link of the other
   platform is shown with a **Switch** action.
3. Check the cleaned preview: valid, invalid and duplicate links are counted separately. Pick **MP4** or **MP3**
   and the quality for the current platform.
4. Press **Download**. Each card shows a preview as soon as the site answers, then progress, speed
   and ETA. A queued card can still switch between MP4 and MP3.
5. Finished cards open the file or reveal it in Explorer.

If Instagram refuses a reel without an account, the card offers **Sign in to Instagram**. After sign-in, the
failed jobs retry automatically. YouTube, TikTok and X videos that require a signed-in viewer (age-restricted,
members-only, sensitive content) can use the cookies of a browser chosen in Settings.

## Keyboard

| Shortcut | Action |
|---|---|
| `Ctrl+V` | Paste into the input panel from anywhere |
| `Alt+1` … `Alt+4` | Instagram / YouTube / TikTok / X mode |
| `←` `→` | Move a segmented control (mode, format, quality) |
| `Esc` | Close the settings sheet or a popover |

## Settings

Stored in `media-saver_data\settings.json`. The second column names where the setting is changed in the window.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `output_dir` | Settings > Downloads > **Downloads folder**, or the queue header | `%USERPROFILE%\Videos\Media-Saver` | Destination folder (the Windows Videos library, redirected or not) |
| `filename_template` | Settings > Downloads > **File name** | `{author}_{shortcode}` | Tokens `{author}` `{shortcode}` `{date}` `{title}`; `{shortcode}` (the video id) is required |
| `mode` | Mode switch in the title bar | `instagram` | `instagram`, `youtube`, `tiktok` or `twitter`: the platform shown in the window |
| `format` | Main window | `mp4` | Default output format for new jobs, `mp4` or `mp3` |
| `quality_instagram` | Main window, above **Download** | `max` | `low`, `medium`, `high` or `max`. MP4: short side ≤ 480, ≤ 720, ≤ 1080 or the best available (up to 1080p); MP3: 128, 192, 256 or 320 kbps |
| `quality_youtube` | Main window, above **Download** | `max` | The same four steps; MP4 `max` is the best available, up to 2160p |
| `quality_tiktok` | Main window, above **Download** | `max` | The same four steps; MP4 `max` up to 1080p |
| `quality_twitter` | Main window, above **Download** | `max` | The same four steps; MP4 `max` up to 1080p |
| `h264_compat` | Settings > Downloads > **H.264 for editing** | `true` | Guarantee H.264 / AAC output for video editors (MP4 only) |
| `skip_existing` | Settings > Downloads > **Skip downloaded** | `true` | Mark a job done when the target file already exists |
| `concurrency` | Settings > Downloads > **Simultaneous downloads** | `2` | Parallel downloads, 1–4, shared by both modes |
| `browser_cookies` | Settings > Downloads > **Browser cookies** | *(empty)* | Optional browser to read cookies from: `firefox`, `edge`, `chrome`, `brave`, `opera` (every platform) |
| `clipboard_watch` | Settings > Behavior > **Watch the clipboard** | `true` | Offer links found in the clipboard on window focus |
| `open_folder_when_done` | Settings > Behavior > **Open folder when done** | `false` | Open the output folder when the queue drains |
| `sound_on_done` | Settings > Behavior > **Sound when a download finishes** | `true` | Play a short sound when a download finishes |
| `auth_mode` | Settings > Instagram > **When to sign in** | `auto` | Instagram: `auto` (anonymous first), `always` (session first), `never` |
| `auto_update` | Settings > Engine > **Auto-update engine** | `true` | Check engines on every start |
| `update_channel` | Settings > Engine > **Update channel** | `nightly` | `nightly` or `stable` channel of yt-dlp |
| `auto_download_updates` | Settings > Updates > **Download updates automatically** | `true` | Download a new version in the background; installing is always a click on **Restart** |
| `theme` | Settings > Appearance | `dusk` | Colour theme of the window: `dusk`, `nova`, `frost` or `bloom` |

## Link cleaning rules

Every recognised link is reduced to scheme, host, kind and id. Anything after the id is dropped: trailing slashes,
`?igsh=`, `utm_*`, `t=`, `list=`, `si=`, `feature=`, `is_from_webapp=` and `s=` parameters, fragments, `/embed`,
`/video/1` and `/photo/1` suffixes.

| Input | Result |
|---|---|
| `https://www.instagram.com/reel/DAbC123xYz_/?igsh=MWx0cG9x` | `https://www.instagram.com/reel/DAbC123xYz_` |
| `instagram.com/reels/DAbC123xYz_/` | `https://www.instagram.com/reel/DAbC123xYz_` |
| `https://www.instagram.com/someuser/reel/DAbC123xYz_/` | `https://www.instagram.com/reel/DAbC123xYz_` |
| `https://m.instagram.com/tv/DAbC123xYz_` | `https://www.instagram.com/reel/DAbC123xYz_` |
| `https://www.instagram.com/p/DAbC123xYz_/?img_index=2` | `https://www.instagram.com/p/DAbC123xYz_` |
| `https://l.instagram.com/?u=https%3A%2F%2Fwww.instagram.com%2Freel%2FDAbC123xYz_%2F` | `https://www.instagram.com/reel/DAbC123xYz_` |
| `https://youtu.be/dQw4w9WgXcQ?si=abc&t=42` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| `https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=2` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| `https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share` | `https://www.youtube.com/shorts/dQw4w9WgXcQ` |
| `https://www.youtube.com/embed/dQw4w9WgXcQ` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| `https://www.tiktok.com/@tiktok/video/7106594312292453675?is_from_webapp=1&sender_device=pc` | `https://www.tiktok.com/@tiktok/video/7106594312292453675` |
| `https://vm.tiktok.com/ZMabcdef1/` | `https://vm.tiktok.com/ZMabcdef1` (resolved by the engine) |
| `https://x.com/oshtru/status/1577855540407197696?s=20&t=abc` | `https://x.com/oshtru/status/1577855540407197696` |
| `https://twitter.com/i/web/status/1577855540407197696/video/1` | `https://x.com/i/status/1577855540407197696` |
| `https://www.instagram.com/stories/someuser/` | rejected: not a single reel or post |
| `https://www.youtube.com/playlist?list=PL123` | rejected: not a single video |
| `https://www.tiktok.com/@tiktok/photo/7300000000000000000` | rejected: a photo post |
| `https://x.com/oshtru` | rejected: a profile, not a post |

Ids keep their case. Profiles, stories, audio pages, playlists, channels, searches, photo posts, Spaces, live
broadcasts, `/share/` redirect tokens and `t.co` short links are reported as unsupported.

## Data and privacy

All runtime data lives in `media-saver_data\` next to `MediaSaver.exe` (override with the `MEDIA_SAVER_HOME`
environment variable); nothing is written to the user profile:

| Path | Content |
|---|---|
| `bin\` | Engine executables and their version records |
| `auth\cookies.txt` | Session cookies from the in-app sign-in (Netscape format) |
| `settings.json`, `history.json` | Preferences and the last 300 finished jobs of every platform |
| `cache\` | Thumbnails and per-job working files |
| `logs\` | `app.log`, `shell.log` and full engine output of failed jobs |
| `webview\` | WebView2 profile of the sign-in window |
| `updates\` | Downloaded app updates and `apply.log` |

The app never sees or stores the Instagram password: sign-in happens on Instagram's own page. **Sign out** deletes
the cookie file and clears the WebView2 cookies. Network traffic goes only to Instagram, YouTube, TikTok, X,
GitHub and Codeberg (engine and app updates).

> [!TIP]
> Upgrading from Reels Saver 1.x: the old app kept its data in `%LOCALAPPDATA%\ReelsSaver`. Copy that folder to
> `<folder of MediaSaver.exe>\media-saver_data` to keep your settings, history and Instagram sign-in; otherwise the
> app starts fresh.

## Updates

On every launch a background updater runs before any job starts; links can be queued while it works. Without
network access the app keeps working with the binaries it already has.

| Component | Source | Update rule |
|---|---|---|
| `yt-dlp.exe` | GitHub releases of yt-dlp (nightly or stable) | Local SHA-256 compared with the release checksum file; `--update-to <channel>` or a verified fresh download |
| `gallery-dl.exe` | Codeberg releases of gallery-dl | Same checksum comparison; built-in `-U` |
| `ffmpeg.exe`, `ffprobe.exe` | System `PATH`, otherwise yt-dlp/FFmpeg-Builds (win64 GPL) | Own copy refreshed at most every 30 days |
| `deno.exe` | System `PATH`, otherwise the official Deno release (win64) | JavaScript runtime for yt-dlp's YouTube extraction; SHA-256 verified; own copy refreshed at most every 30 days |
| `MediaSaver.exe` | GitHub Releases of this repository | Checked on launch; size and SHA-256 verified against `SHA256SUMS`; swapped on restart |

- **Engines:** checked on every launch (`auto_update`), or **Update now** in the engine panel.
- **The app:** checked on every launch and then every 5 minutes while it runs; a failed check is retried later with
  a growing wait of up to an hour, and nothing is checked without a network connection. **Check for updates** in the
  popover of the version pill next to the app name or in Settings > Updates checks at once. **Download updates
  automatically** (`auto_download_updates`, on by default) fetches a new version in the background and a notice
  offers **Restart**; installing still needs **Restart to update**, the app never restarts by itself. A new release
  is downloaded to `media-saver_data\updates\`, verified by byte size and SHA-256 against the `SHA256SUMS` asset of
  the release, and installed by **Restart to update**: the downloaded exe waits for the app to exit, swaps
  `MediaSaver.exe` (keeping the previous file as `MediaSaver.exe.old` until the new one started) and relaunches it.
  If anything fails the current exe stays untouched. Closing the window ends the app and every process it started;
  nothing runs in the background after that.
- Versions 2.0.0 and 2.1.0 cannot update themselves (their updater hung in a console window): download the
  current `MediaSaver.exe` once by hand; from 2.1.1 on the update applies in place.
- Every release ships two assets: `MediaSaver.exe` and `SHA256SUMS`. Release notes live in
  [`releases/`](releases/).

## FAQ

**A 4K YouTube video takes minutes and the file is huge.** With *H.264 for editing* on, VP9 / AV1 sources above
1080p are re-encoded at CRF 16 for editors. Pick **High** (≤ 1080p) for a quick AVC file, or switch H.264 off to
keep the original VP9 / AV1 stream.

**Instagram says it needs a sign-in.** Some reels are served only to accounts. Press **Sign in to Instagram** on the
card; the login happens on Instagram's own page and the failed jobs retry by themselves.

**YouTube asks to confirm you are not a bot.** Choose a browser under *Browser cookies* in Settings; the engine then
uses that browser's YouTube session.

**An X post or a TikTok says it needs a sign-in.** Age-restricted and sensitive posts are served to signed-in users
only. Choose a browser under *Browser cookies* in Settings where you are signed in to that site and retry.

**TikTok says the video is blocked for my network.** TikTok refuses some posts by region or IP; the app reports it
as is. A photo post (slideshow) has no video to save.

**Where is FFmpeg?** It is used for merging and for MP3. The app takes `ffmpeg.exe` from `PATH` when present and
otherwise downloads a static build into `media-saver_data\bin` on the first launch.

**Can I run two copies?** Yes: each folder is its own installation with its own settings and history.
