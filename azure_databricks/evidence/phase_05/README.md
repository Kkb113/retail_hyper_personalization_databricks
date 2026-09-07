# Phase 5 — complete; functional MLflow Candidate registered

## Outcome

The actual adaptive retail recommender is packaged as a self-contained MLflow
PythonModel and registered in Unity Catalog as
`intellify_databricks_demo.ml.adaptive_recommender`, version 1. It is owned by
`retail_hp_admins` and has the **Candidate** alias. **Champion is intentionally
unset** because the source release remains `HOLD_PENDING_FUTURE_HOLDOUT`.

The runtime includes ALS, metadata, content/session, co-purchase, promotion and
segment retrieval; the 92-input warm LambdaMART and 34-input cold LambdaMART
rankers; adaptive blending; eligibility, inventory, durable-purchase and
diversity rules; explanations; and deterministic fallbacks. It imports no UI,
LLM, Spark, database or Azure client at inference time.

Four synthetic golden requests exercise known, low-history, new-customer and
scenario routes. The repository runtime, Databricks registration runtime and a
fresh download of the registered artifact all returned the same 40 rows and
canonical SHA-256. Exact route/product ordering, scores within `1e-12`, 100%
inventory validity and 100% request coverage passed. The packaged model has an
explicit input/output signature, input example and eight pinned dependencies.

## Cost and stopped state

Six bounded one-time serverless attempts were terminated: five failed while
hardening runtime-specific compatibility and one accidental duplicate was
canceled. The final failed status occurred only after registered-model reload,
exact parity, validation tags and Candidate assignment; it was caused by an
unsupported post-validation `ALTER MODEL ... OWNER` statement. Ownership was
then repaired and verified through the control-plane SDK with no compute.

The cumulative runtime was 992.69 seconds. At the deliberately conservative
16 DBU/hour and INR 44.91/DBU-hour assumptions, the Phase 5 estimate is INR
198.1409 pre-tax, below the INR 250 ceiling. It is not an invoice guarantee.
Zero clusters and persistent jobs remain; the SQL warehouse is **STOPPED**. No
Azure resource, schedule, serving endpoint, app, GPU, LLM or vector index was
created.

## Evidence

- `artifact_inspection.json`: frozen retrieval/ranker shapes and baseline metrics.
- `local_validation.json`: identifier-free deterministic golden result.
- `dependency_report.json`: downloaded Candidate package metadata.
- `compatibility_matrix.json`: repository, Databricks and newer-local MLflow checks.
- `owner_control.json` and `registry_inspection.json`: owner and alias state.
- `validation_attempts.json`: sanitized attempt categories and cost estimate.
- `cloud_reconciliation.json`: live registry, budget and stopped-state audit.
- `final_compute_state.json`, `acceptance.json`, `completion.json`: exit decision.
- `review_and_research.md`: design rationale, limitations and official sources.

Do not promote Candidate to Champion or create serving infrastructure in Phase 5.
Those actions require later phase authorization and the outstanding holdout gate.
