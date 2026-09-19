"""Prepare reviewed 3D figures without blocking interaction or reading volumes."""
from pathlib import Path
import hashlib
import threading

from PySide6.QtCore import QThread, Signal

_RENDER_LOCK = threading.Lock()


class ViabilityFigureWorker(QThread):
    ready = Signal(str, str, str)
    failed = Signal(str, str)

    def __init__(self, fields, report, output, revision_id, parent=None):
        super().__init__(parent)
        self.fields = fields
        self.report = report
        self.output = Path(output)
        self.revision_id = revision_id

    def run(self):
        from .viability_figure import prepare_viability_figure
        for field in self.fields:
            if self.isInterruptionRequested():
                return
            ident = field["image_id"]
            try:
                destination = self.output / (ident + ".png")
                # Matplotlib's font/render state is shared between threads.
                # A superseded worker may still be finishing its current field.
                with _RENDER_LOCK:
                    if self.isInterruptionRequested():
                        return
                    path = prepare_viability_figure(field["result"], self.report["fields"][ident],
                                                    destination, revision_id=self.revision_id)
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                if not self.isInterruptionRequested():
                    self.ready.emit(ident, str(path), digest)
            except Exception as exc:
                if not self.isInterruptionRequested():
                    self.failed.emit(ident, str(exc))
