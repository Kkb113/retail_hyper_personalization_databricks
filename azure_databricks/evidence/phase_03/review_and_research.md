# Phase 3 review and research

Reviewed and implemented 2026-09-07. This phase moves only the approved synthetic
POC inputs and frozen recommendation assets into the existing governed volumes.
It does not build Delta tables, register an MLflow model, deploy serving, invoke a
foundation model, create an Azure resource, or approve production use.

## Codebase and transfer findings

- The Phase 0 dependency graph is the correct migration source: 45 unique files,
  46 destination copies, 20 Parquet data assets, 26 model assets, and one
  recommendation snapshot shared across the data and model roles.
- The 45 files total 12,733,215 bytes and declare 275,630 rows. The broader local
  artifact cache is unnecessary for runtime and was not transferred.
- Every source is a regular, non-symlink file with an approved JSON, joblib, NPZ,
  or Parquet suffix. The validator rejects executable extensions and direct PII
  column names before any upload.
- The manifest fixes the destination workspace, catalog, versioned volume roots,
  byte sizes, SHA-256 values, row counts, schemas and component roles. A different
  workspace, catalog, root, path traversal, duplicate path or unknown role fails
  closed.
- Joblib/pickle portability was the material migration risk. The approved
  `ranker_preprocessor.joblib` references exactly one function from the legacy
  repository. A 430-byte compatibility module reproduces only that function; it
  is separately hashed, uploaded under the versioned model root, executed in the
  validation environment, and included in both seals. Legacy application code was
  not copied into the Azure repository.

## Platform research and decisions

1. [Unity Catalog volumes](https://learn.microsoft.com/en-us/azure/databricks/volumes/volume-files)
   are the appropriate governed location for non-tabular data and model artifacts.
   Paths use `/Volumes/<catalog>/<schema>/<volume>/...`; volume access is separate
   from catalog and schema access. The already-created managed volumes avoid a new
   storage service.
2. The [Databricks Files API](https://docs.databricks.com/api/workspace/files)
   supports raw file upload/download on Unity Catalog volume paths. The client uses
   `overwrite=False`, verifies every upload by downloading and hashing it, refuses
   differing existing bytes, and performs no compute operation. Data-transfer
   charges can still apply, so the package size is recorded rather than described
   as cost-free.
3. The [Databricks SDK for Python](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/sdk-python)
   supplies the Files and one-time Jobs APIs while reusing Azure CLI authentication.
   Evidence excludes access tokens, raw subscription/tenant/principal IDs, run IDs,
   workspace paths and customer identifiers.
4. [Serverless jobs](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
   are suitable for a one-time bounded validation without creating a cluster or
   scheduled job. The submitted run is CPU-only, unscheduled, task-time-limited,
   controller-deadline-limited and verified terminated.
5. [Serverless environment dependencies](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/dependencies)
   must be explicitly pinned when serialization compatibility matters. Environment
   version 5 uses Python 3.12, but its base packages alone did not load the frozen
   pandas 3.0.3 artifacts. Six exact package versions are therefore declared both
   in the notebook environment and the hash-locked development environment.
6. Databricks [billing system tables](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing)
   remain unavailable to the current identity. Admission used the verified INR
   resource-group budget; elapsed validation estimates use the official Azure
   Retail Prices API rate captured on 2026-09-07. Actual billed usage may lag.

## Live implementation

- The initial remote preflight found both volumes empty: zero payloads and zero
  seals. This was the expected start state.
- Forty-six payload destinations and two control-manifest copies were uploaded
  without overwrite. Maximum transferred bytes, including the duplicated snapshot
  and manifests, were 13,041,264 bytes.
- The first runtime attempt exposed a pandas serialization mismatch. The second
  corrected the exact dependency environment and exposed an incorrect validation
  assertion. The third corrected the assertion and exposed the single legacy module
  dependency. The fourth used the reviewed compatibility module and passed.
- The successful Databricks run recomputed 46 hashes, parsed all 20 data assets and
  275,630 rows, loaded all 26 model files, and initialized both existing-customer
  and new-customer loaders.
- Two deterministic seals were written only after that result passed. A final
  read-only audit confirmed 46 payload hashes, two control manifests, the runtime
  dependency, and two seal bodies all match the local approved state.
- Final compute inventory is zero clusters and zero persistent jobs. The Phase 2
  SQL warehouse remains stopped. No Azure resource was created.

## Cost assessment

The West US automated serverless rate captured from the Microsoft Azure Retail
Prices API was INR 44.91 per DBU-hour. Because the submission API does not provide
a pre-run DBU ceiling, the controller conservatively planned at 16 DBU/hour, capped
each task at 150 seconds and retained a four-minute controller deadline. All four
runs terminated.

Their cumulative elapsed-time estimate is INR 103.3578 pre-tax. Applying the 2x
risk guard produces INR 206.7156, below the owner-authorized INR 250 Phase 3
validation ceiling. This is a planning estimate, not an invoice guarantee; taxes,
discounts, billing delay and Files API transfer/storage charges are not inferred.

## Acceptance honesty and next design implication

The two Unity Catalog volume roots are immutable by this application's contract:
versioned paths, exact hashes, no-overwrite upload behavior and deterministic
seals. They are **not** configured as infrastructure-enforced WORM storage. A
workspace administrator could still change or delete volume files outside this
client. Phase 3 therefore satisfies the POC transfer gate but must not be presented
as regulatory records retention.

The frozen joblib assets are successfully migrated, but their dependency coupling
is technical debt. Phase 5 should package the functional recommender as an MLflow
model with explicit dependencies and parity tests rather than making the eventual
serving layer depend indefinitely on a legacy pickle import shim.
