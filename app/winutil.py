"""Windows helpers used by the shell: clipboard, Explorer, single instance, DWM, message box.

Everything here is ctypes / stdlib only and degrades to a harmless no-op on non-Windows hosts, so the module can be
imported by the unit tests on any machine.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from urllib.parse import urlparse

log = logging.getLogger("mediasaver.winutil")

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

SW_RESTORE = 9
SW_SHOW = 5

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")

    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    _user32.MessageBoxW.restype = ctypes.c_int

    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    _dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


# --- clipboard ---------------------------------------------------------------------------------------------------

def _open_clipboard(retries: int = 10) -> bool:
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def read_clipboard() -> str:
    """Plain text of the clipboard; "" when it is empty, busy or holds something other than text."""
    if not IS_WINDOWS:
        return ""
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _open_clipboard():
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def write_clipboard(text: str) -> bool:
    """Put plain text on the clipboard (CF_UNICODETEXT)."""
    if not IS_WINDOWS:
        return False
    data = (text + "\0").encode("utf-16-le")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        _kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _open_clipboard():
        _kernel32.GlobalFree(handle)
        return False
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False
        # The system owns the memory block after a successful SetClipboardData.
        return True
    finally:
        _user32.CloseClipboard()


# --- Explorer / shell --------------------------------------------------------------------------------------------

def reveal_in_explorer(path: str) -> bool:
    """Open Explorer with the file selected; falls back to opening the parent folder."""
    if not path:
        return False
    path = os.path.normpath(path)
    try:
        if os.path.exists(path):
            # One command-line string on purpose: explorer parses `/select,"<path>"` itself and rejects the
            # argument vector Python would build for a list.
            subprocess.Popen(f'explorer /select,"{path}"', creationflags=CREATE_NO_WINDOW)
            return True
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            os.startfile(parent)  # type: ignore[attr-defined]
            return True
    except OSError:
        log.exception("reveal_in_explorer failed for %s", path)
    return False


def open_path(path: str) -> bool:
    """Open a file or folder with the shell default handler."""
    if not path or not os.path.exists(path):
        return False
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except OSError:
        log.exception("open_path failed for %s", path)
        return False


def open_url(url: str) -> bool:
    """Open an https:// URL in the default browser. Anything else is refused."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return False
    try:
        os.startfile(parsed.geturl())  # type: ignore[attr-defined]
        return True
    except OSError:
        log.exception("open_url failed for %s", url)
        return False


def message_box(title: str, text: str, error: bool = False) -> None:
    """Blocking native message box; prints to stderr where there is no Windows."""
    if not IS_WINDOWS:
        print(f"{title}: {text}", file=sys.stderr)
        return
    _user32.MessageBoxW(None, text, title, MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION))


# --- single instance ---------------------------------------------------------------------------------------------

def acquire_single_instance(name: str):
    """Create the named mutex. Returns its handle (keep it alive), or None when another instance already owns it."""
    if not IS_WINDOWS:
        return object()
    handle = _kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        return object()  # cannot decide; do not block the launch
    if err == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def find_window(title: str) -> int:
    if not IS_WINDOWS:
        return 0
    return _user32.FindWindowW(None, title) or 0


def bring_window_to_front(title: str) -> bool:
    """Restore and focus the top-level window with this exact title."""
    hwnd = find_window(title)
    if not hwnd:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        _user32.ShowWindow(hwnd, SW_SHOW)
    return bool(_user32.SetForegroundWindow(hwnd))


# --- DWM ---------------------------------------------------------------------------------------------------------

def set_rounded_corners(hwnd: int) -> bool:
    """Windows 11 rounded corners for a frameless window. Harmless (returns False) on older systems."""
    if not IS_WINDOWS or not hwnd:
        return False
    value = ctypes.c_int(DWMWCP_ROUND)
    hr = _dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


def dwm_set_int(hwnd: int, attribute: int, value: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    v = ctypes.c_int(value)
    return _dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), ctypes.sizeof(v)) == 0


# --- frameless window: edge resizing -----------------------------------------------------------------------------
#
# A frameless pywebview window (WinForms FormBorderStyle.None) has no sizing border, so it cannot be resized from its
# edges. `FramelessResizeHook` gives the form back WS_THICKFRAME (the system then draws its usual invisible resize
# border and handles hit-testing, DWM shadow and Aero snap) and makes WM_NCCALCSIZE keep the whole window rect as
# client area except for that border, i.e. no title bar and no visible chrome appear.

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_CAPTION = 0x00C00000
WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
SW_SHOWMAXIMIZED = 3

if IS_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallWindowProcW.restype = LRESULT
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


