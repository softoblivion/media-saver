"""Pure parts of the resizable window: layout / physical units, fitting into a work area, the Scale arithmetic, the
restore of the stored place and size, the Scale steps that fit a monitor, the step a session applies on a monitor,
and the window-procedure hook driven with a stubbed user32 and a fake original procedure."""

import ctypes
import ctypes.wintypes
import unittest
from unittest import mock

from app import winutil
from app.core.settings import SCALE_STEPS
from app.main import MIN_SIZE, START_SIZE
from app.winutil import (Monitor, allowed_scale, centered_rect, client_px, colorref, corrected_window_size,
                         dpi_changed_size, fit_rect, fits, fitting_scales, layout_size, restore_rect, saved_size,
                         scaled_rect, session_layout, start_layout)

FULL_HD = Monitor((0, 0, 1920, 1032), 1.0, True)          # 1920 x 1080 at 100 %, taskbar at the bottom
WIDE_RIGHT = Monitor((1920, 0, 4480, 1400), 1.25, False)  # 2560 x 1440 at 125 % to the right of it


def centre(rect):
    x, y, w, h = rect
    return x + w / 2, y + h / 2


class SizesTest(unittest.TestCase):
    def test_start_size_is_not_below_the_minimum(self):
        self.assertGreaterEqual(START_SIZE[0], MIN_SIZE[0])
        self.assertGreaterEqual(START_SIZE[1], MIN_SIZE[1])


class UnitsTest(unittest.TestCase):
    def test_client_px_multiplies_zoom_and_dpi(self):
        self.assertEqual(client_px((940, 640), 1.0, 1.0), (940, 640))
        self.assertEqual(client_px((940, 640), 1.25, 1.0), (1175, 800))
        self.assertEqual(client_px((940, 640), 1.5, 1.5), (2115, 1440))

    def test_layout_size_is_the_inverse(self):
        for zoom in (1.0, 1.25, 1.5, 1.75, 2.0):
            for dpi in (1.0, 1.25, 1.5, 2.0):
                with self.subTest(zoom=zoom, dpi=dpi):
                    self.assertEqual(layout_size(client_px((1180, 780), zoom, dpi), zoom, dpi), (1180, 780))


class FitRectTest(unittest.TestCase):
    def test_raises_a_small_size_to_the_minimum(self):
        self.assertEqual(fit_rect((100, 100, 800, 500), FULL_HD.work, (940, 640)), (100, 100, 940, 640))

    def test_moves_inside_the_work_area_by_the_least_distance(self):
        self.assertEqual(fit_rect((1500, 700, 1000, 700), FULL_HD.work, (940, 640)), (920, 332, 1000, 700))
        self.assertEqual(fit_rect((-300, -50, 1000, 700), FULL_HD.work, (940, 640)), (0, 0, 1000, 700))

    def test_caps_the_size_at_the_work_area(self):
        self.assertEqual(fit_rect((0, 0, 2500, 1500), FULL_HD.work, (940, 640)), (0, 0, 1920, 1032))

    def test_minimum_wins_over_a_smaller_work_area(self):
        self.assertEqual(fit_rect((50, 50, 1000, 700), (0, 0, 900, 600), (940, 640)), (0, 0, 940, 640))

    def test_offset_work_area(self):
        self.assertEqual(fit_rect((1000, 100, 1250, 875), WIDE_RIGHT.work, (1175, 800)), (1920, 100, 1250, 875))

    def test_unknown_work_area_only_applies_the_minimum(self):
        self.assertEqual(fit_rect((-10, 5, 100, 100), None, (940, 640)), (-10, 5, 940, 640))


