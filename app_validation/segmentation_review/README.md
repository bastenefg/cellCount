# Supplied test1 DEAD signal review

The existing settings produce 106 LIVE-channel detections and 1,402
DEAD-channel detections. Matching gives 72 live-only, 1,368 dead-only and 34
double-positive objects: 4.885% viability under the existing definition.
Re-running the unchanged `pipeline.core.segment` function reproduces the saved
DEAD label array exactly.

The baseline accepts many small, weak DEAD objects:

- 1,199 of 1,402 (85.5%) have maximum raw intensity at most 15.
- 973 (69.4%) have area at most 20 pixels; the minimum is 12 pixels.
- 1,096 (78.2%) have peak background-subtracted contrast below 6; the high
  threshold is 4.
- 1,225 (87.4%) come from regions containing only one seed. Splitting large
  regions is therefore not the source of most detections.

The test1 DEAD image contains 87.70% zero-valued pixels and 12.05% pixels at
exactly 15. Together these two values account for 99.75% of pixels. In the
reference DEAD images, the common nonzero values are 3 and 4. The median peak
contrast of test1 DEAD detections is 5.03, versus 10.47, 12.26 and 11.09 for
reference S5, S6 and S7. The absolute segmentation settings therefore encounter
different intensity distributions.

The user subsequently confirmed that `test1_dead.tif` was processed and
re-saved. These diagnostics measure that stored TIFF. References to raw
intensity mean values before this pipeline's processing, not an unprocessed
camera acquisition. Processing may change intensities and noise; final
quantitative analysis should preferably use original acquisition exports.
Tuning a preset cannot recover signal lost in an earlier processing step.

In the diagnostic panels, many test1 DEAD contours surround small speckle
clusters away from the larger bright structures. This supports the concern
that the existing thresholds admit background-like signal. These observations
do not determine which detections are cells or establish a corrected viability.
Thresholds and object-size criteria require visual validation against the
images and appropriate experimental controls.

## Artifacts

- `test1_raw_and_original_boundaries.png`: full field and central crop showing
  raw LIVE, raw DEAD and unchanged channel contours.
- `dead_reference_comparison.png`: reference S5 and test1 at identical DEAD
  display limits, with unchanged contours.
- `dead_signal_diagnostics.json`: counts, raw-intensity distributions and
  parameters used.
- `detection_comparison.csv`: compact per-image statistics.
- `review_dead_signal.py`: reproducible read-only diagnostic script. Run from
  the project folder with `.venv/Scripts/python.exe
  app_validation/segmentation_review/review_dead_signal.py`.

The PNG display limits are 0–120 for LIVE and 0–30 for DEAD. They affect only
appearance, not raw arrays or segmentation. No input images, configuration,
pipeline code or previous results were changed.
