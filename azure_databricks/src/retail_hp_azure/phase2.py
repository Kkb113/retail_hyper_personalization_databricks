"""Scoped Phase 2 discovery. Raw identifiers and authentication stay in memory."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retail_hp_azure.config import HOST
from retail_hp_azure.safety import (
    REQUIRED_TAGS,
    SafetyError,
    check_environment,
    check_fingerprint,
    require,
)

CATALOG = "intellify_databricks_demo"
SCHEMAS = ("bronze", "silver", "features", "ml", "gold", "serving", "agent", "monitoring")
GROUPS = ("retail_hp_admins", "retail_hp_engineers", "retail_hp_viewers", "retail_hp_app_runtime")
VOLUMES = (("bronze", "transfer_landing"), ("ml", "model_assets"))
MARKER = "Retail HP Phase 2 managed foundation; synthetic POC only"
ROOT = Path(__file__).resolve().parents[2]


def governance_plan() -> dict[str, Any]:
    return {
        "version": "azure_phase2_governance_v1", "scope": {
            "resource_group": "Databricks", "workspace_host": HOST, "catalog": CATALOG,
        },
        "groups": list(GROUPS), "schemas": [f"{CATALOG}.{s}" for s in SCHEMAS],
        "managed_volumes": [f"{CATALOG}.{s}.{v}" for s, v in VOLUMES],
        "tags": REQUIRED_TAGS, "object_owner": "retail_hp_admins",
        "workspace_assignment": "USER", "paid_compute_allowed": False,
        "warehouse_creation_allowed": False, "data_upload_allowed": False,
        "predictive_optimization": "DISABLE",
        "poc_expiry": None, "expiry_requires_owner_review": True,
        "automatic_deletion": False,
        "note": "Additive project governance only; no workspace-admin grants or user invitations",
    }


def grant_matrix() -> dict[tuple[str, str], dict[str, list[str]]]:
    matrix = {("catalog", CATALOG): {
        group: ["USE_CATALOG"] for group in GROUPS
    }}
    matrix[("catalog", CATALOG)]["retail_hp_admins"].append("CREATE_SCHEMA")
    for schema in SCHEMAS:
        grants = {"retail_hp_admins": ["USE_SCHEMA", "CREATE_TABLE", "CREATE_FUNCTION",
                                       "CREATE_VOLUME", "SELECT", "MODIFY", "EXECUTE"],
                  "retail_hp_engineers": ["USE_SCHEMA", "CREATE_TABLE", "CREATE_FUNCTION",
                                         "SELECT", "MODIFY", "EXECUTE"]}
        if schema == "serving":
            grants["retail_hp_viewers"] = ["USE_SCHEMA", "SELECT", "EXECUTE"]
            grants["retail_hp_app_runtime"] = ["USE_SCHEMA", "SELECT", "EXECUTE"]
        if schema == "ml":
            for group in GROUPS[:2]:
                grants[group].append("CREATE_MODEL")
        matrix[("schema", f"{CATALOG}.{schema}")] = grants
    for schema, volume in VOLUMES:
        matrix[("volume", f"{CATALOG}.{schema}.{volume}")] = {
            group: ["READ_VOLUME", "WRITE_VOLUME"] for group in GROUPS[:2]
        }
    return matrix


def safe_arm_route(method: str, path: str, group_id: str, workspace_id: str) -> None:
    resource = path.split("?", 1)[0].lower()
    routes = {
        ("GET", group_id + "/providers/Microsoft.Consumption/budgets"),
        ("POST", group_id + "/providers/Microsoft.CostManagement/query"),
        ("GET", group_id + "/providers/Microsoft.Resources/tags/default"),
        ("PATCH", group_id + "/providers/Microsoft.Resources/tags/default"),
        ("GET", workspace_id + "/providers/Microsoft.Resources/tags/default"),
        ("PATCH", workspace_id + "/providers/Microsoft.Resources/tags/default"),
    }
    require((method, resource) in {(verb, value.lower()) for verb, value in routes},
            "ARM route is not an approved Phase 2 operation")


class CloudContext:
    """Authenticate only after checking the pinned Azure account and resource scope."""

    def __init__(self, *, apply: bool = False) -> None:
        self.apply = apply
        check_environment(os.environ)
        az = shutil.which("az")
        require(az is not None, "Azure CLI is required")
        self.az = str(az)
        self.account = self.az_json(["account", "show"])
        check_fingerprint("subscription", self.account["id"])
        check_fingerprint("tenant", self.account["tenantId"])
        require(self.account["state"] == "Enabled", "Subscription is not enabled")
        self.subscription = self.account["id"]
        self.group = self.az_json(["group", "show", "--name", "Databricks",
                                   "--subscription", self.subscription])
        check_fingerprint("resource_group_id", self.group["id"])
        self.workspace = self.az_json([
            "resource", "show", "--subscription", self.subscription,
            "--resource-group", "Databricks", "--name", "intellify-databricks-demo",
            "--resource-type", "Microsoft.Databricks/workspaces",
        ])
        check_fingerprint("workspace_arm_id", self.workspace["id"])
        properties = self.workspace["properties"]
        check_fingerprint("workspace_id", str(properties["workspaceId"]))
        require(f"https://{properties['workspaceUrl']}" == HOST, "Unexpected workspace host")
        require(self.workspace["location"] == "westus", "Unexpected workspace region")
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        self.client = WorkspaceClient(config=Config(
            host=HOST, auth_type="azure-cli", config_file=os.devnull,
            azure_workspace_resource_id=self.workspace["id"],
        ))

    def az_json(self, arguments: list[str]) -> Any:
        result = subprocess.run(  # noqa: S603 - fixed Azure CLI argv, no shell
            [self.az, *arguments, "--output", "json", "--only-show-errors"],
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        require(result.returncode == 0, "Azure CLI read failed; raw output suppressed")
        return json.loads(result.stdout)

    def arm(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        import requests

        require(path.lower().startswith(self.group["id"].lower() + "/providers/"),
                "ARM operation is outside the approved resource group")
        safe_arm_route(method, path, self.group["id"], self.workspace["id"])
        if method not in {"GET", "POST"}:
            require(self.apply, "Cloud writes require an explicit apply command")
        token = self.az_json(["account", "get-access-token", "--subscription", self.subscription,
                              "--resource", "https://management.azure.com/"])["accessToken"]
        response = requests.request(
            method, "https://management.azure.com" + path,
            headers={"Authorization": "Bearer " + token}, json=body, timeout=45,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            try:
                code = response.json().get("error", {}).get("code", "Unknown")
            except ValueError:
                code = "Unknown"
            safe_code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(code)) else "Unknown"
            retry = response.headers.get(
                "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after",
                response.headers.get("Retry-After", "unknown"),
            )
            retry = retry if str(retry).isdigit() else "unknown"
            raise SafetyError(f"Scoped ARM request failed: HTTP {response.status_code} "
                              f"{safe_code}; retry_after_seconds={retry}")
        return response.json() if response.content else {}


def inspect_environment(context: CloudContext) -> dict[str, Any]:
    result: dict[str, Any] = {"mode": "read_only", "scope_verified": True, "checks": {}}
    client = context.client

    def check(name: str, operation: Any) -> None:
        try:
            result["checks"][name] = {"status": "PASS", "value": operation()}
        except Exception as exc:  # discovery reports each denied capability independently
            result["checks"][name] = {"status": "UNAVAILABLE", "error_type": type(exc).__name__}
            if isinstance(exc, SafetyError):
                result["checks"][name]["reason"] = str(exc)

    check("catalog", lambda: {
        "name": client.catalogs.get(CATALOG).name,
        "has_managed_storage": bool(client.catalogs.get(CATALOG).storage_root),
    })
    check("schemas", lambda: [s.name for s in client.schemas.list(CATALOG)])
    check("volumes", lambda: [v.full_name for schema in SCHEMAS
                              if schema in {s.name for s in client.schemas.list(CATALOG)}
                              for v in client.volumes.list(CATALOG, schema)])
    check("account_groups_api", lambda: {
        "count": client.api_client.do("GET", "/api/2.0/account/scim/v2/Groups",
                                      query={"count": 1}).get("totalResults"),
    })
    check("catalog_grants", lambda: [
        {"principal_kind": "project_group" if (a.principal or "").startswith("retail_hp_")
         else "other_redacted", "privileges": [p.value for p in a.privileges or []]}
        for a in client.grants.get("catalog", CATALOG).privilege_assignments or []
    ])
    check("warehouses", lambda: [{"name": w.name, "state": str(w.state)}
                                  for w in client.warehouses.list()])
    check("system_billing_tables", lambda: [
        t.name for t in client.tables.list("system", "billing")
    ])
    check("system_schema_states", lambda: [{"schema": s.schema, "state": s.state}
          for s in client.system_schemas.list(client.metastores.current().metastore_id)])
    check("budget_inventory", lambda: {"count": len(context.arm("GET", context.group["id"] +
          "/providers/Microsoft.Consumption/budgets?api-version=2024-08-01").get("value", []))})
    def read_cost() -> dict[str, Any]:
        properties = context.arm("POST", context.group["id"] +
          "/providers/Microsoft.CostManagement/query?api-version=2025-03-01", {
              "type": "ActualCost", "timeframe": "MonthToDate", "dataset": {
                  "granularity": "None", "aggregation": {
                      "totalCost": {"name": "PreTaxCost", "function": "Sum"}},
              },
          }).get("properties", {})
        # No resource identifiers, continuation links or billing account metadata in evidence.
        return {key: properties.get(key, []) for key in ("columns", "rows")}

    check("month_to_date_cost", read_cost)
    result["existing_workspace_tags"] = sorted((context.workspace.get("tags") or {}).keys())
    result["cloud_mutations_performed"] = False
    return result


def apply_governance(context: CloudContext) -> dict[str, Any]:
    """Create only project metadata; stop on conflicting pre-existing objects."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.catalog import (
        EnablePredictiveOptimization,
        PermissionsChange,
        Privilege,
        VolumeType,
    )

    require(context.apply, "Cloud writes require explicit apply")
    client = context.client
    current = client.current_user.me()
    require(any(g.display == "admins" for g in current.groups or []),
            "A verified workspace administrator must bootstrap governance")
    result: dict[str, Any] = {"plan": governance_plan(), "actions": [],
                              "cloud_mutations_performed": False, "paid_resources_created": 0}

    def record(action: str, name: str) -> None:
        result["actions"].append({"action": action, "name": name})
        result["cloud_mutations_performed"] = True
        # Persist each completed mutation, so an interruption is not reported as no change.
        record_evidence("governance_result.json", result)
        cumulative: dict[tuple[str, str], dict[str, str]] = {}
        for filename in ("governance_first_apply.json", "governance_last_changes.json",
                         "governance_cumulative_actions.json"):
            previous = ROOT / "evidence/phase_02" / filename
            if previous.exists():
                for entry in json.loads(previous.read_text(encoding="utf-8")).get("actions", []):
                    cumulative[(entry["action"], entry["name"])] = entry
        for entry in result["actions"]:
            cumulative[(entry["action"], entry["name"])] = entry
        record_evidence("governance_cumulative_actions.json", {
            "description": "Unique completed metadata mutations, not a billing or audit log",
            "actions": list(cumulative.values()),
        })

    workspace_groups = {g.display_name: g for g in client.groups.list()}
    for group in GROUPS:
        found = client.api_client.do(
            "GET", "/api/2.0/account/scim/v2/Groups",
            query={"filter": f'displayName eq "{group}"'},
        ).get("Resources", [])
        require(len(found) <= 1, "Duplicate project group requires manual review")
        if not found:
            entry = client.api_client.do("POST", "/api/2.0/account/scim/v2/Groups", body={
                "displayName": group, "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "members": [{"value": current.id}] if group == "retail_hp_admins" else [],
            })
            record("create_account_group", group)
        else:
            group_id = str(found[0].get("id", ""))
            require(group_id.isdigit(), "Project group ID is missing")
            entry = client.api_client.do("GET", "/api/2.0/account/scim/v2/Groups/" + group_id)
        require(entry.get("displayName") == group, "Unexpected project group response")
        if group == "retail_hp_admins":
            require(any(m.get("value") == current.id for m in entry.get("members", [])),
                    "Bootstrap user must belong to the existing project admin group")
        require(not entry.get("roles"), "Project group has unexpected account roles")
        if group not in workspace_groups:
            require(str(entry.get("id", "")).isdigit(), "Project group ID is missing")
            client.api_client.do("PUT", "/api/2.0/preview/permissionassignments/principals/" +
                                 str(entry["id"]), body={"permissions": ["USER"]})
            record("assign_workspace_user", group)

    # Add only missing intended grants, preserving unrelated grants and principals.
    def apply_grants(kind: str, name: str) -> None:
        desired = grant_matrix()[(kind, name)]
        existing = {a.principal: {p.value for p in a.privileges or []}
                    for a in client.grants.get(kind, name).privilege_assignments or []}
        for principal, privileges in desired.items():
            unexpected = existing.get(principal, set()) - set(privileges)
            require(not unexpected, "Project grant drift requires review; privileges preserved")
            missing = set(privileges) - existing.get(principal, set())
            if missing:
                client.grants.update(kind, name, changes=[PermissionsChange(
                    principal=principal, add=[Privilege(p) for p in sorted(missing)],
                )])
                record("grant_project_privileges", f"{kind}:{name}:{principal}")

    apply_grants("catalog", CATALOG)
    for schema in SCHEMAS:
        name = f"{CATALOG}.{schema}"
        try:
            existing_schema = client.schemas.get(name)
            require(existing_schema.comment == MARKER, "Pre-existing schema is not project-managed")
            require(existing_schema.owner in {current.user_name, "retail_hp_admins"},
                    "Project schema ownership drift requires review")
        except NotFound:
            client.schemas.create(schema, CATALOG, comment=MARKER,
                                  properties={"project": REQUIRED_TAGS["project"]})
            record("create_managed_schema", name)
        apply_grants("schema", name)
    for schema, volume in VOLUMES:
        name = f"{CATALOG}.{schema}.{volume}"
        try:
            existing_volume = client.volumes.read(name)
            require(existing_volume.comment == MARKER, "Pre-existing volume is not project-managed")
            require(existing_volume.owner in {current.user_name, "retail_hp_admins"},
                    "Project volume ownership drift requires review")
            require(existing_volume.volume_type == VolumeType.MANAGED,
                    "External volumes are forbidden")
        except NotFound:
            client.volumes.create(CATALOG, schema, volume, VolumeType.MANAGED, comment=MARKER)
            record("create_managed_volume", name)
        apply_grants("volume", name)
    for schema in SCHEMAS:
        name = f"{CATALOG}.{schema}"
        owner = client.schemas.get(name).owner
        require(owner in {current.user_name, "retail_hp_admins"},
                "Project schema ownership drift requires review")
        if owner != "retail_hp_admins":
            client.schemas.update(name, owner="retail_hp_admins")
            record("assign_group_ownership", name)
        if client.schemas.get(name).enable_predictive_optimization != (
            EnablePredictiveOptimization.DISABLE
        ):
            client.schemas.update(name,
                                  enable_predictive_optimization=EnablePredictiveOptimization.DISABLE)
            record("disable_predictive_optimization", name)
    for schema, volume in VOLUMES:
        name = f"{CATALOG}.{schema}.{volume}"
        owner = client.volumes.read(name).owner
        require(owner in {current.user_name, "retail_hp_admins"},
                "Project volume ownership drift requires review")
        if owner != "retail_hp_admins":
            client.volumes.update(name, owner="retail_hp_admins")
            record("assign_group_ownership", name)
    for target, resource in (("resource_group", context.group), ("workspace", context.workspace)):
        if any((resource.get("tags") or {}).get(k) != v for k, v in REQUIRED_TAGS.items()):
            context.arm("PATCH", resource["id"] +
                        "/providers/Microsoft.Resources/tags/default?api-version=2021-04-01", {
                            "operation": "Merge", "properties": {"tags": REQUIRED_TAGS},
                        })
            record("merge_project_tags", target)
    result["status"] = "GOVERNANCE_APPLIED_VERIFICATION_PENDING"
    if result["actions"] and not (ROOT / "evidence/phase_02/governance_first_apply.json").exists():
        record_evidence("governance_first_apply.json", result)
    if result["actions"]:
        record_evidence("governance_last_changes.json", result)
    record_evidence("governance_result.json", result)
    return result


