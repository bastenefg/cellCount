# Segmentation preview, desktop version 1.1.0

The new preview calls the existing `pipeline.core.segment` and `combine`
functions on complete, unchanged TIFF arrays. Neither zoom nor display contrast
changes the arrays used for segmentation. EBFP is not measured in a preview.

## What changed

- A **Preview segmentation** action beside the field list and an **Inspect
  segmentation** action on saved results open an interactive comparison.
- The supplied TIFF and its detected outlines are displayed side by side with
  linked zoom/pan, mask views, IDs and click-to-inspect object measurements.
- Peak/region thresholds, minimum area, smoothing, background, splitting and
  matching controls update a separate preview worker. The default view is DEAD.
- Old previews are marked stale. Apply/save requires a current, visible preview.
- Applying settings prepares a new run; saved runs and the reference preset are
  preserved. Discarding the dialog discards its parameter edits.

## Why the test1 result needs review

The original preset detects 1,402 red objects. Of these, 1,199 (85.5%) have a
maximum stored TIFF intensity of at most 15, and 973 (69.4%) have area at most
20 pixels. The diagnostic panels show numerous outlines around speckled
background. Most detections are separate small regions, rather than excessive
splitting of one large region. The user confirmed this TIFF was processed or
re-saved. These observations support reviewing the preset and acquisition
exports; they do not establish a corrected biological viability value.

No adjusted preset has been selected as a scientifically validated replacement.
The existing preset still reproduces its saved counts. Original acquisition
exports and appropriate controls are preferable for final measurement choices.

## Checks

- `all_tests.log`: all 64 original and GUI tests pass.
- The new preview backend exactly reproduces the stored S5 green, red and
  combined label arrays.
- `source_smoke_final/smoke_test.json`: the real Qt interface and subprocess
  preview reproduce test1 counts, respond to parameter changes, preserve source
  settings, and keep labels unchanged under display/zoom changes.
- `dialog_qa_results.json`: real-data inspection, 1,402 object IDs, and layouts
  at 1460 × 900 and 1020 × 660 were checked.
- The `packaged_smoke` and packaged validation records document checks performed
  on the compiled executable, separately from source testing.

The original scientific code, reference configuration and input images are
preserved. Diagnostic rendering changes display contrast only. Temporary
preview artifacts are removed after loading or closing the review window.
