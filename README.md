# Retail Hyper-Personalization on Azure Databricks

[![Azure Databricks Foundation CI](https://github.com/Kkb113/retail_hyper_personalization_databricks/actions/workflows/azure-phase0-ci.yml/badge.svg)](https://github.com/Kkb113/retail_hyper_personalization_databricks/actions/workflows/azure-phase0-ci.yml)

This repository is the Azure-only source of truth for the retail
hyper-personalization POC. It will contain the governed data pipeline, migrated
recommendation model, agent, evaluation, serving, observability, and deployment
assets built for Azure Databricks.

The original local, SQL Server, Streamlit, and Databricks Free Edition
implementations are intentionally excluded. They remain available in the
separate source repository and are references only—not deployable components of
this Azure solution.

## Current status

Phase 0 established the scope and migration baseline. Phase 1 adds an installable
Azure-only package, resource-free bundle, strict preflight, runtime locks and app
health skeleton. The boundary remains:

- Azure resource group `Databricks`
- Azure Databricks workspace `intellify-databricks-demo`
- Unity Catalog catalog `intellify_databricks_demo`
- synthetic POC data only
- zero new Azure resources and zero incremental Phase 0 cloud cost

Phase 2's metadata governance is now implemented: four project groups, eight
group-owned schemas, two empty managed volumes, least-privilege grants and tags.
Background predictive optimization is disabled on the project schemas. Live
verification passed 85 checks and repeat apply made zero changes. **Phase 2 is
not complete:** Azure cost queries are throttled and Databricks billing access
is denied; paid deployment and Phase 3 remain blocked. See the
[Phase 2 status and runbook](azure_databricks/evidence/phase_02/README.md).

The agreed monthly target is INR 12,000, with an internal stop target of INR
9,000 and INR 3,000 reserve. Two notification recipients were provided privately.
**Budget alerts and the shutdown controller are not deployed.** Azure budgets
are not hard billing caps. Paid deployment remains blocked until those controls
and current pricing are verified. Model release remains POC-only, pending a
future holdout and owner approvals.

The only bundle is `azure_databricks/databricks.yml`, with target `poc`, **zero
resources**, no build hooks and marker-only sync eligibility. No sync is run in
Phase 1. Do not deploy a legacy
root bundle. No app, endpoint, warehouse, job or Azure service is created in
Phase 1. The existing Premium workspace is reused without upgrades or add-ons.

## Repository layout

```text
azure_databricks/                    Phase policies, contracts, evidence, scripts
migration_assets/data_cache/        Approved synthetic data transfer payload
migration_assets/artifacts/         Approved frozen model transfer payload
tests/azure_databricks/              Offline safety and integrity tests
azure_databricks_implementation.md   Phase-wise delivery plan
```

The migration payload contains 45 unique files (20 data files and 26 model
files, with one cross-role file). Every file is pinned by SHA-256; every Parquet
input is also checked against its declared row count and required schema.

## Local validation

Python 3.12 is the reference CI runtime.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe azure_databricks\scripts\generate_phase1.py
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests\azure_databricks -q
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m ruff check azure_databricks/src azure_databricks/scripts tests/azure_databricks
.\.venv\Scripts\python.exe azure_databricks\scripts\scan_repository_secrets.py
```

Use a new environment rather than overwriting an existing legacy environment.
For read-only Azure verification, authenticate with Azure CLI and supply the
checksum-verified CLI version from `azure_databricks/config/toolchain.json`:

```powershell
.\.venv\Scripts\python.exe -m retail_hp_azure.cli verify-live --databricks-cli <path-to-databricks-cli>
```

See [azure_databricks_implementation.md](azure_databricks_implementation.md)
for the complete roadmap and [azure_databricks/README.md](azure_databricks/README.md)
for the foundation runbook and evidence.