class ScaledRectTest(unittest.TestCase):
    """A Scale change multiplies the window size by the ratio of the steps and keeps its centre."""

    def test_keeps_the_centre_and_the_proportions(self):
        rect = (400, 150, 1000, 700)
        bigger = scaled_rect(rect, 1.25, FULL_HD.work, client_px(MIN_SIZE, 1.25, 1.0))
        self.assertEqual(bigger[2:], (1250, 875))
        for a, b in zip(centre(rect), centre(bigger)):
            self.assertLessEqual(abs(a - b), 1)

    def test_round_trip_returns_the_same_rect(self):
        rect = (400, 150, 1000, 700)
        up = scaled_rect(rect, 1.25, FULL_HD.work, client_px(MIN_SIZE, 1.25, 1.0))
        down = scaled_rect(up, 1 / 1.25, FULL_HD.work, client_px(MIN_SIZE, 1.0, 1.0))
        for a, b in zip(rect, down):
            self.assertLessEqual(abs(a - b), 1)

    def test_clamped_to_the_work_area(self):
        rect = scaled_rect((370, 126, 1180, 780), 1.5, FULL_HD.work, client_px(MIN_SIZE, 1.5, 1.0))
        self.assertEqual(rect, (75, 0, 1770, 1032))

    def test_raised_to_the_minimum_of_the_new_step(self):
        rect = scaled_rect((100, 100, 940, 640), 0.8, FULL_HD.work, (940, 640))
        self.assertEqual(rect[2:], (940, 640))


class FittingScalesTest(unittest.TestCase):
    """The minimum per Scale step: a step whose smallest window does not fit the work area is not offered."""

    def test_full_hd_at_100_percent(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, FULL_HD), [100, 125, 150])

    def test_small_laptop(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, Monitor((0, 0, 1366, 728), 1.0, True)), [100])

    def test_4k_at_150_percent_takes_every_step(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, Monitor((0, 0, 3840, 2088), 1.5, True)),
                         list(SCALE_STEPS))

    def test_the_dpi_scale_counts(self):
        # 2560 x 1440 at 125 %: the 150 % minimum is 1763 x 1200 physical px
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, Monitor((0, 0, 2560, 1392), 1.25, True)),
                         [100, 125, 150])

    def test_smallest_step_when_nothing_fits(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, Monitor((0, 0, 800, 600), 1.0, True)), [100])

    def test_every_step_on_an_unknown_monitor(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN_SIZE, None), list(SCALE_STEPS))

    def test_fits_is_inclusive(self):
        self.assertTrue(fits((1920, 1032), FULL_HD.work))
        self.assertFalse(fits((1921, 1032), FULL_HD.work))
        self.assertTrue(fits((99999, 99999), None))


class AllowedScaleTest(unittest.TestCase):
    def test_kept_when_it_fits(self):
        self.assertEqual(allowed_scale(125, [100, 125, 150]), 125)

    def test_largest_fitting_step_below(self):
        self.assertEqual(allowed_scale(200, [100, 125, 150]), 150)
        self.assertEqual(allowed_scale(175, [100]), 100)

    def test_smallest_fitting_step_when_none_is_below(self):
        self.assertEqual(allowed_scale(100, [125, 150]), 125)


