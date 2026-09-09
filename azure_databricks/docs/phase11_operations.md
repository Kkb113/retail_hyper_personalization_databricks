# Phase 11 — Analytics, observability and operations

Status: deployed; SQL reconciliation, manual opportunity job and live chat acceptance
passed. Dashboard visual review is delegated to the user, not claimed verified.
See `evidence/phase_11` for separate SQL, trace and shutdown acceptance records.

## Acceptance and handoff

- Published 1,000 recommendation rows for 100 customers (95 active demo customers).
- 373 distinct eligible recommended products; historical inventory eligibility 100%.
- 323 reproducible opportunity rows: 173 promotion and 150 aggregate-intent candidates.
- Opportunity job completed successfully without a recurring schedule.
- Four live requests and 19 redacted events were read back from the existing MLflow
  experiment, with all evaluated request correlations verified. An interrupted
  Windows-console finalization was recovered without duplicating runs or inference.
- Local Azure suite: 335 passed, four skipped; strict typing passed for 40 source files.
- App, warehouse and recommendation endpoint were verified stopped at 11:18 UTC on
  2026-09-09; independent shutdown job completed. The single INR 220 allowance was
  not extended and is a planning allowance, not measured invoice spend.

Dashboard: **Retail POC — Business and operations**, under `/Shared/retail_hp_phase11`.
Open it during an approved demo window using your Databricks account. Check all eight
tables display, column labels fit, and snapshot/unknown-metric qualifications remain
visible. Refresh can start warehouse compute; no automatic refresh schedule exists.
Browser verification was blocked by sign-in and explicitly left for your manual check.

## Business dashboard

Native AI/BI dashboard **Retail POC — Business and operations**, in
`/Shared/retail_hp_phase11`. Queries use the existing XXSmall warehouse with one-minute
auto-stop. No schedules, subscriptions, embedded publisher credentials or new grants.
Viewers must already have their own data and compute permissions. Refresh is not free;
use only during an approved demo window. The dashboard is administrative aggregate
analytics, not a customer-facing authorization boundary.

| Signal | Meaning and limit |
|---|---|
| Customer coverage | Active published customers / all active customer profiles; not all 5,000 IDs are demo-ready |
| Product coverage | Distinct eligible recommended products / eligible product catalog |
| Inventory validity | Published recommendation rows joining the eligible catalog / published rows; historical global snapshot |
| Route and candidate mix | Counts of rows by route/source combination, not causal attribution or accuracy |
| Batch quality | Exactly ten distinct products and ranks 1–10 per customer, one pinned-version batch |
| Positive feedback | Positive / all unexpired feedback events; NULL when none |
| Feedback rate | Unavailable until request/impression attribution and matching windows are verified |
| Agent evaluation | Historical Phase 9 Luna fixture benchmark, explicitly not current App quality |
| NDCG, Recall, Hit Rate, Purchase Hit Rate | Unavailable for a qualified future holdout of the current release; no manufactured zero or inherited claim |
| Request latency and LLM usage | Observed App traces manually exported; excludes unobserved requests and is not an invoice |
| Cost | Delayed RG budget readout in the operations report; managed-group costs are not claimed covered |

## Deterministic opportunities

The manual-only `retail-hp-poc-opportunity-refresh` SQL job reuses the existing
warehouse. Max concurrency one, no retries, task timeout 480 seconds, job timeout
600 seconds. It does not schedule itself. Run only inside an independently armed
demo lease; outside a lease, a human could still start it and incur cost.

The `monitoring.phase11_opportunities` snapshot contains deterministic hash keys,
pinned batch, rule version, evidence value/time and evaluation time. Snapshot
replacement removes obsolete rows and reproduces exactly on rerun, except evaluation
timestamps. Existing Phase 8 agent opportunity contracts are unchanged.

