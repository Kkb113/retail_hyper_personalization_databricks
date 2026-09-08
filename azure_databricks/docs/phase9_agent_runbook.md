# Phase 9 agent runbook

## Execution boundary

The requested release target is **GPT-5.6 Luna**, not the family alias `gpt-5.6`.
The approved Azure OpenAI deployment exists in `Databricks`; keyless operator inference
now succeeds using the deployment-scoped API (`2024-10-21`) and an Entra token for
`https://ai.azure.com`. The v1 route returned operation-level PermissionDenied even when
read-only model listing, tenant and role checks passed. Do not claim that token audience
alone resolved the failure, or publish historical GPT-OSS evaluation as Luna acceptance.
`phase9_azure_openai.py --deploy`
performs read-only deployment verification; adding `--apply` is an explicit cloud write.
`phase9_luna_probe.py` makes one synthetic operator-identity request behind the existing
INR 250 execution gate and shared INR 100 LLM ledger. Operator authentication is not a
substitute for a separately verified workload identity binding in the future App.
AzureLunaPlanner uses the deployment-scoped chat-completions API to preserve the existing forced
tool contract, exact model name, no reasoning, no stored response, and no fallback. It does
not enable Priority processing or provisioned capacity. Its new code is offline-tested;
the full Luna evaluation is recorded separately. Do not reset the shared ledger.

`create_luna_agent` in `phase9_responses.py` is the server-side MLflow agent factory.
Supply a trusted token provider, the persistent shared ledger, governed tools, authenticated
binding resolver and redacted trace sink. It never discovers developer credentials implicitly.
`phase9_evaluate.py` defaults to Luna; `phase9_validate.py` uses Luna for planning and the
existing non-admin Databricks identity for tools. These local checks explicitly use the Azure
operator identity for the LLM. They do not establish a deployed App workload identity.

The historical baseline uses one GPT-OSS-20B planning call, at most three typed tool calls, and deterministic
product cards. No fallback model is enabled. It does not deploy an App, agent endpoint, cluster,
warehouse, or scheduled evaluation. Existing recommender and SQL warehouse remain stopped
outside explicitly approved demo windows. Pay-token foundation endpoints need no dedicated
capacity allocation by this project.

The agent never runs SQL/code supplied by the user or LLM. It validates customer entitlements
and product references before accessing tools. It never sends product descriptions back to the
planner. Ranking, prices, stock, promotions and model reasons come from the governed tools.
Feedback always requires a separate server-confirmed Phase 8 action; a chat instruction alone
does not authorize a write. Scenarios are temporary and cannot update a customer profile.

## What the POC supports

- Existing authorized customers: batch recommendations, model route/reasons, product facts,
  comparisons and follow-up references.
- Guest preferences: semantic product discovery, explicitly not personalized recommendations.
- Sparse existing profiles: actual recommender route disclosed, including `cold_only`.
- Scenario and real-time requests: available only when the existing recommender is explicitly
  started in an approved demo window; otherwise an unavailable response, never an auto-start.
- Global inventory only; the source has no confirmed currency or regional/store mapping.
- Up to eight products per turn; requests for more ask the user to narrow the result.

## Authentication requirements for Phase 10

`RetailResponsesAgent` takes a trusted binding resolver producing `ToolContext` and `Session`.
It accepts one current user message, not client-supplied history, customer grants or custom
identity inputs. The App must authenticate each request, resolve entitlements server-side,
and retain a separate session for each authenticated user. Do not deploy the fixture binding
from `phase9_adapter_smoke.py`. This phase does not claim that a public HTTP authentication
layer is already deployed. Azure resource-group ownership does not substitute for app-user
authorization. Application customer entitlements are not database row-level security.

The session lock prevents overlapping turns inside a process. This POC uses one worker;
multiple replicas require shared atomic session/admission state. A session is limited to 20
turns. Product text in `custom_outputs.cards` is untrusted display data: render as escaped text,
never HTML/Markdown with executable links or instructions.

## Limits and privacy

Input <=1,600 characters and provider payload <=9,000 bytes; output <=1,024 tokens; no retries;
one planning call; three tool calls; 75-second response deadline checked between operations.
SQL statements have their existing bounded cancellation path. The response deadline is not an
OS-level kill switch: an in-flight SDK/network call can take longer. The separate demo-window
warehouse watchdog protects live validation. The agent never starts compute automatically.

The persistent LLM ledger reserves attempted-call spend before HTTP and retains reservations
on unknown outcomes. Exclusive file locking fails closed for concurrent processes or orphan
locks. Do not delete/reset the ledger to bypass its 400-call / INR 100 phase allowance. The
call bound was increased from 260 for the owner-requested Luna migration, without increasing
the monetary allowance; all historical spending/reservations remain included. Reconcile
an orphan lock only after checking that its process ended and recording the existing spend.
This is an application evaluation allowance, not an Azure hard billing ceiling or a gateway
limit for other users of shared endpoints.

MLflow's automatic full-payload tracing is disabled in the serving adapter. Inject a redacted
trace sink; the agent emits request hashes, versions, action, status, counts and timing only.
The cloud evaluation experiment stores labeled **replayed evaluation records**, not invented
live execution spans. Original measured inference timings remain in evaluation evidence.
No inference-table payload logging or continuous evaluation is enabled by this implementation.
Retain POC evaluation metadata for 30 days after the final demo, then review/remove it manually;
there is no newly billed retention scheduler.

## Reproduce checks

Run the normal repository tests and CI first. The isolated `agent` runtime must also pass
`python -I azure_databricks/scripts/phase9_adapter_smoke.py` and `pip check` after wheel install.
It tests ResponsesAgent prediction, validated streaming, HTTP invocation and identity rejection
without cloud calls.

Paid evaluation is opt-in: set `RETAIL_HP_PHASE9_CEILING_INR=250` in the operator process, then
run `phase9_evaluate.py --mode full --endpoint gpt-5.6-luna`. Reuse the existing ledger.
Do not re-run a successful benchmark without a material change and a remaining allowance.
Use `--resume` only with a same-prompt checkpoint after an interrupted run; completed calls
are not repeated. `phase9_replay.py` tests release boundary fixes against recorded live plans
without retrying failed model calls or hiding the original results.

`phase9_validate.py` is the separate actual Databricks integration check: temporary non-admin
workload credential, batch/facts/comparison/search, unauthorized-customer refusal, stopped
scenario behavior, independent three-minute stop watchdog, unconditional warehouse stop and
credential revocation. It creates no new Azure resource. `phase9_publish.py` requires passing
full evaluation and live acceptance before uploading digest-addressed files and MLflow records.

See [research](phase9_agent_research.md) and [evaluation evidence](../evidence/phase_09/README.md).
