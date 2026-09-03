# Phase 0 codebase review

Reviewed on 2026-09-03 for the clean Azure Databricks implementation.

## Conclusion

The repository contains a valuable, tested recommendation implementation and a
large earlier Databricks POC. It should be treated as a migration source, not
deployed unchanged. The Azure workspace and catalog are new relative to the
legacy configuration, and several earlier choices were tailored to Databricks
Free Edition.

## Repository inventory

- 728 files were tracked before the new Azure Phase 0 files.
- 69 tracked files are under data_cache.
- 74 tracked files are under artifacts.
- The data and frozen model package is small enough for a governed Unity Catalog
  volume transfer.
- Tests cover data engineering, recommendation models, business rules, agent
  behavior, Streamlit, the local custom app, and the earlier Databricks bundle.

## Reusable implementation

| Area | Reusable source | Decision |
|---|---|---|
| Frozen asset integrity | Original source: `custom_app/artifacts.py` | Imported as the self-contained 26-file Azure hash contract |
| Data contract | Original source: `custom_app/contracts/asset_inventory.json` | Imported as the self-contained 20-file Azure data contract |
| Existing-customer inference | Original source: `custom_app/service.py` | Extract domain logic into the future functional MLflow package |
| New-customer inference | Original source: `custom_app/service.py` | Extract after Azure compatibility tests |
| Business filters | Original source: `src/final_recommender.py` and `custom_app/service.py` | Preserve deterministic eligibility and inventory rules |
| Retrieval and rankers | artifacts/retrieval_v2, artifacts/ranker_v2, artifacts/cold_start_v1 | Transfer only after complete hash verification |
| Agent contracts and evaluations | agent/contracts and agent/evals | Reuse cases and schemas selectively; replace runtime wiring |
| Databricks data notebooks | Original source: `notebooks/databricks` | Use as behavior references; parameterize and remove Free Edition assumptions |
| App user journeys | app and custom_frontend | Reuse product concepts; rebuild deployment and authorization for Databricks Apps |

## Findings

### P0-001 — Critical: the legacy catalog is wrong for Azure

The existing bundle, notebooks, scripts, app settings, and contracts repeatedly
use **retail_hyper_personalization**. The verified Azure catalog is
**intellify_databricks_demo**. Deploying the old bundle risks creating a
duplicate namespace or failing against the wrong one.

Resolution: the Phase 0 scope contract pins the verified catalog. Phase 1 must
create a clean bundle and prohibit the legacy value in its Azure target.

### P0-002 — Critical: the old MLflow notebook is not functional inference

The earlier model-training notebook logs component migration/status models and a
FrozenRecommendationBundle backed by a recommendation snapshot. It proves
migration determinism but does not package candidate retrieval, ranking,
cold-start inference, routing, or live business filtering.

Resolution: Phase 5 must package the actual composite recommender. Snapshot
lookup remains a parity fixture and batch fast path, not the model definition.

### P0-003 — Critical: the old migration manifest is incomplete

The earlier model migration contract lists fewer files than real runtime
inference requires. Notable omissions include ALS factors, content matrices,
known/low-history rankers, and the cold-start inference reference.

Resolution: Phase 0 derives one 45-file manifest from the 20 data assets and the
approved 26-file model map. One recommendation snapshot serves both roles, so
the unique total is 45.

### P0-004 — High: personal workspace identity is embedded in legacy config

The old dev and POC configuration contains a named personal profile and owner.
That is unsuitable for a clean client repository and repeatable automation.

Resolution: Phase 0 commits only scope fingerprints and redacted identity state.
Phase 1 must use runtime authentication and later a least-privilege workload
identity.

### P0-005 — High: paid resources are active in the old bundle

The old bundle includes app, vector-search, Lakebase, pipeline, dashboard, Genie,
and many job resources. Some are active for dev and POC. This conflicts with the
new free-first rule and could create avoidable spend if deployed as-is.

Resolution: no old bundle deployment is permitted. The proposed-resource plan
sets every new capability off by default and explicitly rejects a vector-search
endpoint for the current 3,000-product catalog.

### P0-006 — High: the hardcoded foundation endpoint is not in live inventory

Legacy agent code defaults to a Luna endpoint name that is not among the
foundation endpoints currently visible in the Azure workspace.

Resolution: Phase 9 will run a live model bake-off. No LLM endpoint is selected
in Phase 0.

### P0-007 — High: production release remains on HOLD

The frozen release decision is HOLD_PENDING_FUTURE_HOLDOUT. Required holdout and
owner approvals are incomplete, and all results use synthetic data.

Resolution: the Azure work is explicitly a POC. Migration parity is allowed;
production claims are not.

### P0-008 — Medium: serialized-model compatibility must be proven

The current machine loaded the frozen artifacts using newer numpy, pandas,
PyArrow, scikit-learn, XGBoost, and joblib versions than the repository pins.

Resolution: Phase 5 requires an explicit Databricks compatibility matrix and
MLflow dependency lock before serving.

### P0-009 — Medium: duplicated application and agent implementations

The repository contains Streamlit, a local custom application, a React app, and
multiple agent packages. Copying them all would preserve conflicting contracts.

Resolution: Phase 1 establishes a single Azure package. Reuse occurs by tested
behavior and contract, not wholesale folder migration.

## Security review

- The Phase 0 committed inventory contains no raw subscription, tenant, ARM
  resource, principal, or token values.
- Synthetic data is the only approved classification.
- No production customer data transfer is allowed.
- Existing public network access is recorded as a POC limitation, not silently
  presented as a production design.

## Go-forward rule

Only the new azure_databricks subtree is authoritative for Azure deployment.
Legacy files remain read-only sources until a later phase replaces or archives
them after parity.
