## Unified retail and pricing business agent

- Reuse the existing authenticated Databricks App and native LLM connection.
- Add default-off, server-authorized pricing tools, deterministic business summaries,
  scenario follow-ups and combined recommendation/pricing responses.
- Preserve retail behavior when pricing is disabled or unavailable; no invented prices.
- Add five-session concurrency, isolation, input-grounding and 24-case business tests.
- Preserve independent shutdown and cumulative cost reservations; no new paid service.

Local regression: 381 passed, four skipped. Companion pricing package passed six
minimal-Linux tests; full baseline policy replay passed 12,329 decisions with zero
mismatches across 49 fields. Pricing implementation is coordinated with
Kkb113/Dynamic-Pricing#14. Final live status is tracked in pricing-phase4-status.md.
Human browser acceptance is explicitly deferred by the owner, not claimed as passed.
