from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import EnablePredictiveOptimization, VolumeType
from retail_hp_azure.phase2 import (
    CATALOG,
    GROUPS,
    MARKER,
    SCHEMAS,
    CloudContext,
    apply_governance,
    governance_plan,
    grant_matrix,
    safe_arm_route,
    verify_governance,
)
from retail_hp_azure.safety import SafetyError


def test_phase2_metadata_plan_does_not_allow_compute_or_data_upload():
    plan = governance_plan()
    assert len(plan["schemas"]) == 8
    assert len(plan["managed_volumes"]) == 2
    assert len(plan["groups"]) == 4
    assert plan["paid_compute_allowed"] is False
    assert plan["warehouse_creation_allowed"] is False
    assert plan["data_upload_allowed"] is False
    assert plan["automatic_deletion"] is False


def test_viewer_and_app_have_no_raw_data_privileges():
    for (kind, name), grants in grant_matrix().items():
        for group in GROUPS[2:]:
            if kind == "catalog":
                assert grants[group] == ["USE_CATALOG"]
            elif name == f"{CATALOG}.serving":
                assert set(grants[group]) == {"USE_SCHEMA", "SELECT", "EXECUTE"}
            else:
                assert group not in grants
    assert all("ALL_PRIVILEGES" not in privileges for grants in grant_matrix().values()
               for privileges in grants.values())


@pytest.mark.parametrize(("method", "path"), [
    ("PUT", "/rg/providers/Microsoft.Compute/virtualMachines/unsafe"),
    ("DELETE", "/rg/providers/Microsoft.Resources/tags/default"),
    ("PATCH", "/other/providers/Microsoft.Resources/tags/default"),
    ("POST", "/rg/providers/Microsoft.Consumption/budgets"),
    ("PATCH", "/rg/providers/Microsoft.Resources/tags/default/../other"),
])
def test_unapproved_arm_routes_fail_closed(method, path):
    with pytest.raises(SafetyError):
        safe_arm_route(method, path, "/rg", "/rg/providers/DB/workspace")


def test_allowed_tags_and_read_only_cost_route():
    safe_arm_route("PATCH", "/rg/providers/Microsoft.Resources/tags/default?api-version=example",
                   "/rg", "/rg/providers/DB/workspace")
    safe_arm_route("POST", "/rg/providers/Microsoft.CostManagement/query?api-version=example",
                   "/rg", "/rg/providers/DB/workspace")


def test_governance_requires_explicit_apply():
    context = MagicMock(apply=False)
    with pytest.raises(SafetyError):
        apply_governance(context)
    context.client.current_user.me.assert_not_called()


@pytest.fixture
def fake_context(monkeypatch):
    import retail_hp_azure.phase2 as phase2

    monkeypatch.setattr(phase2, "record_evidence", lambda *args: None)
    context = MagicMock(spec=CloudContext)
    context.apply = True
    context.group = {"id": "/rg", "tags": {}}
    context.workspace = {"id": "/rg/providers/DB/workspace", "tags": {}}
    client = context.client = MagicMock()
    client.current_user.me.return_value = SimpleNamespace(
        id="test-bootstrap", user_name="test-user", groups=[SimpleNamespace(display="admins")],
    )
    accounts = {}
    workspaces = {}
    privileges = {}

    def account_api(method, path, **kwargs):
        if method == "GET" and path.endswith("/Groups"):
            name = kwargs["query"]["filter"].split('"')[1]
            # SCIM list results deliberately omit members; detail GET is required.
            return {"Resources": [{"id": accounts[name]["id"]}] if name in accounts else []}
        if method == "GET":
            return next(x for x in accounts.values() if x["id"] == path.rsplit("/", 1)[1])
        if method == "POST":
            body = kwargs["body"]
            accounts[body["displayName"]] = {"id": str(len(accounts) + 100), **body}
            return accounts[body["displayName"]]
        assert method == "PUT"
        entry = next(x for x in accounts.values() if x["id"] == path.rsplit("/", 1)[1])
        workspaces[entry["displayName"]] = SimpleNamespace(
            display_name=entry["displayName"], roles=[], id=entry["id"], members=[],
        )
        return {}

    def get_grants(kind, name):
        return SimpleNamespace(privilege_assignments=[
            SimpleNamespace(principal=g, privileges=ps)
            for g, ps in privileges.get((kind, name), {}).items()
        ])

    def update_grants(kind, name, changes):
        for change in changes:
            target = privileges.setdefault((kind, name), {}).setdefault(change.principal, [])
            target.extend(change.add)

    def arm(method, path, body):
        target = context.workspace if "/DB/" in path else context.group
        target["tags"] = {**target["tags"], **body["properties"]["tags"]}

    context.arm.side_effect = arm
    client.api_client.do.side_effect = account_api
    client.groups.list.side_effect = lambda: list(workspaces.values())
    client.grants.get.side_effect = get_grants
    client.grants.update.side_effect = update_grants
    schemas = {}
    volumes = {}

    def read(store, name):
        if name not in store:
            raise NotFound("test missing")
        return store[name]

    def create_schema(name, catalog, **kwargs):
        schemas[f"{catalog}.{name}"] = SimpleNamespace(
            comment=MARKER, owner="test-user", enable_predictive_optimization=None,
        )

    def create_volume(catalog, schema, name, volume_type, **kwargs):
        volumes[f"{catalog}.{schema}.{name}"] = SimpleNamespace(
            comment=MARKER, owner="test-user", volume_type=volume_type,
        )

    client.schemas.get.side_effect = lambda name: read(schemas, name)
    client.volumes.read.side_effect = lambda name: read(volumes, name)
    client.schemas.create.side_effect = create_schema
    client.volumes.create.side_effect = create_volume

    def update_schema(name, **kwargs):
        for key, value in kwargs.items():
            setattr(schemas[name], key, value)

    client.schemas.update.side_effect = update_schema
    client.volumes.update.side_effect = lambda name, owner: setattr(volumes[name], "owner", owner)
    return context


