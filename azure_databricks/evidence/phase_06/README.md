# Phase 6 — live validation record

Status: batch demo path validated; final endpoint acceptance and shutdown audit
must be checked in `live_inference.json` and `cloud_reconciliation.json`.
Do not infer completion merely from endpoint creation or Champion assignment.

## Release and scope

Champion version 2 is approved for synthetic POC use only. It repairs version 1's
incompatible MLflow 3.8.1/pandas 3.0.3 packaging by using MLflow 3.16.0. The trained
weights and recommendation logic were not changed. Golden reload parity passed;
production remains `HOLD_PENDING_FUTURE_HOLDOUT`.

The successful Azure batch covers **100 representative customers / 1,000 rows**,
not the full 5,000-customer population. History, atomic current snapshot, and
inventory-filtered serving view all passed live checks. The real parameterized
SQL reader returned ten results; first-query latency was 19.281 seconds.
This includes cold query overhead and is not a warm-cache latency measurement.

The initial 5,000-customer attempt hit its ten-minute timeout without publishing
a partial current snapshot. The revised cohort job completed in 163.102 seconds,
under its 180-second limit. It has no schedule and no automatic retries.

## Evidence interpretation

- `endpoint_promote.json`: original version-1 synthetic POC approval, superseded
  by `repackage.json` for the current Champion.
- `endpoint_create.json`: original failed deployment, not current readiness.
- `endpoint_replacement.json`: removal of the proven-unbuildable endpoint and
  replacement under the same name; source data and registry versions retained.
- `batch_run.json`: successful bounded Azure batch and publication checks.
- `batch_read.json`: live governed SQL client smoke test.
- `batch_parity.json`: cross-platform Windows/Azure comparison; any WARN must
  remain visible. This does not substitute for live Azure batch/endpoint parity.
- `live_inference.json`: endpoint acceptance, including exact Azure batch parity,
  invalid-input handling and warm latency, when generated.
- `cloud_reconciliation.json`: final observed state and reported budget usage.

## Cost and operations

Only one Small CPU custom endpoint and one unscheduled job definition are used.
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
