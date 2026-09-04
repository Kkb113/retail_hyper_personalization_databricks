# Phase 2 review and research

Reviewed 2026-09-04. Phase 1 and both dependency-security PRs are on main. The
baseline has 82 passing tests; the new mocked governance suite adds coverage of
allowlisted writes, explicit grants, ownership drift and idempotency.

## Codebase findings

- The Phase 1 bundle is deliberately resource-free. Its existing guards and
  historical evidence must not be weakened to permit arbitrary deployments.
- Phase 2 uses an explicit metadata-only bootstrap entry point. It allowlists
  group names, catalog, schemas, volumes and Azure tag routes independently.
  There is no cluster/warehouse/app/model-endpoint creation route in this path.
- Databricks SDK 0.81.0 is already locked for tooling. SDK imports remain lazy in
  the phase-specific helper, not package-import side effects.
- The frozen 45-file migration payload is not uploaded, rewritten or deserialized.
- Existing unrelated objects/grants must be preserved. Matching names without
  the project marker cause a stop for review; grants are additive and unexpected
  project privileges cause a drift failure instead of silent removal.
- Completed mutations are recorded incrementally, including partial progress if
  an API operation fails. Live principal identifiers never enter evidence.

## Initial live findings

- The pinned subscription, tenant, resource group and workspace fingerprints match.
- Existing catalog managed storage is available. Only default/information_schema
  namespaces exist; no SQL warehouse is present.
- The account-level group API is readable. Group creation rights still require
  validation; workspace-local groups are not a Unity Catalog substitute.
- Azure budget inventory is readable and empty; workspace tags are empty.
- Azure Cost Management returns HTTP 429; currency/current spend are not yet
  verified. Respect backoff and keep compute disabled rather than guessing.
- The current identity cannot read system billing tables or list system schemas.
  Account/metastore-level billing access is a separate permission gate. Do not
  grant broad system-catalog access merely to make a check green.

## Research and implementation decisions

1. [Group management](https://learn.microsoft.com/en-us/azure/databricks/admin/users-groups/manage-groups):
   create narrowly named account groups through the workspace account-SCIM API;
   never nest project groups in workspace admins or invite notification recipients
   as users. Only the existing bootstrap administrator joins the project admin group.
2. [Unity Catalog privileges](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference):
   grant specific USE/CREATE/SELECT/MODIFY/EXECUTE privileges at appropriate
   levels. Viewer and app groups access only the governed serving schema, not
   raw schemas or volumes. Engineer roles do not confer account administration.
3. [Volume privileges](https://learn.microsoft.com/en-us/azure/databricks/volumes/privileges):
   create two managed volumes using existing catalog storage. Metadata creation
   is not data migration; permissions on schemas and volumes are separate.
4. [Azure Cost Management query](https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01):
   use resource-group-scoped cost queries and verify the returned currency. Empty
   or throttled data is unknown, not proof of zero spend.
5. [Budget API](https://learn.microsoft.com/en-us/rest/api/consumption/budgets/create-or-update?view=rest-consumption-2024-08-01):
   use an explicit resource-group budget, monthly period and runtime-provided
   recipients. Do not interpret INR 12,000 as USD 12,000 or silently assume FX.
6. [Action groups](https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups):
   email-only notifications are sufficient; SMS/voice and unrelated integrations
   add no POC value. Test accepted notifications separately from actual inbox receipt.
7. [Budget limitations](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets):
   billing lag means alerts are not an exact hard cap. Pair the INR 9,000 internal
   target with cost admission and bounded sessions, reserving INR 3,000 headroom.
8. [Automation](https://learn.microsoft.com/en-us/azure/automation/overview) and
   [schedules](https://learn.microsoft.com/en-us/azure/automation/shared-resources/schedules):
   evaluate a small managed-identity runbook controller only after pricing,
   permissions and billing visibility are verified. Shared free runtime allowances
   must not be assumed unused. An hourly watchdog cannot replace native idle stop.
9. [SQL warehouse creation](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/create):
   a warehouse is conditional, not mandatory when cost gates fail. If approved,
   select the smallest suitable single-cluster serverless option and one-minute
   API auto-stop; test under a bounded session and explicitly leave it stopped.
10. [Predictive optimization](https://learn.microsoft.com/en-us/azure/databricks/optimizations/predictive-optimization):
    automatic maintenance uses billable serverless jobs compute. Explicitly disable
    it on the eight project schemas until background spending is approved; do not
    change the shared account/catalog default or other schemas.
11. [System tables](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/):
    metadata/retention is free but querying consumes compute. Billing usage includes
    account-wide data, so permission repair should use a reviewed workspace-filtered
    view or narrowly approved access, not broad self-assigned administration.

## Live implementation observations

- The SCIM list operation omitted membership detail; a detail GET is required
  before checking bootstrap group membership. Regression coverage includes that
  actual response pattern and a zero-action second apply.
- Account groups created through the workspace proxy were not automatically
  assigned to the workspace. Explicit USER-only assignments were applied.
- Project schema and volume ownership was transferred to retail_hp_admins, without
  changing the parent catalog owner. CREATE_MODEL is limited to the ml schema.
- Live metadata verification passed 85 checks; repeat apply made zero changes.
- Azure cost requests continued to return 429 after backoff. No numeric cost or
  billing-currency claim, budget creation or paid warehouse fallback was made.
- Full regional pricing/FX and managed-resource-group cost reconciliation remain
  paid-deployment gates, not completed research disguised as an estimate.

## Acceptance honesty

Metadata/grant inspection is not an impersonated viewer/app SQL test. Full
positive/negative execution tests require representative non-admin identities
and approved compute. Report unavailable access and deferred tests explicitly.
Phase 2 must not be called complete while required cost/permission gates remain open.