- Active promotion: an eligible model recommendation with positive snapshot discount.
- Browse/cart without purchase: an active customer's historical aggregate has intent
  events and no purchases, attached to eligible recommendations. This is a customer-level
  outreach candidate, **not proof that the specific recommended product was browsed**.
- Replenishment, back-in-stock, price-change and high-affinity cross-sell are not
  activated: the necessary event transitions/intervals or qualified affinity threshold
  have not been established. Empty results are valid, not a reason to invent triggers.

## Trace and privacy contract

Every completed conversational request returns an opaque SHA-256 request reference.
Tool and LLM events share that hash. Trace records contain only allowlisted status,
action/tool, version hashes, timing and numeric usage. No prompt, answer, customer ID,
user identity, provider error text, tool arguments or product payload is archived.
Raw SQL statement IDs are not yet joined; correlation covers the tool invocation,
not an independently verified SQL execution graph.

During this small POC, sampling is 100%, capped at 200 archived requests per process,
40 events per request and 20 pending request buffers. Files use the existing private
launch volume under `observability/<launch>/<request_hash>.json`. One create-only
file is written on request completion, with no warehouse wake-up. Archive failures
emit a counter event rather than breaking chat. File write latency is not included
in recorded agent-processing latency. No always-on log collector is provisioned.

`phase11_trace_export.py` manually validates/redacts the archive, reconciles request
counts into Delta and publishes observed-event replay spans into the existing MLflow
experiment. Replay timestamps are not live span timings; recorded elapsed_ms is the
measured timing. No autologging of prompts. Re-exporting the same launch into MLflow
verifies an existing completed run rather than duplicating traces. The metadata-only
`phase11_trace_recover.py` verifies persisted request/span counts before finalizing an
interrupted run; it never starts compute. Delta import is keyed
by request hash. Unknown provider usage is not an invoice estimate.

Retention target: 30 days for request analytics/archives; query views hide older
records. Physical removal and MLflow trace deletion are a manual administrative
maintenance action, not an installed automatic retention guarantee. Before deletion,
export incident evidence and restrict targets to the exact observability launch
subdirectories and experiment trace IDs. Never delete launch claim files.

## Incident runbook

1. Invalid batches or no demo-ready customers: block the showcase and reconcile the
   pinned batch. Never substitute another customer's products.
2. Inventory validity below 100%: inspect eligibility and snapshot dates before demo.
3. Missing trace export: inspect `trace_archive_failed` logs and existing volume
   permissions; do not widen permissions or describe missing telemetry as success.
4. Non-success response counts or high latency: inspect request hash in the archived
   events/MLflow replay. Clarifications/refusals are separate from server failures;
   the dashboard's non-success count is not an infrastructure error rate.
5. Budget/cost read unavailable: stop new launches pending reconciliation. Azure cost
   data is delayed; the INR 12,000 budget is not a hard invoice ceiling. Keep reserve.
6. Shutdown deadline passed with active compute: use the existing stop runbook, then
   verify App, warehouse and endpoint states. Do not create another shutdown service.

Alert checks are on-demand quality gates and runbook actions, not continuously
delivered emails. Budget emails and the existing shutdown controller remain separate.

## Research decisions

- [Dashboard API and viewer credentials](https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/dashboard-crud-api): explicitly publish with `embed_credentials=false`.
- [Dashboard concepts](https://learn.microsoft.com/en-us/azure/databricks/dashboards/concepts): governed native assets; queries still require compute.
- [Billable usage schema](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing): account-wide data and correction records must not be treated as instant scoped INR invoices.
- [MLflow tracing](https://mlflow.org/docs/latest/genai/tracing/faq/): manual spans avoid uncontrolled prompt capture.
- [Trace archival](https://mlflow.org/docs/latest/genai/tracing/observe-with-traces/archive-traces/): retention must be explicitly operated; hiding old rows does not delete stored traces.

Optional Genie is not activated or represented as free. No Lakebase, paid vector
index, Log Analytics workspace, new cluster or dedicated serving endpoint is needed.
