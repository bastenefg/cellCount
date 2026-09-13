# Version 1.3.1 validation

The summary figure now displays the entire representative field with outlines
from saved channel/object masks. Region/Peak thresholds and associated settings
come from the completed run's effective configuration. The original counting
pipeline and completed-run artifacts are preserved.

- `all_tests.log`: 128 passing tests, including uncropped boundary pixels,
  saved mask edges, exact threshold text, changed-source/settings rejection,
  asynchronous run switching, cache recovery, and export during a run change.
- `source_smoke.json`: source-app checks on an existing Leica projection run.
- `packaged_smoke.json`: the same checks through the compiled Windows app.
- `release_audit.json`: original package hashes, bundled source/data identity,
  dependency notices, and exact ZIP contents.

Both GUI checks verify full-field extent, the saved segmentation configuration
and mask hash, PNG/SVG export equality while a detection image is selected, and
unchanged original run figures. Verification of the saved run also passes.
Private microscopy, screenshots, exports, and paths from these checks are kept
outside the published validation directory. The two GUI smoke reports retain
only verification flags; they do not assess biological counting accuracy.