class RestoreRectTest(unittest.TestCase):
    def test_first_start_centres_the_start_size_on_the_primary_monitor(self):
        rect, monitor = restore_rect(None, [WIDE_RIGHT, FULL_HD], 1.0, MIN_SIZE, START_SIZE)
        self.assertEqual(monitor, FULL_HD)
        self.assertEqual(rect, (370, 126, 1180, 780))

    def test_stored_place_and_size_on_another_monitor(self):
        saved = {"x": 2100, "y": 100, "width": 1000, "height": 700}
        rect, monitor = restore_rect(saved, [FULL_HD, WIDE_RIGHT], 1.0, MIN_SIZE, START_SIZE)
        self.assertEqual(monitor, WIDE_RIGHT)
        self.assertEqual(rect, (2100, 100, 1250, 875))  # the layout size at that monitor's DPI

    def test_monitor_with_the_largest_overlap_wins(self):
        saved = {"x": 1700, "y": 100, "width": 1000, "height": 700}
        rect, monitor = restore_rect(saved, [FULL_HD, WIDE_RIGHT], 1.0, MIN_SIZE, START_SIZE)
        self.assertEqual(monitor, WIDE_RIGHT)
        self.assertEqual(rect, (1920, 100, 1250, 875))

    def test_missing_monitor_opens_centred_on_the_primary(self):
        saved = {"x": 2100, "y": 100, "width": 1000, "height": 700}  # the right-hand monitor is gone
        rect, monitor = restore_rect(saved, [FULL_HD], 1.0, MIN_SIZE, START_SIZE)
        self.assertEqual(monitor, FULL_HD)
        self.assertEqual(rect, (460, 166, 1000, 700))

    def test_missing_monitor_with_a_size_larger_than_the_primary(self):
        saved = {"x": 2100, "y": 100, "width": 2000, "height": 1300}
        rect, monitor = restore_rect(saved, [FULL_HD], 1.0, MIN_SIZE, START_SIZE)
        self.assertEqual(rect, (0, 0, 1920, 1032))

    def test_stored_size_follows_the_scale(self):
        saved = {"x": 100, "y": 50, "width": 1000, "height": 640}
        rect, _ = restore_rect(saved, [FULL_HD], 1.5, MIN_SIZE, START_SIZE)
        self.assertEqual(rect, (100, 50, 1500, 960))

    def test_clamped_into_the_work_area_and_up_to_the_minimum(self):
        rect, _ = restore_rect({"x": 1500, "y": 900, "width": 500, "height": 300}, [FULL_HD], 1.0, MIN_SIZE,
                               START_SIZE)
        self.assertEqual(rect, (980, 392, 940, 640))
        rect, _ = restore_rect({"x": 0, "y": 0, "width": 1200, "height": 800}, [FULL_HD], 1.5, MIN_SIZE, START_SIZE)
        self.assertEqual(rect, (0, 0, 1800, 1032))

    def test_damaged_value_counts_as_none(self):
        for saved in ("x", 5, {"x": 1}, {"x": 1, "y": 2, "width": "a", "height": 3}):
            with self.subTest(saved=saved):
                rect, monitor = restore_rect(saved, [FULL_HD], 1.0, MIN_SIZE, START_SIZE)
                self.assertEqual((rect, monitor), ((370, 126, 1180, 780), FULL_HD))

    def test_no_monitor_information(self):
        self.assertEqual(restore_rect(None, [], 1.0, MIN_SIZE, START_SIZE), ((0, 0, 1180, 780), None))
        rect, _ = restore_rect({"x": 5, "y": 5, "width": 1000, "height": 700}, [], 1.25, MIN_SIZE, START_SIZE)
        self.assertEqual(rect, (0, 0, 1250, 875))

    def test_saved_size(self):
        self.assertEqual(saved_size({"x": 0, "y": 0, "width": 1000, "height": 700}), (1000, 700))
        self.assertIsNone(saved_size(None))
        self.assertIsNone(saved_size({"width": 1000}))


DESK = Monitor((0, 0, 2560, 1392), 1.0, True)           # 2560 x 1440 at 100 %: every step fits
PANEL = Monitor((2560, 0, 4480, 1032), 1.25, False)     # 1920 x 1080 at 125 % to its right: 100 and 125 fit


def inside(rect, work):
    x, y, w, h = rect
    return x >= work[0] and y >= work[1] and x + w <= work[2] and y + h <= work[3]


