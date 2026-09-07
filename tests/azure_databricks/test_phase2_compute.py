from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import State, StatementState
from retail_hp_azure.phase2 import CATALOG
from retail_hp_azure.phase2_compute import (
    APPROVED_CEILING_INR,
    MAX_RUNTIME_MINUTES,
    WAREHOUSE_NAME,
    _verify_warehouse_contract,
    apply_warehouse_acl,
    paid_test_plan,
    run_native_idle_shutdown_test,
    run_paid_test,
    stop_project_warehouse,
)
from retail_hp_azure.safety import REQUIRED_TAGS, SafetyError


def _warehouse(state: State = State.RUNNING) -> SimpleNamespace:
    return SimpleNamespace(
        id="redacted-test-id",
        name=WAREHOUSE_NAME,
        cluster_size="2X-Small",
        enable_serverless_compute=True,
        enable_photon=True,
        min_num_clusters=1,
        max_num_clusters=1,
        auto_stop_mins=1,
        warehouse_type=SimpleNamespace(value="PRO"),
        state=state,
        tags=SimpleNamespace(custom_tags=[
            SimpleNamespace(key=key, value=value) for key, value in REQUIRED_TAGS.items()
        ]),
    )


def _context(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setenv("RETAIL_HP_PHASE2_PAID_TEST_CEILING_INR", "250")
    context = MagicMock()
    context.apply = True
    context.group = {"id": "/rg"}
    context.arm.return_value = {"properties": {
        "amount": 12000,
        "currentSpend": {"amount": 0.0, "unit": "INR"},
        "notifications": {str(index): {} for index in range(5)},
    }}
    client = context.client
    client.warehouses.list.return_value = []
    client.warehouses.create.return_value.response.id = "redacted-test-id"
    client.warehouses.get.return_value = _warehouse()
    client.warehouses.wait_get_warehouse_stopped.return_value = _warehouse(State.STOPPED)
    client.statement_execution.execute_statement.return_value = SimpleNamespace(
        status=SimpleNamespace(state=StatementState.SUCCEEDED)
    )

    def effective(_kind: str, name: str, *, principal: str) -> SimpleNamespace:
        if name == f"{CATALOG}.serving":
            values = ["USE_SCHEMA", "SELECT", "EXECUTE"]
            if principal == "retail_hp_engineers":
                values.append("MODIFY")
        else:
            values = []
        return SimpleNamespace(privilege_assignments=[SimpleNamespace(
            privileges=[SimpleNamespace(privilege=SimpleNamespace(value=value))
                        for value in values]
        )])

    client.grants.get_effective.side_effect = effective
    return context


def test_paid_plan_stays_below_250_inr_with_risk_multiplier():
    plan = paid_test_plan()
    assert plan["owner_approved_ceiling_inr"] == APPROVED_CEILING_INR == 250
    assert plan["wall_clock_deadline_minutes"] == MAX_RUNTIME_MINUTES == 12
    assert plan["guarded_estimate_inr"] < APPROVED_CEILING_INR
    assert plan["serverless"] is True
    assert plan["auto_stop_minutes"] == 1
    assert plan["max_clusters"] == 1
    assert plan["azure_budget_is_hard_cap"] is False


def test_paid_test_requires_exact_runtime_approval(monkeypatch: pytest.MonkeyPatch):
    context = _context(monkeypatch)
    monkeypatch.setenv("RETAIL_HP_PHASE2_PAID_TEST_CEILING_INR", "251")
    with pytest.raises(SafetyError, match="Exact runtime approval ceiling 250"):
        run_paid_test(context)
    context.client.warehouses.create.assert_not_called()


def test_warehouse_contract_rejects_size_or_shutdown_drift():
    warehouse = _warehouse()
    warehouse.cluster_size = "Small"
    with pytest.raises(SafetyError, match="size drift"):
        _verify_warehouse_contract(warehouse)
    warehouse = _warehouse()
    warehouse.auto_stop_mins = 10
    with pytest.raises(SafetyError, match="auto-stop drift"):
        _verify_warehouse_contract(warehouse)


def test_paid_test_creates_only_bounded_warehouse_and_always_stops(monkeypatch):
    context = _context(monkeypatch)
    result = run_paid_test(context)
    assert result["status"] == "PASS"
    assert result["warehouse_created"] is True
    assert result["warehouse_final_state"] == "STOPPED"
    assert result["sql_smoke_test"] == "PASS"
    assert result["identity_effective_grant_tests"]["status"] == "PASS"
    assert result["representative_principal_authentication"].startswith("NOT_RUN")
    kwargs = context.client.warehouses.create.call_args.kwargs
    assert kwargs["cluster_size"] == "2X-Small"
    assert kwargs["enable_serverless_compute"] is True
    assert kwargs["min_num_clusters"] == kwargs["max_num_clusters"] == 1
    assert kwargs["auto_stop_mins"] == 1
    context.client.warehouses.stop.assert_called_once_with("redacted-test-id")


def test_sql_failure_still_stops_warehouse(monkeypatch):
    context = _context(monkeypatch)
    context.client.statement_execution.execute_statement.return_value = SimpleNamespace(
        status=SimpleNamespace(state=StatementState.FAILED)
    )
    with pytest.raises(SafetyError, match="SQL smoke test failed"):
        run_paid_test(context)
    context.client.warehouses.stop.assert_called_once_with("redacted-test-id")
    context.client.warehouses.wait_get_warehouse_stopped.assert_called_once()


def test_budget_headroom_failure_prevents_create(monkeypatch):
    context = _context(monkeypatch)
    context.arm.return_value["properties"]["currentSpend"]["amount"] = 11751
    with pytest.raises(SafetyError, match="insufficient test headroom"):
        run_paid_test(context)
    context.client.warehouses.create.assert_not_called()


def test_emergency_stop_is_exact_name_scoped(monkeypatch):
    context = _context(monkeypatch)
    unrelated = SimpleNamespace(id="other-id", name="other-warehouse")
    context.client.warehouses.list.return_value = [unrelated, _warehouse()]
    result = stop_project_warehouse(context)
    assert result["matched"] == 1
    assert result["final_state"] == "STOPPED"
    context.client.warehouses.stop.assert_called_once_with("redacted-test-id")


def test_warehouse_acl_is_group_only_and_least_privilege(monkeypatch):
    context = _context(monkeypatch)
    context.client.warehouses.list.return_value = [_warehouse(State.STOPPED)]
    context.client.warehouses.get.return_value = _warehouse(State.STOPPED)
    levels = {
        "retail_hp_admins": ["CAN_MANAGE"],
        "retail_hp_engineers": ["CAN_MONITOR", "CAN_USE", "CAN_VIEW"],
        "retail_hp_viewers": ["CAN_USE", "CAN_VIEW"],
        "retail_hp_app_runtime": ["CAN_USE", "CAN_VIEW"],
    }
    context.client.warehouses.get_permissions.return_value = SimpleNamespace(
        access_control_list=[SimpleNamespace(
            group_name=group,
            all_permissions=[SimpleNamespace(permission_level=SimpleNamespace(value=value))
                             for value in values],
        ) for group, values in levels.items()]
    )
    result = apply_warehouse_acl(context)
    assert result["status"] == "PASS"
    assert result["warehouse_final_state"] == "STOPPED"
    acl = context.client.warehouses.update_permissions.call_args.kwargs["access_control_list"]
    assert {entry.group_name for entry in acl} == set(levels)
    assert all(entry.user_name is None and entry.service_principal_name is None for entry in acl)


def test_native_idle_shutdown_observes_stop_without_fallback(monkeypatch):
    context = _context(monkeypatch)
    context.client.warehouses.list.return_value = [_warehouse(State.STOPPED)]
    context.client.warehouses.get.return_value = _warehouse(State.STOPPED)
    result = run_native_idle_shutdown_test(context)
    assert result["status"] == "PASS"
    assert result["native_idle_stop_observed"] is True
    assert result["fallback_fired"] is False
    assert result["warehouse_final_state"] == "STOPPED"
    context.client.warehouses.start.assert_called_once_with("redacted-test-id")


def test_committed_live_evidence_stays_below_approved_ceiling_and_stopped():
    evidence = Path(__file__).resolve().parents[2] / "azure_databricks/evidence/phase_02"
    pricing = json.loads((evidence / "pricing_snapshot.json").read_text(encoding="utf-8"))
    acceptance = json.loads((evidence / "acceptance.json").read_text(encoding="utf-8"))
    live = json.loads((evidence / "warehouse_live_test.json").read_text(encoding="utf-8"))
    idle = json.loads((evidence / "warehouse_idle_shutdown_test.json").read_text(
        encoding="utf-8"
    ))
    assert pricing["approved_test_ceiling_inr"] == 250
    assert pricing["implemented_guarded_estimate_inr"] < 250
    assert acceptance["live_validation"]["conservative_total_test_estimate_inr_pre_tax"] < 250
    assert live["warehouse_final_state"] == "STOPPED"
    assert idle["native_idle_stop_observed"] is True
    assert idle["warehouse_final_state"] == "STOPPED"
