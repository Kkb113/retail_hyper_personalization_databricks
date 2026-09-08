# Phase 7 operational-state runbook

Phase 7 uses no Lakebase resource. The durable fallback consists of governed Delta
tables plus one manual-only export job. Current-session conversation state may be
ephemeral inside the future Databricks App.

## Safe inspection

~~~powershell
.\.venv\Scripts\python.exe azure_databricks\scripts\phase7_control.py inspect
~~~

Inspection is read-only and does not start compute. A healthy stopped state has:

- nine expected Delta tables, all append-only and owned by `retail_hp_admins`;
- `SELECT` and `MODIFY` for `retail_hp_app_runtime` only on the eight `agent` tables;
- one export job, no schedule, no active run, and a 180-second timeout;
- zero Lakebase projects, clusters, or apps;
- stopped SQL warehouse and model endpoint.

## Demo write session

The application must authenticate as its Databricks App identity or the approved
non-admin workload identity. It must obtain the HMAC key from an uncommitted secret
binding and pass the authenticated subject through `ActorContext`. Do not store the
raw subject, credentials, or tokens in event payloads.

Start the existing SQL warehouse only as an explicit demo operation. The warehouse
has one-minute auto-stop; stop it explicitly at session end. A state write must use
`DeltaOperationalStore`, an opaque idempotency key, and an actor-scoped read.

## Manual feedback export

Run the export only when feedback analytics are needed:

~~~powershell
$env:RETAIL_HP_PHASE7_CEILING_INR='250'
.\.venv\Scripts\python.exe azure_databricks\scripts\phase7_control.py run-export
Remove-Item Env:\RETAIL_HP_PHASE7_CEILING_INR
~~~

The job is never scheduled. It runs on bounded serverless compute, performs the
same insert-only merge twice, proves the second pass adds zero rows, and terminates.

## Emergency stop and verification

Use the existing Phase 2 exact-name stop control for the SQL warehouse and the
Phase 6 exact-name endpoint stop control. Then run Phase 7 `inspect`. Never issue a
broad stop or delete command. Job definitions and Delta storage do not consume
idle compute, but small managed-storage charges can remain.

## Rollback

Prefer a Git revert for code. The tables contain only synthetic POC events and the
job is unscheduled. Deleting tables, the job, notebooks, or data is destructive and
requires a separate reviewed request; Phase 7 does not provide an automatic destroy
command.
