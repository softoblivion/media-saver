import unittest

from app.winutil import corrected_window_size


class CorrectedWindowSizeTest(unittest.TestCase):
    NC = (14, 7)
    NORMAL = (1194, 787)

    def test_border_loss_is_undone(self):
        # WinForms re-applies the stored client size (window minus border) as the window size after a restore
        self.assertEqual(corrected_window_size((1180, 780), self.NORMAL, self.NC), self.NORMAL)

    def test_other_sizes_pass_through(self):
        for proposed in ((1194, 787), (2576, 1408), (1000, 700), (1180, 787), (1194, 780)):
            with self.subTest(proposed=proposed):
                self.assertEqual(corrected_window_size(proposed, self.NORMAL, self.NC), proposed)

    def test_no_recorded_size(self):
        self.assertEqual(corrected_window_size((1180, 780), None, self.NC), (1180, 780))

    def test_zero_border_is_identity(self):
        self.assertEqual(corrected_window_size((1194, 787), self.NORMAL, (0, 0)), self.NORMAL)
        self.assertEqual(corrected_window_size((900, 600), self.NORMAL, (0, 0)), (900, 600))

    def test_repeated_restores_converge(self):
        # the shrink does not accumulate: every proposal of (normal - border) comes back as normal
        size = self.NORMAL
        for _ in range(5):
            proposed = (size[0] - self.NC[0], size[1] - self.NC[1])
            size = corrected_window_size(proposed, self.NORMAL, self.NC)
        self.assertEqual(size, self.NORMAL)


if __name__ == "__main__":
    unittest.main()
