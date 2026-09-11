# Live/Dead Cell Counter 1.2

A 64-bit Windows desktop interface for paired LIVE/DEAD fluorescence images and
optional EBFP analysis. The app can review other cell types; its bundled CHO
reference remains a reproducibility example, and detection settings need review
for each acquisition. The original analysis code and reference measurements are
preserved.

## Open the portable app

1. Extract **Live-Dead-Cell-Counter-1.2.0-Windows-x64.zip** completely into a new folder.
2. Open the extracted **Live-Dead Cell Counter** folder.
3. Double-click **Live-Dead Cell Counter.exe**. Python is included; no installation or
   command line is needed.

Keep the executable and its `_internal` folder together. Share the complete ZIP,
not just the executable. The app runs locally and reads images on your computer.
Choose a writable location for results. Windows may identify this locally built,
unsigned application as an unrecognized app.

## Check the bundled example first

Open **Reference check**, choose an output location, and click **Run reference
check** to analyze the bundled S5, S6, and S7 microscopy images. Inspect the
results and reference validation status. Expected counts:

| Image | Live only | Dead only | Double positive | Total | Apparent viability |
|---|---:|---:|---:|---:|---:|
| S5 | 190 | 155 | 93 | 438 | 43.4% |
| S6 | 199 | 234 | 93 | 526 | 37.8% |
| S7 | 120 | 137 | 76 | 333 | 36.0% |

A passing reference run matches all nine segmentation label arrays, all 1,297
object records, and 486 EBFP classifications. Floating-point comparisons use the
original tolerances. Figures may render slightly differently across computers.

## Analyze your images

Open **New analysis**. The default **Quick analysis** mode needs just two
files: choose the **LIVE · green** TIFF and the **DEAD · red** TIFF from the same
field. Add **EBFP · optional** only if that channel was acquired. No CSV or sample
IDs are needed. **Swap LIVE / DEAD** corrects the channel assignment, and **Clear
EBFP** removes the optional file. Image size and uint8/uint16 format are read from
the TIFFs; both channels must have the same size and bit depth. Pixel values are
preserved. Review the selected preset's thresholds and physical calibration for
your acquisition before using its measurements.

Click **Preview segmentation** to inspect and adjust detections, then **Use
settings for next run** when ready. Choose an output location and run name, and
click **Run analysis**. The app writes the input manifest and effective settings
with the results automatically.

For multiple fields or replicates, choose **Batch / CSV**, then use **+ Add
field** to add one row per field and select its images. You can also import an
existing sample CSV with **Import CSV**, or save your field list with **Export CSV**.
Each image ID must be unique. IDs use letters, numbers, dots, hyphens,
or underscores, and start with a letter or number.

Give nonoverlapping fields from the same independently prepared gel the same
replicate ID. The analysis pools counts within each replicate, then reports an
equally weighted replicate mean and sample SD. One replicate has no
between-replicate SD. Run each condition/timepoint separately.

Use **Load settings** to select a configuration or **Review / edit** to inspect
and adapt a copy. Click **Preview segmentation** to review the object boundaries
and adjust detection settings before running the full analysis (see below).
Select a new output folder and click **Run analysis**. The
app checks inputs before starting. All modes require registered, scalar,
single-plane TIFFs. The bundled reference and default batch configuration expect
1024 × 1024 uint8 images with the reference acquisition and calibration. RGB
composites and image stacks are unsupported. Use a separately named configuration
when acquisition or calibration differs; review thresholds against controls before
analyzing a complete comparison. The GUI does not change the numerical method.

EBFP can be absent for some or all fields. Leave its entry blank when it was not
acquired: missing means **not measured**, not a negative result. An acquired
channel with no detectable signal remains a measurement. Extended QC adds EBFP
mask-dilation and spatial-shift diagnostics; primary counts remain the same.

## See and adjust cell segmentation

After choosing a TIFF pair, adding fields or importing `samples.csv`, click **Preview segmentation**
on **New analysis**. For a saved run, open **Results** and click **Inspect
segmentation**. The original image files must still be available. The preview
opens on the **DEAD · red** channel; use **Field** and **Channel** to inspect
other images or the LIVE signal.

The input TIFF and segmented objects appear side by side. Zoom with the mouse
wheel, drag to pan, or use **1:1** and **Fit**; both views stay aligned. Switch
between **Outlines**, **Filled masks**, **Labels only** and **No overlay**.
Enable **Object IDs**, then click a detection to inspect its area, stored input intensity
and peak contrast. **Working signal** displays the smoothed,
background-subtracted signal on which the thresholds operate.

**Display black**, **White**, **Auto display** and **Full range** control
brightness for viewing. They do not change the image data, segmentation or
counts. Start by checking the input image as well as the contours, especially the
dim background and touching objects.

Adjust one detection setting at a time and inspect its effect:

| Control | What it changes |
|---|---|
| Peak threshold | Minimum peak contrast above the estimated background. Raising it rejects weaker detections. |
| Region threshold | Contrast required to grow a region around a peak. It must not exceed the peak threshold. |
| Minimum area (px²) | Minimum accepted segmented object area; raising it rejects smaller masks. |
| Smoothing (px) | Gaussian smoothing of the selected channel before background subtraction. |
| Background (px) | Background-estimation scale, shared by both channels. |
| Peak spacing / Peak window | Separation of local peaks, shared by both channels. Greater spacing can reduce splitting. |
| Match distance (px) | Allowed distance for LIVE/DEAD matching, still subject to the existing overlap rule. |
| Exclude border objects | Whether detections touching the image border are excluded, for both channels. |

