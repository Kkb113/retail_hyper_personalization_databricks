# Phase 10 research and design decisions

Reviewed 2026-09-08. Findings below distinguish documentation from actual workspace tests.

2026-09-09 RELEASE UPDATE (supersedes historical pending claims below): live App
inference, API/browser acceptance and running-to-stopped controller tests passed.
See [live acceptance](phase10_live_acceptance.md). Research found that the
[Apps environment](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/system-env)
uses Python 3.11 for pip, while
[uv dependency management](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/dependencies)
supports an explicit Python version with pyproject.toml and uv.lock. The release
therefore uses locked Python 3.12. Actual source export rejected a 33.5 MB file at
the 10 MB limit; verified 8 MB parts resolved it. The
[App API authentication guidance](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/connect-local)
requires Databricks OAuth rather than a direct Entra token. Non-admin OAuth and
browser OBO both passed. No new Azure resource or hosting service was added for these fixes.

2026-09-09 shutdown update: registration is verified and the Basic managed-identity
Automation account/runbook are deployed. The first stopped-state test returned HTTP 403
because SQL APIs require the SQL-access entitlement. Explicit approval for that entitlement
was subsequently granted by the user. The additive entitlement is applied and the second
stopped-state test completed successfully in about 5.8 seconds. Live stop/failure recovery
remain untested; no paid Databricks compute start occurred. API references used:
[Automation account creation](https://learn.microsoft.com/en-us/rest/api/automation/automation-account/create-or-update?view=rest-automation-2024-10-23),
[managed identity token access](https://learn.microsoft.com/en-us/azure/automation/enable-managed-identity-for-automation),
[additive permissions versus replacement](https://docs.databricks.com/api/access-management/v1/update-object-permissions),
and [Automation pricing](https://azure.microsoft.com/en-us/pricing/details/automation/).
Automation's 500 monthly job minutes are shared at subscription scope and subject to
rate-plan eligibility; no assertion of guaranteed free execution is made.

| Topic | Decision and evidence |
| --- | --- |
| Hosting | Native Databricks App only. [App overview](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/) describes OAuth/Unity Catalog integration. Existing workspace is Premium; no workspace SKU upgrade was made. |
| Compute | One Medium instance is the smallest documented option, 0.5 DBU/hour. [Compute sizes](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/compute-size). Do not confuse DBU units with INR or assume App scale-to-zero. |
| Authentication | [App authorization](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth) distinguishes App identity from user OBO. Verify forwarded tokens through the workspace, enforce server entitlements and use user credentials for customer SQL. The workspace rejected explicitly requesting the default current-user scope; explicit SQL/model-serving scopes succeeded. |
| Azure identity | [Service credentials](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-services/service-credentials) wrap an access connector/managed identity and support workspace isolation. Actual App-principal token exchange and provider model-metadata access passed without API keys. Inference remains untested. |
| Gateway alternative | [Model provider service authentication](https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/create-model-provider-services) supports service credentials. A gateway was not added: direct short-lived credential exchange preserves the tested Azure deployment-scoped route and avoids another service layer. |
| Model | Preserve exact [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna); reuse the Phase 9 evaluated planner. OpenAI public API pricing is not substituted for Azure pricing. No model upgrade or additional deployment. |
| Shutdown | [Azure Automation overview](https://learn.microsoft.com/en-us/azure/automation/overview) documents Basic accounts and 500 free job minutes per subscription. Proposed on-demand stop-only runbook is independent of the App/local laptop. Provider currently NotRegistered: no Automation account or jobs created. No claim that the shared free allowance is unused. |
| Persistence | Reuse Phase 7 Delta operational tables. Ephemeral chat navigation is labeled temporary; confirmed feedback must return a store receipt. No new database or browser-only authoritative feedback storage. |

## Code review outcomes

- Phase 1's historical health skeleton stays unchanged; Phase 10 has a separate HTTP boundary.
- The existing Phase 8 backend can wake a SQL warehouse through Statement Execution.
  The App adapter checks exact warehouse name and RUNNING state before reads/writes.
  A concurrent stop/query race still needs the independent stop controller.
- Existing tool contracts already enforce customer authorization and schema-validated facts.
  The new HTTP layer never accepts a ToolContext, history, raw SQL or a token-provider callback.
- App M2M identity is not automatically an Azure managed identity. The keyless connector
  resolves that distinction without tenant-wide app registration or operator-token fallback.
- Phase 9's local ledger is not a durable cross-restart App cost ceiling. Do not call Phase 10
  ready until a cumulative validation ledger, immutable lease and independent stop path exist.
- A merged Phase 5 dependency PR cleared currently open GitHub Dependabot alerts.
  This alone does not prove every historical registered model's environment changed.
- Frontend dependencies are exact-pinned with an npm lockfile and a clean audit at review time.
  Third-party node_modules are ignored; credential scans now include JS/JSX/HTML/CSS source.

## UI scope

The user clarified that the App should be a simple conversation, not an eight-view workbench.
The UI now has a transcript and composer, inline product cards, collapsed sources and a
minimal authorized-customer selector. Existing protected backend tools remain available;
separate dashboards, filters and feedback forms are not part of this chat UI. Responses
arrive as complete messages, not token streaming. No Azure resource change is required.

The Sites-building skill informed the focused chat layout, responsive states and
local preview loop. Azure Databricks hosting and existing Delta persistence override
Sites-specific hosting/database defaults. No Cloudflare, D1, R2, Sites hosting or speculative
WebMCP tools were added. No decorative/generated images are necessary for this operator UI.

Full roadmap polish is not claimed: profile purchase history/value measures, extra opportunity
triggers, optional free-text corrections, a live trace timeline, streaming transport and
browser accessibility validation need explicit acceptance or a documented POC scope decision.
