# Durable customer readiness and business evidence

Status: implemented locally; live view migration and App acceptance are pending.
The previously deployed business-language release is not this revision.
Staged release: `34b91f434900c63f4741e10c5f95e74598278ebcb1cbc7a7786a57396dfdc2ee`.
Validation: 327 Python tests passed, 4 mutually exclusive launch cases skipped;
6 frontend tests and production build passed; lint, type checks, credential scan,
and Azure metadata/identity preflight passed. No compute was started for staging.

## Root causes and corrections

- Launch configuration granted IDs 1–100 while the published batch selected every
  fiftieth customer. Only two IDs overlapped. Approved cohort actors now resolve
  complete published customer sets with the caller's own Databricks identity.
  Explicitly restricted users retain their exact grants; no automatic cohort role
  is inferred from sign-in, email, a prompt, or quality access.
- `tool_customers` omitted profile preferences and purchase detail already present
  in the lakehouse. Its v2 definition adds bounded purchase evidence and explicitly
  labels synthetic profile attributes. It excludes cancelled orders and purchases
  at or after the customer feature cutoff. It exposes no order IDs or raw access.
- The writer could invent item relevance or duplicate caveats. Recommendation
  summary, customer facts, ordered product reasons and availability notes now use
  deterministic rendering. The LLM continues routing and supplying next-step or
  general retail advice; this is not a universal factual guarantee for that prose.
- Ten-product requests override a conflicting planner default. Empty batches return
  an availability explanation, never recommendations borrowed from another customer.
- Technical recommendation codes no longer appear in business product cards.

## Access, freshness and cost

`cohort_subjects` is a private, server-owned launch role. It is granted only to the
existing operator for this synthetic POC. Every normal authentication refreshes
coverage using OBO SQL; no cross-user membership cache exists. A cohort change
invalidates the old browser session, requiring a new chat. Complete ten-row batches
must have unique products/ranks, one batch ID, the pinned model version, an active
customer and eligible products. This does not grant access to all 5,000 customers.

Dynamic cohort resolution adds a bounded SQL query to normal authentication and may
wake the existing warehouse inside the lease. It is capped at 200 lookups per App
process; failed queries consume a reservation. It never starts the real-time endpoint.
Health/dependency probes verify identity without resolving the cohort or waking SQL.
Underlying Unity Catalog permissions still apply to each data query.

No new Azure resources, SQL warehouses, serving endpoints, embeddings, or retraining
are required. The existing launch, LLM and shutdown limits are unchanged. No further
spending allowance has been added for this revision.

## Release sequence

1. Build and stage the exact tested wheel and frontend with the existing packager.
2. Run metadata/identity preflight, including required source column checks.
3. Obtain one approved bounded deployment window; retain all earlier reservations.
4. The launcher arms shutdown, starts the existing warehouse, and upgrades only
   `serving.tool_customers` before starting the new App. The prior view DDL is saved
   under ignored `build/` for rollback; its admin ownership is restored explicitly.
   The view remains backward-compatible with the previous App's selected columns.
5. Migration acceptance checks full active published coverage and validates customer
   contracts at first/middle/last cohort positions. Failure prevents App startup and
   triggers the launcher's existing cleanup; no additional resources are attempted.
6. Validate the operator's cohort, restricted tester denial, multiple customer
   histories, ten products, customer switching, follow-up and general retail answers.
   Confirm the UI's business reasons and retain final shutdown evidence.

## Repeatable offline review

`demo_customer_review.py` evaluates the same shared cohort selection policy as the
batch notebook using the frozen local model. It checks ten distinct products per
customer and reports category variety and purchase-entry counts. This is a demo
selection aid, not a recommender accuracy measure or proof of live availability.
Do not substitute diversity for relevance, or silently reorder the model to look
better. Stronger model quality needs a separate evaluation/retraining decision.

## Research and remaining validation

The existing on-behalf-of-user design is retained because Databricks applies the
caller's Unity Catalog permissions to data access. See [Microsoft's Databricks App
authorization guidance](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth).
Azure metadata inspection confirmed all six customer-context source schemas without
starting compute. SQL execution, actual grant preservation, operator cohort access
and live App behavior must still pass in the approved rollout window.
