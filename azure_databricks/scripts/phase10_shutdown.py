"""Install the fixed-target stop controller and run a stopped-state identity test."""

import argparse
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import require

ACCOUNT = "retail-hp-poc-shutdown"
RUNBOOK = "retail-hp-stop-demo"


def preserved_acl(acl, application_id):
    """Retain every existing direct grant when adding the scoped controller."""
    result = []
    for entry in acl:
        identity = {key: entry[key] for key in
                    ("user_name", "group_name", "service_principal_name") if entry.get(key)}
        require(len(identity) == 1, "Ambiguous ACL identity")
        if identity.get("service_principal_name") == application_id:
            continue
        for permission in entry.get("all_permissions", []):
            if not permission.get("inherited", False):
                result.append({**identity, "permission_level": permission["permission_level"]})
    result.append({"service_principal_name": application_id, "permission_level": "CAN_MANAGE"})
    return result


def install(context, *, approve_sql_entitlement=False):
    require(context.apply, "Explicit apply required")
    root = ("https://management.azure.com" + context.group["id"]
            + "/providers/Microsoft.Automation/automationAccounts/" + ACCOUNT)
    token = context.az_json(["account", "get-access-token", "--subscription",
                            context.subscription, "--resource",
                            "https://management.azure.com/"])["accessToken"]

    def arm(method, suffix="", body=None, content=None):
        response = requests.request(
            method, root + suffix + "?api-version=2024-10-23",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "text/powershell" if content else "application/json"},
            json=body, data=content, timeout=45, allow_redirects=False,
        )
        require(200 <= response.status_code < 300,
                f"Shutdown ARM {method} HTTP {response.status_code}")
        return response.json() if response.content else {}

    account = arm("GET")
    require(account.get("tags", {}).get("purpose") == "bounded-poc-shutdown",
            "Account ownership drift")
    principal = context.az_json(["ad", "sp", "show", "--id",
                                 account["identity"]["principalId"]])
    application_id = principal["appId"]
    client = context.client
    warehouses = [w for w in client.warehouses.list() if w.name == "retail-hp-poc-sql"]
    require(len(warehouses) == 1, "Warehouse ambiguity")
    warehouse = warehouses[0]
    require(warehouse.state.value == "STOPPED", "Warehouse must be stopped")
    require(re.fullmatch(r"[a-f0-9]{16}", warehouse.id), "Invalid warehouse identifier")
    require(client.apps.get("retail-hp-poc-app").compute_status.state.value == "STOPPED",
            "App must be stopped")
    api = client.api_client
    endpoint = api.do("GET", "/api/2.0/serving-endpoints/retail-hp-poc-recommender")
    require(endpoint["state"].get("suspend") == "STOPPED", "Endpoint must be stopped")
    endpoint_id = endpoint["id"]
    require(re.fullmatch(r"[A-Za-z0-9-]+", endpoint_id), "Invalid endpoint identifier")
    existing = api.do("GET", "/api/2.0/preview/scim/v2/ServicePrincipals",
                      query={"filter": f'applicationId eq "{application_id}"'})
    if not existing.get("Resources"):
        require(approve_sql_entitlement, "Explicit SQL entitlement approval required")
        created = api.do("POST", "/api/2.0/preview/scim/v2/ServicePrincipals", body={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
            "applicationId": application_id, "displayName": ACCOUNT,
            "active": True, "entitlements": [{"value": "workspace-access"},
                                             {"value": "databricks-sql-access"}],
        })
    else:
        require(len(existing["Resources"]) == 1, "Ambiguous controller identity")
        created = existing["Resources"][0]
    if not any(x.get("value") == "databricks-sql-access"
               for x in created.get("entitlements", [])):
        require(approve_sql_entitlement, "Explicit SQL entitlement approval required")
        api.do("PATCH", "/api/2.0/preview/scim/v2/ServicePrincipals/" + created["id"], body={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "entitlements", "value": [
                {"value": "databricks-sql-access"}]}],
        })
    # Databricks has no stop-only App/endpoint role. CAN_MANAGE is restricted to
    # these three objects; no workspace admin, data grants or Azure RBAC is added.
    for path in ["/api/2.0/permissions/apps/retail-hp-poc-app",
                 "/api/2.0/permissions/sql/warehouses/" + warehouse.id,
                 "/api/2.0/permissions/serving-endpoints/" + endpoint_id]:
        before = api.do("GET", path)["access_control_list"]
        preserved = preserved_acl(before, application_id)
        # Warehouse PATCH rejects IS_OWNER even when unchanged. PATCH updates
        # only supplied entries, unlike PUT replacement; verify owner afterward.
        updates = [entry for entry in preserved if entry["permission_level"] != "IS_OWNER"]
        api.do("PATCH", path, body={"access_control_list": updates})
        after = preserved_acl(api.do("GET", path)["access_control_list"], application_id)
        require(all(entry in after for entry in preserved), "Existing direct ACL changed")
    source = (Path(__file__).parents[1] / "app/stop_demo.ps1").read_text()
    source = source.replace("__WAREHOUSE_ID__", warehouse.id)
    route = "/runbooks/" + RUNBOOK
    arm("PUT", route, {"location": "westus", "properties": {
        "runbookType": "PowerShell", "logVerbose": False, "logProgress": False,
        "description": "Fixed retail POC stop targets; managed identity; no start operations.",
        "draft": {},
    }})
    arm("PUT", route + "/draft/content", content=source.encode())
    arm("POST", route + "/publish")
    job = str(uuid4())
    arm("PUT", "/jobs/" + job, {"properties": {"runbook": {"name": RUNBOOK},
        "parameters": {"DeadlineUnix": str(int(time.time()))}}})
    return {"job_id": job, "account": ACCOUNT, "runbook": RUNBOOK,
            "status": "STOPPED_STATE_TEST_SUBMITTED", "compute_started": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--approve-sql-entitlement", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(CloudContext(apply=True),
                             approve_sql_entitlement=args.approve_sql_entitlement), indent=2))
