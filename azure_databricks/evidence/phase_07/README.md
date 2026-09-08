# Phase 7 acceptance

Phase 7 is complete for the synthetic, occasional-demo POC using the documented
Delta/ephemeral fallback.

## Live result

- Eight append-only operational Delta tables exist under `agent`.
- One append-only analytical feedback table exists under `monitoring`.
- All nine tables are owned by `retail_hp_admins`.
- The non-admin runtime identity authenticated and wrote one synthetic probe.
- Replaying the same write created no duplicate; a second actor could not read it.
- The raw authenticated subject was not persisted, and the temporary OAuth secret
  was revoked.
- The manual feedback export job ran successfully; its second pass added zero rows.
- The export job has no schedule, no retries, a 180-second timeout, and no active run.
- Lakebase was not created. No Azure resource, cluster, app, vector endpoint, GPU,
  or continuous workload was added.
- The SQL warehouse and model endpoint are stopped.

Final local acceptance passed 175 Azure tests, Ruff, strict mypy, the repository
credential scan, a wheel build, and clean jobs/serving/agent/app import smokes.

## Cost result

The initial conservative Phase 7 plan reserved INR 178.9965 pre-tax under the INR
250 phase ceiling. Observed wall-clock estimates were INR 5.9811 for the warehouse
acceptance, INR 11.3678 for the first export, and INR 12.2285 for the final
retention-aware export, or INR 29.5774 combined pre-tax. The remedial run was
admitted at INR 107.1689 including prior elapsed estimates and its 2.5x timeout
guard. These are planning estimates, not metered invoice values. Billing can lag,
taxes and discounts are not included, and small managed-storage charges remain.

## Honest limitations

- Delta is not an OLTP database. This fallback is suitable for a low-volume,
  single-writer demonstration, not concurrent production traffic.
- Logical expiry is present, but physical deletion is not scheduled because the
  tables are append-only and continuous maintenance is disallowed.
- The future Databricks App needs its own service principal grant/binding when the
  app is created. Phase 7 validated the existing equivalent non-admin runtime
  service principal rather than creating an app early.
- No real PII, customer communications, online learning, or production SLA is in
  scope.

See the [operational contract](../../contracts/phase7_operational_state_contract.md),
[runbook](../../docs/phase7_operational_runbook.md), and sanitized JSON evidence in
this directory.
