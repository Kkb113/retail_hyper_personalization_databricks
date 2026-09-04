# Phase 2 — governance implemented; cost gate blocked

## Outcome

The metadata foundation has been applied to the pinned Azure Databricks workspace.
Live verification passed **85 checks**. Reapplying the bootstrap produced **zero
actions**. This is **partial Phase 2 completion**, not permission to start Phase 3
or paid compute.

The full local suite passed **97 tests** (two existing dependency-deprecation
warnings), plus Ruff, mypy, dependency compatibility and the credential scan.
See `acceptance.json` for the separate passed and blocked gates.

Created/configured:

- Four account groups, assigned to this workspace as **USER**, never ADMIN.
- Eight schemas: bronze, silver, features, ml, gold, serving, agent, monitoring.
- Two empty managed volumes: bronze.transfer_landing and ml.model_assets.
- Ownership of these ten objects assigned to retail_hp_admins.
- Specific catalog/schema/volume grants; no ALL PRIVILEGES grant.
- Project tags merged onto the existing Databricks resource group and workspace.
- Predictive optimization explicitly DISABLE on all eight project schemas.

Only the existing bootstrap administrator was added to retail_hp_admins. The
other groups have no newly invited users or service principals. Notification
recipients were not made Databricks users. The parent catalog owner was not changed.
No source data/model uploads, SQL statements, model calls, compute starts, Azure
resource creations, paid add-ons or deletions were performed.

## Access contract

| Group | Catalog | Project schemas | Managed volumes | Workspace |
|---|---|---|---|---|
| retail_hp_admins | USE, CREATE SCHEMA | Owns eight schemas; explicit create/read/write/execute | Owns both; read/write | USER, not workspace admin |
| retail_hp_engineers | USE | Create tables/functions; select/modify/execute; CREATE MODEL in ml | Read/write both | USER, no account-admin role granted |
| retail_hp_viewers | USE | USE, SELECT, EXECUTE on serving only | None | USER |
| retail_hp_app_runtime | USE | USE, SELECT, EXECUTE on serving only | None | USER; workload principal pending |

The exact machine-readable privilege matrix is `grant_matrix()` in phase2.py.
The serving schema is reserved for approved serving data/views/functions; do not
put raw or sensitive tables there. Future model execution should be exposed
through an explicitly reviewed tool/function or narrow model grant, not raw-data
access for the app. Jobs, registered models, views, warehouse and app resources
do not yet exist, so their object-specific ACLs are not claimed as applied.

Verification inspects explicit grants, owners, group assignment, direct admin
group membership, absence of broad grants to other principals, tags and schema
settings. It is **not** a SQL execution test using a viewer/app identity. Effective
permissions of future users, including other/nested groups, must be tested when
identities are approved. The bootstrap user remains a pre-existing workspace
administrator; these checks do not constrain that identity outside this workflow.

## Runbook

Use the repository's locked Python 3.12 environment, installed editable, and
authenticate with Azure CLI privately. Run from the repository root:

```powershell
python -m retail_hp_azure.phase2 plan-governance
python -m retail_hp_azure.phase2 inspect
python -m retail_hp_azure.phase2 apply-governance
python -m retail_hp_azure.phase2 verify-governance
python -m retail_hp_azure.phase2 inspect-compute
```

Only `apply-governance` writes cloud state. Its fixed scope fingerprints and
allowlisted names/routes forbid compute creation, resource deletion and uploads.
Conflicting names, project privilege drift or foreign ownership stop the apply;
unrelated grants/objects are preserved. Partial mutations are recorded as they
complete. Review the ledger and resolve a conflict before rerunning; do not
automatically remove objects or privileges to make a check pass.

`inspect` records individual denied/unavailable capabilities without claiming
overall readiness. Respect Azure retry headers and avoid repeatedly polling cost
queries. `verify-governance` exits nonzero on failed metadata checks. It can pass
while `phase2_complete` remains false because the cost/identity gate is separate.
Phase 1's resource-free bundle, configuration and historical evidence are unchanged.

## Blockers and next actions

1. **Azure cost/currency visibility:** repeated RG-scoped Cost Management requests
   returned HTTP 429, including after a long backoff. No trustworthy current spend
   or billing currency was returned. The budget inventory is readable and empty.
   An Azure billing owner should verify current-month cost and currency in Cost
   Analysis for this scope and resolve the API throttling/support issue. A denied
   or empty response is not zero spend. Do not guess a currency conversion.
2. **Databricks billing visibility:** the current identity receives PermissionDenied
   for system.billing table listing and system-schema state listing. Ask an
   account/metastore administrator for a least-privilege, workspace-filtered
   billing view in monitoring (usage plus approved price information), or explicit
   approval for narrowly scoped billing access. System billing contains other
   workspaces' usage; do not self-grant broad account/metastore administration.
3. **Budget and controller:** still not deployed. After currency and price checks,
   configure actual/forecast notifications to the two privately supplied recipients,
   test delivery, implement/test scoped admission and shutdown controls, and then
   evaluate a single smallest suitable serverless warehouse with one-minute idle
   stop and bounded session duration. No warehouse is required merely to create
   these schemas and volumes.
4. **Identity tests:** confirm the client workload identity and representative
   non-admin identities, then test allowed and denied SQL/file/job operations
   during an approved bounded compute session. Do not create dummy people or
   publish credentials for this purpose.
5. **Expiry:** owner review is required; expiry remains null. No deletion schedule
   exists. Agree the date before a handoff or unattended deployment.

The INR 12,000 monthly target, INR 9,000 internal stop target and INR 3,000 reserve
remain unchanged. The auto-stop tag expresses policy, **not a running shutdown
controller**. Repository gates are not a hard Azure invoice cap. Existing storage
and other workspace costs can persist with compute off; the associated managed
resource group's cost coverage must be reconciled before quoting a total. No
managed-resource-group writes were made or authorized by this implementation.

## Evidence

- `review_and_research.md`: source review and primary-source decisions.
- `live_discovery.json`: sanitized scope, namespaces and blocked cost checks.
- `governance_first_apply.json`: initial metadata creation.
- `governance_cumulative_actions.json`: unique completed metadata actions.
- `governance_last_changes.json`: last apply that changed metadata.
- `governance_result.json`: latest repeat apply; zero actions.
- `governance_verification.json`: all 85 live metadata checks passed.
- `compute_inventory.json`: final read-only platform inventory.

None of these reports contains recipient addresses, bearer tokens or raw Azure
subscription/tenant/principal identifiers. Metadata evidence is not a billing
statement or a replacement for Databricks audit logs.
