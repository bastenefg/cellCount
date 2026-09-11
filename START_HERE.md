# Live/Dead Cell Counter

A local Windows app for reviewing and counting cells in paired LIVE/DEAD
fluorescence TIFF images, with optional EBFP measurements.

## Use the app

Download the version 1.2.0 Windows ZIP from this repository's Releases page,
extract the entire archive, and open **Live-Dead Cell Counter.exe**. Python is
included. Keep the executable and its `_internal` folder together.

**Quick analysis** accepts a LIVE TIFF and a DEAD TIFF without a spreadsheet or
sample IDs. Select **Preview segmentation** to compare the input and detected
outlines, zoom, inspect individual objects, and adjust detection settings.
**Batch / CSV** supports multiple fields and replicate groups.

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
