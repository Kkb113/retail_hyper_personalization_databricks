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

Paid resource creation remains blocked until a budget threshold and notification
recipient are approved outside source control.
