# Live/Dead Cell Counter 1.2.1

This patch adds PNG/SVG summary export, identifies the loaded run beside the
preview, and clears stale images when a selected preview cannot load.

- All 10 tests in `tests/test_gui_review.py` passed, including four new regression
  cases for run switching, missing/corrupt previews, current-run export and
  preservation of completed run files.
- `source_smoke/smoke_test.json` and `packaged_smoke/smoke_test.json` passed:
  real widget rendering, PNG/SVG byte equality while a detection is selected,
  and worker verification confirming that the original run stays intact.
- `release_audit.json` passed: 81 original files unchanged, 167 bundled source/data
  mappings matched, and all 1,505 ZIP files matched the built app.
- Screenshots and exported figures here use the previously supplied test pair.

The scientific pipeline was not changed. Full analysis/reference benchmarks
were not rerun for this GUI-only patch; the 1.2.0 evidence remains available in
`../release_1.2.0/`.
