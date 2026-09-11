# Reproducible CHO–EBFP2 live/dead analysis

This package reproduces the recovered S5–S7 analysis and applies the same numerical method to explicitly listed new images. It includes the nine original TIFFs, the original scripts and expected results, an editable parameter file, and a command-line runner with checksums and reference checks. Version 1.1.0 adds optional EBFP acquisition while preserving the original live/dead and EBFP measurement methods.

**Reproducible computation does not establish biological accuracy.** The measurements are apparent viability and detectable cell-associated blue fluorescence among resolved image objects. They are not a validated census of all cells in a gel or evidence of ongoing EBFP production.

## 1. Install and reproduce the original analysis

Extract the ZIP and open a terminal in the folder containing `run_pipeline.py`. The recorded environment uses **Python 3.12.13**; use this version for the closest reproduction. The dependency lock pins the scientific and plotting packages and their required dependencies. The full workflow was tested in the recorded Linux environment; installation on Windows and macOS was not executed here.

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run_pipeline.py reference --output runs/reference
.\.venv\Scripts\python.exe run_pipeline.py verify --run runs/reference
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

**macOS or Linux**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python run_pipeline.py reference --output runs/reference
.venv/bin/python run_pipeline.py verify --run runs/reference
.venv/bin/python -m unittest discover -s tests
```

The remaining examples use `python` as shorthand for the appropriate environment's executable above. No activation is necessary when using that executable directly.

`reference` uses the bundled source images and `configs/reference_48h.json`. It compares the reproduced segmentation masks, per-object results, classifications, and sample results with the recovered reference exports. A passing run should reproduce:

| Image | Green only | Red only | Both | Total | Apparent viability | EBFP among all objects | EBFP among green-only objects |
|---|---:|---:|---:|---:|---:|---:|---:|
| S5 | 190 | 155 | 93 | 438 | 43.4% | 170/438 = 38.8% | 148/190 = 77.9% |
| S6 | 199 | 234 | 93 | 526 | 37.8% | 201/526 = 38.2% | 170/199 = 85.4% |
| S7 | 120 | 137 | 76 | 333 | 36.0% | 115/333 = 34.5% | 94/120 = 78.3% |
| Mean ± sample SD | | | | | **39.1 ± 3.8%** | **37.2 ± 2.3%** | **80.6 ± 4.2%** |

S5–S7 are provisionally separate replicates for this reproduction. Their physical gel identities remain unconfirmed, so the original SD describes the three images rather than established independent biological replicates.

Every analysis requires a **new output directory**. To repeat it, use a new path such as `runs/reference_repeat`; the runner refuses to overwrite an existing run.

## 2. Analyze a new experiment

Copy `samples_template.csv` to `samples.csv` and list your files. Each row is one field with registered, corresponding live/dead images and optional EBFP. Paths are relative to the CSV file, not the terminal's working directory.

```csv
image_id,replicate_id,green,red,ebfp
gel1_field1,gel1,images/gel1_field1_live.tif,images/gel1_field1_dead.tif,images/gel1_field1_ebfp.tif
gel1_field2,gel1,images/gel1_field2_live.tif,images/gel1_field2_dead.tif,images/gel1_field2_ebfp.tif
gel2_field1,gel2,images/gel2_field1_live.tif,images/gel2_field1_dead.tif,images/gel2_field1_ebfp.tif
```

Give every image a unique `image_id`. Multiple **nonoverlapping** fields from the same independently prepared gel must share its `replicate_id`. Do not treat fields or individual detected objects as independent gels. Each run summarizes one prespecified condition/timepoint; the manifest does not provide condition grouping.

For acquisition settings comparable to S5–S7:

```bash
python run_pipeline.py analyze --manifest samples.csv --config configs/reference_48h.json --output runs/new_experiment
python run_pipeline.py verify --run runs/new_experiment
```

Add `--extended-qc` to `reference` or `analyze` to include additional EBFP mask-dilation and computational spatial-shift diagnostics. These diagnostics take longer and do not change the primary measurements.

### Live/dead-only images and missing EBFP

If EBFP was not acquired, leave the `ebfp` cell blank or omit that column entirely. For example:

```csv
image_id,replicate_id,green,red
gel1_field1,gel1,images/gel1_field1_live.tif,images/gel1_field1_dead.tif
gel2_field1,gel2,images/gel2_field1_live.tif,images/gel2_field1_dead.tif
```

A ready-to-edit `samples_live_dead_template.csv` is included. Use the same `analyze` command and configuration. The live/dead counts and viability use all listed fields. EBFP values are missing/not measured, not 0% positive. A live/dead-only run generates a viability figure with live, dead, and merged representative images. No artificial blue acquisition is generated.

Mixed runs may include EBFP for some fields and leave it blank for others. The object table marks absence as `ebfp_status = channel_not_provided`, and the summaries record which fields/objects have EBFP measurements. EBFP percentages describe only the scorable subset; check that its coverage is appropriate for the biological comparison. The figure identifies EBFP coverage and labels an unacquired representative panel explicitly.

An existing EBFP TIFF with no detectable signal is different: it is analyzed normally and can produce a measured 0% detection fraction. A nonblank EBFP filename that cannot be found is an error, not an instruction to skip the channel. Do not create zero-filled TIFFs as substitutes for unacquired images.

If all fields lack EBFP, `--extended-qc` skips EBFP diagnostics and records the reason. Viability threshold sensitivity still runs. Configurations created with version 1.0.0 remain compatible with version 1.1.0; the actual running version is recorded separately.

### Input requirements

- Two required scalar TIFFs per field: green/live and red/dead. Blue/EBFP is optional. RGB composites and stacks are not accepted; the runner does not project or select planes for you.
- Corresponding channels must already have the same field, dimensions, and alignment. No automatic registration is applied.
- The reference preset expects **1024 × 1024, uint8** images and a **2327.27 × 2327.27 µm** field, or **2.272724609375 µm/pixel**. The supplied original TIFFs use identity channel palettes; their stored scalar values are preserved.
- The same dimensions and bit depth are not sufficient to establish comparability. Exposure, gain, illumination, objective, staining, and export processing also matter.

**Do not apply the reference thresholds automatically to a new acquisition regime.** A different bit depth, sampling resolution, or acquisition protocol requires review and a separately named configuration. Review controls and detection overlays, define the settings, then freeze them before evaluating the complete comparison. Do not retune thresholds to obtain a preferred viability or EBFP percentage.

## 3. Definitions and method

The parameter values in `configs/reference_48h.json` are the authoritative settings. Original TIFFs remain unchanged. Filtering below creates separate working arrays for segmentation only.

1. Subtract a broad Gaussian background estimate (σ = 12 pixels) from a lightly smoothed channel image (green σ = 1.2; red σ = 1).
2. Detect green candidates using high/low contrast thresholds of 10/5 and red candidates using 4/2. Split touching candidates using a deterministic local-peak/priority-flood procedure. Minimum retained areas are 20 pixels for green and 12 for red. Border-touching detections are excluded.
3. Match green and red detections one-to-one using centroid distance of at most 6 pixels and overlap after one-pixel green-mask dilation. Count a matched pair once as double positive.
4. Define **apparent viability = green only / (green only + red only + both)**. Double-positive objects have red priority for this metric; the tables also retain that category separately. This is a classification convention, not proof that every double-positive detection represents one dead cell.
5. Measure unchanged blue intensities within each combined object mask, expanded by one pixel and excluding neighboring labels. Compare the observed sum against translated masks of the same shape placed at eligible nearby background positions, 18–65 pixels away.
6. Compute a one-sided empirical p value and apply Benjamini–Hochberg adjustment across scorable objects in the **entire run**. The reference blue-signal threshold is **q ≤ 0.05**. Blue is never a cell-detection gate.

EBFP measurements with missing masks or fewer than 100 eligible local background placements are **unscorable**, reported separately, and excluded from the relevant EBFP percentage denominator. They are not silently called EBFP-negative. All reference objects are scorable. Thus, on a new dataset, “EBFP among all objects” means among all **EBFP-scorable** detections; inspect the missing counts before interpreting it.

**Freeze the image cohort as well as the parameters.** Adding or removing images with scorable EBFP measurements changes the Benjamini–Hochberg family and can change borderline EBFP classifications even when an image's raw measurements remain identical. Retain the exact manifest used for each reported result.

### Replicates and uncertainty

Counts are pooled across the nonoverlapping fields belonging to each `replicate_id`. Fractions are calculated from those pooled counts. The final mean and sample SD (`ddof = 1`) are then calculated across replicates with equal replicate weight. A single replicate cannot provide a between-replicate SD.

The default sensitivity output varies green and red detection thresholds independently by ±25%. The EBFP sensitivity output evaluates several q cutoffs. These are algorithm-sensitivity analyses, **not confidence intervals**, and they do not replace biological or assay controls.

### Preserved limitation of the original mask method

To preserve the reference calculation, the combined label image retains the original last-write behavior where unmatched objects overlap: a later object label can take pixels from an earlier one. The object table exposes `mask_pixels_overwritten`, `mask_pixels_final`, and `mask_lost` for review. A missing final mask makes the object's EBFP result unscorable; its live/dead detection remains in the viability denominator. Resolving this behavior differently would be a new method version and should not silently replace the reference method.

## 4. Inspect and archive the outputs

| Output | Purpose |
|---|---|
| `image_summary.csv` | Counts and fractions for each field |
| `replicate_summary.csv` | Counts pooled within each gel/replicate and resulting fractions |
| `aggregate_summary.json` | Unweighted replicate means and sample SDs |
| `objects.csv` | Individual detections, live/dead classes, blue measurements, p/q values, and audit flags |
| Label `.npz` files | Channel and combined segmentation masks |
| Numbered detection overlays | Visual review of the objects counted and classified |
| PNG and SVG figure | Viability/EBFP plots and representative microscopy panels |
| Threshold- and EBFP-sensitivity CSV files | Dependence on the prespecified parameter variations |
| Run provenance/checksum records | Effective settings, input hashes, code hashes, environment versions, and output integrity |

Check numbered overlays against the original channels before accepting a new dataset. Inspect missed dim objects, merged neighbors, debris, double-positive matches, edge exclusions, and unscorable blue measurements. Detected-object density is an image-plane count per area, not a volumetric cell concentration.

Microscopy panels use documented fixed linear display scaling, separately from analysis. The reference ranges are green 0–120, red 0–30, and EBFP 0–34; upper values are clipped for display only. Do not use screenshots or these rendered panels as future analysis input. The raw TIFFs are the input.

`verify` checks source, code, and output checksums, including the integrity of the reference-validation record when present. It does not recompute measurements; use a new `reference` run for that. Keep the package version, effective configuration, exact input manifest, source TIFFs, complete output directory, and environment lock together. Verification of the same run requires the recorded source files and code to remain available at their recorded locations; hashes also allow comparison when transferring an archive.

The numerical reference checks are the standard for reproducing this analysis. Rendered PNG/SVG bytes can vary with platform, fonts, and plotting dependencies even when the measurements and masks agree. Matching figure pixels is not a substitute for matching detections and classifications.

`reference/ORIGINAL_ANALYSIS_REPORT.md` and `reference/original_code/` preserve the recovered earlier analysis for provenance. Their historical paths and run commands refer to the old package; use the commands in this README for this package.

## 5. Use with Codex

`CODEX_PROMPT.md` contains a ready-to-paste instruction for setting up the environment, reproducing the bundled example, and running your next dataset with an auditable record. Start with the reference check every time you move this package to a new computational environment.

## Verification supplied with this release

The included reference validation checks all nine channel/combined label arrays, all 1,297 object records, and all 486 EBFP detections against the recovered outputs. Masks and classifications must match exactly; per-object floating-point fields use `atol = rtol = 1e-12` (summary absolute tolerance `1e-10`). Correctness tests cover empty/dead-only images, nonsquare borders, matching, background statistics, missing measurements, and pooling fields within gels. The release passes 22 correctness tests. Full live/dead-only and mixed-EBFP runs also preserve all nine reference label arrays and every viability count/fraction; missing EBFP stays undefined. The blank-image end-to-end check from version 1.0.0 returned undefined fractions and SD rather than false zeros. `validation/` contains the current reference check, optional-channel checks, and execution summary.

The figure layout is regenerated for a configurable number of replicate groups; it preserves the measurement and original microscopy content, but is not promised to be a pixel-identical copy of the earlier figure. The original plotting script is retained under `reference/original_code/`.