The first four controls apply to the selected channel. Background, splitting,
matching and border controls affect the combined result. **Auto-update** reruns
the preview after a short pause in editing; turn it off and click **Update preview** to compare
several edits together. The preview processes the complete selected field,
even when you are zoomed in. Its counts are field-level LIVE/DEAD counts; EBFP
and replicate summaries are calculated by a full run.

Repeated previews reuse loaded images and unchanged channel segmentations, so
editing only DEAD settings does not reprocess LIVE. Complete-image counts are
retained, and the saved analysis still runs the original pipeline. The first
preview takes longer because it starts the analysis session. Edits that change
both channels or unusually dense fields can take longer than edits to one channel.

Use **Reset to opening settings** to return to the configuration that opened
the dialog. Once the preview has finished for the current settings, click
**Use settings for next run**, or **Use for new run** when reviewing results.
These actions copy the reviewed settings into **New analysis**. Click **Run
analysis** to create a new run with those settings. **Save reviewed preset…**
optionally saves a named JSON configuration for collaborators or later use.
Closing the dialog without applying discards its edits. Saved results and the
reference configuration are preserved.

Choose settings by reviewing objects and experimental controls, then use the
same reviewed preset across the comparison. Changing settings to reach an
expected viability is not a validation procedure.

## What the supplied test1 image shows

With the original reference settings, `samples.csv` produces 106 LIVE-channel
and 1,402 DEAD-channel detections, yielding 4.885% apparent viability after
matching. Most DEAD detections are small and weak: 69.4% occupy at most 20
pixels, and 85.5% have maximum raw intensity at most 15. The DEAD image contains
many isolated bright speckles, and its dominant nonzero intensity is 15,
compared with 3 or 4 in the reference images. The same absolute thresholds
therefore encounter a different intensity distribution.

The supplied `test1_dead.tif` was processed and re-saved before this analysis.
Processing can change intensity levels and the appearance of noise. Prefer the
original acquisition exports for final quantitative work; a visually reviewed
preset cannot recover signal lost during processing. Here, **raw intensity**
means the stored TIFF pixel value before this pipeline's own processing, not
necessarily an unprocessed camera measurement.

Visual inspection supports reviewing background-like detections in this
dataset. The GUI preserves the original numerical method and makes its
parameters inspectable; it does not supply a validated replacement viability.
The source distribution's `app_validation/segmentation_review/` folder contains
input-image panels, unchanged segmentation contours and diagnostic measurements.

## Review and share results

Review the counts, summary figure, and numbered detection overlays. Check dim
objects, touching neighbors, debris, double-positive matches, edge exclusions,
and unscorable EBFP objects before accepting a new dataset. Apparent viability is
**live only / (live only + dead only + double positive)**. Double positives remain
a separate reported category.

Use **Results** to inspect a completed run: **Counts** shows the tables and
**Figure & detections** shows the figure and detection overlays. **Inspect
segmentation** opens the interactive review with the saved run's inputs and
effective settings. Use **Open run…**
to load an earlier run or **Save summary CSV** to export the displayed summary.
Use **Open run folder**
for per-image and per-replicate CSV tables, individual
objects, figures, masks, effective settings, and provenance records. Keep the
complete run folder and the original images. Every run needs a new output folder;
existing results are never overwritten by the pipeline.

**Verify files** checks saved inputs, analysis code, and output hashes. It does
not rerun the measurement or establish biological accuracy. It needs the source
images at their recorded locations. Repeat the reference run after moving to a
new computer to check the execution environment. The original README explains
the method, assumptions, and limitations in full.

## Run or rebuild from source

Use 64-bit Python 3.12 (recorded version 3.12.13). In PowerShell, open the source
folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe run_app.py
```

After setup, **Launch App.cmd** starts the GUI. The original command-line runner
remains available as `run_pipeline.py`.

To create the Windows portable app on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_app.ps1 -Zip
```

This installs the build requirements into `.venv`, builds the executable, and
creates `dist/1.2.0/Live-Dead-Cell-Counter-1.2.0-Windows-x64.zip`. The executable is in
`dist/1.2.0/Live-Dead Cell Counter/`. Builds use a new release folder and refuse to
replace an existing app directory, so an older open app remains intact. To
rebuild the same version, choose another folder, for example
`-ReleaseFolder 1.2.0-rebuild1`. Use `-SkipInstall` to use an
already prepared environment. All scientific dependency versions remain pinned
in the unchanged `requirements-lock.txt`. Qt and dependency notices are included
in `_internal/third_party_notices`.

The portable bundle also carries the GUI/build source in `_internal/source` and
the original pipeline, configs, reference files, and requirements in `_internal`.
For rebuilding from that bundle, copy `_internal` to a writable source folder and
copy the contents of its `source` subfolder into that folder before following the
source instructions above. Keep the third-party notices with redistributed
copies. The included source has no newly assigned license.

After building, audit the release contents and ZIP with:

```powershell
.\.venv\Scripts\python.exe .\packaging\validate_release.py --app-directory ".\dist\1.2.0\Live-Dead Cell Counter" --zip ".\dist\1.2.0\Live-Dead-Cell-Counter-1.2.0-Windows-x64.zip" --output ".\app_validation\release_1.2.0_audit.json"
```

This audit checks bundled sources, preserved original package files, excluded
host-specific ICU libraries and ZIP integrity. Run the app's **Reference check**
and inspect its GUI on the destination computer as well.
