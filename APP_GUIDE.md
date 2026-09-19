# Live/Dead Cell Counter 1.4.3

A 64-bit Windows desktop interface for paired LIVE/DEAD fluorescence images and
optional EBFP analysis. The app can review other cell types; its bundled CHO
reference remains a reproducibility example, and detection settings need review
for each acquisition. The original analysis code and reference measurements are
preserved.

## Open the portable app

1. Extract **Live-Dead-Cell-Counter-1.4.3-Windows-x64.zip** completely into a new folder.
2. Open the extracted **Live-Dead Cell Counter** folder.
3. Double-click **Live-Dead Cell Counter.exe**. Python is included; no installation or
   command line is needed.

Look for the circular green/red cell icon in Explorer and the app's taskbar button.

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
settings for next run** when ready. Under **Count in**, choose **2D · projection /
single image** or **3D · original Z stack**, choose an output location and run
name, and click **Run analysis**. Both counting modes use the settings from this
preview. A 3D run requires imported Leica stacks with known Z spacing; an ordinary
TIFF pair supports 2D counting. The app saves the input selection and effective
settings with the results automatically.

For multiple fields or replicates, choose **Batch / CSV**, then use **+ Add
field** to add one row per field and select its images. You can also import an
existing sample CSV with **Import CSV**, or save your field list with **Export CSV**.
Each image ID must be unique. IDs use letters, numbers, dots, hyphens,
or underscores, and start with a letter or number.

Give nonoverlapping fields from the same independently prepared gel the same
replicate ID. In 2D mode the analysis pools counts within each replicate, then
reports an equally weighted replicate mean and sample SD. One replicate has no
between-replicate SD. A 3D batch pools candidate categories within replicates
without estimating viability. Run each condition/timepoint separately.

Use **Load settings** to select a configuration or **Review / edit** to inspect
and adapt a copy. Click **Preview segmentation** to review the object boundaries
and adjust detection settings before running the full analysis (see below).
Select a new output folder and click **Run analysis**. The
app checks inputs before starting. The original 2D counting engine uses registered, scalar,
single-plane TIFFs. Leica imports prepare those inputs automatically and retain
the connection to the original stack for 3D counting. The bundled reference and default batch configuration expect
1024 × 1024 uint8 images with the reference acquisition and calibration. RGB
composites and direct multi-page TIFF input are unsupported. Use the Leica
importer below for .lif/.lof Z stacks. Use a separately named configuration
when acquisition or calibration differs; review thresholds against controls before
analyzing a complete comparison. This 2D workflow preserves the original numerical method.

EBFP can be absent for some or all fields. Leave its entry blank when it was not
acquired: missing means **not measured**, not a negative result. An acquired
channel with no detectable signal remains a measurement. Extended QC adds EBFP
mask-dilation and spatial-shift diagnostics; primary counts remain the same.

## Import Leica SP8 files (.lif / .lof)

Click **Import Leica .lif / .lof…** in Quick analysis, or **Import Leica…** in
Batch / CSV. Open a Leica file (or drop it into the import window), then:

1. Select the image series. A LIF can contain several independent acquisitions;
   choose the one to analyze. The image list loads without reading all pixels.
2. Assign the acquired channels to LIVE, DEAD and optional EBFP. The initial
   LIVE/DEAD choices are the first two channels; **check these assignments** using
  your staining and acquisition settings. Names such as Ch0/Ch1 do not identify
   a dye. Confirm the assignments after reviewing the previews.
3. For a Z stack, choose **Single Z plane** and use the Z slider, or explicitly
   choose **Maximum intensity projection** and its first/last Z planes. Select a
   time point if the acquisition contains more than one.
4. Check **Pixel size (µm)**. Values come from Leica metadata when available and
   can be edited here. If metadata is absent, confirm the displayed preset value.
5. Click **Use these images**, then preview segmentation and run analysis as usual.

## Use the same controls for 2D and 3D counting

For a Leica stack, select **Maximum intensity projection** and the desired Z
range during import. Then:

1. Open the usual **Preview segmentation**. Tune LIVE/DEAD thresholds, smoothing,
   background estimation, minimum area and matching while viewing the projection.
2. Click **Use settings for next run**.
3. Under **Count in**, choose **2D · projection / single image** to count the
   projection, or **3D · original Z stack** to count through its source volume.
4. Click the ordinary **Run analysis** button. Results open on the usual Results
   page. A batch processes each field's selected stack once; Z slices are not
   separate fields or replicates.