class StartLayoutTest(unittest.TestCase):
    def test_stored_step_that_fits_is_applied(self):
        step, rect, monitor = start_layout(None, [DESK, PANEL], 175, SCALE_STEPS, MIN_SIZE, START_SIZE)
        self.assertEqual((step, monitor), (175, DESK))
        self.assertEqual(rect[2:], (2065, 1365))  # the start size at 175 %

    def test_a_small_monitor_applies_the_largest_step_that_fits(self):
        saved = {"x": 2700, "y": 50, "width": 1180, "height": 780}
        step, rect, monitor = start_layout(saved, [DESK, PANEL], 175, SCALE_STEPS, MIN_SIZE, START_SIZE)
        self.assertEqual((step, monitor), (125, PANEL))
        self.assertTrue(inside(rect, PANEL.work))
        minimum = client_px(MIN_SIZE, 1.25, 1.25)
        self.assertTrue(rect[2] >= minimum[0] and rect[3] >= minimum[1], rect)

    def test_the_monitor_alone_after_the_desk_is_gone(self):
        saved = {"x": 300, "y": 100, "width": 1180, "height": 780}  # stored on the desk monitor, which is unplugged
        step, rect, monitor = start_layout(saved, [PANEL], 175, SCALE_STEPS, MIN_SIZE, START_SIZE)
        self.assertEqual((step, monitor), (125, PANEL))
        self.assertTrue(inside(rect, PANEL.work))


class SessionLayoutTest(unittest.TestCase):
    """The step and rect of a running window after a move to another monitor, a DPI, display or work-area change."""

    def test_move_to_a_smaller_monitor_lowers_the_step_and_fits_the_window(self):
        # Scale 175 on the desk monitor, dragged to the panel: WM_DPICHANGED keeps the layout size, so the page area
        # arrives as 2581 x 1706 physical, larger than the panel's 1920 x 1032 work area
        arrived = (2700, 50) + dpi_changed_size((2065, 1365), 1.0, 1.25)
        step, rect = session_layout(175, 175, arrived, PANEL, SCALE_STEPS, MIN_SIZE, fit_place=False)
        self.assertEqual(step, 125)
        self.assertTrue(inside(rect, PANEL.work), rect)
        self.assertGreaterEqual(rect[2], client_px(MIN_SIZE, 1.25, 1.25)[0])
        self.assertGreaterEqual(rect[3], client_px(MIN_SIZE, 1.25, 1.25)[1])

    def test_back_on_the_large_monitor_the_stored_step_returns(self):
        on_panel = (2700, 0, 1844, 1032)
        back = (300, 100) + dpi_changed_size(on_panel[2:], 1.25, 1.0)
        step, rect = session_layout(175, 125, back, DESK, SCALE_STEPS, MIN_SIZE, fit_place=False)
        self.assertEqual(step, 175)
        self.assertTrue(inside(rect, DESK.work), rect)
        self.assertEqual(layout_size(rect[2:], 1.75, 1.0), layout_size(back[2:], 1.25, 1.0))

    def test_a_resolution_change_on_the_same_monitor(self):
        smaller = Monitor((0, 0, 1920, 1032), 1.0, True)
        step, rect = session_layout(175, 175, (300, 100, 2065, 1365), smaller, SCALE_STEPS, MIN_SIZE)
        self.assertEqual(step, 150)
        self.assertTrue(inside(rect, smaller.work), rect)

    def test_a_work_area_change_pulls_the_title_bar_back_on_screen(self):
        taskbar_top = Monitor((0, 48, 2560, 1440), 1.0, True)
        step, rect = session_layout(100, 100, (200, 10, 1180, 780), taskbar_top, SCALE_STEPS, MIN_SIZE)
        self.assertEqual((step, rect), (100, (200, 48, 1180, 780)))

    def test_the_users_own_move_on_one_monitor_keeps_its_place(self):
        partly_off = (2300, 900, 1180, 780)  # dragged past the right and bottom edges on purpose
        self.assertEqual(session_layout(100, 100, partly_off, DESK, SCALE_STEPS, MIN_SIZE, fit_place=False),
                         (100, partly_off))
        self.assertEqual(session_layout(100, 100, partly_off, DESK, SCALE_STEPS, MIN_SIZE, fit_place=True),
                         (100, (1380, 612, 1180, 780)))

    def test_no_monitor_information_changes_nothing(self):
        self.assertEqual(session_layout(175, 175, (0, 0, 10, 10), None, SCALE_STEPS, MIN_SIZE), (175, (0, 0, 10, 10)))


