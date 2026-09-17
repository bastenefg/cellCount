# Live/Dead Cell Counter

<img src="../desktop/assets/live-dead-cell-counter.png" alt="Green LIVE cell and red DEAD cell app icon" width="96" height="96">

A Windows desktop app for counting and visually reviewing cells in paired
LIVE/DEAD fluorescence TIFF images, with optional EBFP analysis.

**[Download the Windows app](https://github.com/bastenefg/cellCount/releases/latest)**
· **[User guide](../APP_GUIDE.md)** · **[Project overview](../START_HERE.md)**

Extract the entire release ZIP and open **Live-Dead Cell Counter.exe**. Python
is included; keep the executable and its `_internal` folder together.

![Quick analysis: choose paired TIFFs, preview segmentation, or run analysis](images/quick-analysis.png)

- **Leica SP8 import:** open LIF/LOF files, select channels and a Z slice or
  explicit maximum-intensity projection, and retain acquisition calibration.
- **Review in Z:** scroll synchronized LIVE/DEAD slices, inspect side sections
  and depth profiles, and annotate projected overlaps. Background loading and
  cached stack planes keep navigation responsive.
- **3D candidates:** count calibrated volumes separately from the original 2D
  workflow, with threshold previews, saved label volumes and explicit unresolved
  associations. This new mode needs review against your experimental images.
- **Threshold sliders:** adjust peak/region thresholds while keeping exact
  numeric controls; previews update after releasing the slider.
- **Quick analysis:** select LIVE and DEAD TIFFs without a CSV or sample IDs.
- **Visual review:** compare input images with outlines or masks, zoom, inspect
  individual detections, and tune segmentation settings.
- **Responsive previews:** tested DEAD-threshold edits update in about two
  seconds on the development machine, using full-resolution images.
- **Batch analysis:** process multiple fields and biological replicate groups
  with saved settings, counts, figures and provenance records.
- **Full-field summaries:** view the entire representative field and save it
  directly as a PNG or SVG, with saved detection outlines and threshold values.
  Older runs work too, without recounting cells.

![Synthetic Z review example: green and red cells occupy the same XY position at different depths](images/z-review.png)

The repository includes source, tests, reference fixtures, the supplied sample
pair, and validation evidence. Version 1.4.0 passed **187 tests**, including
depth separation, touching objects, calibrated distances, saved thresholds,
cache integrity and GUI lifecycle checks. Real 1024² slice navigation took about
21 ms median; a two-channel 25-plane 3D analysis took about 7 seconds on the
development machine. See the [performance measurements and their limitations](../app_validation/release_1.4.0/performance_validation.json).
Earlier Leica pixel checks and packaged reference validation remain available.

The [original scientific pipeline documentation](../README.md) is preserved.
Its bundled CHO preset is a reference example; review thresholds and pixel
calibration for your acquisition. Software reproducibility does not establish
biological accuracy on a new dataset.

See [START_HERE.md](../START_HERE.md) and [APP_GUIDE.md](../APP_GUIDE.md) for
source setup, rebuilding, validation and licensing notes.
