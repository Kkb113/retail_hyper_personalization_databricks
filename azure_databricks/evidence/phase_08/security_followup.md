# Dependency security follow-up — 2026-09-08

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
