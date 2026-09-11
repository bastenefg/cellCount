# Interactive preview performance validation

The preview still segments the original full-resolution arrays using the unchanged `pipeline.core.segment`. `PreviewSession` retains TIFF inputs and independent LIVE and DEAD segmentation results in bounded memory. TIFF cache keys include the resolved path, file size, nanosecond modification/creation times, file identity and input configuration. Channel cache keys include the input, shared segmentation settings and that channel's settings. Matching changes reuse both segmented channels; display and EBFP settings do not trigger segmentation.

Profiling the supplied `samples.csv` field identified repeated full-image scans in `pipeline.core.combine` as the largest computation cost: 8.62 of 11.80 instrumented seconds. The preview-only `desktop.preview_matching.combine_preview` retains the original centroid-distance, overlap, dilation and Hungarian matching logic and original object ordering. It replaces the repeated per-object full-image masks with integer lookup arrays. Each channel label maps to its assigned object ID; the pixelwise maximum selects the last object's write exactly as the original implementation does. A matched object's area is the sum of its channel areas minus their intersection. Final mask areas and overwritten/lost flags follow from the same resulting label image. The original batch analysis implementation is unchanged.

`tests/test_segmentation_preview.py` checks complete combined label and object-record equality against the original implementation on all three reference images and `test1`, plus synthetic matching ties, fractional centroids, both dilation settings, matched overlaps, overwritten/lost masks and empty channels. Session tests verify reuse, file/configuration invalidation, read-only arrays, detached metadata and bounded cache sizes. Uncompressed worker payloads round-trip exactly and never overwrite an existing output folder. All 11 backend tests passed.

Run the reproducible benchmark from the project directory:

```
.venv\Scripts\python.exe app_validation/performance_review/benchmark_preview.py
```

`benchmark_results.json` contains three measured repetitions, their medians, original-pipeline baselines and compressed/uncompressed payload timings. The benchmark checks exact masks, contrasts and records at original settings and after a DEAD threshold edit and matching-distance edit. Computation timings exclude process startup, GUI painting and debounce; they are host-specific measurements, not latency guarantees. No source images, reference configuration or original pipeline files are written by this validation.
