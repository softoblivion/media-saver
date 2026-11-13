"""Windows helpers used by the shell: clipboard, Explorer, single instance, message box, and the resizable frameless
window (sizing borders, minimum size, no maximize, DPI changes, DWM corners and border colour).

Everything here is ctypes / stdlib only and degrades to a harmless no-op on non-Windows hosts, so the module can be
imported by the unit tests on any machine. The geometry (`fit_rect`, `scaled_rect`, `restore_rect`,
`fitting_scales`, `corrected_window_size`) is pure and tested without a window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Any, Callable, NamedTuple, Optional, Sequence
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


# --- window geometry (pure) --------------------------------------------------------------------------------------
#
# Rects are (x, y, w, h) of the window's page area (the client area, which is what the user sees: the sizing
# borders around it are invisible) in physical screen px; work areas are (left, top, right, bottom). A size in
# layout px (the page's CSS px at Scale 100 %) times the Scale factor `zoom` times the monitor's DPI scale gives
# physical px.

class Monitor(NamedTuple):
    work: tuple[int, int, int, int]  # work area (without the taskbar), physical px
    scale: float                     # DPI scale (1.0 at 96 dpi)
    primary: bool = False


def client_px(size: tuple[int, int], zoom: float, scale: float) -> tuple[int, int]:
    """Physical size of a page area of `size` layout px at the Scale factor `zoom` and the DPI scale `scale`."""
    return int(round(size[0] * zoom * scale)), int(round(size[1] * zoom * scale))


def layout_size(px: tuple[int, int], zoom: float, scale: float) -> tuple[int, int]:
    """Layout px of a page area of `px` physical px (the inverse of client_px)."""
    factor = zoom * scale
    return int(round(px[0] / factor)), int(round(px[1] / factor))


def fit_rect(rect: tuple[int, int, int, int], work: Optional[tuple[int, int, int, int]],
             min_px: tuple[int, int]) -> tuple[int, int, int, int]:
    """`rect` with its size kept between `min_px` and the work area (the minimum wins on a work area smaller than
    it) and moved the least distance that brings it inside the work area (its top-left corner when it is larger)."""
    x, y, w, h = rect
    w, h = max(w, min_px[0]), max(h, min_px[1])
    if work is None:
        return x, y, w, h
    left, top, right, bottom = work
    w = max(min_px[0], min(w, right - left))
    h = max(min_px[1], min(h, bottom - top))
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
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


def scaled_rect(rect: tuple[int, int, int, int], ratio: float, work: Optional[tuple[int, int, int, int]],
                min_px: tuple[int, int]) -> tuple[int, int, int, int]:
    """The rect of a Scale change: the size times `ratio` (new step / old step), the centre kept, then fitted
    (fit_rect). The layout size of the page stays the same unless the work area or the minimum clamps it."""
    x, y, w, h = rect
    nw, nh = int(round(w * ratio)), int(round(h * ratio))
    nx = int(round(x + (w - nw) / 2))
    ny = int(round(y + (h - nh) / 2))
    return fit_rect((nx, ny, nw, nh), work, min_px)


def fits(size_px: tuple[int, int], work: Optional[tuple[int, int, int, int]]) -> bool:
    if work is None:
        return True
    return size_px[0] <= work[2] - work[0] and size_px[1] <= work[3] - work[1]


def fitting_scales(steps: Sequence[int], min_size: tuple[int, int], monitor: Optional[Monitor]) -> list[int]:
    """The Scale steps (percent) whose minimum window fits the monitor's work area; every step when the monitor is
    unknown, the smallest step when none fits (the window then overflows at its minimum, as no smaller layout
    exists)."""
    if monitor is None:
        return list(steps)
    result = [s for s in steps if fits(client_px(min_size, s / 100.0, monitor.scale), monitor.work)]
    return result or [min(steps)]


def allowed_scale(percent: int, fitting: Sequence[int]) -> int:
    """`percent` when it fits, else the largest fitting step below it, else the smallest fitting step."""
    if percent in fitting:
        return percent
    lower = [s for s in fitting if s < percent]
    return max(lower) if lower else min(fitting)


def _overlap(rect: tuple[int, int, int, int], work: tuple[int, int, int, int]) -> int:
    x, y, w, h = rect
    dx = min(x + w, work[2]) - max(x, work[0])
    dy = min(y + h, work[3]) - max(y, work[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def restore_rect(saved: Optional[dict], monitors: Sequence[Monitor], zoom: float, min_size: tuple[int, int],
                 start_size: tuple[int, int]) -> tuple[tuple[int, int, int, int], Optional[Monitor]]:
    """Where the window opens: the stored place and size (`saved`, Settings.window: x / y physical px, width /
    height layout px) on the monitor it overlaps most, fitted into that monitor's work area; centred on the primary
    monitor when the stored place lies on no connected monitor (the size is kept, fitted); `start_size` centred on
    the primary monitor when nothing is stored. Returns (rect, monitor)."""
    if not monitors:
        size = client_px(saved_size(saved) or start_size, zoom, 1.0)
        return fit_rect((0, 0) + size, None, client_px(min_size, zoom, 1.0)), None
    primary = next((m for m in monitors if m.primary), monitors[0])
    stored = saved_size(saved)
    if stored is not None and saved is not None:
        best, best_area = None, 0
        for monitor in monitors:
            rect = (int(saved["x"]), int(saved["y"])) + client_px(stored, zoom, monitor.scale)
            area = _overlap(rect, monitor.work)
            if area > best_area:
                best, best_area = (rect, monitor), area
        if best is not None:
            rect, monitor = best
            return fit_rect(rect, monitor.work, client_px(min_size, zoom, monitor.scale)), monitor
    size = client_px(stored or start_size, zoom, primary.scale)
    min_px = client_px(min_size, zoom, primary.scale)
    size = (max(size[0], min_px[0]), max(size[1], min_px[1]))
    width, height = primary.work[2] - primary.work[0], primary.work[3] - primary.work[1]
    size = (max(min_px[0], min(size[0], width)), max(min_px[1], min(size[1], height)))
    return fit_rect(centered_rect(size, primary.work), primary.work, min_px), primary


def saved_size(saved: Optional[dict]) -> Optional[tuple[int, int]]:
    """(width, height) of a stored Settings.window, or None."""
    if not isinstance(saved, dict):
        return None
    try:
        return int(saved["width"]), int(saved["height"])
    except (KeyError, TypeError, ValueError):
        return None


def corrected_window_size(proposed: tuple[int, int], normal: Optional[tuple[int, int]],
                          nc: tuple[int, int]) -> tuple[int, int]:
    """Window size (width, height) a WM_WINDOWPOSCHANGING should apply.

    `proposed` is the size in the message, `normal` the last window size recorded while the window was neither
    minimized nor maximized (None before the first record), `nc` the window size minus the client size (the invisible
    sizing border). When `proposed` is exactly `normal` minus the border, WinForms is re-applying the client size it
    stored for the borderless form as the window size (restore after minimize), and `normal` is returned so the
    window keeps its size. Any other size is the system's or the user's choice and is returned unchanged.
    """
    if normal is None:
        return proposed
    if tuple(proposed) == (normal[0] - nc[0], normal[1] - nc[1]):
        return normal[0], normal[1]
    return proposed


# --- resizable frameless window ----------------------------------------------------------------------------------
#
# The pywebview form is borderless (WinForms FormBorderStyle.None). `ResizableHook` subclasses its window procedure:
# - WS_THICKFRAME gives the system sizing borders on the left, right and bottom; Windows 11 draws them invisible
#   outside the page area (like the borders of any window) with the system resize cursors, and DWM keeps the
#   shadow and the rounded corners. WM_NCCALCSIZE gives the top border back to the page, so no frame strip shows
#   above the title bar; the top edge and corners are sized from the page (`begin_native_drag` with HTTOP...).
# - WS_MAXIMIZEBOX stays off and SC_MAXIMIZE is swallowed, so neither the system menu, a title-bar double click,
#   Win+Up nor dragging to the top of the screen maximizes; a maximized state that still arrives
#   (ShowWindow(SW_MAXIMIZE) from outside) is undone with SC_RESTORE. Side tiling (Snap) stays: it is a normal
#   size the user chose.
# - WM_GETMINMAXINFO and WM_WINDOWPOSCHANGING hold the minimum: the layout's minimum size times the Scale factor
#   times the monitor's DPI scale, plus the borders.
# - WinForms re-applies the borderless client size as the window size when the form comes back from minimized, so
#   the window would lose the border width on every restore; `corrected_window_size` undoes that.
# - WM_DPICHANGED (the process is per-monitor DPI aware) keeps the page's layout size on the new monitor; WebView2
#   follows the monitor's scale on its own, so the page stays sharp.
# - The end of a move or size loop, and any other change of place or size, is reported through `on_settled` (the
#   shell stores the geometry); a DPI change through `on_dpi`.

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WM_SIZE = 0x0005
WM_GETMINMAXINFO = 0x0024
WM_WINDOWPOSCHANGING = 0x0046
WM_WINDOWPOSCHANGED = 0x0047
WM_STYLECHANGING = 0x007C
WM_NCDESTROY = 0x0082
WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WM_DPICHANGED = 0x02E0
SIZE_MAXIMIZED = 2
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120
HTCAPTION, HTTOP, HTTOPLEFT, HTTOPRIGHT = 2, 12, 13, 14
DRAG_HITS = {"move": HTCAPTION, "top": HTTOP, "top-left": HTTOPLEFT, "top-right": HTTOPRIGHT}
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1
MDT_EFFECTIVE_DPI = 0
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
SM_SWAPBUTTON = 23
MINIMIZED_POSITION = -32000  # x / y of a minimized top-level window
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

if IS_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                          wintypes.LPARAM)
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
    _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.ReleaseCapture.restype = wintypes.BOOL


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


class _STYLESTRUCT(ctypes.Structure):
    _fields_ = [("styleOld", wintypes.DWORD), ("styleNew", wintypes.DWORD)]


def set_per_monitor_dpi_awareness() -> bool:
    """Make the process per-monitor DPI aware (v2) before any window exists, so a window moved to a monitor of
    another DPI gets WM_DPICHANGED and WebView2 renders at that monitor's scale instead of a bitmap stretch. Call
    it before webview.start(): pywebview's own SetProcessDPIAware() (system aware) then fails harmlessly."""
    if not IS_WINDOWS:
        return False
    try:
        fn = _user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        return bool(fn(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    except (AttributeError, OSError):  # before Windows 10 1703
        return False


def _monitor_info(handle) -> Optional[_MONITORINFO]:
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not handle or not _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    return info


def _monitor_scale(handle) -> float:
    try:
        x, y = wintypes.UINT(), wintypes.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(handle, MDT_EFFECTIVE_DPI, ctypes.byref(x), ctypes.byref(y)) == 0:
            return (x.value or 96) / 96.0
    except (AttributeError, OSError):
        pass
    return 1.0


def _as_monitor(handle) -> Optional[Monitor]:
    info = _monitor_info(handle)
    if info is None:
        return None
    w = info.rcWork
    return Monitor((w.left, w.top, w.right, w.bottom), _monitor_scale(handle),
                   bool(info.dwFlags & MONITORINFOF_PRIMARY))


def list_monitors() -> list[Monitor]:
    """Every connected monitor: work area, DPI scale, primary flag."""
    if not IS_WINDOWS:
        return []
    handles: list[Any] = []

    def collect(handle, _hdc, _rect, _data):
        handles.append(handle)
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(collect), 0)
    return [m for m in (_as_monitor(h) for h in handles) if m is not None]


def window_monitor(hwnd) -> Optional[Monitor]:
    """The monitor the window is on (the nearest one)."""
    if not IS_WINDOWS or not hwnd:
        return None
    return _as_monitor(_user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))


