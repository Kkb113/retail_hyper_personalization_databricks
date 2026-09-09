# Phase 10 conversational revision — 2026-09-09

This revision addresses the owner's failed rich-personalization demonstration. The
previous narrow API acceptance was not sufficient evidence for that conversation.
**Current status: implemented and locally tested, NOT live accepted.** The first
upgrade attempt was blocked by Azure's still-pending automatic old-release restart.
That restart consumed the refreshed one-use ticket, so validation was stopped rather
than replaying it or resetting a spending ledger. The launcher now waits for a newly
observed terminal restart before publishing the live ticket. This race fix has an
offline regression test; its live verification needs another authorized window.
Live acceptance for this revision is recorded separately in
`../evidence/phase_10/chat_validation.json`; absence or FAIL is not acceptance.

## Changes

- Customer IDs are extracted from chat text; there is no customer selector. A
  server-side session retains the customer and verified evidence for follow-ups.
  Switching customers clears prior context; revoked grants clear retained evidence.
- A retail-advice route supports general retail questions without customer selection
  or SQL. The existing exact Luna deployment handles routing and response writing.
- Personalized responses combine model-ranked recommendations, product facts and
  customer context, with optional promotions and a separate exploratory candidate.
  The bounded limit is eight tool calls, ten cards and two LLM calls per answer;
  there are no automatic billable retries. The existing shared LLM ledger remains.
- Structured rich answers contain a summary, topical sections, explanations and
  next steps. React renders text, not executable model-generated HTML.
- Partial tool failures preserve retrieved facts. If writing is unavailable, a
  verified-data fallback is explicit; no synthetic success or invented evidence.
- A stopped SQL warehouse may wake only within the active demo lease, with a
  bounded wait and shutdown headroom. One-minute auto-stop and the independent
  absolute-deadline shutdown controller are retained. Endpoint wake-up is not added.
- Internal logs retain safe failure type/action metadata, not raw customer prompts
  or provider secrets. The screenshot's exact original exception cannot be recovered
  from the old catch-all message alone.

## Boundaries that remain necessary

Authentication, authorized synthetic-customer scope, tool schemas, explicit write
consent, spending reservations and demo expiry remain enforced. A POC does not justify
exposing other customers, arbitrary SQL or secrets. General retail knowledge is not
presented as measured business data. Individual orders, explicit brand/price affinity,
regional stock and source currency are not fabricated when unavailable.

No new Azure resource, paid tier or always-on service is required. The owner approved
one additional estimated INR 220 bounded validation window. Its separate persistent
ledger is `build/phase10-chat-validation.local.json`; previous INR 440 validation and
INR 220 owner-demo reservations are preserved. These are estimates, not invoice caps.
The upgrade starts the old snapshot with an expired lease, then deploys the new
snapshot with the live one-use ticket, after the shutdown controller is armed.

## Validation

Local tests cover prompt customer resolution, quoted multipart recommendations,
five-card hydration and rank preservation, follow-up evidence, customer switching,
revocation, general retail with no SQL, provider fallback, invented product rejection,
API session continuity and lease-bounded warehouse wake-up. Existing phase tests remain.
The live test uses the existing non-admin identity and prompt-only requests; temporary
OAuth credentials are revoked in finally. It requires real LLM narratives, not fallback,
for personalization, follow-up and general retail, plus unauthorized-ID denial.

## Research used

- [Databricks App authentication](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth):
  preserve user authorization for data operations and App identity for workload access.
- [Azure structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs):
  typed response contracts and application-side validation; this implementation uses
  forced function calls and validates responses, not an assertion of provider strict mode.
- [SQL warehouses](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/):
  allow cost-conscious idle termination with bounded on-demand availability.
