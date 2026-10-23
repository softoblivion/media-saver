# Third-party components

| Component | License | How it ships |
|---|---|---|
| [CPython](https://www.python.org/) | PSF-2.0 | bundled into the exe |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause | bundled into the exe |
| [pythonnet](https://github.com/pythonnet/pythonnet), clr_loader | MIT | bundled into the exe |
| bottle, proxy_tools | MIT | bundled into the exe |
| cffi / pycparser | MIT-0 / BSD-3-Clause | bundled into the exe |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0-or-later with the bootloader exception | bundled into the exe |
| [Unbounded](https://fonts.google.com/specimen/Unbounded), [Geist Mono](https://github.com/vercel/geist-font) fonts | SIL Open Font License 1.1 | bundled into the exe |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | downloaded at first start from its official release channel |
| [gallery-dl](https://codeberg.org/mikf/gallery-dl) | GPL-2.0 | downloaded at first start from its official release channel |
| [FFmpeg](https://ffmpeg.org/) ([yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)) | GPL | read from PATH; otherwise downloaded at first start |
| [Deno](https://github.com/denoland/deno) | MIT | read from PATH; otherwise downloaded at first start |
| Microsoft Edge WebView2 Runtime | Microsoft licence | part of Windows; not shipped |

The downloaded components are fetched from their official release channels and are not redistributed with this
project.
