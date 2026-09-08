# Phase 6 serving operations

## Scope

Synthetic POC only. Champion is a deployment label, not production approval.
Version 2 repairs version 1's MLflow/pandas dependency conflict; trained weights
and recommendation logic are unchanged. Production remains on hold pending a
future holdout evaluation.

The initial batch is a deterministic **100-customer demo cohort out of 5,000**,
selected by sorting customer identifiers and taking every fiftieth customer.
It is not full-population coverage. The first full-population attempt timed out
without publishing a current table. Do not repeat that attempt blindly.

## Artifacts

- One manual, unscheduled job: `retail-hp-poc-batch-recommendations`.
- Versioned Delta history: `gold.customer_recommendation_history`.
- Atomic Delta current snapshot: `gold.customer_recommendation_current`.
- Inventory-filtered app view: `serving.customer_recommendations`.
- One Small CPU custom endpoint: `retail-hp-poc-recommender`, scale-to-zero enabled.
- Typed client: `retail_hp_azure.serving_client`.

All tables are in `intellify_databricks_demo`. Check the evidence directory for
actual validation status; this runbook is not itself evidence of deployment.

## Demonstration lifecycle

1. Read the latest cost ledger and resource state. Azure budgets are alerts, not
   a hard spending cap; billing data can lag. Include prior attempts, startup
   charges, tax, storage, and idle time when assessing remaining allowance.
2. Explicitly start only the existing demo warehouse and custom endpoint needed
   for the demonstration. The batch reader refuses to start a stopped warehouse.
3. Check the endpoint is READY. A stopped endpoint needs an explicit start;
   queries do not start it. Allow preparation time before presenting.
4. Select an existing customer from the published cohort for the Delta path.
   If absent, show “not in the demo batch”; do not silently start batch compute.
   New-customer/scenario inference uses the typed client and current eligibility.
5. Inference is read-only. A repeated request creates no feedback or audit row.
   Feedback persistence is Phase 7. Retry only 429/503 once within the deadline;
   never retry invalid input or authentication failures.
6. Finish by explicitly stopping the custom endpoint and the warehouse. Confirm
   terminal states and no active job runs. Do not rely solely on a READY label
   or the resource group's `auto-stop` tag.

The batch job has no schedule, no retries, one concurrent run, and a 180-second
timeout. Its controller refuses a second paid run until the previous run has
been reconciled. Do not increase the timeout without revising the cost estimate.

## Acceptance targets

Warm dynamic inference target: p95 <= 5 seconds for ten recommendations on the
small synthetic workload. Cold request deadline: 120 seconds, then show a safe
“warming/unavailable” message. These are POC targets, not service guarantees.
Readiness, prediction parity, inventory validity, invalid input handling, and
stop/start behavior must be measured before declaring the live path ready.

## Recovery

Keep the previous current snapshot if scoring or validation fails. History uses
a deterministic batch identifier; an existing complete batch is reused, not
appended again. Re-run publication only after reviewing the failure and costs.
Do not delete registered model versions or source data to recover an endpoint.

## Sources

[Endpoint management](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/manage-serving-endpoints)
documents explicit stop/start, container build logs, and the restriction against
stopping an endpoint during an in-progress configuration update.
[Custom model serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models)
documents native scale-to-zero. An idle endpoint can take 30 minutes to scale
down; explicit stop after a demonstration avoids relying on that idle tail.