def test_governance_creates_only_declared_objects(fake_context):
    result = apply_governance(fake_context)
    assert fake_context.client.schemas.create.call_count == len(SCHEMAS)
    assert fake_context.client.volumes.create.call_count == 2
    assert fake_context.arm.call_count == 2
    assert result["paid_resources_created"] == 0
    assert result["cloud_mutations_performed"] is True
    assert fake_context.client.schemas.update.call_count == 16
    assert fake_context.client.volumes.update.call_count == 2
    assignments = [call for call in fake_context.client.api_client.do.call_args_list
                   if call.args[0] == "PUT"]
    assert len(assignments) == 4
    assert all(call.kwargs["body"] == {"permissions": ["USER"]} for call in assignments)
    assert all("admins" != action["name"] for action in result["actions"])


def test_unmanaged_existing_schema_is_not_modified(fake_context):
    fake_context.client.schemas.get.side_effect = None
    fake_context.client.schemas.get.return_value = SimpleNamespace(comment="unrelated")
    with pytest.raises(SafetyError, match="not project-managed"):
        apply_governance(fake_context)
    fake_context.client.schemas.create.assert_not_called()


def test_existing_schema_marker_is_explicit():
    assert "Phase 2" in MARKER
    assert "synthetic" in MARKER


def test_owner_budget_deferral_does_not_authorize_paid_platform_activation():
    root = Path(__file__).resolve().parents[2] / "azure_databricks"
    acceptance = json.loads((root / "evidence/phase_02/acceptance.json").read_text())
    assert acceptance["governance_complete"] is True
    assert acceptance["phase2_complete"] is False
    assert acceptance["phase3_authorized"] is False
    assert acceptance["paid_deployment_allowed"] is False
    decision = acceptance["owner_budget_deferral"]
    assert decision["decision"] == "DEFER_BUDGET_UNTIL_IT_CONFIRMS_CURRENCY"
    assert decision["budget_deployed"] is False
    assert decision["paid_compute_authorized_by_deferral"] is False
    policy = json.loads((root / "config/poc.json").read_text())
    assert policy["cost"]["paid_resource_creation_allowed"] is False


def test_second_governance_apply_is_noop_with_scim_list_omitting_members(fake_context):
    assert apply_governance(fake_context)["actions"]
    fake_context.client.api_client.do.reset_mock()
    fake_context.arm.reset_mock()
    result = apply_governance(fake_context)
    assert result["actions"] == []
    assert result["cloud_mutations_performed"] is False
    fake_context.arm.assert_not_called()
    assert all(call.args[0] == "GET" for call in fake_context.client.api_client.do.call_args_list)


def test_group_ownership_drift_fails_closed(fake_context):
    apply_governance(fake_context)
    fake_context.client.schemas.get(f"{CATALOG}.bronze").owner = "unrelated-owner"
    with pytest.raises(SafetyError, match="ownership drift"):
        apply_governance(fake_context)


def test_governance_verification_fails_for_excess_privileges(fake_context):
    from retail_hp_azure.safety import REQUIRED_TAGS

    client = fake_context.client
    def verify_account_api(method, path, **kwargs):
        if path.endswith("/Groups"):
            name = kwargs["query"]["filter"].split('"')[1]
            return {"Resources": [{"id": str(GROUPS.index(name))}]}
        return {"displayName": GROUPS[int(path.rsplit("/", 1)[1])], "roles": []}

    client.api_client.do.side_effect = verify_account_api
    client.groups.list.side_effect = None
    client.groups.list.return_value = [
        SimpleNamespace(display_name=g, id=str(i), roles=[], members=[])
        for i, g in enumerate((*GROUPS, "admins"))
    ]
    client.groups.get.return_value = SimpleNamespace(members=[])
    client.schemas.get.side_effect = None
    client.schemas.get.return_value = SimpleNamespace(
        comment=MARKER, owner="retail_hp_admins",
        enable_predictive_optimization=EnablePredictiveOptimization.DISABLE,
    )
    client.volumes.read.side_effect = None
    client.volumes.read.return_value = SimpleNamespace(
        comment=MARKER, owner="retail_hp_admins", volume_type=VolumeType.MANAGED,
    )
    client.warehouses.list.return_value = []
    fake_context.group["tags"] = REQUIRED_TAGS
    fake_context.workspace["tags"] = REQUIRED_TAGS

    def grants(kind, name):
        return SimpleNamespace(privilege_assignments=[
            SimpleNamespace(principal=g, privileges=[SimpleNamespace(value=p) for p in ps])
            for g, ps in grant_matrix()[(kind, name)].items()
        ])

    client.grants.get.side_effect = grants
    assert verify_governance(fake_context)["status"] == "PASS"
    client.grants.get.side_effect = None
    client.grants.get.return_value = SimpleNamespace(privilege_assignments=[
        SimpleNamespace(principal="retail_hp_viewers",
                        privileges=[SimpleNamespace(value="MODIFY")]),
    ])
    assert verify_governance(fake_context)["status"] == "FAIL"
