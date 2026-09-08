"""Read-only Phase 10 inventory with public-safe output. Never wakes SQL or serving."""

import json
from datetime import UTC, datetime

from phase10_control import inspect_app
from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase10_runtime import SERVICE_CREDENTIAL
from retail_hp_azure.safety import require


def audit():
    context = CloudContext()
    inventory = inspect_compute(context)
    app = inspect_app(context)
    require(app.get("compute_state") == "STOPPED", "App must be stopped")
    acl = context.client.apps.get_permissions("retail-hp-poc-app")
    require(all(x.group_name in {None, "admins"} for x in acl.access_control_list or []),
            "Unexpected App group access; review before deployment")
    require(inventory["cluster_count"] == 0, "Unexpected cluster")
    require(
        all(x["state"] == "STOPPED" for x in inventory["project_warehouses"]),
        "Project warehouse running",
    )
    endpoint = context.client.api_client.do(
        "GET", "/api/2.0/serving-endpoints/retail-hp-poc-recommender"
    )
    require(endpoint["state"].get("suspend") == "STOPPED", "Recommender running")
    require(not list(context.client.jobs.list_runs(active_only=True)), "Active job detected")
    credential = context.client.credentials.get_credential(SERVICE_CREDENTIAL)
    bindings = context.client.workspace_bindings.get_bindings("credential", SERVICE_CREDENTIAL)
    ids = [x.workspace_id for x in bindings]
    require(
        ids == [int(context.workspace["properties"]["workspaceId"])], "Credential binding drift"
    )
    require(credential.isolation_mode.value == "ISOLATION_MODE_ISOLATED", "Credential not isolated")
    provider = context.az_json(
        ["provider", "show", "--namespace", "Microsoft.Automation", "--query", "registrationState"]
    )
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "status": "LIVE_DEPLOYMENT_BLOCKED",
        "app": app,
        "project_warehouse_states": [x["state"] for x in inventory["project_warehouses"]],
        "project_recommender_state": "STOPPED",
        "active_jobs": 0,
        "clusters": 0,
        "credential_isolated_to_approved_workspace": True,
        "automation_provider_registration": provider,
        "phase10_inference_calls": 0,
        "phase10_paid_compute_starts": 0,
        "invoice_cap_guaranteed": False,
        "managed_resource_group_cost_coverage": "NOT_VERIFIED_NO_OUT_OF_SCOPE_WRITES",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
