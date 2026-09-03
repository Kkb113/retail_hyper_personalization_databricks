# Contributing

All changes must support the Azure Databricks POC and remain within the scope
declared in `azure_databricks/config/phase0_scope.json`.

Use a short-lived branch, keep commits phase-focused, and open a pull request
against `main`. A change that creates or changes a cloud resource must document
the SKU, region, lifecycle, rollback path, and expected monthly cost before it is
applied. Secrets and raw Azure identifiers must never be committed.

Run the offline gate before opening a pull request:

```powershell
.\.venv\Scripts\python.exe azure_databricks\scripts\build_phase0.py
.\.venv\Scripts\python.exe -m ruff check azure_databricks\scripts tests\azure_databricks
.\.venv\Scripts\python.exe -m pytest tests\azure_databricks -q
.\.venv\Scripts\python.exe azure_databricks\scripts\scan_repository_secrets.py
```

Do not deploy the old Databricks Free Edition bundle. The first deployable
Azure bundle will be introduced and validated in Phase 1.
