# Business-facing response revision

The conversational writer now targets retail business users with a customer overview,
plain-language recommendations and a next best action. It suppresses model scores,
internal routing terminology and repeated caveats in the narrative while retaining
grounding, recommendation order, customer authorization and essential data limitations.
The offline fallback uses an allowlist of business facts instead of dumping tool rows.
Existing product-card technical detail panels are unchanged.

Validation: 304 Python tests passed, 2 skipped; targeted lint and type checks passed.
Live deployment succeeded on 2026-09-09 with release
`060af8794ef109786e1c3f9627cda555c16e239c2e5fbed830fd1541f8151e77`.
Live checks passed for five hydrated recommendations, follow-up, general retail,
customer authorization and absence of internal terminology in the recommendation
narrative. The temporary validation credential was revoked. Human inspection confirmed
business-oriented headings and actions; wording can still repeat evidence limitations
and the optional availability note can duplicate a writer-generated heading.

The owner approved an additional INR 220 estimated window, taking cumulative chat
reservations to INR 660 without resetting earlier reservations. This is not actual
invoice spend or a guaranteed invoice cap. The real-time endpoint remained stopped.
The independent shutdown deadline is 2026-09-09 07:40:42 UTC (13:10:42 IST).
The App was left running for owner testing; final shutdown is not yet observed.
