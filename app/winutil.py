"""Windows helpers used by the shell: clipboard, Explorer, single instance, message box, and the fixed-size frameless
window (size lock, no maximize, DWM corners and border colour).

Everything here is ctypes / stdlib only and degrades to a harmless no-op on non-Windows hosts, so the module can be
imported by the unit tests on any machine. The size choice (`pick_window_size`, `centered_rect`) is pure and tested
without a window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

log = logging.getLogger("mediasaver.winutil")

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_BORDER_COLOR = 34

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


def colorref(hex_color: str) -> int:
    """'#rrggbb' -> COLORREF 0x00BBGGRR."""
    text = hex_color.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def set_border_color(hwnd: int, hex_color: str) -> bool:
    """Windows 11 window border colour (DWMWA_BORDER_COLOR); the page background hides the light frame line.
    Harmless (returns False) before Windows 11 build 22000."""
    if not IS_WINDOWS or not hwnd:
        return False
    value = wintypes.DWORD(colorref(hex_color))
    hr = _dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


# --- window size (pure) ------------------------------------------------------------------------------------------

def pick_window_size(ladder: Sequence[tuple[int, int]], work: Optional[tuple[int, int]],
                     zoom: float = 1.0) -> tuple[int, int]:
    """The largest size of `ladder` (largest first, logical px) whose size times `zoom` (the Scale setting as a
    factor) fits the work area `work` (logical px); the smallest step when none fits, the first one when the work
    area is unknown."""
    if not ladder:
        raise ValueError("empty size ladder")
    if work is None:
        return ladder[0]
    for width, height in ladder:
        if width * zoom <= work[0] and height * zoom <= work[1]:
            return width, height
    return ladder[-1]


def placed_rect(size: tuple[int, int], current: Optional[tuple[int, int]],
                work: Optional[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a window of `size` that keeps its `current` top-left corner where possible: moved left or
    up so it stays inside the work area, never past the work area's top-left; centred when `current` is None."""
    if current is None:
        return centered_rect(size, work)
    w, h = size
    x, y = current
    if work is not None:
        x = min(x, work[2] - w)
        y = min(y, work[3] - h)
        x = max(work[0], x)
        y = max(work[1], y)
    return x, y, w, h


