# CHO–EBFP2 cells in PEG–PEG gels after 48 h

## Main result

**Apparent viability: 39.1 ± 3.8%. Detectable EBFP fluorescence: 80.6 ± 4.2% among live-only detections, or 37.2 ± 2.3% among all detected objects.** Values are the arithmetic mean ± sample standard deviation across S5, S6, and S7 (ddof = 1), with each image given equal weight.

These are **exploratory estimates from resolved cell-like objects in three single focal planes**, not a validated measurement of viability throughout the gels. The user authorized provisional treatment of S5–S7 as independent replicates but does not recall whether they are separate gels. The SD therefore describes these three images; biological independence remains unconfirmed.

| Sample | Green only | Red only | Green + red | Total detected | Apparent viability | EBFP / all objects | EBFP / live-only objects |
|---|---:|---:|---:|---:|---:|---:|---:|
| S5 | 190 | 155 | 93 | 438 | 43.4% | 170/438 = 38.8% | 148/190 = 77.9% |
| S6 | 199 | 234 | 93 | 526 | 37.8% | 201/526 = 38.2% | 170/199 = 85.4% |
| S7 | 120 | 137 | 76 | 333 | 36.0% | 115/333 = 34.5% | 94/120 = 78.3% |
| Mean ± SD | — | — | — | — | **39.1 ± 3.8%** | **37.2 ± 2.3%** | **80.6 ± 4.2%** |

A total of 1,297 objects was scored. Individual objects are not treated as independent experimental replicates.

## Interpretation and uncertainty

