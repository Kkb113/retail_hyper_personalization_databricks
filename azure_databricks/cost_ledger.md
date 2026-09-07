# Azure Databricks POC cost ledger

## Policy

- Owner planning currency: INR; Azure billing currency must be verified before
  creating the budget (do not assume INR or use an unverified conversion).
- Azure scope: resource group **Databricks** only.
- Free or existing capability is always evaluated before a paid resource.
- Contract/list prices are refreshed immediately before a paid creation.
- Owner monthly target: INR 12,000; internal stop target INR 9,000; reserve INR 3,000.
- Two notification recipients supplied privately; addresses are not committed.
- Budget alerts: deployed at INR 12,000 monthly with five actual/forecast rules.
- Shutdown controller: not deployed.
- Owner confirmed INR with IT. Budget deployment is not authorization to bypass
  the remaining paid-deployment or runtime controls.
- Paid resource creation: blocked pending controls, current pricing and approval
  of the specific resource. No premium add-ons or automatic paid fallback.

## Phase ledger

| Phase | Cloud mutations | Incremental cost | State | Evidence |
|---:|---|---:|---|---|
| 0 | None; read-only inventory only | USD 0.00 | Complete | live_inventory.json and resource_inventory.json |
| 1 | Local code and read-only bundle validation | INR 0 new compute | Foundation | evidence/phase_01 |
| 2 | Groups, schemas, empty managed volumes, grants, tags, INR budget; predictive optimization disabled | Current spend INR 0.00 at budget verification; no compute started | Governance and budget verified; runtime gate pending | evidence/phase_02 |
| 3+ | Transfer and bounded compute | Not yet estimated | Blocked | Current pricing, budget notifications and shutdown controls required |

Phase 2 has not created any Azure resource or SQL warehouse. It uses existing
managed storage for empty volume metadata. This is not a claim of zero total
Azure spend: the generic cost query returned HTTP 429 and Databricks system
billing access is denied. The budget resource now verifies INR and reports INR
0.00 current spend, subject to billing delay. Managed-resource-group/storage cost
coverage is not verified. The auto-stop tag is a policy label, not evidence that
an automated stop controller is running.

The 2026-09-07 Microsoft retail-price snapshot lists Premium Serverless SQL at
INR 66.8824 per DBU-hour in West US. A 2X-Small warehouse is documented at four
DBU/hour, yielding an estimated INR 267.5296 per running hour or INR 89.1765 for
20 minutes, before tax and without negotiated discounts. A conservative INR 250
ceiling is proposed for one bounded live test and remains pending owner approval.

## Required future controls (not deployed in Phase 1)

- Zero all-purpose clusters.
- One smallest serverless SQL warehouse with one-minute API auto-stop.
- Triggered jobs with schedules paused at deployment.
- Twenty minutes is illustrative, not mandatory. Optimize idle settings per
  service using measured cost and acceptable cold-start behavior. Native
  30-minute serving scale-to-zero may be evaluated alongside triggered/batch
  inference; all serving remains disabled until the cost/deployment gate passes.
- Explicitly bounded development/demo sessions, end-of-session cleanup and
  maximum runtime/token limits are required in addition to idle shutdown.
- Reject a session start if its conservative full cost estimate would exhaust
  the remaining operating allowance. Include applicable taxes, storage and
  currency conversion in planning, and reserve for delayed usage reporting.
- No GPU and no provisioned foundation-model throughput.
- One MEDIUM app, stopped outside development and demo windows.
- Lakebase off by default; if approved, maximum 1 CU, rapid scale-to-zero, no
  HA, and no replica.
- Zero vector-search endpoints by default.
- Pay-per-token model calls with token and rate limits.

## Remaining budget work before paid deployment

The owner has approved the target and supplied two recipients. Implementation must:

1. Verify Azure billing currency and current regional prices.
2. Create scoped actual/forecast notifications and test delivery to both recipients.
3. Build/test an idempotent, scoped stop controller with an INR 9,000 internal target.
4. Test human inactivity, session expiry, job timeout and manual shutdown independently.
5. Verify all billable compute is stopped after development/demo sessions.

Source control records only the state and amount, never personal notification
details. Budget alerts do not stop resources and are evaluated using delayed cost
data. No custom PAYG resource-group hard billing cap exists, so even a stop
controller cannot guarantee an exact INR 12,000 invoice. Small storage/log costs
can remain while compute is off. Do not describe these future controls as active.

Sources: [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit),
[custom-model scaling](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models).
