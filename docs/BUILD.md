# Build, run and troubleshoot

Scope: the toolchain, building and releasing the exe, and troubleshooting; section 5 describes the shell code.
Architecture and contracts: `ARCHITECTURE.md`, `BRIDGE_API.md`; the updater: `SELF_UPDATE.md`.

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

Requirements files: `requirements.txt` (runtime), `requirements-dev.txt` (build tools). `app/core` is stdlib-only;
the self-update package is vendored in `app/vendor/selfupdate/` (no install step, see `SELF_UPDATE.md`).

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
- `--fake` `app/fake_engine.py` instead of `app/core/engine.py`: both platforms, mp3 jobs, simulated previews, a
  simulated app update (next minor version offered; `MEDIA_SAVER_FAKE_UPDATE=uptodate|error` simulates the other
  outcomes), a link whose id contains `fail` ends with an error, simulated engine update; login cookies still go to
  the data folder.
- `--ui-dir PATH` serve the UI from another folder (dev override); default is `ui/` of the project, or the bundled
  copy in a frozen build.

Where the data goes: everything the app writes lives in `media-saver_data\` next to `MediaSaver.exe` (the project root when run
from source; `media-saver_data\` and `Downloads\` there are not tracked): settings, history, cookies, logs, the WebView2 profile,
yt-dlp / gallery-dl / ffmpeg in `bin\`, `updates\`. The default output folder is `Downloads\` next to the exe.
`MEDIA_SAVER_HOME` replaces the `media-saver_data\` folder (and the default output folder becomes `%MEDIA_SAVER_HOME%\Downloads`);
always set it to a scratch folder when testing:

```powershell
$env:MEDIA_SAVER_HOME = "$env:TEMP\media_saver_scratch"; run_dev.bat --fake
```

The UI is served by pywebview's built-in local HTTP server (`http://127.0.0.1:42001/`, next free port if busy) because
Chromium refuses ES modules from `file://`. The fixed port keeps the page origin, and with it `localStorage`, stable.

## 3. Tests

```powershell
$env:MEDIA_SAVER_HOME = "$env:TEMP\media_saver_scratch"
.venv\Scripts\python -m unittest discover -s tests
```

`tests/test_core_*` and `test_linkclean.py` cover the core (docs in `ENGINE.md`, `SELF_UPDATE.md`);
`tests/test_bridge_*.py` cover the shell with a fake window and the fake engine (event buffering / coalescing /
escaping, delegation, error messages, login cookie logic, the simulated engine itself); no window is opened. The
self-update tests run the apply mode of the updater against a stand-in process (takes a few seconds). `build.ps1` runs
the whole suite with its own scratch `MEDIA_SAVER_HOME`.

