# Live/Dead Cell Counter

A local Windows app for reviewing and counting cells in paired LIVE/DEAD
fluorescence TIFF images, with optional EBFP measurements.

## Use the app

Download the version 1.4.2 Windows ZIP from this repository's Releases page,
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

For a stack, tune those controls in the usual **Preview segmentation**, click
**Use settings for next run**, then choose **2D · projection / single image** or
**3D · original Z stack** under **Count in**. Click the same **Run analysis** button
for either mode. The 3D run uses those exact saved settings on the original
optical sections; **Inspect Z stack…** lets you review the resulting masks in
depth. There is no second threshold setup for a new 3D run.

The projection remains a fast way to tune parameters. Its masks and counts can
differ from a 3D run because projection combines signal and background from
different depths. Check completed 3D masks before interpreting cell identities.

On **Results → Figure & detections**, use **Save summary figure…** to export
the loaded 2D run's summary as a PNG or SVG. Its microscopy panels show the full
representative field, matching the extent of that field's detection overlay.
Older runs also get the full-field summary when reopened, using the saved counts
and original TIFFs. Saved detection outlines and the exact threshold values are
included. Leica summaries identify the analyzed Z slice or projection.
For 3D runs, select a field and export its PNG summary with the saved 3D masks
and shared thresholds. The usual Results page also shows pooled candidate tables.

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

## Version 1.4.2 update

3D Results now show an assumption-based viability range immediately. Use
**Inspect Z stack** to mark mixed candidates as separate cells, a supported
same-cell pair, uncertain, or explicit pairs within a complex group. A true
LIVE/DEAD pair counts once as nonviable for the L3224 assay. Uncertain groups
remain in the range. Once all mixed groups are resolved, a single reviewed
viability percentage is shown.

**Apply to results** saves the review locally and updates counts, CSV exports and
summary figures without repeating segmentation. Closing the viewer also saves
pending decisions. **Save review** and **Load review** on Results transfer those
decisions with the original run folder. Original masks, settings and completed
analysis files remain unchanged. EBFP scoring remains available in 2D.

## Version 1.4.1 update

Unifies the detection controls and Run button for 2D and 3D counting. Review
thresholds on the maximum projection, choose the counting dimension, and run
one field or a batch through the usual workflow. 3D results open on the standard
Results page with per-field figures, candidate tables and access to Z review.

3D uses each slice's background-corrected contrast with the saved channel
smoothing and thresholds. Minimum area applies to the largest XY cross-section
of an object; Z calibration governs depth-dependent separation and matching.
Green-only, red-only, dual-positive candidates and unresolved associations remain
separate categories. No definitive 3D viability percentage is inferred.

Existing 2D runs and older 1.4.0 raw-intensity 3D results remain readable with
their original settings. Saved figures are never regenerated with new controls.

## Version 1.4.0 update

Adds Z-stack review and a separate 3D candidate counting mode. Open **Review Z /
count in 3D…** after Leica import or from saved results. Compare synchronized
optical slices, side sections and depth profiles; record uncertain overlaps.
Background loading and a disk cache keep slice navigation independent of full
volume analysis. Original LIF/LOF files must remain available.

The original 1.4.0 3D mode used calibrated physical spacing, its own intensity
thresholds and volume settings, and saved new label volumes, object tables and
a summary figure. Associations remain candidates for review; unresolved groups
are reported explicitly. The original 2D method and previous results are preserved.

## Version 1.3.2 update

Adds a recognizable green/red cell icon to the Windows executable, app window,
and taskbar. The icon includes multiple sizes for clear display in Explorer
and shortcuts. Counting and segmentation behavior are unchanged.

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
