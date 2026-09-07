# Phase 4 review and research

## Review outcome

The sealed Phase 3 package contains 20 Parquet sources and 275,630 rows. The
existing local model depends on customer, product, event, order, inventory,
promotion and snapshot data, but the inherited prototype did not provide a
governed, leakage-safe Lakehouse contract. Phase 4 therefore builds reusable
Delta layers directly from the sealed transfer rather than copying application
or Streamlit storage behavior.

The implementation creates no Azure resource and no always-running Databricks
asset. A one-time CPU serverless task created the objects; one read-only query on
the existing auto-stopped SQL warehouse completed reconciliation. No schedule,
pipeline, endpoint, GPU, LLM, vector index or online store was introduced.

## Research applied

- [Unity Catalog Feature Engineering](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/)
  supports governed feature tables and informational primary keys. The design
  uses three offline Unity Catalog feature tables instead of an additional
  online service.
- [Point-in-time feature joins](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/time-series)
  require time-series keys to avoid training/serving leakage. Customer behavior
  features use a `TIMESERIES` timestamp and prove every source event is strictly
  earlier than its feature row.
- [Serverless jobs](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
  remove idle job-cluster capacity. The build used bounded one-time submissions,
  not a persistent scheduled job.
- [Serverless environment dependencies](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/dependencies)
  informed use of the native environment without extra package installation.
- [COPY INTO](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-copy-into)
  and [schema evolution](https://learn.microsoft.com/en-us/azure/databricks/data-engineering/schema-evolution)
  were reviewed. The implementation instead uses explicit snapshot schemas and
  conditional Delta merges because this POC needs a sealed-hash contract and
  deterministic fail-closed drift behavior.
- Databricks' official
  [Parquet nanosecond timestamp guidance](https://kb.databricks.com/en_US/python/illegal-parquet-type-exception-when-reading-a-parquet-file-with-timestamp-column-of-datatype-int-nanoseconds)
  explains Spark's unsupported `TIMESTAMP_NANOS` failure. The affected Bronze
  column is retained as raw nanoseconds and converted deterministically to
  microseconds in Silver.
- [Serverless budget policies](https://learn.microsoft.com/en-us/azure/databricks/admin/usage/budget-policies)
  can attribute and govern usage, but do not create a guaranteed invoice cap.
  Existing Azure budget admission, runtime limits and stopped-state checks remain
  the POC controls.

## Findings and decisions

1. Bronze is append-only and version/hash aware; Silver is key-merged and
   idempotent. Reapplying the same version produced zero changes.
2. The source does not define a currency. Inventing INR, USD or another business
   meaning would be incorrect; the governed value is `UNSPECIFIED` and production
   remains blocked on a currency contract.
3. Unity Catalog key constraints are informational. Duplicate, null and orphan
   tests remain mandatory.
4. These small snapshot tables do not justify continuous pipelines, managed
   monitoring, clustering/optimization jobs or an online feature store.
5. The Gold recommendation view exposes the frozen transferred snapshot; the
   functional MLflow recommender is intentionally Phase 5 work.
6. A Databricks AI/BI dashboard, Genie space and Databricks App depend on later
   semantic, model and agent phases. Phase 4 supplies their governed data base.
7. The timed-out build ended before its owner-transfer block. A fail-closed
   metadata audit detected this; the metadata API then changed exactly the 50
   expected objects to `retail_hp_admins` without starting compute, and the
   post-repair audit found zero mismatches.

## Limitations

- Synthetic snapshot only; no streaming or source freshness SLA.
- Application-level sealed transfer, not storage-level WORM.
- Source currency is unspecified.
- Cost figures are conservative estimates; metered billing can lag and taxes or
  contract discounts are not represented.
- Production privacy, security, data retention and business approval remain HOLD.