## 4. Build the exe

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1            # venv + deps + tests + icon + exe + SHA256SUMS
powershell -ExecutionPolicy Bypass -File build\build.ps1 -Shortcut  # also a desktop shortcut
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipTests # skip the unit tests
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipBuild # only create / refresh .venv
```

Result in `release\` (not tracked):

| File | Content |
|---|---|
| `MediaSaver.exe` | the app, about 12.6 MB: Python + pywebview + pythonnet + the UI. yt-dlp, gallery-dl and ffmpeg are not bundled; they are downloaded at first launch into `media-saver_data\bin` next to the exe. |
| `SHA256SUMS` | one line `<sha256 lowercase>  MediaSaver.exe` (two spaces, LF); the build prints it. The self-updater verifies the downloaded exe against the copy attached to the GitHub release. |

What the spec does (`build/MediaSaver.spec`): one-file, windowed (no console), icon `build/icon.ico`, Windows
version resource "Media Saver" with `app.__version__` (`build/_version_info.txt` is rewritten on every build),
`ui/` added as data (extracted to `sys._MEIPASS/ui`), every `app.*` module as hidden import (the engine is imported
lazily), pywebview hooks from PyInstaller-hooks-contrib, no UPX (UPX-packed exes are flagged by antivirus far more
often), heavy unused modules excluded. `build/make_icon.py` converts `ui/assets/icon-1024.png` to a multi-size
`icon.ico`; without that PNG it draws a placeholder droplet with a down arrow.

Bump the version only in `app/__init__.py`; the spec and the engine read it from there. The full build takes under
a minute once `.venv` exists.

## 5. Window behaviour (frameless)

- The window is created `frameless=True, easy_drag=False`; the page draws its own title bar. Dragging works on elements
  with the class `pywebview-drag-region` (pywebview moves the window through the bridge); buttons call
  `window_minimize` and `window_close`. There is no maximize button.
- **Fixed size**: the window is created with
  `resizable=False` and cannot be resized or maximized: no sizing border, no maximize button, no maximize by system
  command, double click or keyboard. The page covers the whole client area, so the window size equals the viewport.
  `resizable=False` alone is not enough with pywebview 6.2.1 on Windows (the form keeps `WS_MAXIMIZEBOX`, accepts
  `SC_MAXIMIZE` / `ShowWindow(SW_MAXIMIZE)` and any `SetWindowPos` size), so `app/winutil.py` (`FixedSizeHook`,
  installed from `_setup_native_window` after the `shown` event) subclasses the window procedure:
  - every size proposal of the restored window is replaced by the locked size (`WM_WINDOWPOSCHANGING`);
  - `SC_MAXIMIZE` is swallowed (system menu, keyboard), and a maximized state that still arrives from outside
    (`WM_SIZE` / `SIZE_MAXIMIZED`) is answered with a posted `SC_RESTORE`;
  - `WS_MAXIMIZEBOX` and `WS_THICKFRAME` are cleared, `WS_MINIMIZEBOX` is set (taskbar click and `Win+Down` minimize).
- **Size**: start size 1180x780 logical pixels. On a small work area (for example 1366x768 with a taskbar) the
  shell takes the first step of `SIZE_LADDER` in `app/main.py` that fits the work area of the monitor the window is
  on at its DPI: 1180x780, 1120x745, 1060x710, 1000x675, 940x640. The smallest step wins when even that does not
  fit. The choice is pure (`pick_window_size`, unit-tested); the size is applied once through the form's
  `SetBounds` and the window is centred on the work area.
- **Restore keeps the size**: without `WS_THICKFRAME` the window size equals the client size, so WinForms re-applies
  the right size after minimize and restore (the frame-width shrink of versions up to 2.4.x came from the thick
  frame and no longer applies), and the hook holds the size anyway.
- **DWM**: `set_rounded_corners` asks for Windows 11 rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`), and
  `set_border_color` sets `DWMWA_BORDER_COLOR` to the page background (`BACKGROUND` in `app/main.py`, `#0b0d14`) so
  no light frame line shows. Both are no-ops before Windows 11.
- Check by hand after a change to the hook (a short ctypes probe against the running `run_dev.bat --fake` window): the style has neither
  `WS_THICKFRAME` nor `WS_MAXIMIZEBOX`; `ShowWindow(SW_MAXIMIZE)` and `SetWindowPos` to another size leave the
  size unchanged; minimize and restore keep it; `GetClientRect` equals the locked size.
