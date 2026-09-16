"""Validate deployed identity and launch claims without compute or inference."""

import io
import json
from pathlib import Path
from uuid import uuid4

from databricks.sdk.errors import AlreadyExists, ResourceAlreadyExists
from retail_hp_azure.customer_context import REQUIRED_SOURCE_COLUMNS
from retail_hp_azure.phase2 import CATALOG, CloudContext
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.phase10_release import STATE_VOLUME, verify_files
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def verify(context):
    control = json.loads((ROOT / "build/phase10-release.local.json").read_text())
    verify_files(Path(control["local_package"]))
    app = context.client.apps.get("retail-hp-poc-app")
    require(app.compute_status.state.value == "STOPPED", "App must be stopped")
    principal = context.client.service_principals.get(str(app.service_principal_id))
    workload, secret_id, principal_id = _workload_client(context, principal)
    path = f"{STATE_VOLUME}/preflight-{uuid4().hex}.json"
    created = False
    report = {
        "status": "FAIL",
        "release": control["release"],
        "compute_started": False,
        "inference_calls": 0,
    }
    try:
        if control.get("customer_context_version") == "customer_context_v2":
            for name, required in REQUIRED_SOURCE_COLUMNS.items():
                columns = context.client.tables.get(f"{CATALOG}.{name}").columns or []
                require(
                    required <= {c.name for c in columns}, f"Customer source schema drift: {name}"
                )
            report["customer_context_sources_verified"] = True
        workload.files.upload(path, io.BytesIO(b"{}"), overwrite=False)
        created = True
        try:
            workload.files.upload(path, io.BytesIO(b"{}"), overwrite=False)
        except (AlreadyExists, ResourceAlreadyExists):
            report["duplicate_launch_rejected"] = True
        require(report.get("duplicate_launch_rejected"), "Claim is not create-only")
        tables = list(context.client.tables.list(CATALOG, "bronze"))
        require(tables, "No raw tables available to inspect")
        for table in tables:
            grants = context.client.grants.get_effective(
                "table", table.full_name, principal=app.service_principal_client_id
            )
            for assignment in grants.privilege_assignments or []:
                for privilege in assignment.privileges or []:
                    require(
                        privilege.privilege.value not in {"SELECT", "ALL_PRIVILEGES"},
                        "App identity unexpectedly has raw read grants",
                    )
        report["raw_table_read_grants_absent"] = True
        report["status"] = "PASS_METADATA_AND_CLAIM"
    finally:
        try:
            if created:
                workload.files.delete(path)  # Only this newly created disposable probe.
        finally:
            context.client.service_principal_secrets_proxy.delete(principal_id, secret_id)
            report["temporary_oauth_secret_revoked"] = True
    return report


if __name__ == "__main__":
    result = verify(CloudContext(apply=True, direct_operator_token=True))
    (ROOT / "azure_databricks/evidence/phase_10/release_preflight.json").write_text(
        json.dumps(result, indent=2)
    )
    print(json.dumps(result, indent=2))
