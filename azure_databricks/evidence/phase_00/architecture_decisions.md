# Phase 0 architecture decisions

## ADR-0001 — Reuse the existing workspace and catalog

Status: Accepted.

Use resource group **Databricks**, workspace **intellify-databricks-demo**, and
catalog **intellify_databricks_demo**. Do not create a second workspace or
catalog for the POC.

## ADR-0002 — Establish a clean Azure subtree

Status: Accepted.

The existing Databricks implementation targets different assumptions and
contains old hardcoded names. The azure_databricks subtree becomes the
authoritative implementation. Legacy code is a migration source only.

## ADR-0003 — Transfer by dependency-derived manifest

Status: Accepted.

The transfer unit is 45 unique files: 20 data files and 26 approved model files
with one overlap. Each file has a SHA-256, size, role, destination, and data
contract where applicable.

## ADR-0004 — Package real recommender inference

Status: Accepted.

The future MLflow model must perform retrieval, ranking, cold start, routing,
business filtering, and evidence generation. A status wrapper or snapshot-only
lookup cannot pass the Phase 5 gate.

## ADR-0005 — LLM is orchestration, not ranking authority

Status: Accepted.

The LLM selects governed tools and explains their results. Product identifiers,
scores, prices, inventory, promotions, and customer facts come from deterministic
services only.

## ADR-0006 — Batch first, dynamic serving only where needed

Status: Accepted.

Existing-customer recommendations are materialized in Gold. One scale-to-zero
endpoint handles new-customer and non-persistent what-if requests.

## ADR-0007 — No default vector endpoint

Status: Accepted.

For 3,000 products, store embeddings in Delta and search a bounded in-memory
matrix. Reconsider a service only when measured scale or latency requires it.

## ADR-0008 — Paid creation is fail-closed

Status: Accepted.

All new resources are disabled until their phase. Paid creation remains blocked
until a budget threshold and notification recipient are approved. A failed Free
SKU lookup never selects a paid tier.
