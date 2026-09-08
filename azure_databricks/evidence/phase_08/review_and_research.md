# Phase 8 review and research — 2026-09-08

Reviewed the roadmap, Phase 4 view definitions, sealed 3,000-product catalog and
80 categories, Phase 6 serving client/model release, Phase 7 operational store,
identity/bootstrap controls, dependency locks, CI, and merged PR #15 corrections.
Live preflight found one stopped warehouse, no clusters or Apps, two manual jobs,
and the existing GTE embedding endpoint ready. The INR 12,000 budget exists;
managed-group invoice coverage is not verified.

Primary references and decisions:

1. [Microsoft: query embedding models](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/query-embedding-models)
   documents the existing endpoint's batch-input/vector-response interface. Use
   the same live-verified model identity for document and query vectors.
2. [Microsoft: Foundation Model API limits](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/foundation-model-apis/limits)
   identifies embedding limits and unnormalized GTE outputs. Validate dimensions,
   finiteness and normalize explicitly; fail closed on throttling.
3. [Databricks: Foundation Model Serving pricing](https://www.databricks.com/product/pricing/foundation-model-serving)
   lists GTE input usage at 1.857 DBU per million tokens. A 500,000-token generation
   allowance and INR 44.91/DBU planning conversion reserve about INR 41.70 before
   margin. Conversion is a planning assumption, not a guaranteed invoice rate.
4. [Microsoft: SQL parameter markers](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-parameter-marker)
   supports separating untrusted values from SQL structure. Tools have closed
   identifiers and typed values; no LLM-supplied SQL.
5. [Microsoft: serverless job retries](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
   explains that serverless auto-optimization can add retries independently of
   the task retry count. Explicitly disable it for predictable bounded POC runs.

No additional vector service is justified at 3,000 products. Exact cosine search
over a bounded product-only snapshot avoids dedicated compute and customer-cache
leakage. An optional Azure AI Search Free resource was deliberately not created:
the additional service would not improve this phase's necessary acceptance scope.
