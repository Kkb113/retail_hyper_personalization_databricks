# Phase 8 PR follow-up — 2026-09-08

PR #16 had no submitted reviews, issue comments or inline review comments when
queried. The owner's request was therefore handled as a direct implementation
review rather than an assumed list of GitHub findings.

## Corrected

1. Feedback confirmation now requires the boolean `True`, not a truthy string,
   integer or collection.
2. Feedback membership validates the returned recommendation and requires the
   requested product ID; an unrelated nonempty backend response is insufficient.
3. Semantic search rejects a stale index missing any currently filtered eligible
   product before making a paid embedding call, rather than silently returning
   incomplete search results.
4. Snapshot versions, timezone-bearing timestamps and product identifiers are
   validated instead of coercing arbitrary values to strings.
5. Embedding-call quota reservation is atomic across concurrent callers, includes
   failed attempts, and occurs before network work. Response index is checked.
6. Batch recommendation/explanation lookup joins the current eligible-product
   view, so a newly unavailable product cannot authorize feedback or be returned
   solely because it appeared in an older batch.

Sixteen additional offline regression cases cover these changes and read-only
runtime verification. The complete suite passes 226 tests. No notebook, model release,
warehouse configuration, source artifact or Azure resource was changed. The new
SQL join is regression-tested offline, not claimed as a fresh live SQL test.
Existing Phase 8 live acceptance remains historical evidence for the base tools.

## Security triage corrected and verified

All 19 open alerts still reference the captured `mlflow==3.8.1` model requirements.
The current advisory affected ranges exclude 3.16.0, including the two alerts
whose `first_patched_version` field is null. A read-only download of Champion 3's
actual requirements confirmed it already uses MLflow 3.16.0 from the Phase 6
repair. The endpoint references version 3 and remains STOPPED. The earlier
statement that another runtime upgrade was required was incorrect; see
`runtime_security_verification.json`. No model was loaded or invoked for this check.

Primary examples:

- [Model-loader deserialization bypass](https://github.com/advisories/GHSA-gqvg-gmmx-x4hm):
  affected below 3.15.0; reported patch 3.15.0.
- [Job API authorization](https://github.com/advisories/GHSA-7qhf-v65m-g5f3):
  current affected range at or below 3.10.1.
- [Temporary-file permissions](https://github.com/advisories/GHSA-f2m9-wcf4-cwwx):
  reported patch 3.11.0.

No second upgrade or paid parity run is needed to address these specific alerts.
Do not dismiss the historical alerts or rewrite the old capture. The local 3.15.0
installation attempt was cancelled before running a model once the existing
Phase 6 repair was identified. Phase 9 can proceed with its normal dependency,
authorization and cost gates; this triage is not blanket production security clearance.
