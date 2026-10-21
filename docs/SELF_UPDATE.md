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

The check runs on every launch (`Engine.start`; only the headless CLI and tests pass `app_updates=False`) and on
the "Check for updates" button. Automatic download and install is the UI's job and a separate setting,
`auto_install_updates` (default off): ARCHITECTURE.md 5.7, item 5.

## 4. Download and verification

1. Fetch `SHA256SUMS` of the release tag (the URL from the API asset list, otherwise
   `https://github.com/<repo>/releases/download/<tag>/SHA256SUMS`) and take the entry for `MediaSaver.exe`. A missing
   file or entry stops here: "The release has no checksum file". Nothing is downloaded without a checksum.
2. Stream the asset with `Net.download` to `updates\MediaSaver-<latest>.exe.part`; progress is published at most once
   per percent.
3. Verify the byte size against the release asset size (when known) and the streamed SHA-256 against the checksum.
   A mismatch deletes the `.part` file and ends in "The downloaded file is damaged (checksum mismatch)".
4. `os.replace` to `MediaSaver-<latest>.exe`, remember the expected SHA-256, stage `ready`.

## 5. Apply (the downloaded exe swaps itself in)

`apply()` returns `True` only after the new exe has been started in apply mode; the bridge then closes the window
the way the title bar button does. Preconditions: stage `ready`, `exe_path` set (frozen build), the file still
there with the expected SHA-256 (re-hashed). Steps:

1. Write `updates\applied.json` = `{"version": "<latest>", "at": <epoch>}`.
2. Start `updates\MediaSaver-<latest>.exe --apply-update <pid> <target>` (`<pid>` = this process, `<target>` =
   the installed `MediaSaver.exe`; `--no-launch` is appended in tests). The child gets a clean environment
   (`clean_env()`: `_PYI_*` / `_MEIPASS2` removed, `PYINSTALLER_RESET_ENVIRONMENT=1`) and the flags
   `CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` (plain `CREATE_NEW_PROCESS_GROUP` when the job refuses
   breakaway), stdin / stdout / stderr on `DEVNULL`. There is no `cmd`, no console and no pipe anywhere: a windowed
   exe starts another windowed exe.
3. The app exits.

What the new exe does in apply mode (`run_apply`, reached from `app/main.py` before the single-instance mutex and
before any window; every step is appended to `updates\apply.log`):

1. Wait for `<pid>` to end: `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject` in one-second slices, at most 120 s.
   A pid that no longer exists counts as exited.
2. Swap: remove a stale `<target>.old`, rename `<target>` to `<target>.old`, copy itself (`sys.executable`) over
   `<target>` and compare sizes. The rename is retried up to 40 times, 0.5 s apart (the old exe may still be held by
   its own shutdown or by an antivirus scan). When the copy fails, `.old` is moved back and the run stops.
3. Start `<target>` (clean environment, cwd = its folder) and exit. The copy in `updates\` stays until the new
   version's `startup()` deletes it as "not newer than the running version".

Why not a script: 2.0.0 and 2.1.0 used a `cmd` helper started with `DETACHED_PROCESS`; a detached `cmd` has no
console, so every console tool it ran (`tasklist`, `find`, `ping`) opened a visible console of its own and the
`tasklist | find` pipe hung waiting for keyboard input. The helper could not be fixed from the new side: it is the
old exe that starts it, so 2.0.0 and 2.1.0 installs need one manual download of a newer `MediaSaver.exe`.

## 6. Failure handling

| Failure | Result |
|---|---|
| Offline / GitHub limit | status `error` with the message; nothing on disk changes |
| Asset or checksum file missing | status `error`; nothing downloaded |
| Interrupted download, size or checksum mismatch | `.part` deleted; status `error`; the next `download_update()` starts over |
| Disk full / folder not writable | "Could not save the update file" |
| File changed between download and apply | file deleted, "The downloaded file is damaged (checksum mismatch)", `apply()` returns `False`, the app keeps running |
| The new exe cannot be started in apply mode | marker removed, "Could not start the update helper", app keeps running |
| Exe locked for 20 s or the move fails | old exe stays (or is restored); the next launch finds a marker with another version, logs "did not apply", and the update is offered again |
| Running from source | `can_apply` false; `apply()` refuses |

The running exe is never touched before the apply mode has waited for the app to exit; a failed swap moves
`.old` back, and `apply.log` in `updates\` says what happened.

## 7. Platform notes (symptom / cause / fix)

- A `cmd` helper started with `DETACHED_PROCESS` has no console; every console child (`tasklist`, `find`, `ping`)
  then allocates a visible console of its own and a pipe between them breaks (`find` reads its new console and
  waits for keyboard input). Seen on a friend's PC with 2.1.0: a console window titled `find.exe " <pid> "` hung
  after "Restart to update". Fix: no script and no console tools at all; the windowed exe does the swap
  (`run_apply`). `CREATE_NO_WINDOW` is also ignored when combined with `DETACHED_PROCESS`, which is why the
  window was visible in the first place.
- A process started from a PyInstaller one-file exe inherits `_PYI_*` variables that make a second launch of the
  same exe reuse the first one's extraction folder (which is being deleted). `clean_env()` drops them and sets
  `PYINSTALLER_RESET_ENVIRONMENT=1` (PyInstaller 6.9+), so the apply-mode exe and the relaunched app each extract
  afresh.
- A launcher that puts the app in a job object with "kill on close" would end the apply-mode exe together with the
  app; `CREATE_BREAKAWAY_FROM_JOB` detaches it when the job allows that, otherwise the plain flags are used.
- `os.replace` of the running exe can fail for a moment after the app exits (its own shutdown, antivirus scan);
  the rename is retried for up to 20 s.

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
  `exe_path`, after the file changed or vanished, and its marker / spawn arguments; `startup()` toast, stale
  marker, `.old` deletion, leftover cleanup, broken marker; the apply mode itself (`run_apply`): a stand-in
  process of two seconds is "the app", the function waits for it,
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
