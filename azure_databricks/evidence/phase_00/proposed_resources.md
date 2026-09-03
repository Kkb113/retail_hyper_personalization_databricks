# Proposed Azure Databricks resources

Machine-readable source: **azure_databricks/config/proposed_resources.json**.

## Existing resources to retain

| Resource | Scope | State |
|---|---|---|
| Resource group Databricks | Azure | Existing; never delete without approval |
| Workspace intellify-databricks-demo | Databricks / West US / Premium | Existing; reuse without resizing |
| Catalog intellify_databricks_demo | Unity Catalog | Existing; create managed child objects later |

## Planned minimum platform

| Phase | Capability | Lowest-cost configuration |
|---:|---|---|
| 2 | Eight managed schemas | Existing catalog; metadata only |
| 2 | Two managed volumes | Small versioned transfer and model assets |
| 2 | SQL warehouse | One smallest serverless warehouse, one cluster, one-minute API auto-stop |
| 3 onward | Jobs | Triggered serverless; schedules paused at deploy |
| 5 | MLflow model | One functional registered recommender, Candidate before Champion |
| 6 | Dynamic serving | One Small CPU endpoint with scale-to-zero |
| 8 | Semantic retrieval | Embeddings in Delta; no vector endpoint |
| 9 | Agent LLM | Pay per token; selected by cost-quality bake-off |
| 10 | App | One MEDIUM Databricks App; stopped outside demo |
| 11 | Dashboard | One AI/BI dashboard sharing the auto-stopped warehouse |

## Optional and disabled

| Capability | Default | Activation gate |
|---|---|---|
| Lakebase Autoscaling | Off | Quota, price, 1-CU cap, scale-to-zero, and fallback tests |
| Azure AI Search | Off | Free SKU only; never fall back to Basic |
| Genie | Off | Recheck current pricing; human demo only |
| Detailed inference logging | Off | Privacy, retention, and serverless-cost approval |
| Managed continuous quality monitoring | Off | Measured need and cost approval |

Databricks AI Search is rejected for the initial 3,000-product catalog because a
bounded Delta embedding search is simpler and avoids a base endpoint cost.

## Phase 0 mutation result

No Azure or Databricks resource was created, updated, deleted, started, or
stopped. Only read-only inventory calls were executed.
