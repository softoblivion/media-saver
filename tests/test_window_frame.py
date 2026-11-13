"""The window frame's Scale over monitors (app.main.WindowFrame with a fake hook, window and monitors): the stored
step is the user's choice and is never rewritten by a screen that cannot hold it; the applied step is the largest one
that fits the monitor the window is on, decided at start, at the end of a move, after a DPI change and after a display
or work-area change; the window is then fitted into the work area."""

import unittest
from unittest import mock

from app import main, winutil
from app.winutil import Monitor, client_px, dpi_changed_size, layout_size

DESK = Monitor((0, 0, 2560, 1392), 1.0, True)           # 2560 x 1440 at 100 %: every step fits
PANEL = Monitor((2560, 0, 4480, 1032), 1.25, False)     # 1920 x 1080 at 125 %: 100 and 125 fit


def inside(rect, work):
    x, y, w, h = rect
    return x >= work[0] and y >= work[1] and x + w <= work[2] and y + h <= work[3]


class FakeHook:
    def __init__(self, scale=1.0):
        self.hwnd = 1
        self.zoom = 1.0
        self.scale = scale
        self.nc = (8, 0, 8, 8)
        self.rect = None
        self.sizing = False
        self.on_settled = self.on_dpi = self.on_display = None

    def client_rect(self):
        return self.rect

    def window_rect_for(self, rect):
        x, y, w, h = rect
        return x - 8, y, w + 16, h + 8

    def min_client_px(self):
        return client_px(main.MIN_SIZE, self.zoom, self.scale)

    def set_client_rect(self, rect, apply=None):
        self.rect = tuple(rect)
        if apply is not None:
            apply(*self.window_rect_for(rect))

    def is_normal(self):
        return True

    def is_sizing(self):
        return self.sizing


class FakeNative:
    InvokeRequired = False

    def __init__(self):
        self.webview = type("WebView", (), {"ZoomFactor": 1.0})()
        self.bounds = []

    def SetBounds(self, *bounds):
        self.bounds.append(bounds)


class FakeEngine:
    def __init__(self):
        self.patches = []

    def set_settings(self, patch):
        self.patches.append(patch)


class FakeBridge:
    def __init__(self):
        self.states = []

    def _on_window_state(self, **state):
        self.states.append(state)


class WindowFrameScaleTest(unittest.TestCase):
    def setUp(self):
        self.monitors = [DESK, PANEL]
        self.on = DESK  # the monitor window_monitor() reports
        for name, fake in (("list_monitors", lambda: list(self.monitors)), ("window_monitor", lambda hwnd: self.on)):
            patcher = mock.patch.object(winutil, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.hook = FakeHook()
        self.window = type("Window", (), {})()
        self.window.native = FakeNative()
        self.engine = FakeEngine()
        self.bridge = FakeBridge()
        self.frame = main.WindowFrame(self.window, self.hook, self.engine, self.bridge)

    def tearDown(self):
        self.frame.save_now()  # stops the geometry timer

    def drag_to(self, monitor, x, y):
        """A drag that ends on `monitor`: WM_DPICHANGED keeps the layout size, WM_EXITSIZEMOVE settles."""
        old = self.hook.scale
        self.hook.scale = monitor.scale
        self.hook.rect = (x, y) + dpi_changed_size(self.hook.rect[2:], old, monitor.scale)
        self.on = monitor
        self.frame._on_settled()

    def stored_scales(self):
        return [p["ui_scale"] for p in self.engine.patches if "ui_scale" in p]

    def test_move_to_a_small_monitor_and_back(self):
        self.frame.restore(None, 175)
        self.assertEqual((self.frame.percent, self.window.native.webview.ZoomFactor), (175, 1.75))
        page = layout_size(self.hook.rect[2:], 1.75, 1.0)
        self.drag_to(PANEL, 2700, 50)
        self.assertEqual((self.frame.stored, self.frame.percent), (175, 125))
        self.assertEqual(self.window.native.webview.ZoomFactor, 1.25)
        self.assertTrue(inside(self.hook.rect, PANEL.work), self.hook.rect)
        self.assertEqual(self.bridge.states[-1], {"scales": [100, 125], "scale": 125})
        self.drag_to(DESK, 300, 100)
        self.assertEqual(self.frame.percent, 175)
        self.assertTrue(inside(self.hook.rect, DESK.work), self.hook.rect)
        self.assertEqual(self.bridge.states[-1], {"scales": [100, 125, 150, 175, 200], "scale": 175})
        self.assertLessEqual(layout_size(self.hook.rect[2:], 1.75, 1.0)[1], page[1])
        self.frame.save_now()
        self.assertEqual(self.stored_scales(), [])
        self.assertTrue(all(set(p) == {"window"} for p in self.engine.patches))

    def test_a_start_on_the_small_monitor_leaves_the_stored_step(self):
        self.monitors = [PANEL]
        self.on = PANEL
        self.hook.scale = 1.25
        self.frame.restore({"x": 300, "y": 100, "width": 1180, "height": 780}, 175)
        self.assertEqual((self.frame.stored, self.frame.percent), (175, 125))
        self.assertTrue(inside(self.hook.rect, PANEL.work), self.hook.rect)
        self.frame.save_now()
        self.assertEqual(self.stored_scales(), [])

    def test_a_display_change_fits_the_window_at_once(self):
        self.frame.restore(None, 175)
        smaller = Monitor((0, 0, 1920, 1032), 1.0, True)  # the resolution of the same monitor was lowered
        self.monitors, self.on = [smaller], smaller
        self.frame._after_display()
        self.assertEqual((self.frame.stored, self.frame.percent), (175, 150))
        self.assertTrue(inside(self.hook.rect, smaller.work), self.hook.rect)

    def test_a_dpi_change_during_a_drag_waits_for_its_end(self):
        self.frame.restore(None, 175)
        self.hook.sizing = True
        self.hook.scale, self.on = PANEL.scale, PANEL
        self.hook.rect = (2700, 50) + dpi_changed_size(self.hook.rect[2:], 1.0, 1.25)
        self.frame._on_dpi()
        self.assertEqual(self.frame.percent, 175)  # still dragging: nothing is decided yet
        self.hook.sizing = False
        self.frame._on_settled()
        self.assertEqual(self.frame.percent, 125)
        self.assertTrue(inside(self.hook.rect, PANEL.work))

    def test_a_scale_change_by_the_user_is_stored_and_applied(self):
        self.frame.restore(None, 100)
        self.frame.apply_scale(150)
        self.assertEqual((self.frame.stored, self.frame.percent, self.window.native.webview.ZoomFactor),
                         (150, 150, 1.5))
        self.assertEqual(self.bridge.states[-1]["scale"], 150)


if __name__ == "__main__":
    unittest.main()