class _User32Stub:
    """The user32 calls the window procedure makes, with a fake original procedure that counts its calls."""

    def __init__(self, original=None):
        self.calls = 0
        self.posted = []
        self.iconic_error = False
        self.original = original or (lambda msg, wparam, lparam: 7)

    def CallWindowProcW(self, prev, hwnd, msg, wparam, lparam):
        self.calls += 1
        return self.original(msg, wparam, lparam)

    def IsIconic(self, hwnd):
        if self.iconic_error:
            raise OSError("the window is gone")
        return 0

    def IsZoomed(self, hwnd):
        return 0

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.posted.append((msg, wparam))
        return 1

    def GetDpiForWindow(self, hwnd):
        return 96

    def GetClientRect(self, hwnd, rect):
        return 0

    def SetWindowPos(self, *args):
        return 1


@unittest.skipUnless(winutil.IS_WINDOWS, "the window procedure hook is Windows only")
class WindowProcedureTest(unittest.TestCase):
    def setUp(self):
        self.user32 = _User32Stub()
        patcher = mock.patch.object(winutil, "_user32", self.user32)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        self.hook = winutil.ResizableHook(1, MIN_SIZE, on_settled=lambda: self.events.append("settled"),
                                          on_dpi=lambda: self.events.append("dpi"),
                                          on_display=lambda: self.events.append("display"))
        self.hook._prev_proc = 1
        self.hook.nc = (8, 0, 8, 8)

    def send(self, msg, wparam=0, lparam=0):
        return self.hook._wnd_proc(1, msg, wparam, lparam)

    def test_the_minimum_track_size_follows_the_scale(self):
        info = winutil._MINMAXINFO()
        self.user32.original = lambda *a: info.ptMinTrackSize.__setattr__("x", 1) or 0
        self.hook.zoom = 1.75
        self.assertEqual(self.send(winutil.WM_GETMINMAXINFO, 0, ctypes.addressof(info)), 0)
        self.assertEqual((info.ptMinTrackSize.x, info.ptMinTrackSize.y), (1645 + 16, 1120 + 8))
        self.assertEqual(self.user32.calls, 1)

    def test_maximize_is_swallowed_and_a_maximized_size_restored(self):
        self.assertEqual(self.send(winutil.WM_SYSCOMMAND, winutil.SC_MAXIMIZE + 2), 0)  # low bits: the source
        self.assertEqual(self.user32.calls, 0)
        self.send(winutil.WM_SYSCOMMAND, winutil.SC_RESTORE)
        self.assertEqual(self.user32.calls, 1)
        self.send(winutil.WM_SIZE, winutil.SIZE_MAXIMIZED)
        self.assertEqual(self.user32.posted, [(winutil.WM_SYSCOMMAND, winutil.SC_RESTORE)])

    def test_the_page_owns_the_top_edge(self):
        params = winutil._NCCALCSIZE_PARAMS()
        params.rgrc[0].left, params.rgrc[0].top = 100, 50

        def original(msg, wparam, lparam):
            params.rgrc[0].left += 8
            params.rgrc[0].top += 31
            return 0

        self.user32.original = original
        self.send(winutil.WM_NCCALCSIZE, 1, ctypes.addressof(params))
        self.assertEqual((params.rgrc[0].left, params.rgrc[0].top), (108, 50))
        self.assertEqual(self.user32.calls, 1)

    def test_the_style_keeps_the_frame_and_drops_maximize(self):
        style = winutil._STYLESTRUCT(0, winutil.WS_MAXIMIZEBOX)
        gwl_style = ctypes.c_size_t(winutil.GWL_STYLE).value  # WPARAM is unsigned
        self.send(winutil.WM_STYLECHANGING, gwl_style, ctypes.addressof(style))
        self.assertEqual(style.styleNew, winutil.WS_THICKFRAME | winutil.WS_MINIMIZEBOX)

    def test_the_original_procedure_runs_once_when_a_handler_fails(self):
        pos = winutil._WINDOWPOS(None, None, 10, 10, 1200, 800, 0)
        self.user32.iconic_error = True
        with self.assertLogs("mediasaver", "DEBUG"):
            self.assertEqual(self.send(winutil.WM_WINDOWPOSCHANGED, 0, ctypes.addressof(pos)), 7)
        self.assertEqual(self.user32.calls, 1)
        for msg in (winutil.WM_NCDESTROY, winutil.WM_EXITSIZEMOVE, 0x0007):  # 0x0007: a message the hook passes on
            self.user32.calls = 0
            self.send(msg)
            self.assertEqual(self.user32.calls, 1, hex(msg))

    def test_display_and_work_area_changes_reach_the_frame(self):
        self.send(winutil.WM_DISPLAYCHANGE, 32, 0)
        self.send(winutil.WM_SETTINGCHANGE, winutil.SPI_SETWORKAREA, 0)
        self.send(winutil.WM_SETTINGCHANGE, 0x0068, 0)  # another setting
        self.assertEqual(self.events, ["display", "display"])
        self.assertEqual(self.user32.calls, 3)

    def test_a_dpi_change_is_handled_here(self):
        suggested = ctypes.wintypes.RECT(2600, 40, 4000, 1000)
        self.assertEqual(self.send(winutil.WM_DPICHANGED, 120 | (120 << 16), ctypes.addressof(suggested)), 0)
        self.assertEqual((self.hook.scale, self.events, self.user32.calls), (1.25, ["dpi"], 0))

    def test_the_end_of_a_move_settles(self):
        self.send(winutil.WM_ENTERSIZEMOVE)
        self.assertTrue(self.hook.is_sizing())
        self.send(winutil.WM_EXITSIZEMOVE)
        self.assertFalse(self.hook.is_sizing())
        self.assertEqual(self.events, ["settled"])


