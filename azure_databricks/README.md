# Azure Databricks implementation

This directory is the Azure-native implementation area for the retail
hyper-personalization POC. Legacy local, Streamlit, SQL Server, and Free Edition
application code is intentionally excluded from this repository.

Canonical Git repository:
[retail_hyper_personalization_databricks](https://github.com/Kkb113/retail_hyper_personalization_databricks)

## Phase 0 status

Phase 0 is read-only in Azure and Databricks. It creates local, sanitized
evidence only:

- Exact scope policy with non-reversible resource fingerprints.
- Complete data and model transfer manifest.
- Local model and repository baseline.
- Live Azure and Databricks inventory.
- Proposed resource and cost-control plan.
- Codebase review, research record, decisions, and acceptance gate.

Install the small Phase 0 test toolchain and run the local generator:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe azure_databricks\scripts\build_phase0.py
~~~

Collect a fresh live inventory:

~~~powershell
.\.venv\Scripts\python.exe azure_databricks\scripts\build_phase0.py --collect-live
~~~

Run the Phase 0 gate:

~~~powershell
.\.venv\Scripts\python.exe -m ruff check azure_databricks\scripts tests\azure_databricks
.\.venv\Scripts\python.exe -m pytest tests\azure_databricks -q
.\.venv\Scripts\python.exe azure_databricks\scripts\scan_repository_secrets.py
~~~

The live command uses Azure CLI authentication and the Databricks SDK. It does
not create, update, delete, start, or stop a cloud resource.

Phase 0 evidence is historical. Current owner decisions and deployment gates
are in `config/poc.json`; earlier pending-budget wording in historical evidence
is superseded, not evidence of a deployed Azure budget.

## Phase 1 foundation

For the current metadata-only Phase 2 bootstrap, live evidence and unresolved
cost/identity gates, see [Phase 2 status and runbook](evidence/phase_02/README.md).
The Phase 1 restrictions below remain the historical resource-free bundle contract.

The runtime lives only in `src/retail_hp_azure`. No legacy code is imported.
The wheel contains configuration/preflight helpers and a local app skeleton;
it does not contain migration data, frozen models or credentials.

1. Install Python 3.12 in a clean local environment.
2. Install `../requirements-dev.lock` with `pip --require-hashes`.
3. From the repository root, install the project using `pip install --no-deps -e .`.
4. Run `python azure_databricks/scripts/generate_phase1.py` from that root.
5. Run `python -m retail_hp_azure.cli plan`. This has no network calls.
6. Run lint, mypy and `pytest -c pyproject.toml tests/azure_databricks`.

Use the root pyproject explicitly: an old ignored local `pytest.ini` must not
select legacy tests or change the Azure gate.

### Live validation (read-only)

Download the official Databricks CLI version and verify its ZIP SHA-256 against
`config/toolchain.json`. Authenticate through `az login` privately. Do not commit
tokens, profiles, raw Azure identifiers or recipient addresses.

```sh
python -m retail_hp_azure.cli verify-live --databricks-cli /absolute/path/to/databricks --record
```

This checks the Azure subscription and tenant fingerprints **before** reading
resources, then pins all Azure reads to that subscription and resource group.
It checks the ARM workspace identity, region and host, reads the approved catalog,
runs strict bundle validation, verifies resolved variables, and requires an empty
bundle plan. Successful sanitized evidence is saved under `evidence/phase_01`.
Raw CLI output is not persisted. Non-zero exit means the gate failed.

The CLI deliberately exposes **no deploy, start, stop or delete command**. Its
preflight rejects profile, host, bundle-variable, engine and bundle-root overrides;
it runs the official CLI with Azure CLI auth and an empty profile source.
The sole target is `poc`. Only the fixed harmless `bundle_files/README.md` marker
is eligible for future file sync; no sync occurs during validate/plan. The marker
content and directory membership are guarded. Resource declarations, includes,
script hooks, build hooks and additional targets are prohibited. Variables are
reserved names, not a promise that flipping a flag enables deployment.

These checks prevent accidental misuse of this workflow; they are not an Azure
RBAC boundary against someone deliberately running another CLI or editing code.
Least-privilege identities and Azure governance must be implemented in Phase 2.
Missing tags on an existing shared workspace are reported, not silently changed;
every proposed new resource must carry all required tags, but all writes remain
blocked in Phase 1 regardless of whether its SKU is free.

### App skeleton

```sh
python -m uvicorn retail_hp_azure.app:create_app --factory --host 127.0.0.1 --port 8000
```

This starts a **local** process only. `/health/live` returns 200;
`/health/ready` returns 503 because the model/agent are not deployed;
`/version` identifies POC-only status. There is no recommendation API or deployed
Databricks App yet. Authentication and real user journeys belong to later phases.

### Cost decisions

- Monthly target: INR 12,000; internal stop target: INR 9,000; reserve: INR 3,000.
- Occasional demonstrations: at most four per month; no continuous showcase.
- Two notification recipients are known privately; no addresses in this repo.
- No cloud mutations or new billable resources in Phase 1.
- All optional features are disabled; free-to-paid fallback is forbidden.
- Budget alerts, idle watchdog and cost stop controller are **not deployed**.
- Twenty minutes was an example, not a hard limit. Select service-specific idle
  settings to balance measured cost, cold starts and demo quality. A 30-minute
  native serving idle window is eligible for evaluation, not permission to deploy.
  Jobs need runtime limits, and apps need separate session/idle controls. All
  compute must be stopped outside explicitly bounded development/demo sessions.
- Before enabling compute, estimate the full session cost against the remaining
  operating allowance and enforce runtime/token limits. Reserve headroom for
  storage, billing delay, currency changes and applicable taxes. Exact rates and
  usable session hours must be verified before paid deployment.
- Azure budgets do not guarantee a hard INR 12,000 cap; storage and delayed usage
  can still be charged while compute is stopped. See `cost_ledger.md`.

CI has no Azure credentials. It runs offline policy/contracts/app checks and four
clean Linux runtime installations. Live validation is a separate explicit local
gate; a green offline build alone is not permission to deploy.

### Rollback

Phase 1 has no cloud resources to destroy or data transfers to undo. Stop any
manually started local health server and use a reviewed Git revert if the
foundation changes need to be rolled back. Do not run `bundle destroy`, delete
the existing workspace/catalog or touch the frozen migration payload.