- There are substantial red-positive populations in all three fields, including many objects that are not prominent in a bright-green display. Most green-only detections have detectable blue-channel fluorescence.
- The primary viability definition is **green only / (green only + red only + double positive)**. Green/red matches are counted once and assigned to the membrane-compromised group. The [Thermo Fisher L3224 kit](https://www.thermofisher.com/order/catalog/product/L3224) uses calcein-AM and ethidium homodimer-1 to assess esterase activity and membrane integrity. A green signal alone does not override a positive red signal.
- **20.6 ± 2.6%** of detected objects are double positive. These may include compromised cells, imperfectly separated neighbors, or optical cross-talk; single-color controls were not provided. If double-positive objects are omitted entirely, the fraction among the remaining objects is **49.2 ± 5.1%**. That is a different, restricted denominator, not the primary viability result. Counting every green-positive object as live would give 59.7 ± 4.6% and ignore the red signal in double-positive objects.
- **Detection threshold uncertainty exceeds the reported between-image SD.** Independently varying green and red detection cutoffs by ±25% gives a mean viability range of **34.0–47.3%**. This is an algorithm sensitivity range, not a confidence interval. An independent component-counting method without watershed splitting gave approximately **38.9 ± 4.6%**, supporting the primary estimate despite different segmentation choices.
- EBFP detection at q thresholds of 0.01–0.10 gives mean fractions of **32.2–40.7% overall** and **72.8–83.1% among live-only objects**. Small changes in ROI dilation give much narrower variation. A computational spatial-shift diagnostic gives only 0.6–2.1% detections at the original cutoff, supporting cell-associated signal above spatial background.
- “EBFP detected” means significant **cell-associated blue-channel signal relative to nearby image background**. It does not establish reporter specificity without an EBFP-negative control, ongoing protein production, expression rate, or retention relative to day 0. Absence of detected fluorescence does not establish loss of expression. EBFP signal is also detected in 9.4 ± 0.5% of red-positive detections, consistent with fluorescence not being a substitute for the live/dead assay.
- Cells negative in both viability channels, poorly resolved cells, very dim cells, cells outside the focal plane, and touching cells that cannot be separated reliably are not fully represented. At a 5×/0.15 objective and 2.273 µm/pixel, the relative sampling of cytoplasm and nuclei can affect a single-plane estimate.

## Original files and provenance

Nine TIFFs were supplied: live, dead, and EBFP channels for S5–S7. Each is a single 1024 × 1024 image. The user confirmed a 2327.27 × 2327.27 µm field, giving 2.272724609375 µm/pixel and 5.4161856529 mm² per field.

Files are 8-bit, indexed-color TIFFs. Their palettes are identity mappings to the named channel, so the stored pixel indices are the scalar channel values. Embedded acquisition metadata also reports uint8 and one Z plane; a higher-bit source cannot be assumed to exist. The blue data are sparse and quantized, with most nonzero pixels at value 17. Green-channel pixels at the stored upper limit (255) number 791, 796, and 329 in S5, S6, and S7 respectively. Quantitative green intensity comparisons would therefore require additional care.

**No original TIFF was overwritten, rescaled, filtered, registered, or otherwise modified.** The SHA-256 checksums in `data/source_manifest.json` were verified after analysis. All processing below produces separate analysis arrays, masks, tables, and figures.

## Detection and counting method

The same settings were applied to all three samples. Thresholds were selected after visual examination of full fields and object crops to exclude isolated speckle clusters while retaining resolved cellular or nuclear profiles. These are heuristic image-analysis parameters, not thresholds validated with biological positive and negative controls.

1. Read each original TIFF into a scalar floating-point analysis array, preserving the stored values.
2. For **detection only**, calculate local contrast as Gaussian-smoothed intensity minus a broad Gaussian background estimate (σ = 12 pixels). Use σ = 1.2 pixels for green and 1.0 pixel for red. This derived contrast array is not used as the representative microscopy image.
3. Segment candidate regions with high/low contrast thresholds of **10/5 for green** and **4/2 for red**, in stored 8-bit intensity units. Require a region to contain a peak above the high threshold; grow it within the low-threshold mask.
4. Identify local maxima in a 7 × 7 neighborhood, require peak separation of at least 6 pixels, and use a priority-flood watershed to separate touching candidates. Require final region areas of **at least 20 pixels for green** and **12 pixels for red**. These correspond to intensity-defined areas of approximately 103 and 62 µm², not true cell/nuclear cross-sectional areas. Exclude any detection touching an image border.
5. Associate green and red detections one-to-one by minimum centroid distance, with a maximum distance of 6 pixels (13.6 µm) and overlap after one-pixel dilation of the green mask. No channel registration transform is applied. Green–blue alignment is within approximately one pixel by correlation.
6. Count each matched pair once as double positive. Unmatched green detections are live only; unmatched red detections are dead only. The union of these objects is the denominator for the primary metrics.

All segmentation labels, centers, areas, and classifications are supplied. `data/S*_green.csv` and `data/S*_red.csv` provide channel-specific detections. `data/S*_masks.npz` contains integer labels for `live`, `dead`, and combined `objects`. Object identifiers restart within each sample. Numbered QC panels correspond to `data/ebfp_objects.csv`.

## EBFP measurement

Blue is **not used to detect cells or choose the viability denominator**.

- Measure the sum of unchanged blue pixel intensities in each combined object mask with one-pixel dilation. Exclude pixels assigned to neighboring object labels.
- Translate the same-shaped mask to eligible cell-free locations 18–65 pixels away. Exclude overlap with detected-object masks expanded by two pixels. Thousands of local placements typically provide a shape- and area-matched empirical background distribution.
- Calculate the one-sided empirical probability as `(1 + number of background sums ≥ observed sum) / (1 + number of background placements)`.
- Apply Benjamini–Hochberg adjustment across all 1,297 objects; call blue signal detected at **q ≤ 0.05**. These q values quantify enrichment relative to the image background model, not biological validation of reporter expression.
- Sensitivity to q threshold, mask dilation, and a 35-pixel computational shift is supplied in `data/ebfp_validation.csv` and `data/ebfp_methods.json`. Shifts are diagnostic array operations only and do not affect the main measurement or any displayed source image.

## Figure legend and image integrity

**CHO–EBFP2 cells encapsulated in PEG–PEG gels after 48 h of culture.** (A) Green-only fraction among the union of detected green/red objects. (B) Detectable EBFP signal among all detected objects and among green-only objects. Symbols show S5–S7; black bars show the unweighted mean ± sample SD. (C) Representative images from S6, the sample with the median viability estimate. The crop is fixed at x = 320:704 and y = 320:704 pixels for every channel, approximately 873 × 873 µm. Counts use the entire available field, not this crop. Scale bars, 100 µm.

Displayed microscopy images use only fixed linear mappings, shared across samples: **green 0–120; red 0–30; blue 0–34**. Values above these bounds are clipped for display only. EBFP is shown in grayscale separately for legibility and in blue in the RGB merge. There is no image denoising, background subtraction, nonlinear gamma, selective enhancement, registration, or replacement of image content in the figure. The linear display scaling is separate from both the original files and analysis measurements. All-field images and detection overlays are included for review.

## Files and reproducibility

- `figures/CHO_48h_main_figure.png`: high-resolution main figure.
- `figures/CHO_48h_main_figure.svg`: figure with editable vector plot elements and embedded raster microscopy panels.
- `figures/CHO_48h_all_fields.png`: all nine full-field images with consistent display settings.
- `figures/CHO_48h_threshold_sensitivity.png`: independently varied green/red cutoffs.
- `qc/S*_numbered_detections.png`: numbered detections over a display-only RGB composite; cyan outer rings mark detected EBFP.
- `data/summary.csv`: sample-level counts and fractions.
- `data/ebfp_objects.csv`: per-object classification, coordinates, raw-blue measurements, background values, p values, and q values.
- `data/source_manifest.json`: source checksums and image properties.
- `analysis/`: Python source for segmentation, sensitivity, EBFP, aggregation, figure generation, and QC.

Place the nine unchanged TIFFs in a directory and run:

```bash
python analysis/run_all.py --source /absolute/path/to/original_tiffs
```

Dependencies are Python 3.12 with NumPy, SciPy, pandas, Pillow, and Matplotlib. Exact versions used are listed in `analysis/environment.json`. Output tables and figures are written into this analysis directory. The script checks source checksums before and after processing. Original TIFFs are not duplicated in this package because they were already supplied separately.

## Most useful follow-up

For a stronger biological estimate, confirm which gel each image came from, collect multiple randomly selected fields and focal depths per independently prepared gel, and acquire matched live/dead and EBFP-negative controls. Higher-magnification images or a compatible independent cell-count marker would help distinguish weak cells from background and resolve double-positive objects. The current images do support detectable EBFP in many surviving cells, but do not establish uniform survival through the depot.
