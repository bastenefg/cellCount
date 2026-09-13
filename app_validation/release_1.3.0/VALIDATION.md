# Live/Dead Cell Counter 1.3.0

This release adds native Leica LIF/LOF import and synchronized peak/region
threshold sliders. The original counting pipeline is unchanged.

- All 117 tests passed (`all_tests.log`), covering original TIFF behavior,
  import selection, axis order, uint16 preservation, projections, cancellation,
  cache integrity, provenance, channel mapping and slider scheduling.
- Both focused Leica dialog tests passed again after the compact layout fix.
- Five real LIF/LOF cases matched independent full-image NumPy extraction exactly,
  including a 115-plane projection. Source file hashes remained unchanged.
  `leica_reader_validation.json` records timings and scope without private paths.
- Source GUI import, slider review and full analysis passed. The final compiled
  app passed LIF and LOF import/preview checks; its LOF projection analysis matched
  the preview counts and saved import provenance. Summarized runtime reports are
  `source_leica_smoke.json`, `packaged_lif_smoke.json` and `packaged_lof_smoke.json`.
- Importer layouts were checked at 1040x820 and 880x720. The smaller layout scrolls
  without overlapping controls. Peak/region slider layouts were checked at
  1460x900 and 1020x660.
- `release_audit.json` confirms all 81 original files unchanged, 174 bundled
  source/data mappings identical, required notices included, and all 1,514 ZIP
  files matching the built app.

Real microscopy files, extracted TIFFs, counts and screenshots used for local
Leica checks remain in the ignored build directory. The published setup screenshot
contains no imported images. Real uint16 Leica fixtures were unavailable;
synthetic 12-bit data stored in uint16 were preserved exactly. Projection counts
remain 2D counts and must not be interpreted as 3D cell counts.

The earlier full scientific reference validation remains in release_1.2.0.
Performance measurements are specific to the tested machine/files and do not
control the operating-system disk cache.
