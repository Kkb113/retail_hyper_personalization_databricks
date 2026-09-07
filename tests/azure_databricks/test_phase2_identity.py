from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.sql import State, StatementState
from retail_hp_azure.phase2_compute import WAREHOUSE_NAME
from retail_hp_azure.phase2_identity import (
    IDENTITY_GROUP,
    IDENTITY_NAME,
    _ensure_identity,
    apply_and_test_workload_identity,
)
from retail_hp_azure.safety import REQUIRED_TAGS


def _warehouse(state: State) -> SimpleNamespace:
    return SimpleNamespace(
        id="test-warehouse-id",
        name=WAREHOUSE_NAME,
        cluster_size="2X-Small",
        enable_serverless_compute=True,
        min_num_clusters=1,
        max_num_clusters=1,
        auto_stop_mins=1,
        warehouse_type=SimpleNamespace(value="PRO"),
        state=state,
        tags=SimpleNamespace(custom_tags=[
            SimpleNamespace(key=key, value=value) for key, value in REQUIRED_TAGS.items()
        ]),
    )


def test_identity_bootstrap_creates_non_admin_and_adds_only_runtime_group():
    context = MagicMock()
    client = context.client
    principal = SimpleNamespace(
        id="test-principal-id", application_id="test-application-id",
        display_name=IDENTITY_NAME, active=True, roles=[],
    )
    client.service_principals.list.return_value = []
    client.service_principals.create.return_value = principal

    def account_api(method, path, **kwargs):
        if method == "GET" and path.endswith("/Groups"):
            return {"Resources": [{"id": "123"}]}
        if method == "GET":
            return {"displayName": IDENTITY_GROUP, "members": []}
        assert method == "PATCH"
        assert kwargs["body"]["Operations"][0]["value"] == [{"value": principal.id}]
        return {}

    client.api_client.do.side_effect = account_api
    client.groups.list.return_value = [SimpleNamespace(display_name="admins", id="admins-id")]
    client.groups.get.return_value = SimpleNamespace(members=[])
    created, result = _ensure_identity(context)
    assert created is principal
    assert result["identity_created"] is True
    assert result["runtime_group_membership_added"] is True
    assert result["workspace_admin"] is False
    client.service_principals.create.assert_called_once_with(
        active=True, display_name=IDENTITY_NAME
    )


def test_authenticated_identity_test_revokes_secret_and_stops(monkeypatch: pytest.MonkeyPatch):
    import databricks.sdk
    import retail_hp_azure.phase2_identity as identity_module

    monkeypatch.setenv("RETAIL_HP_PHASE2_PAID_TEST_CEILING_INR", "250")
    context = MagicMock()
    context.apply = True
    context.group = {"id": "/rg"}
    context.arm.return_value = {"properties": {
        "amount": 12000,
        "currentSpend": {"amount": 0.0, "unit": "INR"},
        "notifications": {str(index): {} for index in range(5)},
    }}
    principal = SimpleNamespace(
        id="test-principal-id", application_id="test-application-id",
        display_name=IDENTITY_NAME, active=True, roles=[],
    )
    monkeypatch.setattr(identity_module, "_ensure_identity", lambda _context: (
        principal,
        {"identity_created": True, "runtime_group_membership_added": True,
         "workspace_admin": False, "account_admin_roles": 0, "identity_cost_inr": 0},
    ))
    client = context.client
    client.warehouses.list.return_value = [_warehouse(State.STOPPED)]
    client.warehouses.get.side_effect = [_warehouse(State.STOPPED), _warehouse(State.RUNNING)]
    client.warehouses.wait_get_warehouse_stopped.return_value = _warehouse(State.STOPPED)
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="test-secret-id", secret="memory-only-test-value"  # noqa: S106 - inert fixture
    )
    client.service_principal_secrets_proxy.list.return_value = []
    workload = MagicMock()
    workload.current_user.me.return_value = SimpleNamespace(
        id=principal.id, user_name=principal.application_id,
        groups=[SimpleNamespace(display=IDENTITY_GROUP)],
    )
    workload.schemas.get.return_value = SimpleNamespace(name="serving")
    workload.tables.list.return_value = []
    workload.volumes.read.side_effect = NotFound("hidden")
    workload.statement_execution.execute_statement.return_value = SimpleNamespace(
        status=SimpleNamespace(state=StatementState.SUCCEEDED)
    )
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **_kwargs: workload)

    result = apply_and_test_workload_identity(context)
    assert result["status"] == "PASS"
    assert result["authenticated_checks_passed"] == 6
    assert result["temporary_oauth_secret_revoked"] is True
    assert result["oauth_secret_recorded"] is False
    assert result["identity_identifiers_recorded"] is False
    assert result["warehouse_final_state"] == "STOPPED"
    client.service_principal_secrets_proxy.delete.assert_called_once_with(
        principal.id, "test-secret-id"
    )
    client.warehouses.stop.assert_called_once_with("test-warehouse-id")


def test_committed_identity_evidence_closes_phase2_without_secret_or_running_compute():
    evidence = Path(__file__).resolve().parents[2] / "azure_databricks/evidence/phase_02"
    identity = json.loads((evidence / "workload_identity_verification.json").read_text(
        encoding="utf-8"
    ))
    acceptance = json.loads((evidence / "acceptance.json").read_text(encoding="utf-8"))
    assert identity["status"] == "PASS"
    assert identity["authenticated_checks_passed"] == 6
    assert identity["temporary_oauth_secret_revoked"] is True
    assert identity["oauth_secret_recorded"] is False
    assert identity["warehouse_final_state"] == "STOPPED"
    assert acceptance["phase2_complete"] is True
    assert acceptance["phase3_authorized"] is False
    assert acceptance["live_validation"][
        "conservative_total_phase2_test_estimate_inr_pre_tax"
    ] < 250
