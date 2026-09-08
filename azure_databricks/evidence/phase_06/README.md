# Phase 6 — live validation record

Status: **Phase 6 accepted for the synthetic 100-customer POC scope.** Live batch,
endpoint parity, invalid-input, warm-latency, explicit cold-resume, and promotion
gates passed. `cloud_reconciliation.json` records the final observed shutdown.

## Release and scope

**Champion version 3** is approved for synthetic POC use only. Version 2 repaired
version 1's incompatible MLflow 3.8.1/pandas 3.0.3 packaging with MLflow 3.16.0,
but failed serving parity. Version 3 stabilizes candidate-boundary ties and
numeric ranking ties. Trained weights are unchanged; the ranking runtime changed
and was revalidated. Production remains `HOLD_PENDING_FUTURE_HOLDOUT`.

The successful Azure batch covers **100 representative customers / 1,000 rows**,
not the full 5,000-customer population. History, atomic current snapshot, and
inventory-filtered serving view all passed live checks. The real parameterized
SQL reader returned ten results with identical repeated output: first read
22.812 seconds, warm read **4.297 seconds**. Dynamic warm p95 across 12 requests
was **3.236 seconds**. All 1,000 Azure batch/endpoint rows match exactly, and
local/Azure parity also passes. Inventory validity is 100%; invalid input
returns HTTP400. Explicit cold resume passed in **99.062 seconds**.

The initial 5,000-customer attempt hit its ten-minute timeout without publishing
a partial current snapshot. The version-2 cohort run took 163.102 seconds;
the final version-3 cohort run took **167.404 seconds**, under its 180-second
limit. It has no schedule and no automatic retries. Native 30-minute idle
scale-to-zero was configured but not separately timed; explicit stop/start was tested.

## Evidence interpretation

- `endpoint_promote.json`: original version-1 synthetic POC approval, superseded
  by version-2 packaging and finally `retest_promote.json` for Champion 3.
- `endpoint_create.json`: original failed deployment, not current readiness.
- `endpoint_replacement.json`: removal of the proven-unbuildable endpoint and
  replacement under the same name; source data and registry versions retained.
- `batch_run.json`: successful bounded Azure batch and publication checks.
- `batch_read.json`: live governed SQL client smoke test.
- `batch_parity.json`: passing version-3 Windows/Azure comparison.
- `live_inference_v2.json`: retained failed version-2 gate; never presented as passed.
- `live_inference.json`: endpoint acceptance, including exact Azure batch parity,
  invalid-input handling and warm latency.
- `cold_resume.json`: explicit stop/start test, not a native idle-timer test.
- `local_determinism.json`: all 1,000 rows match with one and four CPU threads.
- `cloud_reconciliation.json`: final observed state and reported budget usage.

## Cost and operations

Only one Small CPU custom endpoint and one unscheduled job definition are used.
The user explicitly increased the Phase 6 validation allowance to **INR400**.
The revised guarded reservation is INR399.2408, including INR300 for prior work
and the batch retest, plus INR99.2408 for two launches/runtime/idle risk. This is
a planning reservation, not actual billed usage. No additional Azure resource,
GPU, schedule, or LLM endpoint was created.
The existing 2X-Small SQL warehouse has a one-minute idle stop; the batch-reader
test explicitly stopped it. Endpoint tests request explicit stop in a `finally`
block. Native scale-to-zero is enabled but is not equivalent to a tested stop.

West US retail planning rates: serving INR 7.8348/DBU-hour, Small CPU four
concurrent requests (INR 31.3392/hour), automated serverless INR 44.9067/DBU-hour.
Startup is **two DBUs per scale-from-zero launch**, not one. Cost admission now
includes this correction. The original create/replacement checks underestimated
startup reservation by one DBU; this is not a claim of actual billed usage.

The 16 DBU/hour batch estimate is a planning assumption, not an enforced platform
quota. Azure cost data lags and budget alerts are not hard caps. Do not claim an
invoice guarantee or zero ongoing storage charges. Reconcile usage before any
further paid rerun or launch.

[Azure pricing and launch-charge FAQ](https://azure.microsoft.com/en-us/pricing/details/databricks/)
and [endpoint stop/start documentation](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/manage-serving-endpoints)
are the authoritative operational references. See `azure_databricks/docs/phase6_serving_runbook.md`
for the demonstration lifecycle and failure handling.
