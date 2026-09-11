# Desktop app validation

The desktop interface wraps the existing pipeline. `run_pipeline.py`, every
original `pipeline/*.py` file, the reference settings and data, and all other
81 files listed in `PACKAGE_SHA256.json` remain byte-for-byte unchanged.

## Scientific reproduction

`scientific_validation.json` records a fresh reference run and comparisons:

- All 22 original correctness tests pass.
- All nine 1024 × 1024 label arrays match exactly, with zero differing pixels.
- All 1,297 object records and 486 EBFP calls reproduce within the original
  comparison tolerance. The greatest floating-point difference is 5.68e-13.
- Apparent viability is 39.082577% ± 3.827716% sample SD across the three images.
- Source, analysis-code and output checksum verification passes.

The environment uses Python 3.12.14 on Windows, compared with the recorded
Python 3.12.13 environment. All scientific package versions match the supplied
lock. Numerical agreement is verified; image rendering need not match bytes.

## Existing test dataset

`existing_sample_validation.json` records a run through the GUI's input adapter
and worker dispatcher using the existing `samples.csv`, its explicit `test1`
replicate, and the existing settings. It reproduces the archived
`runs/new_experiment` results: 72 live-only, 1,368 dead-only, 34 double-positive
objects, 1,474 total, and 4.884668% apparent viability. All three masks and all
object records agree. EBFP remains unmeasured. This establishes implementation
fidelity; it does not independently validate the acquisition or biological model.

## Interface and worker checks

`all_tests.log` records 42 passing tests: the 22 original tests and 20 GUI tests.
The added tests cover parameter precision, missing measurements, explicit
replicate IDs, portable filenames, preservation of prior results on malformed
input, invalid TIFFs, worker errors, and cancellation. Synthetic fixtures are
identified as such and are separate from experimental data.

`gui_smoke.json` records a real Qt event loop running a complete synthetic
blank-image analysis and subsequent file verification. `final_source_smoke/`
contains screenshots and verification from the app's diagnostic entry point.
The setup, results and reference pages were also inspected at 1000 × 650;
scrolling keeps controls accessible on smaller screens.

## Packaged application

The `portable_build*` logs record Windows package builds. The executable's fresh
reference run in `runs/packaged_reference_validation_v2` passes the full original
comparison, and its file verification passes. `packaged_gui_smoke/` records the
executable rendering the actual interface and verifying that run through its
own subprocess worker.

Packaging tests exposed and resolved two dependency issues: the SVG exporter
needed explicit inclusion, and an unrelated ICU 78 library shadowed the Windows
ICU library required by Qt. The specification includes both Agg and SVG and
excludes that incompatible ICU pair. No scientific code was changed for either
fix. Earlier failed-run records are retained so the successful checks are not
confused with the initial attempts. `packaged_release.json` records the passing
final package audit (156 matching files), native Windows startup and clean
shutdown, GUI checks, ZIP CRC verification, and distribution SHA256. The
shareable ZIP is `dist/CHO-Cell-Counter-Windows-x64.zip` (106.1 MiB).

`build_environment.txt` records installed versions. Test logs and result folders
are retained for inspection; none replace the original reference records.
