# Phase 0–8 readiness audit — 2026-09-08

## Decision

The existing architecture is suitable for a small, synthetic-data, occasional-demo
POC. Phase 8 is **not implemented**: it is the next phase, not a completed phase.
Do not describe the solution as ready for Phase 9 or production.

| Phase | Assessment | Evidence / qualification |
|---|---|---|
| 0 — Scope and safety | POC baseline established | Scope allowlists, immutable source contracts, credential scanning. Historical budget deferral is superseded by Phase 2, not current permission to deploy. |
| 1 — Project foundation | Suitable POC scaffold | Packaging, four runtime dependency locks, CI and fail-closed configuration. The initial empty bundle is not an authoritative inventory of later imperative deployments. |
| 2 — Governance | Established with billing limitations | UC groups, schemas and volumes; one-minute SQL auto-stop previously observed; INR 12,000 budget verified live. Managed-resource-group billing coverage remains unverified. |
| 3 — Transfer | Sealed source design is appropriate | Hash verification, no-overwrite transfer, two application seals. This is not storage-enforced WORM. Missing-file inspection false positive corrected in this audit. |
| 4 — Lakehouse | Appropriate Bronze/Silver/features/Gold separation | Historical validation: 50 objects, 275,630 Bronze and Silver rows, zero missing objects/owner mismatches. Later Phase 6 Gold objects must not be mistaken for unauthorized Phase 4 drift. |
| 5 — MLflow | Functional POC recommender | Historical model acceptance plus current registry owner/aliases verified. Current Champion is version 3, superseding the Phase 5 version-1 snapshot. Production holdout remains outstanding. |
| 6 — Serving | Bounded POC serving established | Current endpoint serves version 3; scale-to-zero enabled, explicitly stopped; batch job manual, timeout 180 seconds, no active runs. Historical parity covers 100 demo customers, not full-population batch validation. |
| 7 — Operational state | Foundation established; safety fixes in this audit | Nine owned append-only tables and manual export verified live. Single-process synthetic POC; authenticated App integration is still future work. |
| 8 — Semantic tools | NOT STARTED | Embeddings, typed governed tools, authorization integration and semantic acceptance evidence are absent. Implement and validate before Phase 9. |

## Corrections made

- Transfer inspection now fails when required payloads or control manifests are missing.
- Operational SQL checks the approved warehouse contract and RUNNING state before
  submission; it refuses to intentionally wake stopped compute. A state check is
  not an atomic platform lock: a concurrent stop/start race remains possible.
- SQL deadlines include the initial request; truncated results fail closed.
- Durable replay returns persisted event metadata, not the incoming replay's
  timestamp/correlation ID. Duplicate rows fail closed. A process-wide lock
  serializes writes within the supported single-process POC, not across replicas.
- In-memory payloads are copied on storage and retrieval; event times require UTC-
  convertible, timezone-aware values. Durable actor-filtered reads are available.
- Export invocations get distinct idempotency tokens, avoiding permanent reuse of
  an earlier completed export. SDK retries within one invocation retain its token;
  restarting the command is a new invocation, while insert-only MERGE avoids
  replaying existing exported events.
- Export deployment checks source content and refuses to reset active,
  unrecognized or automatically triggered jobs. Execution checks task bounds.
- Warehouse shutdown is attempted even if temporary-secret revocation fails.
  Client-construction failure attempts revocation immediately.
- Elapsed-cost estimates no longer clip observed time to configured timeouts.

## Explicit limits before expanding the demo

The runtime service principal has table-level SELECT/MODIFY privileges. Actor
filtering is a trusted-server application contract, **not database row-level
security**. A browser or LLM must never supply an arbitrary actor hash or subject.
Future authenticated App/tool code must derive it from verified identity and test
denied access. Generic event envelopes are not automatic PII redaction; synthetic
payloads only. Do not expose the low-level store as a public API.

Delta is not a multi-writer OLTP database. Keep one application writer process;
do not enable replicas until durable concurrency control is designed and tested.
Expiry filters hide expired events; physical deletion and export retention are
not automated. No real personal data should be loaded under this arrangement.

The stopped-state audit is not an invoice audit. Resource-group cost reported
approximately INR 0.00176, which must not be represented as the complete project
bill. Billing lag, storage and unverified managed-group coverage remain. Budgets
are alerts, not hard spending caps. No compute was started or resource created
for this review. The new control code is locally regression-tested; its changed
write/run paths have not been re-exercised on paid compute in this audit.

## Research

Microsoft documents that [queries can start a stopped SQL warehouse](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/).
The [Jobs API idempotency contract](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/api/latest/jobs)
returns an existing run for a reused token. These support the cost and export
corrections above. No paid vector-search service or always-on operational database
is justified by the current POC size.
