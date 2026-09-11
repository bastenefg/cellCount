"""Full-resolution microscopy rendering; display choices never alter measurements."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsView,
)


def _channel_key(channel):
    key = str(channel).lower() if channel is not None else ""
    return {"dead": "red", "live": "green", "ebfp": "blue"}.get(key, key)


def label_boundaries(labels: np.ndarray) -> np.ndarray:
    """Mark the inside edge of each object, including touching labels and borders."""
    foreground = labels > 0
    edges = np.zeros(labels.shape, dtype=bool)
    edges[0, :] = foreground[0, :]
    edges[-1, :] = foreground[-1, :]
    edges[:, 0] = foreground[:, 0]
    edges[:, -1] = foreground[:, -1]
    different = labels[1:, :] != labels[:-1, :]
    edges[1:, :] |= different & foreground[1:, :]
    edges[:-1, :] |= different & foreground[:-1, :]
    different = labels[:, 1:] != labels[:, :-1]
    edges[:, 1:] |= different & foreground[:, 1:]
    edges[:, :-1] |= different & foreground[:, :-1]
    return edges


def render_segmentation(
    image: np.ndarray, labels: np.ndarray | None, display_low: float,
    display_high: float, mode: str = "outlines", channel: str = "red",
) -> np.ndarray:
    """Return RGB pixels without modifying input arrays or deriving any new masks.

    Contrast is a fixed linear display window in the image's intensity units.
    Labels are shown exactly at their original image pixel coordinates.
    """
    image = np.asarray(image)
    if image.ndim != 2 or not image.size:
        raise ValueError("The viewer needs a nonempty two-dimensional image.")
    if mode not in {"outlines", "filled", "labels", "none"}:
        raise ValueError(f"Unknown segmentation display mode: {mode}")
    if not np.isfinite(display_low) or not np.isfinite(display_high):
        raise ValueError("Display contrast limits must be finite.")
    if display_high <= display_low:
        raise ValueError("Display maximum must be greater than display minimum.")
    if labels is not None:
        labels = np.asarray(labels)
        if labels.shape != image.shape or labels.dtype.kind not in "uib":
            raise ValueError("Labels must be integers with the same shape as the image.")
        if labels.dtype.kind == "i" and np.any(labels < 0):
            raise ValueError("Labels cannot contain negative object IDs.")

    gray = np.asarray(image, dtype=np.float64)
    gray = (gray - display_low) * (255.0 / (display_high - display_low))
    gray = np.nan_to_num(gray, nan=0.0, posinf=255.0, neginf=0.0)
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    if labels is None or mode == "none":
        return rgb

    # Cyan is legible over the DEAD-channel gray image; LIVE gets yellow.
    outline = np.array((255, 224, 55) if _channel_key(channel) == "green" else (0, 237, 255),
                       dtype=np.uint8)
    if mode == "outlines":
        rgb[label_boundaries(labels)] = outline
        return rgb

    foreground = labels > 0
    # Hash just foreground IDs: no full-image loop for every object, and no
    # allocation proportional to the largest ID (IDs may be sparse).
    ids = labels[foreground].astype(np.uint64)
    hashed = ids * np.uint64(2654435761)
    colors = np.column_stack([
        64 + ((hashed >> np.uint64(shift)) & np.uint64(191))
        for shift in (0, 8, 16)
    ]).astype(np.uint8)
    if mode == "labels":
        rgb.fill(0)
        rgb[foreground] = colors
    else:
        rgb[foreground] = (
            rgb[foreground].astype(np.float32) * 0.42 + colors * 0.58
        ).astype(np.uint8)
        rgb[label_boundaries(labels)] = outline
    return rgb


class SegmentationCanvas(QGraphicsView):
    """Pixel-accurate viewer with linked zoom/pan and click-to-inspect signals.

    Connect ``left.viewChanged`` to ``right.set_view`` and vice versa. Receiving
    a view does not emit another change, so this link has no feedback loop.
    """

    viewChanged = Signal(dict)
    pixelClicked = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#111c20"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Nearest-neighbor enlargement preserves the exact segmentation pixels.
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setMinimumSize(160, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Scroll to zoom; drag to pan; click a cell to inspect it. Double-click to fit.")
        self._shape = None
        self._labels = None
        self._image = None
        self._updating = False
        self.fit_mode = True
        self._press_position = None
        self._dragged = False
        self.horizontalScrollBar().valueChanged.connect(self._emit_view)
        self.verticalScrollBar().valueChanged.connect(self._emit_view)

    def clear_image(self):
        """Remove pixels, IDs, and click state while awaiting another field."""
        updating = self._updating
        self._updating = True
        try:
            self._shape = None
            self._image = None
            self._labels = None
            self._press_position = None
            self._dragged = False
            self.fit_mode = True
            self.scene().clear()
            self.scene().setSceneRect(0, 0, 0, 0)
            self.resetTransform()
        finally:
            self._updating = updating

    def set_image(
        self, image: np.ndarray, labels: np.ndarray | None, rows: list[dict],
        display_low: float, display_high: float, mode: str = "outlines",
        channel: str = "red", show_ids: bool = False,
    ):
        """Replace displayed pixels, retaining the view when image dimensions match."""
        rgb = render_segmentation(image, labels, display_low, display_high, mode, channel)
        previous = self.get_view() if self._shape == image.shape else None
        previous_fit = self.fit_mode
        self._updating = True
        try:
            self._shape = image.shape
            self._image = image
            self._labels = labels
            self.scene().clear()
            height, width = self._shape
            # copy() gives Qt owned storage independent of the temporary numpy RGB.
            qimage = QImage(rgb.data, width, height, rgb.strides[0],
                            QImage.Format.Format_RGB888).copy()
            self.scene().addPixmap(QPixmap.fromImage(qimage))
            self.scene().setSceneRect(0, 0, width, height)
            if show_ids and mode != "none" and labels is not None:
                self._add_ids(rows, channel)
            if previous is not None:
                self.set_view(previous)
                self.fit_mode = previous_fit
            else:
                self.fit()
        finally:
            self._updating = False
        self._emit_view()

    def _add_ids(self, rows: list[dict], channel: str):
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        pen = QPen(QColor("#13272d"))
        pen.setWidthF(0.8)
        for row in rows:
            if _channel_key(row.get("channel")) not in ("", _channel_key(channel)):
                continue
            object_id = row.get("id", row.get("object_id"))
            try:
                object_id = int(object_id)
                x, y = float(row["x"]), float(row["y"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if object_id <= 0 or not (math.isfinite(x) and math.isfinite(y)):
                continue
            item = QGraphicsSimpleTextItem(str(object_id))
            item.setFont(font)
            item.setBrush(QColor("#fff9b0"))
            item.setPen(pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            # Region centroids use integer indices for pixel centers.
            item.setPos(x + 0.5, y + 0.5)
            item.setZValue(1)
            self.scene().addItem(item)

    def get_view(self) -> dict[str, float | bool]:
        center = self.mapToScene(self.viewport().rect().center())
        return {"scale": self.transform().m11(), "cx": center.x(), "cy": center.y(),
                "fit": self.fit_mode}

    def _emit_view(self, *unused):
        if not self._updating and self._shape is not None:
            self.viewChanged.emit(self.get_view())

    def set_view(self, state: dict[str, Any]):
        """Apply a linked view silently, without recursive synchronization signals."""
        if self._shape is None:
            return
        try:
            scale, cx, cy = (float(state[key]) for key in ("scale", "cx", "cy"))
        except (KeyError, TypeError, ValueError):
            return
        if not all(math.isfinite(value) for value in (scale, cx, cy)) or scale <= 0:
            return
        updating = self._updating
        self._updating = True
        try:
            self.fit_mode = bool(state.get("fit", False))
            self.setTransform(QTransform.fromScale(scale, scale))
            self.centerOn(cx, cy)
        finally:
            self._updating = updating

    def fit(self):
        if self._shape is None:
            return
        updating = self._updating
        self._updating = True
        try:
            self.fit_mode = True
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        finally:
            self._updating = updating
        self._emit_view()

    def zoom(self, factor: float):
        """Zoom about the viewport center (for +/- toolbar buttons)."""
        self._zoom_at(factor, QPointF(self.viewport().rect().center()))

    def _zoom_at(self, factor: float, position: QPointF):
        if self._shape is None or not math.isfinite(factor) or factor <= 0:
            return
        old_scale = self.transform().m11()
        target = max(0.01, min(64.0, old_scale * factor))
        before = self.mapToScene(position.toPoint())
        updating = self._updating
        self._updating = True
        try:
            self.fit_mode = False
            self.scale(target / old_scale, target / old_scale)
            after = self.mapToScene(position.toPoint())
            center = self.mapToScene(self.viewport().rect().center())
            self.centerOn(center + before - after)
        finally:
            self._updating = updating
        self._emit_view()

    def reset_full_size(self):
        """Set one image pixel to one viewport pixel, retaining the viewed center."""
        if self._shape is not None:
            self.zoom(1.0 / self.transform().m11())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()
        else:
            self._emit_view()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            delta = event.pixelDelta().y()
        if delta:
            self._zoom_at(1.2 ** (delta / 120.0), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is not None:
            distance = (event.position().toPoint() - self._press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._dragged = True
                self.fit_mode = False
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        clicked = self._press_position is not None and not self._dragged
        self._press_position = None
        if clicked and self._shape is not None:
            position = self.mapToScene(event.position().toPoint())
            x, y = math.floor(position.x()), math.floor(position.y())
            if 0 <= y < self._shape[0] and 0 <= x < self._shape[1]:
                object_id = 0 if self._labels is None else int(self._labels[y, x])
                self.pixelClicked.emit(x, y, object_id)
        self._emit_view()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = None
            self.fit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(1.25)
        elif event.key() == Qt.Key.Key_Minus:
            self.zoom(1 / 1.25)
        elif event.key() in (Qt.Key.Key_0, Qt.Key.Key_F):
            self.fit()
        elif event.key() == Qt.Key.Key_1:
            self.reset_full_size()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
