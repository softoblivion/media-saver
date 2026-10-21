"""Apply mode: the downloaded exe waits for the running app to exit, swaps itself in and restarts it.

Started as `<App>-<v>.exe --apply-update <pid> <target> [--no-launch]` by `SelfUpdater.apply()`; the host app routes
that argv to `run_apply` before it creates a window or takes its single-instance mutex (docs/SELF_UPDATE.md).
No cmd script, no console window, no pipes: a windowed exe does the swap.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

APPLY_FLAG = "--apply-update"
NO_LAUNCH_FLAG = "--no-launch"     # tests: swap only, do not start the target
APPLY_LOG = "apply.log"            # written next to the running (downloaded) exe, i.e. in the updates folder
APPLY_WAIT_S = 120.0
SWAP_ATTEMPTS = 40                 # x 0.5 s: antivirus may hold the old exe for a while

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

SpawnFn = Callable[[str, list, dict, str], None]   # (exe, argv, env, cwd)


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a process that must start an exe built by PyInstaller one-file, including its own.

    A process started from a PyInstaller one-file exe inherits `_PYI_*` variables that make a second launch of the
    same exe reuse the first one's extraction folder; they are dropped and PYINSTALLER_RESET_ENVIRONMENT asks the new
    bootloader to behave as a fresh top-level process.
    """
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith("_PYI") and k.upper() != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _popen_detached(argv: list[str], env: dict, cwd: str | None) -> None:
    """Start a windowed exe that must outlive this process. No console is involved: the app exe has none.

    CREATE_BREAKAWAY_FROM_JOB lets the child survive when this process sits in a job object that kills its tree
    (some launchers do that); it is refused when the job forbids breakaway, so the plain flags are the fallback.
    """
    kwargs: dict[str, Any] = dict(cwd=cwd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        try:
            subprocess.Popen(argv, creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB, **kwargs)
            return
        except OSError:
            subprocess.Popen(argv, creationflags=_CREATE_NEW_PROCESS_GROUP, **kwargs)
            return
    subprocess.Popen(argv, start_new_session=True, **kwargs)


def spawn_apply(exe: str, argv: list[str], env: dict, cwd: str) -> None:
    """Start the downloaded exe in apply mode (default SpawnFn)."""
    _popen_detached([exe, *argv], env, cwd)


def wait_for_pid(pid: int, timeout_s: float = APPLY_WAIT_S) -> bool:
    """Block until the process is gone. True when it exited (or never existed), False on timeout."""
    deadline = time.monotonic() + timeout_s
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
        if not handle:
            return True  # no such process (already gone) or not ours to watch
        try:
            while True:
                left = max(0.0, deadline - time.monotonic())
                rc = kernel32.WaitForSingleObject(handle, int(min(left, 1.0) * 1000))
                if rc == _WAIT_OBJECT_0:
                    return True
                if rc != _WAIT_TIMEOUT or left <= 0:
                    return rc == _WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def swap_exe(target: str, new_file: str, attempts: int = SWAP_ATTEMPTS, delay: float = 0.5) -> None:
    """Rename target -> target.old, copy new_file over target. Restores the old file when the copy fails."""
    old = target + ".old"
    last: OSError | None = None
    for _ in range(max(1, attempts)):
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(target, old)
            break
        except OSError as exc:  # the old exe is still open (exiting, antivirus scan)
            last = exc
            time.sleep(delay)
    else:
        raise OSError(f"cannot move the running exe aside: {last}")
    try:
        shutil.copyfile(new_file, target)
        if os.path.getsize(target) != os.path.getsize(new_file):
            raise OSError("copy is incomplete")
    except OSError:
        try:
            if os.path.exists(target):
                os.remove(target)
            os.replace(old, target)
        except OSError:
            pass
        raise


def launch_target(target: str, env: dict | None = None) -> None:
    _popen_detached([target], env if env is not None else clean_env(), os.path.dirname(target))


def run_apply(args: list[str], *, new_file: str | None = None, log_path: str | None = None,
              wait: Callable[[int, float], bool] = wait_for_pid,
              launch: Callable[[str, dict], None] = launch_target) -> int:
    """`<App>-<v>.exe --apply-update <pid> <target> [--no-launch]`: the apply mode of the downloaded exe.

    `args` is the argv after the flag. Runs with no window and no console. Steps are appended to `apply.log` next
    to this exe (the updates folder). Returns 0 when the target holds the new exe, 1 otherwise (the old exe is
    restored or was never touched).
    """
    new_file = new_file or os.path.abspath(sys.executable)
    log_path = log_path or os.path.join(os.path.dirname(new_file), APPLY_LOG)
    lines: list[str] = []

    def note(text: str) -> None:
        lines.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}")
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(lines[-1] + "\n")
        except OSError:
            pass

    do_launch = NO_LAUNCH_FLAG not in args
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) < 2:
        note(f"bad arguments: {args!r}")
        return 1
    try:
        pid = int(positional[0])
    except ValueError:
        note(f"bad pid: {positional[0]!r}")
        return 1
    target = os.path.abspath(positional[1])
    note(f"apply {new_file} -> {target}, waiting for pid {pid}")
    started = time.monotonic()
    exited = wait(pid, APPLY_WAIT_S)
    note(f"pid {pid} {'exited' if exited else 'still running'} after {time.monotonic() - started:.1f} s")
    try:
        swap_exe(target, new_file)
    except OSError as exc:
        note(f"swap failed, old exe kept: {exc}")
        return 1
    note("swap done")
    if do_launch:
        try:
            launch(target, clean_env())
            note("started the new exe")
        except OSError as exc:
            note(f"could not start the new exe: {exc}")
            return 1
    return 0