def verify_governance(context: CloudContext) -> dict[str, Any]:
    """Inspect metadata and explicit ACLs; never claim impersonated SQL execution."""
    from databricks.sdk.service.catalog import EnablePredictiveOptimization, VolumeType

    client = context.client
    checks: dict[str, bool] = {}
    workspace_groups = {g.display_name: g for g in client.groups.list()}
    for group in GROUPS:
        entries = client.api_client.do("GET", "/api/2.0/account/scim/v2/Groups",
                                      query={"filter": f'displayName eq "{group}"'})
        found = entries.get("Resources", [])
        valid_group = False
        if len(found) == 1 and str(found[0].get("id", "")).isdigit():
            detail = client.api_client.do("GET", "/api/2.0/account/scim/v2/Groups/" +
                                          str(found[0]["id"]))
            valid_group = detail.get("displayName") == group and not detail.get("roles")
        checks[f"account_group:{group}"] = valid_group
        checks[f"workspace_user:{group}"] = group in workspace_groups and not (
            workspace_groups[group].roles or []
        )
    project_ids = {g.id for name, g in workspace_groups.items() if name in GROUPS}
    admins = workspace_groups.get("admins")
    if admins is not None:
        require(bool(admins.id), "Workspace admin group ID is missing")
        admins = client.groups.get(str(admins.id))
    checks["no_project_group_in_workspace_admins"] = admins is not None and not any(
        member.value in project_ids for member in admins.members or []
    )
    for (kind, name), desired in grant_matrix().items():
        existing = {a.principal: {p.value for p in a.privileges or []}
                    for a in client.grants.get(kind, name).privilege_assignments or []}
        for group in GROUPS:
            checks[f"explicit_grants:{kind}:{name}:{group}"] = (
                existing.get(group, set()) == set(desired.get(group, []))
            )
        # Unrelated broad inherited grants could invalidate group isolation.
        checks[f"no_broad_other_grants:{kind}:{name}"] = all(
            not (privileges - {"USE_CATALOG", "USE_SCHEMA", "BROWSE"})
            for principal, privileges in existing.items() if principal not in GROUPS
        )
    for schema in SCHEMAS:
        name = f"{CATALOG}.{schema}"
        obj = client.schemas.get(name)
        checks[f"schema:{name}"] = obj.comment == MARKER and obj.owner == "retail_hp_admins"
        checks[f"predictive_optimization_disabled:{name}"] = (
            obj.enable_predictive_optimization == EnablePredictiveOptimization.DISABLE
        )
    for schema, volume in VOLUMES:
        name = f"{CATALOG}.{schema}.{volume}"
        item = client.volumes.read(name)
        checks[f"volume:{name}"] = (item.comment == MARKER and item.owner == "retail_hp_admins"
                                          and item.volume_type == VolumeType.MANAGED)
    for label, resource in (("resource_group", context.group), ("workspace", context.workspace)):
        checks[f"tags:{label}"] = all((resource.get("tags") or {}).get(k) == v
                                      for k, v in REQUIRED_TAGS.items())
    checks["no_sql_warehouse"] = not list(client.warehouses.list())
    return {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "scope_verified": True, "cloud_mutations_performed": False,
        "sql_identity_tests": "DEFERRED: representative identities and approved compute required",
        "phase2_complete": False,
    }