WM_GETMINMAXINFO = 0x0024
SM_CXSIZEFRAME, SM_CXPADDEDBORDER = 32, 92


def _monitor_work_area(hwnd):
    """(left, top, right, bottom) of the work area of the monitor the window is on, or None."""
    monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def _fit_maximized_to_work_area(hwnd, lparam) -> None:
    """Maximize to the monitor work area (not over the taskbar): WinForms uses the whole monitor for borderless forms."""
    monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return
    dpi = _user32.GetDpiForWindow(hwnd) or 96
    border = _user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + _user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
    mmi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
    work, mon = info.rcWork, info.rcMonitor
    mmi.ptMaxPosition.x = work.left - mon.left - border
    mmi.ptMaxPosition.y = work.top - mon.top - border
    mmi.ptMaxSize.x = (work.right - work.left) + 2 * border
    mmi.ptMaxSize.y = (work.bottom - work.top) + 2 * border


class FramelessResizeHook:
    """Subclass of the form's window procedure; keep the instance alive as long as the window exists."""

    def __init__(self, hwnd: int, min_client_size: tuple[int, int] = (0, 0)) -> None:
        self.hwnd = hwnd
        self.min_client_size = min_client_size  # logical pixels
        self._prev = None
        self._proc = None
        self._nc = (0, 0)  # window rect minus client rect (width, height) while not maximized
        self.installed = False

    def install(self) -> bool:
        if not IS_WINDOWS or not self.hwnd or self.installed:
            return self.installed
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE) or 0
        _user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        self._proc = _WNDPROC(self._wnd_proc)
        self._prev = _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p).value)
        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, flags)
        self._nc = self._measure_nc()
        self.installed = bool(self._prev)
        return self.installed

    def _measure_nc(self) -> tuple[int, int]:
        outer, inner = wintypes.RECT(), wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(outer))
        _user32.GetClientRect(self.hwnd, ctypes.byref(inner))
        return (outer.right - outer.left) - (inner.right - inner.left), (outer.bottom - outer.top) - (inner.bottom - inner.top)

    def set_client_size(self, width: int, height: int, apply=None) -> None:
        """Resize so the page viewport is width x height logical pixels, keeping the window centre.

        `apply(x, y, w, h)` performs the move (pass the form's SetBounds so WinForms remembers the size it restores to
        after maximize); the default is a plain SetWindowPos.
        """
        if not self.installed:
            return
        scale = (_user32.GetDpiForWindow(self.hwnd) or 96) / 96
        rect = wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        new_w, new_h = round(width * scale) + self._nc[0], round(height * scale) + self._nc[1]
        x = rect.left + ((rect.right - rect.left) - new_w) // 2
        y = rect.top + ((rect.bottom - rect.top) - new_h) // 2
        work = _monitor_work_area(self.hwnd)
        if work is not None:
            x = max(work[0], min(x, work[2] - new_w))
            y = max(work[1], min(y, work[3] - new_h))
        if apply is not None:
            apply(x, y, new_w, new_h)
        else:
            _user32.SetWindowPos(self.hwnd, None, x, y, new_w, new_h, SWP_NOZORDER | SWP_NOACTIVATE)

    def _apply_min_track(self, lparam) -> None:
        min_w, min_h = self.min_client_size
        if not (min_w and min_h):
            return
        scale = (_user32.GetDpiForWindow(self.hwnd) or 96) / 96
        mmi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
        mmi.ptMinTrackSize.x = round(min_w * scale) + self._nc[0]
        mmi.ptMinTrackSize.y = round(min_h * scale) + self._nc[1]

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE and wparam:
            params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
            original_top = params.rgrc[0].top
            result = _user32.CallWindowProcW(self._prev, hwnd, msg, wparam, lparam)
            if _user32.IsZoomed(hwnd):
                work = _monitor_work_area(hwnd)
                if work is not None:  # client area = exactly the work area, whatever the frame metrics are
                    params.rgrc[0].left, params.rgrc[0].top, params.rgrc[0].right, params.rgrc[0].bottom = work
            else:
                params.rgrc[0].top = original_top  # no title-bar band: the page owns the top edge
            return result
        if msg == WM_GETMINMAXINFO:
            result = _user32.CallWindowProcW(self._prev, hwnd, msg, wparam, lparam)
            try:
                _fit_maximized_to_work_area(hwnd, lparam)
                self._apply_min_track(lparam)
            except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
                log.debug("work-area fit failed", exc_info=True)
            return result
        if msg == WM_NCDESTROY:
            result = _user32.CallWindowProcW(self._prev, hwnd, msg, wparam, lparam)
            self.installed = False
            return result
        return _user32.CallWindowProcW(self._prev, hwnd, msg, wparam, lparam)