def window_scale(hwnd) -> float:
    """DPI scale of the window (1.0 at 96 dpi)."""
    if not IS_WINDOWS or not hwnd:
        return 1.0
    try:
        return (_user32.GetDpiForWindow(hwnd) or 96) / 96.0
    except (AttributeError, OSError):
        return 1.0


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


def begin_native_drag(hwnd: int, part: str) -> bool:
    """Hand a press on the page to the system's move or size loop, as a press on a title bar or a window edge
    would: `part` is "move" (the title bar) or "top", "top-left", "top-right" (the top edge, which has no system
    border). Call it on the window's UI thread while the primary mouse button is down; when the button is already up
    (a click shorter than the trip from the page) nothing starts, so the window never follows a released mouse."""
    hit = DRAG_HITS.get(part)
    if not IS_WINDOWS or not hwnd or hit is None:
        return False
    button = VK_RBUTTON if _user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
    if not _user32.GetAsyncKeyState(button) & 0x8000:
        return False
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return False
    _user32.ReleaseCapture()
    lparam = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
    return bool(_user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, hit, lparam))


class ResizableHook:
    """Window-procedure subclass of the resizable frameless window; keep the instance alive as long as the window
    exists. `min_size` is the layout's minimum (layout px); `zoom` the Scale factor, set by the shell."""

    def __init__(self, hwnd: int, min_size: tuple[int, int],
                 on_settled: Optional[Callable[[], None]] = None,
                 on_dpi: Optional[Callable[[], None]] = None) -> None:
        self.hwnd = hwnd
        self.min_size = min_size
        self.zoom = 1.0
        self.scale = window_scale(hwnd)  # DPI scale of the monitor the window is on, followed on WM_DPICHANGED
        self.on_settled = on_settled
        self.on_dpi = on_dpi
        self.nc = (0, 0, 0, 0)  # invisible border: left, top, right, bottom (physical px)
        self._normal_size: Optional[tuple[int, int]] = None  # last window size while neither minimized nor maximized
        self._sizing = False  # inside a move or size loop (WM_ENTERSIZEMOVE .. WM_EXITSIZEMOVE)
        self._prev_proc = None
        self._proc = None
        self.installed = False

    # -- setup
    def install(self) -> bool:
        if not IS_WINDOWS or not self.hwnd or self.installed:
            return self.installed
        self._proc = _WNDPROC(self._wnd_proc)
        self._prev_proc = _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                                    ctypes.cast(self._proc, ctypes.c_void_p).value)
        self.installed = bool(self._prev_proc)
        if not self.installed:
            return False
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE) or 0
        _user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, self._style(style))
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                             SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self.nc = self._measure_nc()
        return True

    @staticmethod
    def _style(style: int) -> int:
        return (int(style) | WS_THICKFRAME | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX

    # -- geometry
    def _measure_nc(self) -> tuple[int, int, int, int]:
        outer, inner = wintypes.RECT(), wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not (_user32.GetWindowRect(self.hwnd, ctypes.byref(outer))
                and _user32.GetClientRect(self.hwnd, ctypes.byref(inner))
                and _user32.ClientToScreen(self.hwnd, ctypes.byref(origin))):
            return self.nc
        left, top = origin.x - outer.left, origin.y - outer.top
        right = (outer.right - outer.left) - inner.right - left
        bottom = (outer.bottom - outer.top) - inner.bottom - top
        return left, top, right, bottom

    def client_rect(self) -> Optional[tuple[int, int, int, int]]:
        """(x, y, w, h) of the page area in physical screen px."""
        if not IS_WINDOWS or not self.hwnd:
            return None
        inner = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not (_user32.GetClientRect(self.hwnd, ctypes.byref(inner))
                and _user32.ClientToScreen(self.hwnd, ctypes.byref(origin))):
            return None
        return origin.x, origin.y, inner.right, inner.bottom

    def window_rect_for(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """The window rect (x, y, w, h, borders included) whose page area is `rect`."""
        x, y, w, h = rect
        left, top, right, bottom = self.nc
        return x - left, y - top, w + left + right, h + top + bottom

    def min_client_px(self) -> tuple[int, int]:
        return client_px(self.min_size, self.zoom, self.scale)

    def set_client_rect(self, rect: tuple[int, int, int, int],
                        apply: Optional[Callable[[int, int, int, int], None]] = None) -> tuple[int, int, int, int]:
        """Place the page area at `rect` (physical px). `apply(x, y, w, h)` performs the move with the window rect
        (pass the form's SetBounds so WinForms keeps its bounds in sync); the default is SetWindowPos."""
        x, y, w, h = self.window_rect_for(rect)
        if apply is not None:
            apply(x, y, w, h)
        elif IS_WINDOWS and self.hwnd:
            _user32.SetWindowPos(self.hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        return x, y, w, h

    def is_normal(self) -> bool:
        return bool(IS_WINDOWS and self.hwnd and not _user32.IsIconic(self.hwnd) and not _user32.IsZoomed(self.hwnd))

    # -- messages
    def _min_track(self) -> tuple[int, int]:
        w, h = self.min_client_px()
        left, top, right, bottom = self.nc
        return w + left + right, h + top + bottom

    def _on_pos_changing(self, hwnd, lparam) -> None:
        pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
        if pos.flags & SWP_NOSIZE or pos.x <= MINIMIZED_POSITION or pos.y <= MINIMIZED_POSITION:
            return
        if _user32.IsIconic(hwnd) or _user32.IsZoomed(hwnd):
            return
        cx, cy = pos.cx, pos.cy
        if not self._sizing:
            nc = (self.nc[0] + self.nc[2], self.nc[1] + self.nc[3])
            cx, cy = corrected_window_size((cx, cy), self._normal_size, nc)
        min_w, min_h = self._min_track()
        pos.cx, pos.cy = max(cx, min_w), max(cy, min_h)

    def _on_pos_changed(self, hwnd, lparam) -> None:
        pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
        if pos.x <= MINIMIZED_POSITION or pos.y <= MINIMIZED_POSITION:
            return
        if _user32.IsIconic(hwnd) or _user32.IsZoomed(hwnd):
            return
        if not pos.flags & SWP_NOSIZE and pos.cx > self.nc[0] + self.nc[2] and pos.cy > self.nc[1] + self.nc[3]:
            self._normal_size = (pos.cx, pos.cy)
        if not self._sizing and (pos.flags & (SWP_NOSIZE | SWP_NOMOVE)) != (SWP_NOSIZE | SWP_NOMOVE):
            self._notify(self.on_settled)

    def _on_dpi_changed(self, hwnd, wparam, lparam) -> None:
        """Keep the page's layout size: the page area of the new monitor is the old layout size times the Scale
        factor times the new DPI scale; the place is the system's suggestion (it keeps the window under the
        cursor during a drag)."""
        new_scale = ((int(wparam) & 0xFFFF) or 96) / 96.0
        suggested = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
        current = self.client_rect()
        old_scale = self.scale
        self.scale = new_scale
        if current is None:
            _user32.SetWindowPos(hwnd, None, suggested.left, suggested.top, suggested.right - suggested.left,
                                 suggested.bottom - suggested.top, SWP_NOZORDER | SWP_NOACTIVATE)
            return
        target = dpi_changed_size(current[2:], old_scale, new_scale)
        ratio = new_scale / old_scale
        self.nc = tuple(int(round(v * ratio)) for v in self.nc)  # the system borders scale with the DPI
        left, top, right, bottom = self.nc
        self._normal_size = None  # a size of the old DPI must not be "corrected" back
        _user32.SetWindowPos(hwnd, None, suggested.left, suggested.top, target[0] + left + right,
                             target[1] + top + bottom, SWP_NOZORDER | SWP_NOACTIVATE)
        self.nc = self._measure_nc()
        after = self.client_rect()
        if after is not None and after[2:] != target:  # the border estimate was a pixel off: size it exactly
            left, top, right, bottom = self.nc
            _user32.SetWindowPos(hwnd, None, 0, 0, target[0] + left + right, target[1] + top + bottom,
                                 SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE)

    def _notify(self, callback: Optional[Callable[[], None]]) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
            log.exception("window callback failed")

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCCALCSIZE and wparam:
                params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                top = params.rgrc[0].top
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                params.rgrc[0].top = top  # no top border: the page owns the top edge
                return result
            if msg == WM_GETMINMAXINFO:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
                info.ptMinTrackSize.x, info.ptMinTrackSize.y = self._min_track()
                return result
            if msg == WM_WINDOWPOSCHANGING:
                self._on_pos_changing(hwnd, lparam)
            elif msg == WM_WINDOWPOSCHANGED:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self._on_pos_changed(hwnd, lparam)
                return result
            elif msg == WM_STYLECHANGING and ctypes.c_int(int(wparam)).value == GWL_STYLE:
                style = ctypes.cast(lparam, ctypes.POINTER(_STYLESTRUCT)).contents
                style.styleNew = self._style(style.styleNew)  # WinForms rewrites the style from its own view
            elif msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MAXIMIZE:
                return 0  # system menu, title-bar double click, keyboard
            elif msg == WM_SIZE and int(wparam) == SIZE_MAXIMIZED:
                _user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
            elif msg == WM_ENTERSIZEMOVE:
                self._sizing = True
            elif msg == WM_EXITSIZEMOVE:
                self._sizing = False
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self._notify(self.on_settled)
                return result
            elif msg == WM_DPICHANGED:
                self._on_dpi_changed(hwnd, wparam, lparam)
                self._notify(self.on_dpi)
                return 0
            elif msg == WM_NCDESTROY:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self.installed = False
                return result
        except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
            log.debug("window procedure hook failed for message 0x%04x", msg, exc_info=True)
        return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)


def dpi_changed_size(size: tuple[int, int], old_scale: float, new_scale: float) -> tuple[int, int]:
    """Page-area size (physical px) on a monitor of `new_scale` that keeps the layout size of `size` at
    `old_scale`."""
    ratio = new_scale / old_scale
    return int(round(size[0] * ratio)), int(round(size[1] * ratio))
