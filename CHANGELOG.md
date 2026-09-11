# Changelog

## 1.1.0

- EBFP is optional per field. Omit the column or leave individual cells blank.
- Missing EBFP is explicitly not measured; measured images with no blue signal remain analyzable.
- Live/dead segmentation, matching, and viability formulas are unchanged.
- Missing EBFP objects do not enter the pooled multiple-testing family.
- Reports expose channel availability. Figures adapt to live/dead-only and mixed runs.
- Explicitly named missing files still cause an error.
- Version 1.0.0 configurations remain accepted.

## 1.0.0

Initial reusable release with reference data, numerical reproduction checks, fixed configuration, source/output hashes, and replicate-aware summaries.
