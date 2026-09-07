# Azure Databricks POC cost ledger

## Policy

- Owner planning and verified Azure billing currency: INR.
- Azure scope: resource group **Databricks** only.
- Free or existing capability is always evaluated before a paid resource.
- Contract/list prices are refreshed immediately before a paid creation.
- Owner monthly target: INR 12,000; internal stop target INR 9,000; reserve INR 3,000.
- Two notification recipients supplied privately; addresses are not committed.
- Budget alerts: deployed at INR 12,000 monthly with five actual/forecast rules.
- Shutdown controls: deployed for the project SQL warehouse (one-minute native
  idle stop, 12-minute live-test deadline, unconditional final stop).
- Owner confirmed INR with IT. The budget separately admitted the bounded Phase 2
  warehouse test, Phase 3 transfer validation and Phase 4 Lakehouse build; it is
  not general compute approval.
- New paid or persistent resources remain blocked. No premium add-ons, GPUs,
  provisioned throughput or automatic paid fallback.

## Phase ledger

| Phase | Cloud mutations | Incremental cost | State | Evidence |
|---:|---|---:|---|---|
| 0 | None; read-only inventory only | USD 0.00 | Complete | live_inventory.json and resource_inventory.json |
| 1 | Local code and read-only bundle validation | INR 0 new compute | Foundation | evidence/phase_01 |
| 2 | Governance, budget, bounded warehouse, shutdown/ACL and authenticated workload-identity tests | Conservative total INR 16.1284 estimated pre-tax | Complete; warehouse STOPPED; zero active OAuth test secrets | evidence/phase_02 |
| 3 | Approximately 13 MiB volume transfer, four terminated one-time serverless validations, and two seals | Cumulative INR 103.3578 estimated pre-tax; INR 206.7156 with 2x guard | Complete; warehouse STOPPED; zero clusters/jobs | evidence/phase_03 |
| 4 | 50 governed Lakehouse objects; four terminated one-time serverless builds; one bounded read-only SQL validation | Cumulative INR 161.1619 estimated pre-tax | Complete; warehouse STOPPED; zero clusters/jobs | evidence/phase_04 |
| 5 | Functional composite MLflow Candidate; six bounded serverless attempts | Cumulative INR 198.1409 estimated pre-tax | Complete; Candidate only; warehouse STOPPED; zero clusters/jobs | evidence/phase_05 |
| 6+ | Batch/real-time serving, agent and app work | Not yet authorized | Blocked | Separate phase approval required |

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
The final authenticated workload-identity check added INR 1.5676 estimated
pre-tax, for a conservative total Phase 2 estimate of INR 16.1284.

Phase 3 reused the existing managed volumes and created no Azure resource. The
payload has 12,733,215 unique bytes; the maximum uploaded size including one
duplicated cross-role snapshot and two manifests was 13,041,264 bytes, followed by
one 430-byte compatibility module and two small seals. Files API transfer and
managed-storage charges may apply and are not claimed as zero.

The 2026-09-07 Microsoft retail-price snapshot lists West US Premium automated
serverless compute at INR 44.91 per DBU-hour. Four terminated runtime-validation
attempts total 517.83 elapsed seconds. At the deliberately conservative 16
DBU/hour assumption, the cumulative estimate is INR 103.3578 pre-tax; its 2x
planning guard is INR 206.7156, below the INR 250 Phase 3 validation ceiling.
This is not actual metered billing; taxes, discounts and reporting lag remain.

Phase 4 created no Azure resource and no persistent Databricks job, schedule or
pipeline. Four bounded serverless build attempts total 788 seconds under the
conservative 16 DBU/hour assumption, estimated at INR 157.2848 pre-tax. The final
read-only reconciliation used the existing 2X-Small SQL warehouse for 52.17
seconds, estimated at INR 3.8771 pre-tax, and explicitly stopped it. Cumulative
Phase 4 estimated compute is therefore INR 161.1619 pre-tax, below the INR 250
execution ceiling. Metered usage can lag; taxes, discounts and small managed
storage charges are not included, so this is not a hard billing cap.

Phase 5 reused the existing catalog and sealed volume assets. Six bounded
automated-serverless attempts totaled 992.69 seconds: five runtime-hardening
failures and one canceled duplicate. At the conservative 16 DBU/hour and INR
44.91/DBU-hour assumptions, cumulative Phase 5 compute is INR 198.1409 pre-tax,
below the INR 250 ceiling. The registered Candidate was downloaded and validated
locally without compute; its owner was repaired through the control plane. No
serving endpoint, schedule, GPU, app, LLM, vector index or Azure resource was
created. Metered billing may lag and this estimate is not a hard invoice cap.

## Controls required for later phases

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

## Remaining work before Phase 6

The budget, warehouse controls, transfer, Lakehouse and functional Candidate are
complete. Before any Phase 6 compute:

1. Confirm alert email delivery when Azure evaluates a real threshold.
2. Add service-specific timeout/scale-to-zero tests only when those services are created.
3. Verify all billable compute is stopped after every development/demo session.

Source control records only the state and amount, never personal notification
details. Budget alerts do not stop resources and are evaluated using delayed cost
data. No custom PAYG resource-group hard billing cap exists, so even a stop
controller cannot guarantee an exact INR 12,000 invoice. Small storage/log costs
can remain while compute is off. Do not describe these future controls as active.

Sources: [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit),
[custom-model scaling](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models).
