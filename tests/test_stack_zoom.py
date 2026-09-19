"""Image navigation regressions using synthetic pixels, with no Leica reads."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
    from PySide6.QtGui import QMouseEvent, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None
else:
    from desktop.stack_dialog import SectionView, StackReviewDialog


def wheel(widget, position, steps=1):
    position = QPointF(position)
    event = QWheelEvent(position, QPointF(widget.mapToGlobal(position.toPoint())),
                        QPoint(), QPoint(0, round(120 * steps)), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()


def mouse(widget, kind, position, button, buttons):
    position = QPointF(position)
    event = QMouseEvent(kind, position, QPointF(widget.mapToGlobal(position.toPoint())),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def drag(widget, start, end, button=Qt.MouseButton.LeftButton if QApplication else None):
    mouse(widget, QEvent.Type.MouseButtonPress, start, button, button)
    mouse(widget, QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, button)
    mouse(widget, QEvent.Type.MouseButtonRelease, end, button, Qt.MouseButton.NoButton)
    QApplication.processEvents()


def mapped_point(widget, position):
    """Independent coordinate calculation including the physical image aspect."""
    fit_width = min(widget.width(), widget.height() * widget.aspect)
    fit_height = fit_width / widget.aspect
    return (widget.view_center[0] + (position.x() - widget.width() / 2) / (fit_width * widget.zoom_factor),
            widget.view_center[1] + (position.y() - widget.height() / 2) / (fit_height * widget.zoom_factor))


@unittest.skipIf(QApplication is None, "Requires desktop dependencies")
class SectionZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = SectionView()
        self.view.resize(400, 300)
        self.view.set_frame(np.zeros((200, 200, 3), dtype=np.uint8))
        self.view.show()
        self.app.processEvents()
        self.picks = []
        self.view.picked.connect(lambda x, y: self.picks.append((x, y)))

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def test_wheel_zooms_at_cursor_without_changing_selection(self):
        position = QPointF(250, 180)
        anchor = mapped_point(self.view, position)
        wheel(self.view, position, 2)
        self.assertGreater(self.view.zoom_factor, 1)
        np.testing.assert_allclose(mapped_point(self.view, position), anchor, atol=1e-10)
        self.assertEqual(self.picks, [])
        wheel(self.view, position, -2)
        self.assertAlmostEqual(self.view.zoom_factor, 1)
        np.testing.assert_allclose(self.view.view_center, (.5, .5))

    def test_click_after_zoom_maps_to_original_frame_with_physical_aspect(self):
        self.view.set_frame(np.zeros((100, 200, 3), dtype=np.uint8), aspect=2.5)
        self.view.set_viewport(4, (.6, .4))
        position = QPoint(240, 130)
        expected = mapped_point(self.view, QPointF(position))
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(len(self.picks), 1)
        np.testing.assert_allclose(self.picks[0], expected, atol=1e-10)

    def test_all_drag_buttons_pan_without_picking(self):
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            with self.subTest(button=button):
                self.view.set_viewport(3, (.5, .5))
                drag(self.view, QPointF(180, 140), QPointF(216, 158), button)
                np.testing.assert_allclose(self.view.view_center, (.5 - 36 / 900, .5 - 18 / 900))
                self.assertEqual(self.picks, [])

    def test_letterbox_click_does_not_select_image(self):
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=QPoint(10, 150))
        self.assertEqual(self.picks, [])

    def test_frame_replacement_preserves_zoom_and_source_roi_click_mapping(self):
        self.view.set_viewport(4, (.6, .4))
        before = (self.view.zoom_factor, self.view.view_center)
        self.view.set_frame(np.ones((50, 50, 3), dtype=np.uint8), aspect=1,
                            source_rect=QRectF(.475, .275, .25, .25))
        self.assertEqual((self.view.zoom_factor, self.view.view_center), before)
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
        np.testing.assert_allclose(self.picks[0], (.6, .4))

    def test_reset_fits_and_zoom_is_bounded(self):
        wheel(self.view, QPointF(200, 150), 80)
        self.assertLessEqual(self.view.zoom_factor, 64)
        self.assertGreater(self.view.zoom_factor, 1)
        self.view.reset_view()
        self.assertEqual(self.view.zoom_factor, 1)
        np.testing.assert_allclose(self.view.view_center, (.5, .5))
        self.assertEqual(self.picks, [])
        wheel(self.view, QPointF(200, 150), -80)
        self.assertEqual(self.view.zoom_factor, 1)


@unittest.skipIf(QApplication is None, "Requires desktop dependencies")
class StackZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.info = {"paths": {}, "shape_zyx": [5, 128, 160], "spacing_um": [2, .5, .5],
                     "z_positions_um": [0, 2, 4, 6, 8], "z_indices": list(range(5)),
                     "display": {"green": [0, 4095], "red": [0, 4095]},
                     "field_id": "synthetic_zoom", "cache_key": "synthetic_zoom_cache",
                     "source": {"path": "synthetic_zoom.lof"}, "selection": {"mode": "max"}}
        self._write_arrays(self.info)
        self.dialog = StackReviewDialog(config={}, output_base=self.root, cache_dir=self.root / "cache")
        self.dialog.fields.blockSignals(True)
        self.dialog.fields.addItem(self.info["field_id"])
        self.dialog.fields.blockSignals(False)
        self.dialog.accept_stack_info(self.info)
        self.dialog.show()
        self.app.processEvents()

    def _write_arrays(self, info):
        for role in ("green", "red"):
            path = self.root / (role + "_" + str(info["shape_zyx"][2]) + ".npy")
            array = np.zeros(info["shape_zyx"], dtype=np.uint16)
            # A single pixel deliberately absent from the full-field downsample.
            array[:, info["shape_zyx"][1] // 2 + 1, info["shape_zyx"][2] // 2 + 1] = 4095
            np.save(path, array)
            info["paths"][role] = str(path)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_xy_wheel_and_pan_synchronize_channels_without_changing_review_target(self):
        self.dialog._pick_xy(.5, .5)
        original_target = self.dialog._review_target
        self.dialog.note.setText("Keep the selected location while inspecting")
        original_reviews = deepcopy(self.dialog.reviews)
        view = self.dialog.views["green"]
        center = QPointF(view.width() / 2, view.height() / 2)
        wheel(view, center, 4)
        drag(view, center, center + QPointF(30, 10))
        for role in ("green", "red", "merged"):
            for collection in (self.dialog.views, self.dialog.analysis_views):
                self.assertEqual(collection[role].zoom_factor, view.zoom_factor)
                np.testing.assert_allclose(collection[role].view_center, view.view_center)
        self.assertEqual(self.dialog.views["xz"].zoom_factor, 1)
        self.assertEqual(self.dialog._review_target, original_target)
        self.assertEqual(self.dialog.reviews, original_reviews)

    def test_navigation_preserves_zoom_and_never_reloads_or_resegments(self):
        view = self.dialog.views["merged"]
        arrays = dict(self.dialog.arrays)
        profiles = self.dialog.profile.profiles
        with patch("numpy.load", side_effect=AssertionError("Navigation must reuse open arrays")), \
             patch("desktop.stack_source.prepare_stack", side_effect=AssertionError("Navigation must not reload Leica")), \
             patch("desktop.volume_analysis.analyze_volume", side_effect=AssertionError("Navigation must not resegment")):
            wheel(view, QPointF(view.width() / 2, view.height() / 2), 4)
            self.app.processEvents()
            viewport = (view.zoom_factor, view.view_center)
            self.dialog.z_slider.setValue(1)
            self.dialog.z_slider.setValue(4)
            self.app.processEvents()
            self.assertEqual((view.zoom_factor, view.view_center), viewport)
        self.assertIs(self.dialog.profile.profiles, profiles)
        for role, array in arrays.items():
            self.assertIs(self.dialog.arrays[role], array)
        self.assertIsNone(self.dialog.process)

    def test_fit_views_restores_full_field_and_side_sections(self):
        self.dialog.zoom.setCurrentIndex(3)
        for view in [*self.dialog.views.values(), *self.dialog.analysis_views.values()]:
            view.set_viewport(4, (.6, .4))
        self.dialog.fit_views()
        self.assertIsNone(self.dialog.zoom.currentData())
        self.assertEqual(self.dialog._view_bounds, (0, 0, 160, 128))
        for view in [*self.dialog.views.values(), *self.dialog.analysis_views.values()]:
            self.assertEqual(view.zoom_factor, 1)
            np.testing.assert_allclose(view.view_center, (.5, .5))

    def test_click_in_panned_crop_keeps_selected_cell_visible(self):
        self.dialog._pick_xy(.5, .5)
        self.dialog.zoom.setCurrentIndex(3)
        xy = self.dialog.views["green"]
        xy.set_viewport(4, (.75, .75), emit=True)
        xz = self.dialog.views["xz"]
        xz.set_viewport(3, (.6, .4), emit=True)
        side_viewport = (xz.zoom_factor, xz.view_center)
        center = QPointF(xy.width() / 2, xy.height() / 2)
        mouse(xy, QEvent.Type.MouseButtonPress, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        mouse(xy, QEvent.Type.MouseButtonRelease, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self.assertEqual((self.dialog.x, self.dialog.y), (96, 80))
        self.assertEqual(self.dialog._view_bounds, (64, 48, 64, 64))
        self.assertEqual(xy.zoom_factor, 4)
        for role in ("green", "red", "merged"):
            for collection in (self.dialog.views, self.dialog.analysis_views):
                pane = collection[role]
                self.assertTrue(pane.visible_rect().contains(*pane.crosshair))
        self.assertEqual((xz.zoom_factor, xz.view_center), side_viewport)

    def test_side_section_zoom_clicks_keep_physical_coordinate_mapping(self):
        self.dialog._pick_xy(.5, .5)
        xz = self.dialog.views["xz"]
        self.assertAlmostEqual(xz.aspect, 160 * .5 / (5 * 2))
        xz.set_viewport(4, (.6, .4), emit=True)
        center = QPointF(xz.width() / 2, xz.height() / 2)
        mouse(xz, QEvent.Type.MouseButtonPress, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        mouse(xz, QEvent.Type.MouseButtonRelease, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self.assertEqual((self.dialog.x, self.dialog.y, self.dialog.z_slider.value()), (96, 64, 2))
        self.assertEqual((xz.zoom_factor, xz.view_center), (4, (.6, .4)))
        self.assertEqual(self.dialog.views["green"].zoom_factor, 1)

        yz = self.dialog.views["yz"]
        self.assertAlmostEqual(yz.aspect, 128 * .5 / (5 * 2))
        yz.set_viewport(2, (.35, .6), emit=True)
        center = QPointF(yz.width() / 2, yz.height() / 2)
        mouse(yz, QEvent.Type.MouseButtonPress, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        mouse(yz, QEvent.Type.MouseButtonRelease, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self.assertEqual((self.dialog.x, self.dialog.y, self.dialog.z_slider.value()), (96, 44, 3))
        self.assertEqual((yz.zoom_factor, yz.view_center), (2, (.35, .6)))

    def test_zoom_fetches_native_pixels_instead_of_enlarging_downsample(self):
        info = deepcopy(self.info)
        info["shape_zyx"] = [5, 2048, 2048]
        self._write_arrays(info)
        self.dialog.accept_stack_info(info)
        view = self.dialog.views["green"]
        self.assertLessEqual(view.pixmap.width(), 600)
        view.set_viewport(4, (.5, .5), emit=True)
        self.app.processEvents()
        # At 4x the 512-pixel native ROI fits the render budget. The bright
        # pixel at (1025, 1025) must survive even though full-field stride misses it.
        self.assertGreaterEqual(view.pixmap.width(), 512)
        self.assertLessEqual(view.pixmap.width(), 600)
        image = view.pixmap.toImage()
        found = any(image.pixelColor(x, y).green() == 255
                    for y in range(image.height() // 2 - 3, image.height() // 2 + 4)
                    for x in range(image.width() // 2 - 3, image.width() // 2 + 4))
        self.assertTrue(found, "Zoom must display original microscopy pixels")


if __name__ == "__main__":
    unittest.main()
