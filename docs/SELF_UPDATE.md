# App self-update

Owner: self-update worker. Code: `app/core/selfupdate.py`; tests: `tests/test_core_selfupdate.py`. The facade calls
(`check_updates`, `download_update`, `apply_update`, the `update` event) are defined in `ARCHITECTURE.md` section 6 and
`BRIDGE_API.md`; the payload is `UpdateStatus` there. This file owns everything below that: the release layout, how
a release is found and verified, the swap helper, failure handling and how to test it.

## 1. Release layout (what the updater expects on GitHub)

Repository `softoblivion/media-saver` (constructor argument `repo`). The newest **published, non-draft,
non-prerelease** release is the one `GET /releases/latest` returns, so a prerelease is never offered.

| Item | Rule |
|---|---|
| Tag | `vX.Y.Z` (SemVer, leading `v` stripped for display and comparison). A tag that does not start with digits is an error ("Could not read the release version"). |
| Asset `MediaSaver.exe` | the one-file build, exactly this name (constructor argument `asset_name`) |
| Asset `SHA256SUMS` | text file, line `<sha256 lowercase>  MediaSaver.exe` (two spaces; `build\build.ps1` writes it) |
| Release body | Markdown release notes, shown in the update dialog as they are (`docs/releases/vX.Y.Z.md`) |

Both asset names are looked up by name. Rename either and every installed copy reports "The release has no
MediaSaver.exe" / "The release has no checksum file". v1.0.0 (asset `ReelsSaver.exe`, no `SHA256SUMS`) predates the
updater, so 1.x installs cannot update themselves; the first self-updating version is 2.0.0, and the first update
that can be offered is 2.0.1 or later.

## 2. States

`idle` -> `checking` -> `up_to_date` | `available` | `error`; `available` -> `downloading` -> `ready` | `error`.
`ready` -> the app exits and the helper takes over. Messages are the exact strings in the module (one sentence each).

| Stage | Message | Notes |
|---|---|---|
| `idle` | Updates are not checked yet | initial |
| `checking` | Checking for updates… | |
| `up_to_date` | Media Saver is up to date | `latest <= current` |
| `available` | Version X.Y.Z is available | `latest`, `notes`, `size`, `published_at` filled |
| `downloading` | Downloading Media Saver X.Y.Z… 42% | `progress` 0..1 |
| `ready` | Version X.Y.Z is ready to install | `can_apply` true when the app runs as the packaged exe; from source the text gets " (applies to the packaged MediaSaver.exe only)" and `can_apply` stays false |
| `error` | No internet connection / GitHub limited the request, try again later / The release has no MediaSaver.exe / The release has no checksum file / The downloaded file is damaged (checksum mismatch) / Could not read the release version / No release has been published yet / Could not save the update file / Could not start the update helper / The update is not ready to install | `check_updates()` or `download_update()` may be called again |

Rules: one operation at a time (a second request while a check or download runs is ignored); every change goes
through `on_status(copy)` and a failing callback is logged, never raised; `download` is allowed only from
`available`, or from `error` when the last check found a newer release; a check result clears the facts of the
previous one, so a failed check never leaves a stale "latest".

## 3. Check

1. `GET https://api.github.com/repos/<repo>/releases/latest` with `Accept: application/vnd.github+json`,
   `X-GitHub-Api-Version: 2022-11-28` and the shared `User-Agent` (`ReleaseNet`, a `Net` subclass that adds the two
   headers on `api.github.com` only). It yields tag, body (notes), `published_at` and the assets with size and URL.
2. Fallback when the API answers 403 / 429 (unauthenticated limit: 60 requests per hour and IP), cannot be reached
   or returns something unexpected: `GET https://github.com/<repo>/releases/latest` is followed through its redirect and
   the tag is read from the final URL (`/releases/tag/<tag>`). Assets are then
   `https://github.com/<repo>/releases/download/<tag>/<name>`; the size comes from a `HEAD` `Content-Length` (a 404
   there means the asset does not exist), `notes` and `published_at` stay `null`. The page does not count against
   the API limit.
3. SemVer compare: the numeric `X.Y.Z` prefix of the tag against `app.__version__` as tuples of ints (missing parts
   are zero, a suffix such as `-beta.1` is ignored). Newer -> `available`, else `up_to_date`. The asset check
   ("The release has no MediaSaver.exe") applies to newer releases only.
