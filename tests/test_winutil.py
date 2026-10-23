"""Pure parts of the fixed-size window: the size ladder pick and the centring. The hook itself needs a real window
(probed by hand, docs/BUILD.md section 5)."""

import unittest

from app.main import MIN_SIZE, SIZE_LADDER, START_SIZE
from app.winutil import centered_rect, colorref, pick_window_size, placed_rect


class SizeLadderTest(unittest.TestCase):
    def test_ladder_runs_from_start_to_minimum(self):
        self.assertEqual(SIZE_LADDER[0], START_SIZE)
        self.assertEqual(SIZE_LADDER[-1], MIN_SIZE)
        widths = [w for w, _ in SIZE_LADDER]
        heights = [h for _, h in SIZE_LADDER]
        self.assertEqual(widths, sorted(widths, reverse=True))
        self.assertEqual(heights, sorted(heights, reverse=True))

    def test_start_size_when_the_work_area_is_large(self):
        for work in ((1920, 1040), (2560, 1400), (1180, 780)):
            with self.subTest(work=work):
                self.assertEqual(pick_window_size(SIZE_LADDER, work), (1180, 780))

    def test_steps_down_on_a_small_work_area(self):
        # 1366x768 laptop: the taskbar leaves about 728 px of height
        self.assertEqual(pick_window_size(SIZE_LADDER, (1366, 728)), (1060, 710))
        self.assertEqual(pick_window_size(SIZE_LADDER, (1120, 780)), (1120, 745))
        self.assertEqual(pick_window_size(SIZE_LADDER, (1180, 744)), (1060, 710))

    def test_never_below_the_minimum(self):
        for work in ((800, 500), (940, 639), (100, 100), (0, 0)):
            with self.subTest(work=work):
                self.assertEqual(pick_window_size(SIZE_LADDER, work), MIN_SIZE)

    def test_unknown_work_area_gives_the_start_size(self):
        self.assertEqual(pick_window_size(SIZE_LADDER, None), START_SIZE)

    def test_empty_ladder_is_an_error(self):
        with self.assertRaises(ValueError):
            pick_window_size((), (1920, 1080))


class ScaledLadderTest(unittest.TestCase):
    """The Scale setting multiplies the window size: the ladder step is chosen for the scaled size."""

    def test_scaled_size_must_fit(self):
        self.assertEqual(pick_window_size(SIZE_LADDER, (1920, 1032), 1.25), (1180, 780))   # 1475 x 975 fits
        self.assertEqual(pick_window_size(SIZE_LADDER, (1920, 1032), 1.5), (1000, 675))    # 1500 x 1013 fits
        self.assertEqual(pick_window_size(SIZE_LADDER, (2560, 1400), 1.5), (1180, 780))    # 1770 x 1170 fits
        self.assertEqual(pick_window_size(SIZE_LADDER, (1366, 728), 1.25), (940, 640))     # nothing fits: the smallest

    def test_smallest_step_when_nothing_fits_at_the_scale(self):
        self.assertEqual(pick_window_size(SIZE_LADDER, (1920, 1032), 2.0), (940, 640))
        self.assertEqual(pick_window_size(SIZE_LADDER, None, 2.0), (1180, 780))


class PlacedRectTest(unittest.TestCase):
    def test_keeps_the_corner_when_the_window_still_fits(self):
        self.assertEqual(placed_rect((1180, 780), (300, 120), (0, 0, 1920, 1040)), (300, 120, 1180, 780))

    def test_moves_back_inside_the_work_area(self):
        self.assertEqual(placed_rect((1770, 1170), (300, 120), (0, 0, 1920, 1040)), (150, 0, 1770, 1170))
        self.assertEqual(placed_rect((1770, 1170), (2200, 60), (1920, 0, 3840, 1040)), (2070, 0, 1770, 1170))

    def test_centres_without_a_corner(self):
        self.assertEqual(placed_rect((1000, 600), None, (0, 0, 1920, 1040)), centered_rect((1000, 600), (0, 0, 1920, 1040)))


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