class CorrectedWindowSizeTest(unittest.TestCase):
    def test_restore_from_minimized_keeps_the_size(self):
        self.assertEqual(corrected_window_size((1180, 780), (1196, 788), (16, 8)), (1196, 788))

    def test_any_other_size_is_kept(self):
        self.assertEqual(corrected_window_size((1300, 800), (1196, 788), (16, 8)), (1300, 800))
        self.assertEqual(corrected_window_size((1180, 780), None, (16, 8)), (1180, 780))


class DpiChangeTest(unittest.TestCase):
    """Bug 2.7: a move to a monitor of another DPI keeps the layout size."""

    def test_page_area_follows_the_dpi_ratio(self):
        self.assertEqual(dpi_changed_size((1180, 780), 1.0, 1.5), (1770, 1170))
        self.assertEqual(dpi_changed_size((1770, 1170), 1.5, 1.0), (1180, 780))

    def test_layout_size_is_unchanged(self):
        before = layout_size((1475, 975), 1.25, 1.0)
        after = layout_size(dpi_changed_size((1475, 975), 1.0, 1.75), 1.25, 1.75)
        self.assertEqual(before, after)


class CenteredRectTest(unittest.TestCase):
    def test_centred_on_the_work_area(self):
        self.assertEqual(centered_rect((1000, 600), (0, 0, 1920, 1040)), (460, 220, 1000, 600))

    def test_offset_work_area(self):
        self.assertEqual(centered_rect((1000, 600), (1920, 40, 3840, 1080)), (2380, 260, 1000, 600))

    def test_oversized_window_stays_at_the_top_left(self):
        self.assertEqual(centered_rect((2000, 1200), (0, 0, 1366, 728)), (0, 0, 2000, 1200))

    def test_unknown_work_area(self):
        self.assertEqual(centered_rect((1180, 780), None), (0, 0, 1180, 780))


class ColorrefTest(unittest.TestCase):
    def test_hex_to_colorref(self):
        self.assertEqual(colorref("#0b0d14"), 0x140D0B)
        self.assertEqual(colorref("ff0000"), 0x0000FF)

    def test_bad_colour(self):
        with self.assertRaises(ValueError):
            colorref("#fff")


if __name__ == "__main__":
    unittest.main()
