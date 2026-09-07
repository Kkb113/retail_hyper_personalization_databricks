# Phase 2 — governed platform verified; client identity pending

## Outcome

The governed foundation and one bounded SQL warehouse have been applied to the
pinned Azure Databricks workspace. Governance verification passed **85 checks**;
repeat apply produced **zero actions**. The SQL smoke test, **19 effective group
grant checks**, **seven warehouse ACL checks**, explicit final stop, and native
one-minute idle stop all passed. The warehouse is **STOPPED**.

The full local suite passed **110 tests** (two existing dependency-deprecation
warnings), plus Ruff, mypy, dependency compatibility and the credential scan.
See `acceptance.json` for the separate passed and blocked gates.

### Owner decision — INR confirmed; bounded test approved

The owner confirmed the TargetSubscription billing currency as INR with IT. The
resource-group budget is deployed and verified at INR 12,000 monthly with five
actual/forecast notification rules and two private recipients. Recipient addresses
are not recorded in evidence. Notification configuration is verified; delivery
will occur only when Azure evaluates a threshold. This does not itself authorize
general paid compute. The owner separately approved a maximum INR 250 planning
ceiling for this Phase 2 warehouse test. The implemented 12-minute ceiling is
estimated at INR 53.5059 pre-tax; the 3.5x guarded estimate is INR 187.2707.
Recorded successful activity plus a conservative allowance for the first failed
sub-minute attempt totals INR 14.5608 pre-tax. Azure billing can lag, so this is
not an invoice guarantee.

Created/configured:

- Four account groups, assigned to this workspace as **USER**, never ADMIN.
- Eight schemas: bronze, silver, features, ml, gold, serving, agent, monitoring.
- Two empty managed volumes: bronze.transfer_landing and ml.model_assets.
- Ownership of these ten objects assigned to retail_hp_admins.
- Specific catalog/schema/volume grants; no ALL PRIVILEGES grant.
- Project tags merged onto the existing Databricks resource group and workspace.
- Predictive optimization explicitly DISABLE on all eight project schemas.
- One 2X-Small serverless SQL warehouse, min/max one cluster, API auto-stop one minute.
- Group-only warehouse ACLs: admins CAN MANAGE, engineers CAN MONITOR, viewers and
  app runtime CAN USE.
- A 12-minute wall-clock fallback, unconditional final stop, exact-name emergency
  stop, and STOPPED-state verification.

Only the existing bootstrap administrator was added to retail_hp_admins. The
other groups have no newly invited users or service principals. Notification
recipients were not made Databricks users. The parent catalog owner was not changed.
No source data/model uploads, model calls, Azure resource creations, paid add-ons
or deletions were performed. One harmless SQL context/smoke statement was executed.

## Access contract

| Group | Catalog | Project schemas | Managed volumes | Workspace |
|---|---|---|---|---|
| retail_hp_admins | USE, CREATE SCHEMA | Owns eight schemas; explicit create/read/write/execute | Owns both; read/write | USER; warehouse CAN MANAGE |
| retail_hp_engineers | USE | Create tables/functions; select/modify/execute; CREATE MODEL in ml | Read/write both | USER; warehouse CAN MONITOR; no account-admin role |
| retail_hp_viewers | USE | USE, SELECT, EXECUTE on serving only | None | USER; warehouse CAN USE |
| retail_hp_app_runtime | USE | USE, SELECT, EXECUTE on serving only | None | USER; warehouse CAN USE; workload principal pending |

The exact machine-readable privilege matrix is `grant_matrix()` in phase2.py.
The serving schema is reserved for approved serving data/views/functions; do not
put raw or sensitive tables there. Future model execution should be exposed
through an explicitly reviewed tool/function or narrow model grant, not raw-data
access for the app. Jobs, registered models, views, and app resources do not yet
exist, so their object-specific ACLs are not claimed as applied.

Verification inspects explicit grants, owners, group assignment, direct admin
group membership, absence of broad grants to other principals, tags and schema
settings. Effective permissions for the three non-admin project groups were read
from Unity Catalog and passed 19 allow/deny checks. This is **not authenticated
execution as the future app principal**; its credential does not exist yet. The
bootstrap user remains a pre-existing workspace
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
python -m retail_hp_azure.phase2 plan-paid-test
python -m retail_hp_azure.phase2 run-paid-test
python -m retail_hp_azure.phase2 apply-warehouse-acl
python -m retail_hp_azure.phase2 test-idle-shutdown
python -m retail_hp_azure.phase2 stop-project-warehouse
```

The last four compute commands are operational and must not be run casually.
`run-paid-test` additionally requires the exact runtime environment approval
`RETAIL_HP_PHASE2_PAID_TEST_CEILING_INR=250`. Its fixed 12-minute deadline,
one-minute native auto-stop and `finally` stop protect the single allowlisted
warehouse. `stop-project-warehouse` is idempotent and matches that exact name only.
No command permits resource deletion or upload.
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

1. **Azure cost/currency visibility:** INR is confirmed by the owner with IT and
   independently returned by the deployed budget resource. The generic RG-scoped
   Cost Management query previously returned HTTP 429; use the budget current-spend
   field as the bounded Phase 2 admission input and continue to respect reporting lag.
2. **Databricks billing visibility:** the current identity receives PermissionDenied
   for system.billing table listing and system-schema state listing. Ask an
   account/metastore administrator for a least-privilege, workspace-filtered
   billing view in monitoring (usage plus approved price information), or explicit
   approval for narrowly scoped billing access. System billing contains other
   workspaces' usage; do not self-grant broad account/metastore administration.
3. **Budget and controller:** budget admission, explicit stop, deadline fallback,
   exact-name emergency stop, and native idle stop are verified. Notification
   delivery still awaits Azure evaluating a real threshold.
4. **Identity tests:** Unity Catalog effective group grants and warehouse ACLs pass.
   The remaining gap is authenticated execution as the client-approved workload
   principal. Do not create a dummy identity or publish a credential to close it.
5. **Expiry:** owner review is required; expiry remains null. No deletion schedule
   exists. Agree the date before a handoff or unattended deployment.

The INR 12,000 monthly target, INR 9,000 internal stop target and INR 3,000 reserve
remain unchanged. Repository gates are not a hard Azure invoice cap. Existing storage
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
- `budget_verification.json`: INR amount and notification counts; no addresses.
- `pricing_snapshot.json`: Microsoft retail-price input and bounded test estimate.
- `warehouse_live_test.json`: SQL smoke, effective grants, elapsed estimate and final stop.
- `warehouse_acl_verification.json`: sanitized group-only ACL result.
- `warehouse_idle_shutdown_test.json`: observed native idle stop and fallback state.
- `warehouse_stop_verification.json`: exact-name emergency stop result after the
  first parser failure.

None of these reports contains recipient addresses, bearer tokens or raw Azure
subscription/tenant/principal identifiers. Metadata evidence is not a billing
statement or a replacement for Databricks audit logs.
