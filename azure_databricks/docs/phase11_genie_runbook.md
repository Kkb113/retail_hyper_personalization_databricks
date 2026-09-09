# Phase 11 — Governed retail Genie Agent

Status: **deployed and business-accepted** on 9 September 2026. Space ID:
`01f1ac69031b1786b9b61fa434656e33`. The accepted business conversation completed with
one substantive narrative answer and one executed governed query. The warehouse was
explicitly stopped after acceptance.

## Service contract

**Retail Hyper-Personalization Business Analyst** is the governed natural-language
analytics interface for retail leaders, merchandisers, campaign managers and analysts.
Its purpose is decision support across recommendation reach, assortment, inventory,
customer segments, campaign opportunities, feedback, quality and observed operations.

The configuration is reproducible from
`src/retail_hp_azure/phase11_genie.py`. It contains ten curated Unity Catalog sources,
six business-facing starter questions and no stored raw SQL examples or benchmark
questions. The serialized version-2 configuration is semantically read back after
deployment to detect source, question, instruction, raw-example or benchmark drift;
Databricks is allowed to canonicalize inconsequential JSON formatting.

This agent does not expose customer identifiers or unrestricted event rows. Four
aggregate-only views provide the business dimensions needed for analysis, and six
reconciled Phase 11 summaries provide operational measures. The source data is synthetic
and historical; the instruction contract prevents it from being described as live sales,
revenue, demand, profit or store-level inventory.

## Business answer standard

Every substantive response should lead with the executive answer, then show supporting
evidence, the business implication, a practical next action and material limitations.
The agent must preserve denominators for coverage, sample sizes for small populations,
and snapshot dates for inventory-sensitive conclusions. It translates technical model
routes into business language while retaining the source value for traceability.

The agent must not:

- present recommendation scores as probabilities or sum them as business value;
- describe candidate opportunities as conversions, sales or guaranteed uplift;
- assign a currency symbol where the source currency is unspecified;
- turn missing feedback, evaluation or cost data into zero;
- invent trends, causality, forecasts, statistical significance or customer attributes;
- answer customer-level identity questions from aggregate data.

## Governed data sources

The agent uses these aggregate-only sources in
`intellify_databricks_demo.monitoring`:

- `genie_customer_portfolio`
- `genie_product_portfolio`
- `genie_recommendation_insights`
- `genie_opportunity_insights`
- `phase11_coverage_summary`
- `phase11_routes_summary`
- `phase11_quality_summary`
- `phase11_feedback_summary`
- `phase11_evaluation_summary`
- `phase11_requests_summary`

All ten sources are granted `SELECT` to `retail_hp_viewers`. The agent is shared to the
same group with `CAN_RUN`. Unity Catalog evaluates data authorization as the end user,
while the author's embedded compute credential supplies warehouse access. Consumers
must have the consumer-access or Databricks SQL entitlement.

## Deployment and quality gate

Run the controller only from the approved repository revision:

```powershell
& .\.venv\Scripts\python.exe azure_databricks/scripts/phase11_genie_control.py plan
& .\.venv\Scripts\python.exe azure_databricks/scripts/phase11_genie_control.py inspect
& .\.venv\Scripts\python.exe azure_databricks/scripts/phase11_genie_control.py deploy
```

The deploy command fails closed unless the existing XXSmall serverless warehouse is
stopped with one-minute auto-stop, the recommendation endpoint and Databricks App are
stopped, the monthly safety margin is available, no matching agent already exists, and
the one-time local cost ledger is unused. It arms the existing independent shutdown
controller before compute starts, creates and reconciles the four aggregate views,
applies data and agent ACLs, verifies configuration readback, and submits one comprehensive
business question through the GA conversation API. Acceptance requires the response
to complete without an error, contain a substantive business summary and include
successfully executed governed data evidence. No benchmark API or stored ground-truth
SQL is used. Failed acceptance is not silently waived; tune semantic descriptions and
repeat only under a new approved validation window.

The deployment uses the existing warehouse and creates no Azure resource, cluster,
endpoint, application or schedule. The controller explicitly stops the warehouse after
acceptance, and one-minute auto-stop plus the independent deadline remain layered
backstops.

## Operations and continuous improvement

- Business users use the native Genie Agent UI with their own identities. Do not run
  showcase conversations through a service principal.
- Owners review the Monitor tab after business use for negative feedback, requests for
  review and recurring unanswered questions.
- Improve table descriptions and business definitions when monitoring reveals ambiguity;
  raw SQL examples remain excluded unless the owner changes the business-only contract.
- Test realistic business phrasing through isolated acceptance conversations before
  expanding scope.
- Clone the agent before a material semantic redesign; never overwrite the accepted
  configuration without ETag protection and live business-question regression.
- Reconcile Genie usage through `system.billing.usage` using
  `billing_origin_product = 'GENIE'`; system billing data is delayed by several hours.
- Review promotional terms before each release or after 31 January 2027.

## Cost boundary

As checked on 9 September 2026, Microsoft states that Genie One and Genie Agents usage
by human users is free through 31 January 2027; service principals are excluded. The
underlying SQL warehouse remains usage-based and billable. Promotional pricing is not a
permanent entitlement or an Azure invoice ceiling. The operating policy therefore keeps
service-principal automation disabled, uses the existing XXSmall warehouse, performs no
scheduled refresh, and relies on one-minute auto-stop.

## References

- [Create and manage a Genie Agent](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/set-up)
- [Use the Genie Agents API](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/conversation-api)
- [Test and monitor a Genie Agent](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/monitor)
- [Tune Genie Agent quality](https://learn.microsoft.com/en-us/azure/Databricks/genie-agents/tune-quality)
- [Monitor and understand Genie cost](https://learn.microsoft.com/en-us/azure/databricks/genie/monitor-cost)
