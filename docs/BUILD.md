# Build, run and troubleshoot

Owner: shell worker. Architecture and contracts: `ARCHITECTURE.md`, `BRIDGE_API.md`.

## 1. Toolchain (decision and verified versions)

| Part | Version | Note |
|---|---|---|
| Python | **3.14** (python.org install manager, `py -3.14`) | pywebview, pythonnet and PyInstaller all work on 3.14; no fallback to 3.13 was needed. Verified with 3.14.5. |
| pywebview | 6.2.1 (`>=6.2.1,<7`) | `gui="edgechromium"` (WebView2) |
| pythonnet | 3.2.1 | Needed by pywebview's WinForms / WebView2 backend; wheels for 3.14 exist |
| PyInstaller | 6.22.3 (`>=6.22`) | one-file, windowed |
| Pillow | 13.0.0 (`>=12`) | only for `build/make_icon.py` |

Runtime requirement on the target machine: the **Microsoft Edge WebView2 Runtime** (preinstalled on Windows 11 and on
current Windows 10; Evergreen installer: https://developer.microsoft.com/microsoft-edge/webview2/).
The exe itself needs no Python.

Requirements files: `requirements.txt` (runtime), `requirements-dev.txt` (build tools). `app/core` is stdlib-only.

## 2. Run from source

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
run_dev.bat              # real engine, dev tools enabled (right click -> Inspect, F12)
run_dev.bat --fake       # simulated engine: no network, no yt-dlp (UI and shell work)
run_dev.bat --ui-dir D:\some\other\ui
```

`run_dev.bat` is `python -m app.main --debug %*`. Flags of `app.main`:

- `--debug` WebView2 dev tools, DEBUG logging on the console.
- `--fake` `app/fake_engine.py` instead of `app/core/engine.py`: simulated jobs (a link whose shortcode contains
  `fail` ends with a `login_required` error), simulated engine update, login cookies still go to the scratch home.
- `--ui-dir PATH` serve the UI from another folder (dev override); default is `ui/` of the project, or the bundled
  copy in a frozen build.

`REELS_SAVER_HOME` moves all runtime data (settings, history, cookies, logs, WebView2 profile, yt-dlp binaries);
always set it to a scratch folder when testing:

```powershell
$env:REELS_SAVER_HOME = "$env:TEMP\reels_saver_scratch"; run_dev.bat --fake
```

The UI is served by pywebview's built-in local HTTP server (`http://127.0.0.1:42001/`, next free port if busy) because
Chromium refuses ES modules from `file://`. The fixed port keeps the page origin, and with it `localStorage`, stable.

## 3. Tests

```powershell
$env:REELS_SAVER_HOME = "$env:TEMP\reels_saver_scratch"
.venv\Scripts\python -m unittest discover -s tests
```

`tests/test_bridge_*.py` cover the shell with a fake window and fake engine (event buffering / coalescing / escaping,
delegation, error messages, login cookie logic); no window is opened.

## 4. Build the exe

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1            # venv + deps + tests + icon + exe
powershell -ExecutionPolicy Bypass -File build\build.ps1 -Shortcut  # also a desktop shortcut
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipTests # skip the unit tests
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipBuild # only create / refresh .venv
```

Result: `release\ReelsSaver.exe` (about 12 MB: Python + pywebview + pythonnet + the UI; yt-dlp, gallery-dl and ffmpeg
are downloaded at first launch into `%LOCALAPPDATA%\ReelsSaver\bin`, not bundled).

What the spec does (`build/ReelsSaver.spec`): one-file, windowed (no console), icon `build/icon.ico`, Windows
version resource "Reels Saver" with `app.__version__`, `ui/` added as data (extracted to `sys._MEIPASS/ui`),
every `app.*` module as hidden import (the engine is imported lazily), pywebview hooks from PyInstaller-hooks-contrib,
no UPX (UPX-packed exes are flagged by antivirus far more often), heavy unused modules excluded.
`build/make_icon.py` converts `ui/assets/icon-1024.png` to a multi-size `icon.ico`; without that PNG it draws a
placeholder droplet with a down arrow.

Bump the version only in `app/__init__.py`; the spec and the engine read it from there.

## 5. Window behaviour (frameless)

- The window is created `frameless=True, easy_drag=False`; the page draws its own title bar. Dragging works on elements
  with the class `pywebview-drag-region` (pywebview moves the window through the bridge); buttons call
  `window_minimize`, `window_toggle_maximize`, `window_close`.
- **Edge resizing**: a plain frameless pywebview window on Windows has no sizing border. `app/winutil.py`
  (`FramelessResizeHook`) restores `WS_THICKFRAME`, keeps the whole window as client area (no title bar appears) and
  fits maximize to the monitor work area (WinForms would cover the taskbar). Left, right, bottom edges and the
  bottom corners resize; the top edge does not (the page owns the top pixels). Minimum viewport 940x640, start
  viewport 1180x780 (logical pixels). Windows 11 rounded corners and shadow come from DWM.
- Aero Snap by dragging is not available (pywebview moves the window itself); `Win+Arrow` snapping works.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Window stays blank / white | Dev: run `run_dev.bat` and open dev tools (F12) for console errors. Check `logs\shell.log`. Usually the UI folder is missing (`--ui-dir` wrong) or the WebView2 runtime is missing. |
| Message about WebView2, or the window never appears | Install the Evergreen WebView2 Runtime (link above). Check that `%LOCALAPPDATA%\ReelsSaver\webview` is writable (a profile from another Windows user or a locked folder breaks startup; delete the folder to reset it, you only lose the Instagram page session). |
| Second launch does nothing | By design: single instance (named mutex `Local\ReelsSaver.SingleInstance`); the first window is brought to the front. |
| Exe flagged by SmartScreen / antivirus | One-file PyInstaller exes unpack to `%TEMP%\_MEIxxxx` and are unsigned, so heuristic engines sometimes flag them. SmartScreen: "More info" -> "Run anyway". The real fix is code signing; otherwise add an antivirus exclusion for `release\` or report the false positive to the vendor. UPX is intentionally off. |
| Slow first start of the exe | One-file extraction to `%TEMP%` on every launch (about 1-2 s); Defender scans the extracted files. |
| Port 42001 busy | The next free port up to 42040 is used automatically; the UI keeps working, only `localStorage` of the previous origin is not reused. |
| Login window shows no sign-in | Instagram page problem or no network; the app never sees the password. Cookies are read from the window every 1.5 s until `sessionid` appears. |
| "Could not start the app engine" at start | `app/core` import failed. Run from source and read the traceback, or check `logs\shell.log`. |

Logs: `%LOCALAPPDATA%\ReelsSaver\logs\` (or `%REELS_SAVER_HOME%\logs`): `app.log` (core and shell, rotating),
`shell.log` (shell and pywebview only), `jobs\<job_id>.log` (full yt-dlp output of a failed job). The "open logs"
action of the UI opens this folder.