def centered_rect(size: tuple[int, int], work: Optional[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a window of `size` centred on the work area (left, top, right, bottom), kept inside it when
    it fits; (0, 0, w, h) when the work area is unknown. Physical px."""
    w, h = size
    if work is None:
        return 0, 0, w, h
    x = work[0] + ((work[2] - work[0]) - w) // 2
    y = work[1] + ((work[3] - work[1]) - h) // 2
    return max(work[0], x), max(work[1], y), w, h


# --- fixed-size frameless window ---------------------------------------------------------------------------------
#
# A frameless, non-resizable pywebview form (WinForms FormBorderStyle.None) has no sizing border, but a script or the
# shell can still resize or maximize it: the style keeps the WS_MAXIMIZEBOX bit, WM_SYSCOMMAND SC_MAXIMIZE and
# ShowWindow(SW_MAXIMIZE) both fill the work area, and SetWindowPos accepts any size (measured with pywebview 6.2.1).
# `FixedSizeHook` subclasses the window procedure: every size proposal of the restored or maximized window is
# replaced by the fixed size, SC_MAXIMIZE is swallowed, a maximized state that still arrives (ShowWindow(SW_MAXIMIZE)
# from outside) is undone with SC_RESTORE, WS_MAXIMIZEBOX / WS_THICKFRAME are cleared and WS_MINIMIZEBOX is set
# (taskbar click and Win+Down minimize). Minimizing and restoring keep the size without help: without WS_THICKFRAME
# the window size equals the client size, so WinForms re-applies the right size.

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WM_WINDOWPOSCHANGING = 0x0046
WM_SYSCOMMAND = 0x0112
WM_SIZE = 0x0005
WM_NCDESTROY = 0x0082
SIZE_MAXIMIZED = 2
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
MONITOR_DEFAULTTONEAREST = 2
MINIMIZED_POSITION = -32000  # x / y of a minimized top-level window

if IS_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM]
    _user32.CallWindowProcW.restype = LRESULT
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.UINT]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


def monitor_work_area(hwnd) -> Optional[tuple[int, int, int, int]]:
    """(left, top, right, bottom) of the work area of the monitor the window is on (physical px), or None."""
    if not IS_WINDOWS or not hwnd:
        return None
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def window_scale(hwnd) -> float:
    """DPI scale of the window (1.0 at 96 dpi)."""
    if not IS_WINDOWS or not hwnd:
        return 1.0
    try:
        return (_user32.GetDpiForWindow(hwnd) or 96) / 96.0
    except (AttributeError, OSError):
        return 1.0


def window_position(hwnd) -> Optional[tuple[int, int]]:
    """(x, y) of the window's top-left corner in physical px, or None."""
    if not IS_WINDOWS or not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top


def set_zoom_factor(window: Any, factor: float) -> bool:
    """The WebView2 zoom factor of a pywebview window (edgechromium backend): `window.native.webview` is the
    WinForms WebView2 control. Zoom is a device-scale change: the page keeps its logical viewport and text stays
    crisp. Call it on the UI thread. False when the control is not reachable."""
    native = getattr(window, "native", None)
    control = getattr(native, "webview", None)
    if control is None:
        return False
    try:
        control.ZoomFactor = float(factor)
        return True
    except Exception:  # noqa: BLE001 - a backend without the property
        log.exception("zoom factor %.2f could not be set", factor)
        return False


class FixedSizeHook:
    """Window-procedure subclass that holds the window at one physical size; keep the instance alive as long as
    the window exists."""

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.size: Optional[tuple[int, int]] = None  # physical px; None = not locked yet
        self._prev_proc = None
        self._proc = None
        self.installed = False

    def install(self) -> bool:
        if not IS_WINDOWS or not self.hwnd or self.installed:
            return self.installed
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE) or 0
        style = (style | WS_MINIMIZEBOX) & ~(WS_MAXIMIZEBOX | WS_THICKFRAME)
        _user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style)
        self._proc = _WNDPROC(self._wnd_proc)
        self._prev_proc = _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                                    ctypes.cast(self._proc, ctypes.c_void_p).value)
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                             SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self.installed = bool(self._prev_proc)
        return self.installed

    def fit_size(self, ladder: Sequence[tuple[int, int]], zoom: float = 1.0) -> tuple[int, int]:
        """Logical size from `ladder` for the work area of the window's monitor at the window's DPI; `zoom` is the
        Scale setting as a factor (the window takes `zoom` times the logical size)."""
        scale = window_scale(self.hwnd)
        work = monitor_work_area(self.hwnd)
        work_logical = None
        if work is not None:
            work_logical = (int((work[2] - work[0]) / scale), int((work[3] - work[1]) / scale))
        return pick_window_size(ladder, work_logical, zoom)

    def lock(self, width: int, height: int,
             apply: Optional[Callable[[int, int, int, int], None]] = None, zoom: float = 1.0,
             keep_position: bool = False) -> tuple[int, int, int, int]:
        """Fix the window at `width` x `height` logical px times `zoom` (the Scale setting), centred on the work
        area, or at its current top-left corner (kept inside the work area) with `keep_position`. `apply(x, y, w,
        h)` performs the move (pass the form's SetBounds so WinForms keeps its own bounds in sync); the default is
        SetWindowPos. Returns the physical rect (x, y, w, h)."""
        scale = window_scale(self.hwnd)
        self.size = (int(round(width * zoom * scale)), int(round(height * zoom * scale)))
        current = window_position(self.hwnd) if keep_position else None
        x, y, w, h = placed_rect(self.size, current, monitor_work_area(self.hwnd))
        if apply is not None:
            apply(x, y, w, h)
        elif IS_WINDOWS and self.hwnd:
            _user32.SetWindowPos(self.hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        return x, y, w, h

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_WINDOWPOSCHANGING and self.size is not None:
                pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
                if not (pos.flags & SWP_NOSIZE) and pos.x > MINIMIZED_POSITION and not _user32.IsIconic(hwnd):
                    pos.cx, pos.cy = self.size
            elif msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MAXIMIZE:
                return 0  # system menu, keyboard
            elif msg == WM_SIZE and int(wparam) == SIZE_MAXIMIZED:
                _user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
            elif msg == WM_NCDESTROY:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self.installed = False
                return result
        except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
            log.debug("window procedure hook failed for message 0x%04x", msg, exc_info=True)
        return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