- Aero Snap by dragging is not available (pywebview moves the window itself); snapping does not resize the window.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Window stays blank / white | Dev: run `run_dev.bat` and open dev tools (F12) for console errors. Check `media-saver_data\logs\shell.log`. Usually the UI folder is missing (`--ui-dir` wrong) or the WebView2 runtime is missing. |
| Message about WebView2, or the window never appears | Install the Evergreen WebView2 Runtime (link above). Check that `media-saver_data\webview` next to the exe is writable (a locked folder breaks startup; delete the folder to reset it, you only lose the Instagram page session). |
| Second launch does nothing | By design: single instance (named mutex `Local\MediaSaver.SingleInstance`); the first window is brought to the front. |
| Exe flagged by SmartScreen / antivirus | One-file PyInstaller exes unpack to `%TEMP%\_MEIxxxx` and are unsigned, so heuristic engines sometimes flag them. SmartScreen: "More info" -> "Run anyway". The real fix is code signing; otherwise add an antivirus exclusion for the app folder or report the false positive to the vendor. UPX is intentionally off. |
| SmartScreen on the exe written by a self-update | The updater saves the file with plain HTTP code, which does not add the "downloaded from the internet" mark, so SmartScreen does not prompt for it. Antivirus heuristics can still flag a fresh unsigned exe: allow it in the antivirus (see the row above). Only a manual download of the release from the browser carries the mark ("More info" -> "Run anyway"). |
| Slow first start of the exe | One-file extraction to `%TEMP%` on every launch (about 1-2 s); Defender scans the extracted files. |
| Port 42001 busy | The next free port up to 42040 is used automatically; the UI keeps working, only `localStorage` of the previous origin is not reused. |
| Login window shows no sign-in | Instagram page problem or no network; the app never sees the password. Cookies are read from the window every 1.5 s until `sessionid` appears. |
| "Could not start the app engine" at start | `app/core` import failed. Run from source and read the traceback, or check `media-saver_data\logs\shell.log`. |
| `MediaSaver.exe.old` stays next to the exe | The helper replaced the exe while the old one was still locked, or the new version failed to start. It is deleted on the next successful launch; delete it by hand when the app runs fine. |
| "Restart to update" closed the app and the old version is back | The swap did not happen: the exe stayed locked for 20 s (antivirus scan, or the exe folder is not writable, for example under `Program Files`) or `media-saver_data\updates` held no file. The next launch logs "update to X did not apply" in `media-saver_data\logs\app.log` and offers the update again; move the app to a writable folder. |
| Update offered but "Download" fails with "checksum mismatch" | The release `SHA256SUMS` does not match the uploaded `MediaSaver.exe` (the exe was rebuilt after the sums file was made, or one of them was edited on GitHub). Re-upload both files of the same build (section 7). |
| "The release has no MediaSaver.exe" / "...no checksum file" | The release was published with other asset names. The names must be exactly `MediaSaver.exe` and `SHA256SUMS`; fix the assets on the release page. |
| "GitHub limited the request" | The unauthenticated API quota of the network (60 requests per hour and IP) is used up; the check falls back to the release page first, so this appears only when both are limited. Try again later. |
| "Restart to update" closed the app but nothing happened | Read `media-saver_data\updates\apply.log`: it says whether the old process ended, whether the swap worked and whether the new exe started. A 2.0.0 or 2.1.0 install cannot update itself (its helper hangs in a console window): download the new `MediaSaver.exe` once by hand. |

Logs: `media-saver_data\logs\` next to the exe (or `%MEDIA_SAVER_HOME%\logs`): `app.log` (core and shell, rotating),
`shell.log` (shell and pywebview only), `jobs\<job_id>.log` (full yt-dlp output of a failed job). The "open logs"
action of the UI opens this folder.

## 7. Release procedure

Publishing is a public action: a release is made by the maintainer, after the pre-publish checks pass. Steps:

1. Bump `__version__` in `app/__init__.py` (SemVer; the updater compares `X.Y.Z` numerically and only offers a
   higher version than the installed one).
2. Write `docs/releases/vX.Y.Z.md` (sections Added / Changed / Fixed / Removed, each change once). The text becomes the
   release body and is shown in the app's update dialog.
3. Run the build with the tests: `powershell -ExecutionPolicy Bypass -File build\build.ps1`. Check that
   `release\MediaSaver.exe` and `release\SHA256SUMS` exist and that the hash in `SHA256SUMS` is the one the build
   printed.
4. Smoke test: start `release\MediaSaver.exe` with `MEDIA_SAVER_HOME` set to a scratch folder; the window opens and
   `logs\shell.log` has no errors.
5. Commit on `dev` and tag the commit: `git tag vX.Y.Z` (the tag lives on `dev`; there is no other release branch),
   then push `dev` and the tag.
6. Create the release:

   ```powershell
   gh release create vX.Y.Z release\MediaSaver.exe release\SHA256SUMS --target dev `
       --title "vX.Y.Z" --notes-file docs\releases\vX.Y.Z.md
   ```

   Not a draft and not a prerelease (`/releases/latest` ignores both). The two asset names must stay exactly
   `MediaSaver.exe` and `SHA256SUMS` because the self-updater looks them up by name.
7. Verify as an installed copy would: from a scratch folder, run an older exe or the read-only smoke test of
   `SELF_UPDATE.md` section 7 with a lower version; it must report `available` with the new version, and the
   `SHA256SUMS` asset must contain the hash of the uploaded exe
   (`gh release download vX.Y.Z -p SHA256SUMS -O -` compared with `Get-FileHash`).

The first release with the updater is 2.0.0; v1.0.0 (asset `ReelsSaver.exe`) cannot update itself, so its users move
to 2.x by downloading it once.
