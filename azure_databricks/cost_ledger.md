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
- Shutdown controls: deployed for the project SQL warehouse (one-minute native
  idle stop, 12-minute live-test deadline, unconditional final stop).
- Owner confirmed INR with IT. The budget did not authorize anything beyond the
  separately approved bounded Phase 2 test.
- Paid resource creation outside the single approved Phase 2 warehouse remains
  blocked. No premium add-ons or automatic paid fallback.

## Phase ledger

| Phase | Cloud mutations | Incremental cost | State | Evidence |
|---:|---|---:|---|---|
| 0 | None; read-only inventory only | USD 0.00 | Complete | live_inventory.json and resource_inventory.json |
| 1 | Local code and read-only bundle validation | INR 0 new compute | Foundation | evidence/phase_01 |
| 2 | Governance, INR budget, one bounded serverless warehouse and shutdown/ACL tests | Recorded successful runs INR 10.102 estimated pre-tax; conservative total including failed attempt INR 14.5608 | Platform verified; warehouse STOPPED; client identity pending | evidence/phase_02 |
| 3+ | Transfer and bounded compute | Not yet estimated | Blocked | Explicit Phase 3 approval and workload identity required |

Phase 2 created no new Azure resource, but it created one Databricks SQL warehouse
inside the existing workspace. The warehouse is 2X-Small serverless, one cluster,
one-minute auto-stop, and verified STOPPED. This is not a claim of zero total
Azure spend: the generic cost query returned HTTP 429 and Databricks system
billing access is denied. The budget resource now verifies INR and reports INR
0.00 current spend, subject to billing delay. Managed-resource-group/storage cost
coverage is not verified. Native idle stop was observed after 115.03 seconds;
the four-minute fallback did not fire.

The 2026-09-07 Microsoft retail-price snapshot lists Premium Serverless SQL at
INR 66.8824 per DBU-hour in West US. A 2X-Small warehouse is documented at four
DBU/hour, yielding an estimated INR 267.5296 per running hour or INR 89.1765 for
20 minutes, before tax and without negotiated discounts. A conservative INR 250
ceiling was approved for one bounded live test. The implemented 12-minute maximum
is INR 53.5059 pre-tax at retail; a 3.5x planning guard is INR 187.2707. Recorded
successful activity estimated INR 10.102 pre-tax, plus an INR 4.4588 conservative
upper estimate for the failed sub-minute attempt, well below INR 250.

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

## Remaining work before Phase 3 paid deployment

The budget and warehouse controls are deployed. Before wider paid deployment:

1. Confirm alert email delivery when Azure evaluates a real threshold.
2. Provision the client-approved workload identity and run authenticated allow/deny tests.
3. Add service-specific timeout/scale-to-zero tests only when those services are created.
4. Verify all billable compute is stopped after every development/demo session.

Source control records only the state and amount, never personal notification
details. Budget alerts do not stop resources and are evaluated using delayed cost
data. No custom PAYG resource-group hard billing cap exists, so even a stop
controller cannot guarantee an exact INR 12,000 invoice. Small storage/log costs
can remain while compute is off. Do not describe these future controls as active.

Sources: [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit),
[custom-model scaling](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models).
