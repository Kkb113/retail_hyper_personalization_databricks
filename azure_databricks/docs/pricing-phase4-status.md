# Phase 4 — Unified business agent and App

Status (2026-09-16): implemented, deployed, automated HTTP acceptance passed.
Human business-user browser acceptance remains pending by the owner's explicit
choice. Not every original Phase 4 exit gate is complete.

## Delivered

The existing authenticated retail App supports pricing, exact-price simulations,
explanations, scenario discovery, follow-ups and combined retail/pricing requests.
Pricing remains default-off and server-authorized. Session isolation, bounded
scoring, immutable manifests and business-only output projection are enforced.

Registered model version 1 and trained weights are unchanged. The derived App
copy records portable MLflow metadata and LAZY_DATABASE_DRIVER_IMPORT_V1 with
original/adapted hashes. It is not byte-identical to the registered source.
Real database access still requires the driver; pure inference does not.

A live concurrency failure exposed a fallback bug: identifier digits were treated
as candidate prices. The correction preserves exact-ID recommendations when the
LLM queue is busy, but ambiguous numeric simulations still require clarification.

## Validation

- Retail: 382 passed, four skipped; Ruff passed; mypy passed for 42 source files.
  Includes the 24-case offline business matrix.
- Actual pricing payload: six minimal-Linux tests passed without native ODBC or
  network access. Full baseline business replay: 12,329 decisions, 49 fields,
  zero mismatches. Baseline replay and adapted-payload tests are distinct checks.
- Live non-admin HTTP: eight business checks passed, including expected price
  61.28, explanations, simulation, unauthorized-customer rejection, five retail
  products, combined requests, discovery and scenario selection.
- Five independent concurrent sessions: 5/5 successful with exact price parity.
  Durations: 2.328, 8.546, 11.000, 11.000, 15.843 seconds. Nearest-rank p95 for
  this five-observation sample is its maximum, not a production benchmark.
- Local uncached warm deterministic scoring p95: 0.829 seconds over ten calls.
  First live retail request with stopped warehouse: 43.656 seconds; combined:
  24.453 seconds. Do not claim every cold request meets the 30-second target.
- Temporary validation credentials were revoked; no private inference records
  or credentials are included in public evidence.
- Final immutable App release:
  fb0b96405de27bc4c8ceb821f4c839d71ce5f1d3cdab78c18a255e88bc8dd609.
- Prerequisite retail PR #22 was reviewed, passed CI and merged.
  Coordinated PRs are Dynamic-Pricing #14 and retail #23; their current check
  and merge state is authoritative.

## Cost and remaining acceptance

Two separately approved INR 250 estimated windows were reserved (INR 500 cumulative).
This is not measured invoice cost or a guaranteed billing cap. The corrective
deployment retained the second window's original shutdown deadline. No new paid
service, larger compute tier or deadline extension was introduced. Final stopped
states are recorded in the accompanying sanitized live evidence.

The normal signed-in browser journey remains for the owner to accept. API tests
do not replace it. Pricing is advisory on historical synthetic data; currency is
unverified, current inventory is not applied, no price writeback occurs, and
modeled profit is not realized uplift. Phase 5 still owns the wider operational
rehearsal and final POC acceptance.
