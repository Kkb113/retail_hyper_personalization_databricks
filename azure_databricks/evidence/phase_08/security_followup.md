# Dependency security follow-up — 2026-09-08

## Correction after live artifact verification

The initial gate below was overly broad: it did not account for the Phase 6
packaging repair already applied to Champion 3. Read-only download of Champion's
actual `requirements.txt` now confirms **MLflow 3.16.0**, exactly matching
`environments/phase6_model_requirements.txt`. The stopped endpoint is configured
to serve that same version 3. See `runtime_security_verification.json` and the
existing [Phase 6 review](../phase_06/security_review.md).

All 19 currently reported affected ranges exclude 3.16.0. These observed alerts
therefore do not require another Champion upgrade or block Phase 9 on their own.
They remain visible against the archived Phase 5 requirements; no alerts were
dismissed and no historical pin was rewritten. The local 3.15.0 install attempt
was cancelled before a model test, since it was unnecessary and older than the
already validated runtime. No Azure compute was started.

This is scoped triage, not a claim that all dependencies or managed platform
components are vulnerability-free. New Phase 9 dependencies still require their
own security and compatibility checks. The original caution is retained below
as historical context, superseded by this verification.

## Original caution (superseded where it implies Champion still needs upgrading)

GitHub reported 19 open Dependabot alerts during the Phase 8 push: six critical,
eight high, four medium and one low. All point to the historical/frozen
`azure_databricks/environments/phase5_model_requirements.txt` pin `mlflow==3.8.1`.
This file records registered model requirements; it is not the Phase 8 agent lock.
Alerts are not resolved merely because contract tests or CI pass.

The new agent-runtime wheel smoke verifies that the Phase 8 tool adapter imports
without MLflow, NumPy, pandas or joblib. There is no newly deployed App, standalone
MLflow HTTP server or gateway in this phase, and the existing recommender is
stopped. These are scope observations, not proof that all advisories are inapplicable
to the managed model runtime.

One relevant example is [GHSA-gqvg-gmmx-x4hm](https://github.com/advisories/GHSA-gqvg-gmmx-x4hm),
which describes a model-loading deserialization-control bypass, with a reported
first patched version of 3.15.0. Other alerts cover server, gateway and artifact
routes; some have no patched version in the returned advisory metadata. A single
version bump must therefore not be described as resolving every alert without
rechecking them.

Before deploying the Phase 9 agent or approving a broader demonstration:

1. Triage every open advisory against the actually used managed runtime/features.
2. Select a supported patched runtime for new agent dependencies. Do not reuse
   the captured Phase 5 requirements as a new agent environment.
3. If model runtime remediation is needed, create a new candidate and repeat
   compatibility/golden/parity checks under a separately reviewed cost plan.
4. Keep artifact loading restricted to approved, hash-verified sources; do not
   load arbitrary uploaded models or expose standalone MLflow services.
5. Retain unresolved alerts. Do not dismiss them just to make GitHub green.

The frozen model and Champion alias were not silently changed during Phase 8.
This remains a security review gate, separate from functional Phase 8 acceptance.
