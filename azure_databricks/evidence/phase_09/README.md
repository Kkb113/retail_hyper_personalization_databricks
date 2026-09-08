# Phase 9 evidence

Luna agent acceptance and Databricks artifact/MLflow publication have passed. Repository CI
and review are tracked separately; the deployed authenticated App belongs to Phase 10.

## Current Luna acceptance

[Luna acceptance](luna_acceptance.json): authentication fixed and agent configuration
validated. The fresh 135-case benchmark passed its unchanged gates: 134/135 routing/useful
outcomes (99.26%), 100% argument/grounding/safety checks, and all eight follow-up turns.
The guest query `new_customer-13` asked for clarification instead of searching; this quality
miss remains visible. [Live integration](live_762f07c0.json) passed real batch facts/rank,
comparison, semantic search, unauthorized-customer denial and stopped-scenario behavior.
The warehouse is STOPPED and the temporary Databricks tool credential was revoked.
254 local tests and the isolated wheel adapter checks passed. Total recorded Phase 9 spend
and conservative reservations are approximately INR 25 pre-tax, not an invoice measurement.
The Azure operator identity was used for LLM validation; deployed App workload identity
binding and authentication remain Phase 10 requirements. No App was deployed here.
[Publication readback](publication.json) verifies the immutable wheel/prompt/dataset/report
and one MLflow replay graph containing all 143 redacted evaluation records.

**Requested model change:** the owner has requested GPT-5.6 Luna instead of GPT-OSS-20B.
The existing GPT-OSS results below are retained as the baseline, not Luna acceptance evidence.
The owner approved an Azure OpenAI account inside `Databricks`. Account
`retail-hp-poc-openai-4073b6c9` and deployment `gpt-5.6-luna` version `2026-07-09` are provisioned
in West US: S0 account, token-billed GlobalStandard deployment, capacity 10, NoAutoUpgrade,
API keys disabled. No provisioned/hourly model capacity was added.
The initial bounded synthetic Entra-authenticated probes returned HTTP 401 / PermissionDenied.
Changing the token audience alone did not resolve it; the deployment-scoped inference API
(`2024-10-21`) subsequently succeeded with `https://ai.azure.com` audience. The signed-in operator
was granted Cognitive Services OpenAI User on this account only. Read-only diagnostics verified
the token tenant and caller role. The Databricks-native workload identity needs a separately verified Azure
authentication binding before app deployment. No fallback to another model is authorized.
`AzureLunaPlanner` and the `create_luna_agent` factory are the configured Luna path; full
acceptance results are recorded separately from the historical baseline below.
The shared ledger retains all prior spending and unknown-outcome reservations.
See [initial Luna deployment evidence](luna_deployment.json). The original GPT-OSS MLflow
publication export was interrupted; its partial diagnostic records are retained as a killed
run. The separate Luna publication completed and passed readback verification.

## Evaluation interpretation

The 135-case dataset is defined in `phase9_evaluation.py`; it uses synthetic fixture tools.
The initial model screens were diagnostic, not release gates. GPT-OSS-20B used prompt v1 in
the first screen; Llama 3.1 8B used corrected prompt v2. Do not claim a controlled comparative
quality experiment from those two screens. Published token rates and the full GPT evaluation
support selecting the cheaper qualifying model; no fallback or third model was needed.

The original full run was interrupted by a local OneDrive write error after 50 cases. The next
full report resumes those 50 results and evaluates the remaining 85 plus eight multi-turn turns.
All original reports are retained. Reported provider usage is estimated with the documented
DBU conversion, not an Azure invoice measurement.

`release_evaluation.json` replays the recorded live plans through the release agent boundary:
it does not make new model calls and preserves model-call failures. This tests the correction
that refusal/clarification routes do not parse unused tool arguments. It reports:

- Correct routing: 129/135 (95.56%). Six misses remain visible.
- Emitted tool-argument checks: passed for all cases in this benchmark; no-argument refusal
  routes do not have a tool-argument schema. This is not a claim that every provider output
  was valid: two original provider outputs failed and remain unavailable.
- Expected safe/useful response status: 130/135 (96.30%).
- Product/numeric grounding, privacy/injection checks, no automatic writes, trace coverage: 100%.
- Eight live multi-turn turns passed, including preference and customer-context correction.

Scorer correction: an unavailable response to an unrelated poem/sports question is a **quality
failure**, not an unauthorized disclosure. Those quality misses remain counted. Privacy and
injection cases still require refusal and zero backend access; dependency failures require no
fabricated cards. No hard security gate was relaxed. The 95% useful-status quality threshold
is distinct from the 100% security/grounding/trace thresholds.

Fixture grounding does not establish business uplift, personalization accuracy or live model
quality. `live_*.json` is the separate real Databricks check. `publication.json` will record
immutable workspace files, MLflow metadata and final compute state once verified.
