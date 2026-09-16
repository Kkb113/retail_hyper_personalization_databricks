# Unified pricing App — Phase 4 status

## Current completion run

Public PRs and merges are now authorized. The prerequisite Genie PR #22 passed
all six CI checks and was merged. One additional estimated INR 250 validation
window is authorized (cumulative reservation ceiling INR 500, no ledger reset).
Browser acceptance remains pending by the owner's explicit choice.
The App-only lazy-driver source adaptation passed six minimal-Linux tests, and
381 retail tests passed. Registered pricing weights and source remain unchanged.
The previous failed deployment record below is retained as historical evidence;
final live verification is pending at this checkpoint.

2026-09-16: **Not accepted. Both coordinated branches remain local at the owner's request.**
No push, PR or GitHub CI run was performed.

The existing App received the unified pricing package in one approved INR 250
estimated validation window. The deadline was not extended; no new paid service
was created. The App, SQL warehouse and recommendation endpoint were confirmed
STOPPED after testing. Storage and baseline charges are not eliminated by stopping
compute; the allowance is not a hard invoice cap.

## Verified

- 380 retail tests passed, four skipped; lint and type checks passed.
- Six frozen-pricing tests passed on Windows, including portable-path validation.
- Non-admin HTTP access rejected the unauthorized customer and returned five
  retail recommendations. Temporary test credentials were revoked.
- The corrected immutable snapshot deployed successfully. Deployment success did
  not establish pricing readiness: all live pricing checks still returned unavailable.
- A local Linux probe reproduced a missing `libodbc.so.2` startup dependency via
  a database-audit import in the frozen model package. The Windows-specific MLflow
  entry-point metadata was separately normalized only in the derived App copy.

## Remaining

1. Isolate SQL audit dependencies from inference with recorded source changes and
   full accepted-model/business-policy parity evidence, then load the actual release
   in a minimal Linux runtime matching the App dependency lock.
2. Retest live pricing, combined requests, five concurrent sessions and latency
   within a separately authorized window; the existing reservation must not reset.
3. Owner browser acceptance remains pending by choice.
4. Linux CI/publication are deferred by choice. Do not publish without approval.

Private startup diagnostics have been added locally and release tests pass; that
logging change is not in the deployed snapshot. Do not describe Phase 4 as complete
or advance to Phase 5 yet. Detailed pricing-side findings are in the companion
`azure_databricks/PHASE4_STATUS.md` on `codex/azure-phase4-unified-app`.
