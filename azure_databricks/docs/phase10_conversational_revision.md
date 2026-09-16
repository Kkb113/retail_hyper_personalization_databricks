# Phase 10 conversational revision — 2026-09-09

This revision addresses the owner's failed rich-personalization demonstration. The
previous narrow API acceptance was not sufficient evidence for that conversation.
**Current status: conversational API live acceptance passed on 2026-09-09.**
The first upgrade attempt was blocked by Azure's still-pending automatic old-release
restart. The corrected launcher waited for that transition before publishing the
live ticket, and the second deployment succeeded. No ticket was replayed and no
spending reservation was erased. The owner explicitly requested the App remain
available for personal testing until the armed deadline, 2026-09-09 07:15:46 UTC
(12:45:46 PM IST). The real-time endpoint remains stopped. This does not claim a
subsequent shutdown has already been observed or browser acceptance was repeated.
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

### Shared test window adjustment

The owner subsequently approved one further INR 220 test and asked to test personally
before shutdown. The chat ledger's authorized cumulative reservation is INR 440;
the original reservation is not erased. The second window is 20 minutes including
startup, with five minutes of shutdown-retry headroom. The App's shared LLM gate is
reduced to INR 25, and the real-time endpoint is not started. Batch personalization,
customer/product tools and general chat remain in scope; real-time scenarios are not
part of this economical window. SQL retains one-minute idle auto-stop. No four-hour
runtime or further launch is authorized by this change. The operator must hand off
the exact deadline and must not stop early immediately after automated checks.

## Validation

Local tests cover prompt customer resolution, quoted multipart recommendations,
five-card hydration and rank preservation, follow-up evidence, customer switching,
revocation, general retail with no SQL, provider fallback, invented product rejection,
API session continuity and lease-bounded warehouse wake-up. Existing phase tests remain.
The live test uses the existing non-admin identity and prompt-only requests; temporary
OAuth credentials are revoked in finally. It requires real LLM narratives, not fallback,
for personalization, follow-up and general retail, plus unauthorized-ID denial.

Live results: five hydrated, model-ranked recommendations with five narrative sections;
follow-up resolved the first product without repeating the customer ID and returned
three sections; general retail advice returned six sections without new live-data
queries. All three used Luna (not the verified-data fallback), and an unauthorized
customer ID in the prompt was denied. Responses were inspected against their returned
evidence: limited behavioral history, unspecified currency and snapshot inventory
were disclosed rather than fabricated. A new cross-category discovery was unavailable
and was explicitly identified as such. The temporary OAuth test secret was revoked.

## Research used

- [Databricks App authentication](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth):
  preserve user authorization for data operations and App identity for workload access.
- [Azure structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs):
  typed response contracts and application-side validation; this implementation uses
  forced function calls and validates responses, not an assertion of provider strict mode.
- [SQL warehouses](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/):
  allow cost-conscious idle termination with bounded on-demand availability.
