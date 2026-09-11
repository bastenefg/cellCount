"""Rendering and interaction checks on synthetic image and mask fixtures."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem

from desktop.segmentation_view import SegmentationCanvas, render_segmentation


class RenderingTests(unittest.TestCase):
    def test_linear_contrast_and_each_mode_leave_source_arrays_intact(self):
        image = np.array([[0, 10, 20], [30, 40, 50]], dtype=np.uint16)
        labels = np.array([[0, 1, 1], [0, 2, 2]], dtype=np.int32)
        original_image, original_labels = image.copy(), labels.copy()
        rendered = render_segmentation(image, labels, 10, 40, "none")
        np.testing.assert_array_equal(rendered[:, :, 0], [[0, 0, 85], [170, 255, 255]])
        for mode in ("outlines", "filled", "labels", "none"):
            render_segmentation(image, labels, 10, 40, mode)
        np.testing.assert_array_equal(image, original_image)
        np.testing.assert_array_equal(labels, original_labels)

    def test_touching_cells_keep_a_visible_boundary_and_exact_pixel_location(self):
        image = np.zeros((10, 12))
        labels = np.zeros(image.shape, dtype=np.int32)
        labels[2:8, 2:6] = 5
        labels[2:8, 6:10] = 999999
        rgb = render_segmentation(image, labels, 0, 100, "outlines")
        np.testing.assert_array_equal(rgb[4, 5], [0, 237, 255])
        np.testing.assert_array_equal(rgb[4, 6], [0, 237, 255])
        np.testing.assert_array_equal(rgb[4, 3], [0, 0, 0])
        np.testing.assert_array_equal(rgb[1, 5], [0, 0, 0])
        labels_rgb = render_segmentation(image, labels, 0, 100, "labels")
        self.assertFalse(np.array_equal(labels_rgb[4, 4], labels_rgb[4, 7]))
        np.testing.assert_array_equal(labels_rgb[0, 0], [0, 0, 0])

    def test_bad_overlay_shape_is_rejected_instead_of_misaligned(self):
        with self.assertRaises(ValueError):
            render_segmentation(np.zeros((10, 10)), np.zeros((9, 10), dtype=int), 0, 1)


class CanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.image = np.arange(256 * 256, dtype=np.uint16).reshape(256, 256)
        self.labels = np.zeros((256, 256), dtype=np.int32)
        self.labels[100:160, 100:160] = 7
        self.canvas = SegmentationCanvas()
        self.canvas.resize(420, 420)
        self.canvas.show()
        self.canvas.set_image(self.image, self.labels, [], 0, 65535)
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()

    def test_view_is_retained_when_contrast_and_overlay_change(self):
        self.canvas.reset_full_size()
        self.canvas.zoom(4)
        self.canvas.centerOn(145, 135)
        original = self.canvas.get_view()
        self.canvas.set_image(self.image, self.labels, [], 100, 15000, "filled")
        current = self.canvas.get_view()
        self.assertAlmostEqual(current["scale"], original["scale"])
        self.assertAlmostEqual(current["cx"], original["cx"], delta=0.6)
        self.assertAlmostEqual(current["cy"], original["cy"], delta=0.6)

    def test_bidirectional_view_link_has_no_feedback_loop(self):
        other = SegmentationCanvas()
        other.resize(420, 420)
        other.show()
        other.set_image(self.image, None, [], 0, 65535, "none")
        self.app.processEvents()
        self.canvas.viewChanged.connect(other.set_view)
        other.viewChanged.connect(self.canvas.set_view)
        first_spy, other_spy = QSignalSpy(self.canvas.viewChanged), QSignalSpy(other.viewChanged)
        self.canvas.zoom(3)
        self.assertEqual(first_spy.count(), 1)
        self.assertEqual(other_spy.count(), 0)
        self.assertAlmostEqual(self.canvas.get_view()["scale"], other.get_view()["scale"])
        self.assertAlmostEqual(self.canvas.get_view()["cx"], other.get_view()["cx"], delta=1)
        other.close()

    def test_click_reports_original_image_pixel_and_object_id(self):
        self.canvas.reset_full_size()
        self.canvas.zoom(3)
        position = self.canvas.mapFromScene(125.5, 128.5)
        spy = QSignalSpy(self.canvas.pixelClicked)
        QTest.mouseClick(self.canvas.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0), [125, 128, 7])

    def test_linked_fit_mode_survives_resize_and_manual_zoom_clears_both(self):
        other = SegmentationCanvas()
        other.resize(420, 420)
        other.show()
        other.set_image(self.image, None, [], 0, 65535, "none")
        self.app.processEvents()
        self.canvas.viewChanged.connect(other.set_view)
        other.viewChanged.connect(self.canvas.set_view)
        self.canvas.fit()
        other.fit()
        original_scale = self.canvas.get_view()["scale"]
        self.canvas.resize(600, 600)
        other.resize(600, 600)
        self.app.processEvents()
        self.assertTrue(self.canvas.fit_mode)
        self.assertTrue(other.fit_mode)
        self.assertGreater(self.canvas.get_view()["scale"], original_scale)
        self.assertAlmostEqual(self.canvas.get_view()["scale"], other.get_view()["scale"])
        self.canvas.zoom(2)
        self.assertFalse(self.canvas.fit_mode)
        self.assertFalse(other.fit_mode)
        other.close()

    def test_drag_does_not_emit_an_object_click(self):
        self.canvas.zoom(3)
        spy = QSignalSpy(self.canvas.pixelClicked)
        QTest.mousePress(self.canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
        QTest.mouseMove(self.canvas.viewport(), QPoint(240, 240), delay=10)
        QTest.mouseRelease(self.canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(240, 240))
        self.assertEqual(spy.count(), 0)

    def test_real_pipeline_channel_names_show_the_corresponding_ids(self):
        rows = [{"id": 7, "x": 130, "y": 130, "channel": "dead"},
                {"id": 11, "x": 140, "y": 140, "channel": "live"}]
        for channel, expected in (("red", "7"), ("green", "11")):
            self.canvas.set_image(self.image, self.labels, rows, 0, 65535,
                                  channel=channel, show_ids=True)
            texts = [item.text() for item in self.canvas.scene().items()
                     if isinstance(item, QGraphicsSimpleTextItem)]
            self.assertEqual(texts, [expected])

    def test_cleared_canvas_has_no_old_pixels_ids_or_clicks_and_refits(self):
        self.canvas.zoom(3)
        spy = QSignalSpy(self.canvas.pixelClicked)
        self.canvas.clear_image()
        self.assertEqual(self.canvas.scene().items(), [])
        self.assertIsNone(self.canvas._shape)
        self.assertIsNone(self.canvas._image)
        self.assertIsNone(self.canvas._labels)
        QTest.mouseClick(self.canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(210, 210))
        self.assertEqual(spy.count(), 0)
        self.canvas.set_image(self.image, self.labels, [], 0, 65535)
        self.assertTrue(self.canvas.fit_mode)
        self.assertLess(self.canvas.get_view()["scale"], 2)


if __name__ == "__main__":
    unittest.main()
