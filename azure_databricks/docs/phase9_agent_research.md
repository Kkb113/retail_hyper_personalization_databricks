# Phase 9 research and decisions

Reviewed 2026-09-08. Scope: `intellify-databricks-demo` plus the explicitly owner-approved
Azure OpenAI account in the same `Databricks` resource group.

## Owner-requested Luna migration

The current target is exactly `gpt-5.6-luna`, Azure model version `2026-07-09`.
Use S0 / GlobalStandard token billing, capacity 10 (quota units, not provisioned hourly
compute), NoAutoUpgrade and disabled API-key authentication. Azure's INR Retail Prices API
returned 19.1093 input / 114.6555 output per million short-context Standard Global tokens
in West US on 2026-09-08. Cache discounts are not assumed; these are pre-tax estimates.
Historical GPT-OSS/Llama experiments below are retained, not relabeled Luna evidence.

Keyless read-only diagnostics verified the token tenant, OpenAI User role, and successful
model listing. Changing to the documented `https://ai.azure.com` audience did not alone
resolve the v1 inference route's operation-level PermissionDenied. The deployment-scoped
chat-completions route with API version `2024-10-21` succeeded with the same role. Use that
tested route; no additional privileges or API keys were needed. The Azure account-scoped
OpenAI User role was granted to the operator only. A deployed App's workload identity is a
separate Phase 10 acceptance gate; the factory never implicitly falls back to operator auth.

The OpenAI Docs migration review preserved forced function calling, explicit `none`
reasoning, bounded completion tokens, no stored response and no automatic model fallback.
The ResponsesAgent wrapper remains unchanged as the external agent protocol; the internal
Azure call uses chat completions for the tested compatibility path.

Sources: [Luna model contract](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[Azure Entra authentication](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/managed-identity),
[Azure retail pricing API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).

## Architecture

Use one bounded planning call followed by typed Phase 8 tools and deterministic rendering.
The LLM does not rank products, calculate prices, execute SQL, or receive tool-result text.
This makes product-content prompt injection unable to introduce another tool call.
Guest discovery is not claimed to be personalized cold-start inference. Existing sparse-history
customers retain the recommender's actual route disclosure. Regional stock remains unsupported.

MLflow 3.16.0 provides ResponsesAgent and AgentServer. Keep the agent runtime separate from the
frozen recommender runtime. ResponsesAgent accepts one current user message; session context and
customer entitlements must be resolved by trusted server code, never request custom inputs.
AgentServer is tested locally in this phase; a deployed, authenticated App belongs to Phase 10.

The framework automatically traces payloads. Disable that default at the adapter boundary;
record redacted request hashes, versions, action, status, count and latency instead. Never log
credentials, raw prompts, customer identifiers, product payloads or provider error bodies.

Sources: [MLflow AgentServer](https://mlflow.org/docs/latest/genai/serving/agent-server/),
[ResponsesAgent](https://mlflow.org/docs/latest/genai/serving/responses-agent/),
[Databricks function calling](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/function-calling).

## Cost and model selection

The original baseline compared existing GPT-OSS-20B and Llama 3.1 8B pay-token endpoints. Published standard
rates are respectively 1.000/4.286 and 2.143/6.429 DBU per million input/output tokens.
Use INR 44.91 per DBU as a conservative planning assumption, not an invoice quote.
Do not provision throughput, a dedicated agent endpoint, AI Search, or an always-on App.
Haiku is deferred: a small qualifying model is sufficient and avoids an unnecessary third test.

Source: [Databricks foundation serving pricing](https://www.databricks.com/product/pricing/foundation-model-serving).

Each request reserves its maximum estimated cost before sending. A persistent ledger permits
at most 400 attempts and INR 100 estimated LLM spend across this phase's evaluator runs.
The original 260-call bound was increased for the requested Luna migration; the monetary
allowance and accumulated historical spending were not reset or increased.
Failed or interrupted attempts keep their reservation unless usage is confirmed. No retries.
Input payload <=9,000 bytes, generated tokens <=1,024, tool calls <=3, session turns <=20.
The allowance leaves room within INR 250 for a three-minute warehouse validation, tax and
uncertainty. Azure cost telemetry is delayed: this is NOT a guaranteed hard invoice ceiling.
No shared-endpoint gateway settings are changed, because they affect other workspace users.
The application ledger is not represented as a workspace-wide billing control.

Sources: [AI Gateway rate limits](https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/rate-limits),
[inference-table logging](https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/inference-tables).

## Evaluation boundary

Versioned 135-case synthetic benchmark: 25 known, 15 low-history, 15 guest, 15 search,
10 comparison, 10 scenario, 10 unrelated/insufficient-input, 10 privacy, 15 injection/product-text,
10 dependency failures. Add independent multi-turn and ResponsesAgent/server contract checks.
Deterministic assertions avoid paid LLM-as-judge calls. Fixture-backed tool results measure agent
selection, argument validity, grounding and fail-closed behavior, NOT live model quality.
Actual Databricks integration is a separate bounded acceptance test and must be reported separately.
Targets: selection >=95%, arguments >=98%, safety/grounding/traces 100%; no unconfirmed writes.