There is one set of detection controls. A full 3D analysis runs only when you
request a run; moving a threshold slider does not repeatedly process the whole
stack. The projection preview continues to reuse its cached images and unchanged
channel results.

In 3D mode the saved smoothing and background settings are applied separately
to each XY slice, followed by the same peak and region contrast thresholds.
There is no smoothing between Z slices. Signal is connected and separated in
three dimensions rather than counted independently on each plane. Minimum area
means that a 3D object must have at least one XY cross-section of that area; it
is not converted into an assumed cell volume. Peak spacing and match distance
extend into 3D using the geometric mean XY pixel size as their distance unit;
the recorded Z spacing determines distances between planes. The saved results
record the effective peak, matching-window and dilation rules.

**The same settings do not imply identical projected and 3D masks.** A maximum
projection changes both signal and estimated background, and can combine cells
at different depths. Use the projection to tune the controls, then inspect the
completed 3D masks in optical sections before interpreting the candidates.

## Inspect the stack and review 3D candidates

After importing a Leica stack, click **Inspect Z stack…** on New analysis.
You can also open it from Results for a saved Leica run. A 2D analysis is not
required first. The viewer uses the imported range, time point and channel
mapping. Importing a single optical slice gives a single-slice review, not a
3D count.

The viewer reopens the original LIF/LOF and caches the selected LIVE and DEAD
planes on disk. Keep that original file at its recorded location and reconnect
an external drive before opening it. A projected TIFF cannot reconstruct depth.
The cache preserves the full image resolution and intensity values; only screen
previews may be reduced to fit. Loading runs in the background. Slice changes
reuse cached arrays rather than rereading the Leica container or recounting cells.

Use the Z slider to compare LIVE, DEAD and merged slices. The view selector
switches from the full field to a 256, 128 or 64 pixel crop around the inspection
point. Scroll over any image to zoom at the pointer, and drag to pan. LIVE, DEAD
and merged XY panels move together; XZ and YZ panels can be zoomed independently.
Zoom reveals the visible original pixels and persists while changing Z slices.
Dragging does not change the candidate being reviewed. **Fit views** restores
the full field and resets all panes. Selecting a candidate or individual channel
object centers it at the current magnification. Select an existing
2D detection, or click the image to move the inspection point. Side sections
and intensity-versus-depth profiles help distinguish vertically separated cells.
Depth is displayed in micrometers when valid Z calibration is available; unknown
calibration permits slice-index review but prevents a 3D count. Different peak
depths alone do not prove that there are two cells.

When opening a 3D field from Results, select a mixed candidate and inspect its
LIVE and DEAD members through Z. **Separate cells** counts each channel object
separately. **Same-cell signal supported** is available for one LIVE and one
DEAD object and counts them once as nonviable. For complex groups, **Explicit
pairs** lets you choose specific LIVE/DEAD pairs; a member cannot be paired twice.
Confirm that all unmatched members are separate cells. Use **Uncertain** when
identity remains unclear; it stays in the viability range. The member selector
centers the viewer on individual channel objects to help identify them.

Click **Apply to results** to save decisions and update the displayed counts,
viability and figures. Closing the viewer also saves pending decisions. A failed
save keeps the viewer open. Local review revisions are separate from the original
analysis; neither masks nor segmentation parameters change. Use **Save review**
on Results to export a portable JSON, and **Load review** with the same run on
another computer. The app checks that the review belongs to that analysis.
Reviews reopen automatically on the current computer. Optical-section notes
made outside this 3D Results workflow remain annotations only.

The stack viewer shows the completed 3D masks and the saved shared settings.
To change them, use the usual **Preview segmentation** or **Inspect segmentation**,
apply the reviewed settings and create a new run. Saved results keep their
original masks and figures. Older 1.4.0 results remain readable with their
separate raw-intensity and volume settings; reopening does not convert or recount
those analyses.

Results distinguish green-only, red-only, dual-positive candidates and unresolved
associations. A spatial association is evidence for review, not proof of cell
identity. Crowded groups and partial cells at volume boundaries need inspection.
Reopen a saved 3D folder through **Results → Open run…**. Choose a field to inspect
its candidates, summary and depth. The app verifies saved artifacts before
loading them. The output records parameters, calibrated source selections,
object tables, label volumes and per-field summary figures generated from those
saved labels. Batch tables pool candidate categories within the supplied
replicate groups; they do not turn uncertainty into a definitive cell count.
3D Results show **Provisional viability** immediately, as a range across possible
pairings in unreviewed or uncertain groups. Each detected channel object is
assumed to represent one cell; confirmed EthD-positive cells are treated as
nonviable. Pairing one LIVE and one DEAD object reduces the total count by one
and removes that LIVE object from the viable count. All groups remain in the
denominator; ambiguous groups are never silently excluded. This is a scenario
range, not a confidence interval or a bound on segmentation error.

