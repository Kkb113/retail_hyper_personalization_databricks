# Phase 0 official research record

Research was refreshed on 2026-09-03. Primary Microsoft Learn, Azure
Databricks, and MLflow documentation was used.

## Decisions supported by research

### Declarative deployment

Databricks recommends validating Declarative Automation Bundles before
deployment. Bundle identity includes name, target, and deployer, so the Azure
target must be distinct from legacy targets.

- [Develop Declarative Automation Bundles](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/work-tasks)
- [Bundle resource reference](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/resources)

Decision: Phase 1 will create a clean Azure bundle and require validation before
any deploy.

### Governed file transfer

Unity Catalog volumes support arbitrary files and require USE CATALOG, USE
SCHEMA, and WRITE VOLUME privileges for upload.

- [Work with files in Unity Catalog volumes](https://learn.microsoft.com/en-us/azure/databricks/volumes/volume-files)
- [Create and manage volumes](https://learn.microsoft.com/en-us/azure/databricks/volumes/utility-commands)

Decision: the 45-file package will land in managed volumes rather than a new
storage account.

### Cost evidence

The **system.billing.usage** table includes resource, identity, product, and
custom-tag attribution. It can be joined with **system.billing.list_prices** for
list-cost estimates.

- [Monitor costs using system tables](https://learn.microsoft.com/en-us/azure/databricks/admin/usage/system-tables)
- [Billable usage table reference](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing)
- [Serverless usage-policy tags](https://learn.microsoft.com/en-us/azure/databricks/admin/usage/budget-policies)

Decision: future phases must tag usage and append observed usage to the cost
ledger after each controlled run.

### Azure budgets

Budget alerts notify when actual or forecast spend crosses thresholds. They do
not provide a universal automatic hard stop.

- [Cost Management and Billing overview](https://learn.microsoft.com/en-us/azure/cost-management-billing/cost-management-billing-overview)
- [Monitor usage and spending with cost alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending)

Decision: the source-controlled gate blocks paid deployment until the owner
approves a threshold and recipient. Auto-stop and service-level caps remain
mandatory even after a budget is created.

### Model serving

Custom MLflow models can use scale-to-zero. The first request after inactivity
has a cold start, commonly tens of seconds and sometimes minutes, with no
scale-from-zero SLA.

- [Custom models overview](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models)
- [Model serving overview](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/)

Decision: known users use batch Gold tables; only dynamic cold-start and
scenario requests use one small scale-to-zero endpoint.

### Lakebase

Lakebase Autoscaling is documented for West US. Scale-to-zero can suspend idle
compute, with a configurable timeout from 60 seconds to seven days.

- [Lakebase project limits and regions](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/limitations)
- [Lakebase scale to zero](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/scale-to-zero)

Decision: Lakebase is optional and defaults off. A Delta fallback is required.

### Search

Azure AI Search permits one Free service per subscription, subject to limits
and availability. The Free tier cannot scale. Databricks AI Search has a base
cost after an index exists.

- [Try Azure AI Search for free](https://learn.microsoft.com/en-us/azure/search/search-try-for-free)
- [Create an Azure AI Search service](https://learn.microsoft.com/en-us/azure/search/search-create-service-portal)
- [Databricks AI Search cost management](https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search-cost-management)

Decision: precompute embeddings into Delta and use bounded cosine search for
the current 3,000-product catalog. Azure AI Search Free is optional; a failed
Free preflight must never fall back to Basic.

### Genie

Genie usage and cost are visible in billing system tables. Promotional terms
are time-bound and differ for human and service-principal usage.

- [Monitor and understand Genie cost](https://learn.microsoft.com/en-us/azure/databricks/genie/monitor-cost)

Decision: Genie is optional, human-demo-only, and must undergo a current pricing
check immediately before activation.

## Research limitations

- Contract prices can differ from public list prices.
- Preview status, quotas, regional availability, and endpoint inventory can
  change.
- Therefore every paid or optional creation requires a fresh preflight during
  its implementation phase.
