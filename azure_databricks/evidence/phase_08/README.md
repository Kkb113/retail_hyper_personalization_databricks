# Phase 8 — Semantic intelligence and governed tool layer

## Delivered POC scope

- Ten typed, versioned tools, callable without an LLM, with closed input/output
  schemas, customer entitlement checks, parameterized SQL, bounded responses,
  redacted traces and explicit/idempotent feedback writes.
- Four group-owned serving views, an append-only embedding Delta table and one
  product-only managed volume in the existing catalog.
- 2,860 currently eligible products embedded using the existing shared GTE model;
  1,024-dimensional normalized vectors and deterministic cosine retrieval.
- One manual-only, two-minute publish job. An idempotent live rerun reused all
  2,860 embeddings and made no embedding calls.
- No new Azure resources, dedicated endpoints, vector service, App, Lakebase,
  cluster, schedule, GPU or provisioned LLM capacity.

The manual operator preparation invokes the Databricks embedding model while
Spark is off and checkpoints locally before uploading to the governed volume.
The Databricks job validates against Gold and publishes the derived assets.
This is intentionally not a continuous embedding pipeline.

## Acceptance evidence

- `generation_300547874616777.json`: successful final publish/reuse run.
- `operator_embeddings.json`: 2,860 products, 90 requests, 74,542 usage
  tokens reported by the API, no throttles during checkpointed preparation.
- `tool_validation_*.json`: non-admin identity passed semantic relevance for
  jackets, running shoes and headphones; price filtering; batch/customer/product/
  comparison/explanation/opportunity/quality tools; denied customer lookup;
  refused stopped-endpoint invocation; feedback insert and replay. Fifteen
  redacted traces, 25 query-embedding tokens, temporary secret revoked and
  warehouse verified STOPPED.
- Real-time/scenario adapters also execute the actual frozen recommender locally.
  They were not retested against a newly started cloud endpoint in this phase.
  Phase 6 remains the live serving/parity evidence.
- `cloud_reconciliation.json`: final read-only ownership, model, job and compute
  inventory. `retry_hardening.json` records disabled serverless optimization
  retries for the existing batch/export jobs; the semantic job also disables them.
- Local suite: 210 tests; Phase 8 subset: 25 tests, also passing from a non-editable
  wheel. Ruff, strict mypy, dependency compatibility and credential scans pass.

## Failures retained, not hidden

Two initial in-job embedding attempts failed, the second explicitly with HTTP
429. The first also exposed Databricks auto-optimization retries despite the
normal retry count being zero. Those retries are now explicitly disabled.
Checkpointed operator preparation then completed with Spark off.

The first 90-second publish parent timed out, although its child notebook reported
PASS and published all 2,860 vectors. The corrected repeat path avoids collecting
vectors unnecessarily and avoids a repeated quadratic key check. The final
120-second job completed successfully, with 47.27 seconds inside the notebook.
All failed generation reports remain in this directory and are included in cost
accounting. No sealed source data or model artifact was changed.

## Cost and shutdown

Elapsed-time planning estimates, not Azure invoice measurements:

| Component | INR, pre-tax estimate |
|---|---:|
| All four serverless attempts, including failures | 88.6816 |
| Checkpointed product embeddings | 6.2166 |
| Tool-validation warehouse, including stop verification | 9.1336 |
| Query embeddings (25 tokens) | 0.0021 |
| Reserve for unreported embedding usage from failed initial jobs | 83.3979 |
| Additional small diagnostic-call reserve | 1.0000 |
| Combined estimate plus these reserves | 188.4318 |

The final paid-run admission plan was INR 238.77, below the INR 250 planning
allowance. This is not a guaranteed invoice ceiling: rates, taxes, storage,
metering lag and managed-resource-group cost coverage remain qualifications.
The warehouse and recommender endpoint are stopped; all three jobs are manual,
inactive and have optimization retries disabled. No continuing compute is needed
for the stored vectors. Small storage charges remain possible.

## Boundaries for Phase 9

GitHub reports 19 existing MLflow dependency alerts, including six critical.
See the [security follow-up](security_followup.md). Functional acceptance is not
security clearance: complete that triage before deploying the agent or approving
a broader demo. The frozen model runtime was not changed without revalidation.

The tool layer is ready for the next POC phase, not a production security boundary.
The authenticated App must construct entitlements server-side and provide a
stable protected actor secret and a trusted trace sink. Current checks are
application-level authorization, not database row-level security.
Regional stock filtering is rejected because no store/region mapping exists.
Product currency is `UNSPECIFIED`. Batch coverage remains 100 customers; quality
reports coverage, not unmeasured model accuracy. Feedback membership and
explanations use the authoritative batch, not arbitrary real-time impressions.
Physical retention cleanup and the deployed App/LLM agent are later-phase work.

See the [runbook](../../docs/phase8_tool_runbook.md) and
[research record](review_and_research.md).