When all mixed groups are resolved, **Reviewed viability** is
`100 × LIVE cells / total cells`. With no mixed groups, the same calculation is
shown as apparent viability. Empty analyses show **Not measured**. Field and
replicate tables pool integer cell counts before calculating percentages; the
overall result pools all fields, rather than averaging replicate percentages.
EBFP scoring remains available in 2D.

For Thermo L3224, calcein indicates esterase activity and EthD-1 indicates membrane
damage. Confirmed red-positive cells are membrane-compromised even if green
signal remains; this assay does not by itself identify an apoptotic stage.
See the [manufacturer's assay information](https://documents.thermofisher.com/TFS-Assets/LSG/manuals/mp03224.pdf).
Sparse Z sampling, optical blur, channel misregistration and bleed-through can
leave cases unresolved. Review representative stacks and appropriate staining
controls before using 3D candidate counts for experimental conclusions.

A single-plane acquisition is imported directly. A projection takes the maximum
stored value at each XY pixel independently in each selected channel. With
**Count in** set to 2D, counting this projection can merge cells above one
another. Choose 3D to use the source depth. Z slices are not automatically
pooled as independent cells or replicates.
The run records the selected series, channel mapping, Z range, time and calibration.

Only selected channels/planes are read. Maximum projections stream one plane at
a time; the importer never needs the whole stack in memory. Repeated imports of
an unchanged selection reuse verified TIFF exports. Cancel remains available
while reading or exporting. Thumbnails use display scaling only; extracted
uint8/uint16 pixel values and bit depth are preserved without normalization.

Prepared TIFFs and `import_provenance.json` are kept in an **imports** folder
beside the selected runs folder. Keep the entire import folder with your results,
including when exporting/reopening a CSV. Run settings contain a verified copy
of the import record; normal run verification covers the actual analyzed TIFFs.
The Leica source is recorded by path, byte size and modification time rather
than hashing a potentially multi-gigabyte container on each import.

To add more series to a batch, use Import Leica again. New fields get separate
replicate IDs; edit those deliberately if nonoverlapping fields belong to the
same biological replicate. One batch must share XY dimensions, dtype and pixel
calibration. Analyze acquisitions with different formats in separate batches.

Supported: scalar XY fluorescence images in LIF/LOF, uint8/uint16 (including
lower-bit acquisitions stored in those types), Z stacks and selected time points.
RGB composites, varying tile/mosaic dimensions, spectral dimensions, floating
point/FLIM data and bit-packed layouts are rejected with a message. The original
Leica files are opened read-only. The reader is the pinned BSD-licensed
[liffile 2026.7.14](https://github.com/cgohlke/liffile/tree/v2026.7.14).

## See and adjust cell segmentation

**Peak threshold** and **Region threshold** now have sliders beside the exact
numeric inputs. Drag for a quick adjustment or type a precise value. A drag
updates the segmentation after release, so intermediate slider positions do not
queue slow computations. The region slider cannot exceed the peak threshold.
Use **Slider range** for fine adjustments near your thresholds or a wider range
for 16-bit images. Sliders change the same existing threshold settings; they do
not change brightness or the counting algorithm.

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
| Minimum area (px²) | Minimum accepted object area in 2D; in 3D, at least one XY cross-section must reach this area. |
| Smoothing (px) | Gaussian smoothing before background subtraction; 3D applies it to each XY slice separately. |
| Background (px) | Background-estimation scale, shared by both channels. |
| Peak spacing / Peak window | Separation of local peaks, shared by both channels. Greater spacing can reduce splitting. |
| Match distance (px) | Allowed LIVE/DEAD distance. 2D retains its existing overlap rule; 3D uses calibrated spatial matching and keeps ambiguous associations unresolved. |
| Exclude border objects | Excludes detections touching the image border; 3D also checks the first and last selected Z planes. |

The first four controls apply to the selected channel. Background, splitting,
matching and border controls affect the combined result. **Auto-update** reruns
the preview after a short pause in editing; turn it off and click **Update preview** to compare
several edits together. The preview processes the complete selected field,
even when you are zoomed in. Its counts describe the displayed 2D field or
projection, including when you intend to run in 3D. Full-volume counts are
calculated only by a 3D run. EBFP scoring is available in 2D runs; 3D viability
is reported with its remaining association uncertainty.

Repeated previews reuse loaded images and unchanged channel segmentations, so
editing only DEAD settings does not reprocess LIVE. Complete-image counts are
retained, and a 2D analysis still runs the original pipeline. The first
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
dataset. The 2D GUI preserves the original numerical method and makes its
parameters inspectable; it does not supply a validated replacement viability.
The source distribution's `app_validation/segmentation_review/` folder contains
input-image panels, unchanged segmentation contours and diagnostic measurements.

## Review and share results

Review the counts, summary figure, and numbered detection overlays. Check dim
objects, touching neighbors, debris, double-positive matches, edge exclusions,
and unscorable EBFP objects before accepting a new dataset. In 2D mode, apparent viability is
**live only / (live only + dead only + double positive)**. Double positives remain
a separate reported category.

Use **Results** to inspect a completed run: **Counts** shows the tables and
**Figure & detections** shows the figure and detection overlays. **Inspect
segmentation** opens the interactive review with the saved run's inputs and
effective settings. Use **Open run…**
to load an earlier run or **Save summary CSV** to export the displayed summary.
For a 3D run, select a field to view its candidate summary or open **Inspect Z
stack…** to see saved masks in depth and resolve mixed candidates. The summary
CSV exports the displayed viability, cell-count bounds and review progress by
field or replicate. **Save summary figure…** exports the selected field's
3D summary as PNG with the current review interpretation, saved masks and exact
shared settings. Figure preparation runs in the background and does not reread
the source stack. The figure
identifies its field and selected source range. A 3D summary image is an overview
projection; its counts come from the volume.

For a 2D run, use **Save summary figure...** in **Figure & detections** to save the
loaded run's full-resolution PNG or editable SVG. This always exports the
summary, even while a detection overlay is selected. Choose a destination outside
the run folder to keep its verification records intact. The summary contains the
entire representative field, with the same image extent as its detection overlay.
LIVE/DEAD panels show outlines from the saved channel masks; the merged panel
shows the saved object masks. The figure prints the peak and region thresholds
used for detection, together with the smoothing and minimum-area settings.
These come from the completed run's effective settings, so changing controls
afterward cannot change its exported summary. Raw image backgrounds retain the
run's display scaling; the outlines identify the detected regions.
For batches, the representative field is identified in the figure; counts still
summarize all analyzed fields. Leica figures identify the selected Z slice or
projection and time point. The preview caption identifies the loaded run, and
hovering over it shows the image path.

This also works for older completed runs without repeating segmentation. The
app prepares the full-field figure in the background and caches it separately;
the saved counts and original run artifacts remain unchanged. The export button
becomes available when the new summary is ready. Source TIFFs must remain at
their recorded locations; a missing or changed source is reported in the run
notes. Older pipeline figures inside the run folder retain their original crop;
use **Save summary figure...** to save the full-field version.
Use **Open run folder**
for per-image and per-replicate CSV tables, individual
objects, figures, masks, effective settings, and provenance records. Keep the
complete run folder and the original images. Every run needs a new output folder;
existing results are never overwritten by the pipeline.

**Verify files** checks saved inputs, analysis code, and output hashes for 2D
runs; 3D verification checks the saved result artifacts. It does not rerun the
measurement or establish biological accuracy. The 2D input check needs the source
images at their recorded locations; reopening optical sections or rerunning a
3D analysis also needs the original Leica source. Repeat the reference run after moving to a
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
creates `dist/1.4.3/Live-Dead-Cell-Counter-1.4.3-Windows-x64.zip`. The executable is in
`dist/1.4.3/Live-Dead Cell Counter/`. Builds use a new release folder and refuse to
replace an existing app directory, so an older open app remains intact. To
rebuild the same version, choose another folder, for example
`-ReleaseFolder 1.4.3-rebuild1`. Use `-SkipInstall` to use an
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
.\.venv\Scripts\python.exe .\packaging\validate_release.py --app-directory ".\dist\1.4.3\Live-Dead Cell Counter" --zip ".\dist\1.4.3\Live-Dead-Cell-Counter-1.4.3-Windows-x64.zip" --output ".\app_validation\release_1.4.3\bundle_audit.json"
```

This audit checks bundled sources, preserved original package files, excluded
host-specific ICU libraries and ZIP integrity. Run the app's **Reference check**
and inspect its GUI on the destination computer as well.
