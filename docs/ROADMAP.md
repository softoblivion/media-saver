# Roadmap

Planned work, in priority order. Each item moves to a release note in `docs/releases/` when it ships. Items are
promises of direction, not dates.

## Next

- **TikTok and X (Twitter) video support.** A third and fourth platform mode next to Instagram and YouTube, built on
  the same `yt-dlp` engine: link cleaning rules for `tiktok.com` / `vm.tiktok.com` and `x.com` / `twitter.com`
  video URLs, previews, MP4 and MP3 output, per-platform quality setting. Support is announced in the README and
  the repository topics only after real downloads from both sites pass the headless test
  (`app.core.cli`) and the unit tests cover the new link rules.

## Later

- Code signing of `MediaSaver.exe` so Windows SmartScreen stops warning on first start.
- Distribution through `winget`, Scoop and Chocolatey.

## Not planned

- Playlists, channels, profiles and stories as bulk input: the app downloads single videos and posts only.
- A macOS or Linux build: the UI depends on WebView2.
