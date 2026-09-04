# Contributing

All changes must support the Azure Databricks POC and remain within the scope
declared in `azure_databricks/config/phase0_scope.json` and the current strict
`azure_databricks/config/poc.json` policy.

Use a short-lived branch, keep commits phase-focused, and open a pull request
against `main`. A change that creates or changes a cloud resource must document
the SKU, region, lifecycle, rollback path, and expected monthly cost before it is
applied. Secrets and raw Azure identifiers must never be committed.

Run the offline gate before opening a pull request:

```powershell
.\.venv\Scripts\python.exe azure_databricks\scripts\generate_phase1.py
.\.venv\Scripts\python.exe -m ruff check azure_databricks\src azure_databricks\scripts tests\azure_databricks
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests\azure_databricks -q
.\.venv\Scripts\python.exe azure_databricks\scripts\scan_repository_secrets.py
```

Install the hash-locked development requirements and Azure package first, as
described in the README. Do not regenerate historical Phase 0 evidence for
unrelated changes. Any bundle/config change must refresh the sanitized live
validation evidence through the guarded `verify-live` command before approval.

Do not deploy the old Databricks Free Edition bundle. The Azure Phase 1 bundle
is resource-free and its helper exposes no deploy command. CI has no cloud
credentials. Required check `phase0-safety` now aggregates all foundation tests
and clean-runtime checks; a skipped/failed runtime check cannot bypass it.
