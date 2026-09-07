# Phase 4 — complete; Lakehouse validated and platform stopped

## Outcome

The governed Lakehouse and offline feature foundation is live in
`intellify_databricks_demo` from the sealed `retail_hp_transfer_v1` snapshot:

- 20 append-only Bronze Delta tables
- 20 conformed and idempotent Silver Delta tables
- 3 Unity Catalog feature tables with primary-key contracts
- 6 deterministic Gold views
- 1 column-level data dictionary containing 478 rows
- 50/50 expected objects present, with no missing or unexpected project objects
- all 50 objects owned by the approved `retail_hp_admins` group

The validation reconciled 275,630 source rows through both Bronze and Silver,
found zero duplicate-key groups, zero future-data leakage, zero encoded
range violations and zero tested foreign-key orphans. Local independent pandas
golden metrics match the Databricks feature aggregates.

The current Gold snapshot contains 5,000 Customer 360 rows, 3,000 Product 360
rows, 2,860 eligible products and 15,930 frozen current recommendations. Phase 5
must package the functional recommender; the Gold view is not represented as a
replacement for that model.

## Cost and stopped state

Four bounded one-time serverless build attempts were terminated. The fourth
created all 50 objects but timed out during its final validation path, so no fifth
serverless build was run. A single read-only reconciliation query reused the
existing 2X-Small warehouse, completed in 52.17 seconds and explicitly returned
the warehouse to **STOPPED**.

The conservative cumulative elapsed-time estimate is INR 161.1619 pre-tax,
below the Phase 4 INR 250 execution ceiling. This is not a hard invoice guarantee:
Azure usage reporting can lag, and taxes, discounts and small managed-storage
charges are outside the estimate. Final inventory is zero all-purpose clusters,
zero persistent jobs and a stopped project warehouse. No Azure resource,
schedule, continuous pipeline, endpoint, app, LLM or vector index was created.

## Runbook

Read-only local and metadata operations:

```powershell
python -m retail_hp_azure.phase4 plan
python -m retail_hp_azure.phase4 inspect
python -m retail_hp_azure.phase4_sql_validation plan
```

The build runner and SQL validator remain in the repository for reproducibility,
but the Lakehouse is already built. Do not rerun either casually. Any future
mutation requires a fresh cost review and explicit approval. The SQL validator
always stops the warehouse in `finally`; metadata inspection starts no compute.

## Evidence

- `local_validation.json`: identifier-free independent golden metrics.
- `metadata_inspection.json`: final 50/50 Unity Catalog object inspection.
- `ownership_repair.json`: exact metadata-only transfer of all 50 object owners.
- `sql_validation.json`: row, key, range, orphan and temporal reconciliation.
- `validation_attempts.json`: sanitized attempts and cumulative cost estimate.
- `completion.json`: final compute and object summary.
- `final_compute_state.json`: fresh zero-cluster/job and stopped-warehouse check.
- `acceptance.json`: machine-readable exit decision and open gates.
- `review_and_research.md`: design evidence, official sources and limitations.

Evidence contains no recipient addresses, tokens, raw subscription, tenant,
principal, job/run or customer identifiers. Phase 4 completion is not production
approval and does not authorize Phase 5 compute.
