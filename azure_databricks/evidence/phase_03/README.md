# Phase 3 — complete; transfer sealed and compute stopped

## Outcome

The approved synthetic data and frozen recommendation assets are now transferred
to versioned Unity Catalog volume paths and sealed as `retail_hp_transfer_v1`.
Final read-only verification passed every payload and control artifact:

- 45 unique source files and 46 remote payload copies
- 20 Parquet data assets and 275,630 rows
- 26 model assets
- 46/46 payload hashes identical
- 2/2 control manifests identical
- one separately hashed runtime compatibility module identical
- 2/2 deterministic seals identical
- existing-customer and new-customer loaders initialized in Databricks

The package bundle SHA-256 is
`976b7d0cf2390869d78af3bce95842f6f30e09d5c64c4bdd7fddfb46c9a3bc0b`.
The seal SHA-256 is
`26e1520ec4c3e26281def45413459fd24c96796a8810f76daeb0411dee6ec218`.

No new Azure resource, all-purpose cluster, persistent job, schedule, model
endpoint or LLM call was created. The existing project SQL warehouse remains
**STOPPED**. The final inventory is zero clusters and zero persistent jobs.

## Important limitation

“Sealed” is an application-level POC control: versioned paths, approved hashes,
no-overwrite behavior and two deterministic seal files. Unity Catalog storage was
not configured as WORM, so this is not a regulatory immutability guarantee. A
privileged administrator acting outside this client could still alter the volume.

Frozen joblib portability also required an exact six-package Python environment
and one minimal, reviewed compatibility function. This is acceptable for faithful
migration; Phase 5 must replace that coupling with an explicitly packaged MLflow
model and inference-parity evidence.

## Live validation and cost

Four one-time CPU serverless attempts were needed to discover and resolve the
serialization environment, validator contract, and legacy import dependency.
All attempts terminated; none created a persistent job or schedule. The successful
run completed in 77.47 seconds.

Using the captured West US retail rate, a conservative 16 DBU/hour planning
assumption estimates all four runs at INR 103.3578 pre-tax. The 2x guarded value is
INR 206.7156, below the authorized INR 250 validation ceiling. Actual Azure usage
can lag and this estimate excludes tax, discounts and any small volume transfer or
storage charge. The INR 12,000 resource-group budget is an alert, not a hard cap.

## Runbook

Use the hash-locked Python 3.12 environment and authenticate through Azure CLI.
Run commands from the repository root after installing the package editable:

```powershell
python -m retail_hp_azure.phase3 validate-local
python -m retail_hp_azure.phase3 inspect-remote
python -m retail_hp_azure.phase3_runtime plan
```

The local validation and remote inspection commands are read-only. The already
completed mutation commands are retained for reproducibility but must not be rerun
casually:

```powershell
$env:RETAIL_HP_PHASE3_TRANSFER_APPROVED = "retail_hp_transfer_v1"
python -m retail_hp_azure.phase3 apply-transfer
$env:RETAIL_HP_PHASE3_VALIDATION_CEILING_INR = "250"
python -m retail_hp_azure.phase3_runtime run-and-seal
```

`apply-transfer` now fails because the roots are sealed. `run-and-seal` also fails
closed on existing seals; do not remove or overwrite them. A new transfer requires
a new reviewed version name, manifest, paths and explicit authorization. Phase 4
is not authorized by Phase 3 completion.

## Evidence

- `local_validation.json`: all local hashes, formats, rows, keys and null contracts.
- `remote_preflight.json`: empty-volume state before transfer.
- `transfer_apply.json`: immutable upload result and maximum byte count.
- `databricks_validation.json`: successful Databricks load/parse result.
- `validation_attempts.json`: sanitized attempt findings and cumulative estimate.
- `transfer_seal.json`: two-root deterministic seal result.
- `remote_inspection.json`: final payload, manifest, compatibility and seal audit.
- `completion.json`: stopped-platform completion snapshot.
- `acceptance.json`: machine-readable Phase 3 exit decision.
- `review_and_research.md`: codebase findings, primary sources and limitations.

Evidence contains no recipient addresses, bearer tokens, raw Azure subscription,
tenant, principal, job/run or customer identifiers. It is operational evidence,
not a billing statement or production approval.
