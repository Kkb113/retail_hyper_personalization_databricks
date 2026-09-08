# Phase 7 operational state contract

- Version: `retail_hp_operational_delta_v1`
- Scope: synthetic POC only
- Selected store: Delta/ephemeral hybrid
- Lakebase projects created: 0
- Azure resources created: 0

## Decision

Lakebase is the preferred production-style OLTP option, but it is not required
for this occasional-demo POC. Live discovery found no existing Lakebase project.
Creating one would add a paid resource and a second compute lifecycle before the
app exists. Phase 7 therefore uses the roadmap's documented fallback:

- authenticated writes go to small Unity Catalog Delta event tables;
- a running app may keep current conversation turns in memory;
- if the Delta path or SQL warehouse is unavailable, the application remains
  usable for read-only recommendation browsing and rejects stateful writes;
- no recommendation path depends exclusively on the operational store.

This is a deliberately single-writer POC design. It is not a production OLTP
claim and does not provide multi-table atomicity, enforced relational constraints,
or low-latency high-concurrency writes.

## Data contract

The `agent` schema contains eight append-only Delta tables:

1. `conversation`
2. `message`
3. `feedback`
4. `recommendation_impression`
5. `scenario_session`
6. `notification`
7. `idempotency_key`
8. `app_audit_event`

Every table uses the same bounded event envelope: `event_id`, HMAC-derived
`actor_hash`, `idempotency_key`, `correlation_id`, `payload_json`,
`payload_sha256`, `created_at`, and `expires_at`. Payloads are valid JSON, finite,
and no larger than 16 KiB. The authenticated subject is never stored directly.

An event ID is deterministically derived from table, actor hash, and idempotency
key. Writes use a parameterized insert-only `MERGE`. Repeating the same key and
payload returns the existing event; reusing the key with a different payload is
rejected. Every conversation/state read must include the authenticated actor hash.

## Identity and privileges

The existing non-admin `retail-hp-app-runtime` service principal is a member of
`retail_hp_app_runtime`. That group receives only `USE_SCHEMA` plus `SELECT` and
`MODIFY` on the eight operational tables. It receives no `CREATE TABLE`, owner,
admin, or `MODIFY` privilege on the monitoring export. Owners remain
`retail_hp_admins`.

The future Databricks App must use its injected application identity or explicit
on-behalf-of-user identity. It must never contain a personal access token. A
separate, uncommitted secret of at least 32 bytes is required to pseudonymize
authenticated subjects. The SQL client must not silently start the warehouse.

## Retention

| Data | Logical retention |
|---|---:|
| Conversation and message | 1 day |
| Scenario session | 1 day |
| Notification and idempotency key | 7 days |
| Recommendation impression and audit event | 30 days |
| Feedback | 90 days |

`expires_at` is enforced by the read contract. Physical deletion is intentionally
not automated in this phase because the fallback tables are append-only and no
continuous/scheduled maintenance compute is allowed. Production must implement a
reviewed deletion/compaction policy after real-data privacy and retention review.

## Feedback export

`retail-hp-poc-feedback-export` is a manual-only, zero-retry job with a three-minute
timeout and no schedule. It performs an insert-only idempotent export from
`agent.feedback` to `monitoring.feedback_event_export`. It exports only bounded
analytical fields and never conversation/message text. A second pass must add zero
rows.

## Failure behavior

- Store unavailable: recommendations remain read-only; feedback/state writes fail
  with a stable safe error.
- Warehouse stopped: no silent start from the app. Demo start is explicit.
- Duplicate request: return the original event.
- Conflicting idempotency payload: reject the request.
- Cross-actor query: return no records.
- Export unavailable: source feedback remains in the governed append-only table;
  no continuous retry or background compute is started.
