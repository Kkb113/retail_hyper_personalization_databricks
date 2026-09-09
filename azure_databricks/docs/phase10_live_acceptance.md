# Phase 10 live acceptance — 2026-09-09

**POC acceptance passed; App, warehouse and endpoint stopped afterward.**
This is a bounded synthetic-data demonstration, not an always-on production service.

## Accepted behavior

- One chat screen, inline model-backed product cards and collapsed sources.
- Operator browser and separate non-admin OAuth authentication both worked.
- Actual App workload identity called Luna; live chat returned three recommendations.
- Semantic discovery, real-time scenario execution, feedback save and idempotent
  replay passed. Cross-customer access returned HTTP 403.
- Browser Enter-to-send, Shift+Enter multiline input, source expansion and New chat
  reset passed. Desktop layout was visually inspected.
- The independent managed-identity runbook stopped running compute; final read-back
  found the App, warehouse and endpoint STOPPED, with no active jobs or clusters.

Evidence is under `evidence/phase_10`: `live_api_validation.json`,
`browser_validation.json`, `release_preflight.json`, `deployment_validation.json`
and `cloud_reconciliation.json`.

## Release and safety

Existing Medium App, XXSmall warehouse, CPU Small endpoint, Luna deployment and Basic
Automation account are reused. No new Azure resource was created during this release.
Private settings use secret resource bindings; tiny one-use launch claims use a UC
volume. The App has no raw-table SELECT grants. Customer SQL uses verified caller
authorization, with server-owned customer allowlists, CSRF checks and bounded input.

`pyproject.toml` + `uv.lock` select Python 3.12 and constrain resolved dependencies to
the existing App lock. Databricks' default pip environment uses Python 3.11. The
33.5 MB semantic snapshot is exported in 8 MB parts to respect the 10 MB source-file
limit; startup verifies each part and the reassembled SHA-256 before loading it.
Release directories are content-addressed, create-only and checked for mismatches.

Each private launch ticket has an absolute 12-minute deadline. Create-only claims
prevent a process restart from resetting the one-worker/100-operation allowance.
Expiry rejects requests but does not itself stop compute. The independent fixed-target
runbook must be armed before paid starts; stop retries are bounded and check every
target even if another fails. Final STOPPED verification is mandatory.

The first window timed out during cold App allocation. Dependencies stopped, and the
independent runbook stopped the late-starting App. The second window initially hit
the source-file limit; the split-file repair deployed successfully within the SAME
deadline. The warehouse's idle timeout stopped it while packaging; it was explicitly
resumed for acceptance. No third validation window was started.

The owner approved **INR 440 cumulative validation allowance** after the initial INR
250 allowance. Two INR 220 reservations remain recorded. These are conservative
planning reservations, NOT measured invoices and NOT guaranteed billing caps. No
further launch is permitted until cost/authority is reviewed. Never reset the ledger
to bypass this check. Storage and shared Automation usage can still cost money;
managed-resource-group invoice coverage remains unverified. The INR 12,000 monthly
budget sends alerts; it is not a hard spending cap.

## Testing the App

App: <https://retail-hp-poc-app-7405618180989330.10.azure.databricksapps.com>

It is deliberately STOPPED between demonstrations. Do not manually start it in the
portal: an expired/used launch ticket refuses admission while compute could bill.
Ask the operator to open a bounded demonstration after reviewing its allowance.
No recurring showcase or background start schedule is enabled.

Operator workflow from this repository, using the existing Azure CLI authentication:

1. Run `phase10_audit.py`; verify scope, stopped compute and no active jobs.
2. Review the cumulative cost ledger and prior controller completion. Obtain new
   authority if the reservation gate would be exceeded; retain old reservations.
3. Run `phase10_preflight.py` for the exact locally prepared release. Private release
   metadata and cost ledger live under ignored `build/`, never public git.
4. Use `phase10_live.py start` only after those gates. It verifies the budget, creates
   a ticket, arms shutdown and starts the retained successful release without
   redeploying it twice. A different installed release fails closed.
5. Wait for all dependencies to be ready, then open the URL. Try “Recommend 3 products
   for me”, a comparison, or semantic product discovery.
6. Run `phase10_live.py stop` immediately afterward; verify the controller result and
   run `phase10_audit.py`. The independent deadline remains a fallback.

Fresh packaging uses `phase10_package.py --apply` while stopped. Its `--active-window`
repair mode requires an armed controller with time left. `phase10_live.py redeploy`
only repairs a FAILED build in that same lease; neither repair command creates a
new paid window. Retain immutable old releases for review. Upgrade/rollback to a
different release requires a separately reviewed launch.

## Explicit POC limitations

- Mobile/responsive and formal accessibility audits were not performed.
- Source currency is unspecified; inventory is global and data synthetic/dated.
- An apostrophe is HTML-escaped in narrative text; its product card renders correctly.
- Luna plans governed tools; answers are grounded summaries, not unrestricted sales chat.
- Feedback is tested through the API, not a form in the simplified chat UI.
- Replenishment/cart/price-change triggers are not live features.
- Relaunch with a refreshed ticket is locally regression-tested, not a third paid run.
- No claim of production concurrency, multi-instance state, comprehensive chaos or
  security testing, or a guaranteed invoice ceiling.

## Local verification

```powershell
.venv/Scripts/python.exe -m pytest tests/azure_databricks -q
.venv/Scripts/python.exe -m ruff check azure_databricks/src azure_databricks/scripts tests/azure_databricks
.venv/Scripts/python.exe -m mypy
npm test --prefix azure_databricks/app/frontend
npm run build --prefix azure_databricks/app/frontend
.venv/Scripts/python.exe azure_databricks/scripts/scan_repository_secrets.py
```
