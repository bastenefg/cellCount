# Live/Dead Cell Counter

A local Windows app for reviewing and counting cells in paired LIVE/DEAD
fluorescence TIFF images, with optional EBFP measurements.

## Use the app

Download the version 1.3.1 Windows ZIP from this repository's Releases page,
extract the entire archive, and open **Live-Dead Cell Counter.exe**. Python is
included. Keep the executable and its `_internal` folder together.

**Quick analysis** accepts a LIVE TIFF and a DEAD TIFF without a spreadsheet or
sample IDs. Select **Preview segmentation** to compare the input and detected
outlines, zoom, inspect individual objects, and adjust detection settings.
**Batch / CSV** supports multiple fields and replicate groups.

**Import Leica .lif / .lof…** reads Leica SP8 acquisitions directly. Choose a
series, LIVE/DEAD channels and either one Z slice or an explicit maximum-intensity
projection. Pixel calibration is read from metadata and can be reviewed. Peak
and region threshold sliders make segmentation adjustments easier.

On **Results → Figure & detections**, use **Save summary figure…** to export
the loaded run's summary as a PNG or SVG. Its microscopy panels show the full
representative field, matching the extent of that field's detection overlay.
Older runs also get the full-field summary when reopened, using the saved counts
and original TIFFs. Saved detection outlines and the exact threshold values are
included. Leica summaries identify the analyzed Z slice or projection.

Read [APP_GUIDE.md](APP_GUIDE.md) for the workflow, settings, and source/build
instructions. [README.md](README.md) is the preserved original scientific
pipeline documentation. The reference preset comes from the supplied CHO
dataset; review settings and pixel calibration for a different acquisition.

## What is included

- Desktop app source and Windows build scripts.
- Original scientific pipeline, configuration, reference images and expected
  results, preserved byte-for-byte against `PACKAGE_SHA256.json`.
- The supplied sample TIFF pair and `samples.csv`.
- Tests, diagnostic figures, performance measurements and release validation
  records under `tests/`, `validation/` and `app_validation/`.
- Third-party license notices and their collection scripts.

Generated environments, old application bundles, temporary preview arrays and
ordinary run output folders are excluded from Git. Validation records describe
the original local runs and may contain machine-specific paths. The portable
Windows application is distributed as a release asset.

## Version 1.3.1 update

Summary image panels now show the entire field instead of a central crop, in
both the preview and PNG/SVG exports. Summaries are prepared in the background
and cached separately from completed runs; no cells are recounted and the
original run files remain intact. Outlines and threshold annotations come from
the saved masks and effective settings of that run. Keep source TIFFs at their recorded locations
so the app can prepare the full-field panels.

## Version 1.3.0 update

Adds native LIF/LOF import, channel previews, Z-plane and projection choices,
selected time points, acquisition calibration and cached TIFF extraction.
Threshold sliders retain exact numeric controls and update previews after
release. The counting pipeline remains unchanged; projections are explicitly
reported as 2D projected counts. See APP_GUIDE.md for the workflow and limits.

## Version 1.2.1 update

Adds direct PNG/SVG summary export and a preview caption identifying the loaded
run and central crop. Missing or unreadable previews now clear the old image.
All 10 GUI review tests passed, including run switching, failed preview loads,
exact summary exports and preservation of completed run files. The counting
pipeline is unchanged.

## Version 1.2.0 validation

All 82 tests passed. The packaged app reproduced the three reference fields,
including all nine masks, 1,297 object records and 486 EBFP classifications.
The quick TIFF-pair run matched its initial preview counts. Tested DEAD-threshold
edits updated the packaged viewer in 1.72–1.95 seconds on the development
machine, including debounce and drawing; the first preview took 5.65 seconds.

The original batch analysis code is unchanged. The preview reuses channel
results and uses an equivalent faster matching implementation, checked against
the original masks and object records. These software checks do not establish
biological accuracy for a new dataset.

No new source-code license has been assigned. Existing third-party notices
remain applicable.