4. If `updates\MediaSaver-<latest>.exe` is already there and its size and SHA-256 match the release, the stage is
   `ready` straight away (a restart between "Download" and "Restart to update" costs nothing).

Offline and limit errors are told apart by the exception text of `Net` (`HTTP 403` / `HTTP 429` -> limit message);
`Net` retries three times, so an offline check takes about eight seconds before it reports.

The check runs on every launch when `check_app_updates` is on (`Engine.start`) and on the "Check for updates" button.

## 4. Download and verification

1. Fetch `SHA256SUMS` of the release tag (the URL from the API asset list, otherwise
   `https://github.com/<repo>/releases/download/<tag>/SHA256SUMS`) and take the entry for `MediaSaver.exe`. A missing
   file or entry stops here: "The release has no checksum file". Nothing is downloaded without a checksum.
2. Stream the asset with `Net.download` to `updates\MediaSaver-<latest>.exe.part`; progress is published at most once
   per percent.
3. Verify the byte size against the release asset size (when known) and the streamed SHA-256 against the checksum.
   A mismatch deletes the `.part` file and ends in "The downloaded file is damaged (checksum mismatch)".
4. `os.replace` to `MediaSaver-<latest>.exe`, remember the expected SHA-256, stage `ready`.

## 5. Apply (the helper)

`apply()` returns `True` only after the helper is running; the bridge then closes the window the way the title bar
close button does (`Bridge.apply_update`). Preconditions, each failing with a status error and `False`: stage
`ready`, the app runs as the packaged exe (`exe_path`), the downloaded file exists and its SHA-256 **re-hashed now**
still equals the verified one (a changed file is deleted).

Steps of `apply()`:

1. Write `updates\applied.json` `{"version": "<latest>", "at": <epoch>}`.
2. Write `updates\apply-update.cmd` (ASCII, CRLF). The two paths are **not** inside the script: they travel in the
   environment variables `MS_UPDATE_TARGET` (the running `MediaSaver.exe`) and `MS_UPDATE_NEW` (the downloaded
   file), so a profile or folder with non-ASCII characters works (a `.cmd` is read in the OEM code page).
