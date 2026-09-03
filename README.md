# Retail Hyper-Personalization on Azure Databricks

[![Azure Databricks Phase 0 CI](https://github.com/Kkb113/retail_hyper_personalization_databricks/actions/workflows/azure-phase0-ci.yml/badge.svg)](https://github.com/Kkb113/retail_hyper_personalization_databricks/actions/workflows/azure-phase0-ci.yml)

This repository is the Azure-only source of truth for the retail
hyper-personalization POC. It will contain the governed data pipeline, migrated
recommendation model, agent, evaluation, serving, observability, and deployment
assets built for Azure Databricks.

The original local, SQL Server, Streamlit, and Databricks Free Edition
implementations are intentionally excluded. They remain available in the
separate source repository and are references only—not deployable components of
this Azure solution.

## Current status

Phase 0 is complete and tested. It established a read-only scope, safety and
cost baseline for:

- Azure resource group `Databricks`
- Azure Databricks workspace `intellify-databricks-demo`
- Unity Catalog catalog `intellify_databricks_demo`
- synthetic POC data only
- zero new Azure resources and zero incremental Phase 0 cloud cost

Paid resource creation remains blocked until the owner supplies a monthly
budget threshold and notification recipient. Model release remains POC-only and
blocked pending a future holdout and owner approvals.

There is deliberately no deployable `databricks.yml` yet. Phase 1 will create
the first Azure-specific Databricks Asset Bundle; retaining the legacy bundle
would make an accidental deployment to the wrong catalog too easy.

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
.\.venv\Scripts\python.exe azure_databricks\scripts\build_phase0.py
.\.venv\Scripts\python.exe -m pytest tests\azure_databricks -q
.\.venv\Scripts\python.exe azure_databricks\scripts\scan_repository_secrets.py
```

For a refreshed read-only Azure inventory, authenticate with Azure CLI and run:

```powershell
.\.venv\Scripts\python.exe azure_databricks\scripts\build_phase0.py --collect-live
```

See [azure_databricks_implementation.md](azure_databricks_implementation.md)
for the complete roadmap and [azure_databricks/README.md](azure_databricks/README.md)
for Phase 0 details.
