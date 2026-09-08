# Phase 7 review and research

Reviewed on 2026-09-08 against the repository, the pinned Azure scope, and current
Microsoft Learn documentation.

## Repository and live findings

- Phases 0–6 were complete for the synthetic POC. Champion model version 3, the
  model endpoint, batch job, and recommendation tables already existed.
- The custom serving endpoint and SQL warehouse were stopped, with no active batch
  run, zero clusters, and zero Databricks Apps.
- Unity Catalog had an empty `agent` schema and no operational state tables.
- Live Lakebase discovery returned zero projects. The configuration and cost policy
  both kept Lakebase disabled by default.
- The existing non-admin workload service principal had already passed an
  authenticated SQL test and remained in `retail_hp_app_runtime`.

## Current platform research

Microsoft documents Lakebase Autoscaling as the transactional choice for app state.
It is available in West US, supports a 0.5 CU minimum, and can suspend after 60
seconds through seven days of inactivity. A new project's default timeout can be
24 hours, so the five-minute POC target would have to be configured explicitly.
High availability disables scale-to-zero. Databricks App bindings create a database
role for the app service principal and inject connection metadata without storing a
password in source code.

Sources:

- [Manage Lakebase computes](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/manage-computes)
- [Lakebase limits and regions](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/limitations)
- [Lakebase autoscaling](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/autoscaling)
- [Lakebase high availability](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/high-availability)
- [Lakebase with Databricks Apps](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/databricks-apps)
- [Databricks App authorization](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth)

Microsoft documents Delta Lake optimistic concurrency and write-serializable
isolation. Append-only writes do not read existing table state; concurrent
conflicting changes fail instead of corrupting data. This supports a low-volume,
single-writer event fallback but does not transform Delta into an OLTP database.

Sources:

- [Azure Databricks ACID guarantees](https://learn.microsoft.com/en-us/azure/databricks/lakehouse/acid)
- [Delta table properties](https://learn.microsoft.com/en-us/azure/databricks/tables/table-properties)
- [Delta Lake best practices](https://learn.microsoft.com/en-us/azure/databricks/delta/best-practices)

## Decision

Use the existing-resource Delta/ephemeral fallback now. Lakebase would improve OLTP
semantics, but creating it before the app exists adds cost and an unnecessary
resource lifecycle without improving the Phase 7 demo. Reassess Lakebase only if
concurrent usage or latency measurements show that this fallback is inadequate.

The chosen implementation adds no Azure resource, Lakebase compute, continuous
sync, or schedule. It uses bounded payloads, pseudonymous actor keys, parameterized
SQL, insert-only idempotency, actor-scoped reads, logical retention, and a manual
feedback export.