def inspect_compute(context: CloudContext) -> dict[str, Any]:
    """Read workspace compute inventory without starting or invoking anything."""
    client = context.client
    resources = context.az_json(["resource", "list", "--subscription", context.subscription,
                                 "--resource-group", "Databricks"])
    clusters = list(client.clusters.list())
    warehouses = list(client.warehouses.list())
    jobs = list(client.jobs.list())
    apps = list(client.apps.list())
    endpoints = list(client.serving_endpoints.list())
    return {
        "scope_verified": True, "cloud_mutations_performed": False,
        "azure_resource_types": sorted(r["type"] for r in resources),
        "cluster_count": len(clusters), "warehouse_count": len(warehouses),
        "job_count": len(jobs), "app_count": len(apps),
        "serving_endpoint_count": len(endpoints),
        "project_serving_endpoint_count": sum(
            (e.name or "").startswith("retail-hp-") for e in endpoints
        ),
        "note": "Pre-existing foundation endpoints are not invoked; counts are not billed usage",
        "managed_resource_group_cost_coverage": "NOT_VERIFIED; no managed-group writes permitted",
    }


def record_evidence(filename: str, result: dict[str, Any]) -> None:
    output = ROOT / "evidence/phase_02" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({**result, "captured_at": datetime.now(UTC).isoformat()},
                                indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=[
        "inspect", "plan-governance", "apply-governance", "verify-governance",
        "inspect-compute",
    ])
    arguments = parser.parse_args()
    try:
        if arguments.command == "plan-governance":
            result = governance_plan()
        elif arguments.command == "apply-governance":
            result = apply_governance(CloudContext(apply=True))
        elif arguments.command == "verify-governance":
            result = verify_governance(CloudContext())
            record_evidence("governance_verification.json", result)
        elif arguments.command == "inspect-compute":
            result = inspect_compute(CloudContext())
            record_evidence("compute_inventory.json", result)
        else:
            result = inspect_environment(CloudContext())
            record_evidence("live_discovery.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(
            f"Phase 2 operation failed: {type(exc).__name__}; {detail}"
        ) from None
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
