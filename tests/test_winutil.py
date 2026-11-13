"""Pure parts of the resizable window: layout / physical units, fitting into a work area, the Scale arithmetic, the
restore of the stored place and size, the Scale steps that fit a monitor. The window-procedure hook itself needs a
real window (tools: the window driver of docs/BUILD.md section 5)."""

import unittest

from app.core.settings import SCALE_STEPS
from app.main import MIN_SIZE, START_SIZE
from app.winutil import (Monitor, allowed_scale, centered_rect, client_px, colorref, corrected_window_size,
                         dpi_changed_size, fit_rect, fits, fitting_scales, layout_size, restore_rect, saved_size,
                         scaled_rect)

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
