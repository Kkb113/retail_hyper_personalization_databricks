# Azure Databricks POC cost ledger

## Policy

- Currency: USD for planning.
- Azure scope: resource group **Databricks** only.
- Free or existing capability is always evaluated before a paid resource.
- Contract/list prices are refreshed immediately before a paid creation.
- Azure budget amount: pending owner approval.
- Budget notification recipient: pending owner approval and intentionally not
  committed.
- Paid resource creation: blocked.

## Phase ledger

| Phase | Cloud mutations | Incremental cost | State | Evidence |
|---:|---|---:|---|---|
| 0 | None; read-only inventory only | USD 0.00 | Complete | live_inventory.json and resource_inventory.json |
| 1 | Code and bundle validation only | USD 0.00 target | Planned | Not started |
| 2+ | Governed resources and bounded compute | Not yet estimated | Blocked | Fresh pricing and owner budget decision required |

## Hard technical controls

- Zero all-purpose clusters.
- One smallest serverless SQL warehouse with one-minute API auto-stop.
- Triggered jobs with schedules paused at deployment.
- One Small CPU model endpoint with scale-to-zero.
- No GPU and no provisioned foundation-model throughput.
- One MEDIUM app, stopped outside development and demo windows.
- Lakebase off by default; if approved, maximum 1 CU, rapid scale-to-zero, no
  HA, and no replica.
- Zero vector-search endpoints by default.
- Pay-per-token model calls with token and rate limits.

## Budget decision required before Phase 2 paid deployment

The owner must provide:

1. Monthly alert amount.
2. Notification recipient or action group.
3. Whether alerts should be set at multiple thresholds.

Source control records only the state and amount, never personal notification
details. Budget alerts are not treated as an automatic spending stop.
