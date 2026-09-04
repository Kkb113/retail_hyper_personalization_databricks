# Owner cost-policy clarification — 2026-09-04

The owner clarified that roughly twenty minutes was an example, not a mandatory
timeout. Engineering should select the most cost-effective service-specific
settings while delivering a convincing occasional-use POC within the INR 12,000
monthly target. This supersedes Phase 1's earlier strict-idle interpretation.

## Current policy

- Keep the INR 9,000 internal operating/stop target and INR 3,000 reserve.
- Prefer free/existing resources and the smallest suitable usage-based options.
  No Premium add-ons, always-on demo infrastructure or free-to-paid fallback.
- Use explicit development/demo sessions, pre-start cost admission checks,
  maximum runtime/token limits, idle controls and end-of-session cleanup.
- Select idle settings based on measured costs and restart latency. Do not
  interrupt a live demonstration unnecessarily merely to meet the example.
- Native 30-minute custom-serving scale-to-zero is no longer excluded solely
  because it exceeds twenty minutes. Compare it with triggered/batch inference
  before choosing; it is still disabled and not approved for deployment.
- Treat INR 12,000 as the total planning envelope, reserving headroom for applicable
  taxes, currency conversion, persistent storage and delayed usage. Verify actual
  billing currency/rates before setting the usable development/demo-hour budget.
- If an adequate POC cannot fit the conservative estimate, simplify or pause and
  report the tradeoff; do not silently spend beyond the agreed allowance.

## Honest limitation

Azure PAYG does not provide a custom resource-group hard billing cap. Budget
alerts do not stop resources and use delayed cost data. The architecture must
therefore constrain consumption before it happens, not promise that an alert at
INR 12,000 guarantees the final invoice. The budget, admission checks and shutdown
controller remain **not deployed**; this is a policy clarification only.

Validation: 81 local tests, Ruff, strict mypy and the credential scan pass. The
approved workspace passed strict read-only bundle validation and a zero-action
plan after the policy change. No cloud resources were changed or enabled.

Sources: [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit),
[custom-serving scaling](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models).
