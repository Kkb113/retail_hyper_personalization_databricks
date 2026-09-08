# Phase 8 — Semantic intelligence and governed tools

## Architecture

The existing MLflow Champion 3 remains the ranking authority. Semantic retrieval
finds products; it does not replace personalized recommendation scores.

- Ten versioned tools expose Pydantic input/output JSON schemas through
  `retail_hp_azure.phase8.tool_schemas()` and run without an LLM.
- `GovernedTools.execute()` validates requests and customer entitlement before
  any data access. The future authenticated App/agent supplies `ToolContext` from
  verified identity and server-side customer mappings, never browser/LLM input.
- `DatabricksToolBackend` reads closed Unity Catalog view names using SQL value
  parameters. It does not accept SQL, arbitrary URLs or table names.
- GTE `system.ai.gte_large_en_v1_5` generates product embeddings on the existing
  `databricks-gte-large-en` shared pay-per-token endpoint. Vectors are normalized
  before cosine comparison. Equal scores use ascending product ID.
- Generation is a two-stage operator workflow: `phase8_embed.py` invokes the
  Databricks model while Spark is off, preserving each completed product locally
  and uploading an immutable prepared artifact to the governed volume. The
  manual Databricks job verifies the document/model keys against Gold and
  publishes only missing embeddings. It makes no embedding API requests.
  This avoids paying for Spark while the shared endpoint is throttled.
- Product-document/model hashes enable changed-row generation and reuse. The
  embedding table is append-only; a versioned public-product snapshot resides in
  `serving.semantic_assets`. This is a derived artifact, not a modification of
  either sealed Phase 3 transfer root.
- Search filters current eligible product IDs before cosine ranking and checks
  returned facts again. Product text is untrusted data, never tool instructions.
- Feedback requires a server-supplied explicit confirmation, an authorized
  customer, a product present in that customer's batch recommendations, and an
  idempotency key. It uses Phase 7 Delta storage. It does not retrain the model.
- Every attempt emits a redacted trace through an injected sink. Traces retain
  tool/version, pseudonymous actor, hashed request ID, status and elapsed time;
  no raw prompts, customer IDs, credentials or event payloads are logged.

## Tool surface

| Tool | Output / restriction |
|---|---|
| get_customer_360 | Authorized active customer's approved summary fields |
| get_recommendations | Current batch by default; explicit real-time mode requires running endpoint |
| explain_recommendation | Actual batch score, route and reason codes; no invented narrative |
| search_products | Cosine retrieval with category, base-price and promotion filters; up to 20 |
| get_product_details | Eligible product facts, source inventory timestamp |
| compare_products | Two to four product IDs; only eligible facts returned |
| simulate_scenario | Non-persistent model invocation; authorized customer and typed overrides |
| get_opportunities | Active promotions among the customer's batch recommendations |
| record_feedback | Idempotent event receipt, explicit confirmation required |
| get_quality_summary | Restricted aggregate batch coverage, not invented accuracy metrics |

## Operating controls

Run commands from this checkout, including when the package is wheel-installed.

```powershell
.\.venv\Scripts\python.exe azure_databricks/scripts/phase8_control.py plan
.\.venv\Scripts\python.exe azure_databricks/scripts/phase8_control.py inspect
.\.venv\Scripts\python.exe azure_databricks/scripts/phase8_control.py deploy
```

Deploy installs only an unscheduled job definition. Paid execution requires the
operator's bounded-session admission gate `RETAIL_HP_PHASE8_CEILING_INR=250` and
`phase8_embed.py` preparation followed by the `run` command. The operator
preparation uses only the four hash-verified synthetic source datasets needed
for product documents and eligibility; it does not send customer records.
`phase8_validate.py` performs bounded authenticated acceptance
and stops the warehouse in `finally`. Do not run either command repeatedly without
reviewing cumulative evidence and remaining allowance.

There is no new Azure AI Search resource, dedicated vector endpoint, Lakebase,
GPU, permanent cluster, continuous pipeline or scheduled refresh. The existing
warehouse retains one-minute auto-stop. Tools refuse SQL or recommender requests
when the corresponding compute is stopped; they do not intentionally wake it.
The publish job has a 120-second timeout, zero task retries and explicitly
disabled serverless auto-optimization retries.

The checkpointed operator step has a ten-minute wall-clock bound and a cumulative
500,000-byte attempted-input gate (a conservative token bound), including retries.
Only HTTP 429 is retried, at most twice per batch with a ten-second delay. A
timeout or other failure stops preparation; completed product vectors are reused.
Warehouse validation has a three-minute fallback shutdown controller.

The default shared embedding endpoint is not dedicated idle compute. A tool
session admits at most 32 query-embedding requests (maximum configuration 64),
with bounded input lengths, network timeouts and no retries. Costs are still
usage-based; per-session limits are not a subscription-wide hard cap.

## Scope limitations

- Synthetic data only. Application entitlement checks are not database RLS.
  Do not expose the workload identity or `ToolContext` constructor to clients.
- Regional stock filtering is rejected: source inventory has stores but no
  store-to-region mapping. Global inventory must not be described as local stock.
- Product currency remains `UNSPECIFIED`. Prices are not implicitly INR merely
  because Azure billing is INR. The price filter compares source base prices.
- Batch recommendations cover 100 customers. Real-time/scenario tools reuse the
  existing endpoint and require an explicitly started demo session.
- Explanation and feedback membership use batch evidence, not arbitrary earlier
  real-time responses. Real-time impression/feedback linkage is future work.
- The snapshot must be rebuilt after product text/model changes; no background
  refresh runs. Physical deletion of obsolete vectors and feedback is not automated.
- The App/agent and persistent trace-sink integration belong to later phases.
  Supply a trusted trace sink; do not rely on console logs containing raw arguments.
- Resource-group budget telemetry is not a complete reconciled invoice. Storage,
  billing delay and managed-group cost coverage remain limitations.

## Cost evidence interpretation

The first two 180-second job deployments called the embedding API directly and
failed without reporting token usage. Their reports retain a conservative
500,000-token reserve each. Later 90/120-second deployments only publish prepared
vectors and make no embedding API calls; a publishing timeout must not be counted
as another unreported embedding generation. The admission scripts distinguish
these historical deployments by their recorded timeout. Update this accounting
explicitly before changing the workflow or reusing a historical deployment.