3. Start it: `cmd.exe /c apply-update.cmd`, `cwd` = `updates\`, flags `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
   DETACHED_PROCESS`, `close_fds`, stdin / stdout / stderr to `DEVNULL`, environment = the current one **without
   `_PYI*` variables**, plus `PYINSTALLER_RESET_ENVIRONMENT=1` and the two path variables (see 7).

What the helper does:

1. Waits (up to 120 x 1 s) until the app's PID is gone: `tasklist /FI "PID eq <pid>" | find " <pid> "`.
2. `move /y "%MS_UPDATE_TARGET%" "%MS_UPDATE_TARGET%.old"`, up to 20 tries 1 s apart (the one-file bootloader process
   and antivirus may still hold the exe for a moment). All 20 fail: give up, the old exe stays.
3. `move /y "%MS_UPDATE_NEW%" "%MS_UPDATE_TARGET%"`. If that fails, `move` the `.old` file back and give up.
4. `start "" /D "<exe folder>" "%MS_UPDATE_TARGET%"` (launch is switched off only for tests).
5. Deletes itself (`(goto) 2>nul & del "%~f0"` as the last line, reached by every path).

System tools are called by full path (`%SystemRoot%\System32\tasklist.exe`, `find.exe`, `ping.exe`), and the one-second
sleep is `ping -n 2 127.0.0.1`, see 7.

Next launch (`SelfUpdater.startup()`, called by `Engine.start`): delete `<exe>.old`; read `applied.json` and remove it
(the version equals the running one -> toast "Media Saver updated to vX.Y.Z"; different -> the update did not apply,
logged as a warning, no toast); delete leftover `updates\MediaSaver-<v>.exe(.part)` with `v <= running` and a
stale `apply-update.cmd`. Never raises.

## 6. Failure handling

| Failure | Result |
|---|---|
| Offline / GitHub limit | status `error` with the message; nothing on disk changes |
| Asset or checksum file missing | status `error`; nothing downloaded |
| Interrupted download, size or checksum mismatch | `.part` deleted; status `error`; the next `download_update()` starts over |
| Disk full / folder not writable | "Could not save the update file" |
| File changed between download and apply | file deleted, "The downloaded file is damaged (checksum mismatch)", `apply()` returns `False`, the app keeps running |
| Helper cannot start | marker removed, "Could not start the update helper", app keeps running |
| Exe locked for 20 s or the move fails | old exe stays (or is restored); the next launch finds a marker with another version, logs "did not apply", and the update is offered again |
| Running from source | `can_apply` false; `apply()` refuses |

The running exe is never touched before the helper has waited for the app to exit.

## 7. Platform notes (symptom / cause / fix)

- `timeout /t 1 /nobreak` inside the helper returns at once ("Input redirection is not supported") when stdin is
  redirected, which it is for a detached process started with `stdin=DEVNULL`. The wait loop would spin through its
  120 iterations in no time. The helper sleeps with `ping -n 2 127.0.0.1 >nul` instead.
- `find` resolved to `C:\Program Files\Git\usr\bin\find.exe` (Git for Windows ahead of System32 in `PATH`), which
  breaks the PID test. The helper calls `%SystemRoot%\System32\find.exe` and the other system tools by full path.
- A process started from a PyInstaller one-file exe inherits `_PYI_*` environment variables; a second launch of the
  same exe that keeps them reuses the first instance's extraction folder (deleted when the first exits) and fails.
  The helper environment drops them and sets `PYINSTALLER_RESET_ENVIRONMENT=1` (PyInstaller 6.9+), which makes the
  new bootloader act as a fresh top-level process.
- Paths with `%`, spaces, parentheses or non-ASCII characters are safe because they are environment variables that
  are only expanded inside double quotes.

## 8. Testing

`tests/test_core_selfupdate.py` runs without network or a real exe:

- `FakeNet` (canned `get_text` / `download` per URL, errors as values) replaces `Net`; a fake `urlopen` replaces the
  redirect and `HEAD` calls of the fallback; `spawn` is injected so `apply()` never starts a process;
  `exe_path` is a temp file; `MEDIA_SAVER_HOME` is a temp folder.
- Covered: available / up to date / offline / API 403 with fallback through the redirect URL (with and without
  `Content-Length`, missing asset, no release) / bad tag / missing asset; version parsing and comparison; the API
  headers of `ReleaseNet`; one operation at a time; download ok (progress, `.part` gone), size mismatch, checksum
  mismatch, missing `SHA256SUMS` and missing entry, network and disk errors with retry; an already downloaded and
  verified file going straight to `ready` (and a corrupt one not); `apply()` refused when not ready, without
  `exe_path`, after the file changed or vanished, and its marker / helper / spawn arguments; `startup()` toast, stale
  marker, `.old` deletion, leftover cleanup, broken marker; the helper script itself (Windows only): a stand-in
  process of two seconds is "the app", the helper really runs under `cmd.exe` with stdin on `NUL`, waits for it,
  swaps a dummy target (in a folder with spaces and parentheses), keeps `.old`, restores the old file when the new
  one is missing, and deletes itself.
- Not covered: a real GitHub release, the PyInstaller one-file restart in a frozen build, antivirus locking the exe,
  SmartScreen.

`tests/test_bridge_fake_engine.py` and `app/fake_engine.py` simulate the whole flow for the UI: `run_dev.bat --fake`
starts with an update offered (next minor version, a sample changelog, 13 MB), "Download" runs three seconds,
"Restart to update" closes the window. `MEDIA_SAVER_FAKE_UPDATE=uptodate` or `=error` simulates the other outcomes.

Read-only smoke test against the real API (never downloads): from the project root with `MEDIA_SAVER_HOME` set to a
scratch folder,
`.venv\Scripts\python -c "from app.core.paths import AppPaths; from app.core.selfupdate import SelfUpdater; print(SelfUpdater(AppPaths(), '0.0.1', print).check())"`.
While the newest release has no `MediaSaver.exe` it reports "The release has no MediaSaver.exe"; with a version at or above
the newest tag it reports "Media Saver is up to date".

End-to-end by hand (needs two releases): build version N, publish N+1 as described in `BUILD.md` section 7, start
the exe of version N from a scratch folder with `MEDIA_SAVER_HOME` set, press "Check for updates", "Download",
"Restart to update"; the window reopens on N+1, shows the toast, and `MediaSaver.exe.old` is gone.
