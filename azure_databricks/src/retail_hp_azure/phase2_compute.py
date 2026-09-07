"""Bounded Phase 2 SQL warehouse activation and verification.

This module is the only Phase 2 path allowed to start paid compute. Identifiers and
authentication material remain in memory and are never written to evidence.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from retail_hp_azure.phase2 import AZURE_BUDGET_NAME, CATALOG, GROUPS
from retail_hp_azure.safety import REQUIRED_TAGS, SafetyError, require

if TYPE_CHECKING:
    from retail_hp_azure.phase2 import CloudContext

WAREHOUSE_NAME = "retail-hp-poc-sql"
WAREHOUSE_SIZE = "2X-Small"
AUTO_STOP_MINUTES = 1
MAX_RUNTIME_MINUTES = 12
IDLE_TEST_DEADLINE_MINUTES = 4
APPROVED_CEILING_INR = 250.0
RETAIL_DBU_HOURLY_INR = 66.8824
DBU_PER_HOUR = 4
RISK_MULTIPLIER = 3.5
ROOT = Path(__file__).resolve().parents[2]


def paid_test_plan() -> dict[str, Any]:
    estimated = RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * MAX_RUNTIME_MINUTES / 60
    guarded = estimated * RISK_MULTIPLIER
    require(guarded < APPROVED_CEILING_INR, "Guarded estimate exceeds owner ceiling")
    return {
        "warehouse_name": WAREHOUSE_NAME,
        "warehouse_size": WAREHOUSE_SIZE,
        "serverless": True,
        "min_clusters": 1,
        "max_clusters": 1,
        "auto_stop_minutes": AUTO_STOP_MINUTES,
        "wall_clock_deadline_minutes": MAX_RUNTIME_MINUTES,
        "estimated_max_runtime_cost_inr_pre_tax": round(estimated, 4),
        "risk_multiplier": RISK_MULTIPLIER,
        "guarded_estimate_inr": round(guarded, 4),
        "owner_approved_ceiling_inr": APPROVED_CEILING_INR,
        "azure_budget_is_hard_cap": False,
        "controller": "in-process deadline + finally stop + native idle auto-stop",
    }


def _require_runtime_approval() -> None:
    require(
        os.environ.get("RETAIL_HP_PHASE2_PAID_TEST_CEILING_INR") == "250",
        "Exact runtime approval ceiling 250 is required",
    )


def _verify_budget(context: CloudContext) -> dict[str, Any]:
    path = (
        context.group["id"]
        + "/providers/Microsoft.Consumption/budgets/"
        + AZURE_BUDGET_NAME
        + "?api-version=2024-08-01"
    )
    properties = context.arm("GET", path).get("properties", {})
    spend = properties.get("currentSpend") or {}
    require(properties.get("amount") == 12000, "Expected INR 12,000 budget is unavailable")
    require(spend.get("unit") == "INR", "Budget currency is not INR")
    require(len(properties.get("notifications", {})) == 5, "Budget notifications are incomplete")
    current = float(spend.get("amount", 0))
    require(current <= 12000 - APPROVED_CEILING_INR, "Budget has insufficient test headroom")
    return {"amount_inr": 12000, "current_spend_inr": current, "notification_count": 5}


def _tag_values(warehouse: Any) -> dict[str, str]:
    tags = getattr(warehouse, "tags", None)
    return {
        str(item.key): str(item.value)
        for item in (getattr(tags, "custom_tags", None) or [])
    }


def _verify_warehouse_contract(warehouse: Any) -> None:
    warehouse_type = getattr(getattr(warehouse, "warehouse_type", None), "value", None)
    require(warehouse.name == WAREHOUSE_NAME, "Warehouse name drift")
    require(warehouse.cluster_size == WAREHOUSE_SIZE, "Warehouse size drift")
    require(warehouse.enable_serverless_compute is True, "Warehouse is not serverless")
    require(warehouse.min_num_clusters == 1, "Warehouse minimum cluster drift")
    require(warehouse.max_num_clusters == 1, "Warehouse maximum cluster drift")
    require(warehouse.auto_stop_mins == AUTO_STOP_MINUTES, "Warehouse auto-stop drift")
    require(warehouse_type == "PRO", "Warehouse type drift")
    actual_tags = _tag_values(warehouse)
    require(
        all(actual_tags.get(key) == value for key, value in REQUIRED_TAGS.items()),
        "Warehouse cost tags are incomplete",
    )


def _effective_identity_checks(client: Any) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    targets = {
        "retail_hp_engineers": {"USE_SCHEMA", "SELECT", "MODIFY", "EXECUTE"},
        "retail_hp_viewers": {"USE_SCHEMA", "SELECT", "EXECUTE"},
        "retail_hp_app_runtime": {"USE_SCHEMA", "SELECT", "EXECUTE"},
    }
    for principal, expected in targets.items():
        response = client.grants.get_effective(
            "schema", f"{CATALOG}.serving", principal=principal
        )
        assignments = response.privilege_assignments or []
        actual = {
            privilege.privilege.value
            for assignment in assignments
            for privilege in (assignment.privileges or [])
            if privilege.privilege is not None
        }
        checks[f"serving_effective:{principal}"] = expected <= actual
        if principal in GROUPS[2:]:
            checks[f"serving_no_modify:{principal}"] = "MODIFY" not in actual
            for schema in ("bronze", "silver", "features", "ml", "gold", "agent", "monitoring"):
                raw = client.grants.get_effective(
                    "schema", f"{CATALOG}.{schema}", principal=principal
                )
                raw_privileges = {
                    privilege.privilege.value
                    for assignment in (raw.privilege_assignments or [])
                    for privilege in (assignment.privileges or [])
                    if privilege.privilege is not None
                }
                checks[f"no_raw_access:{principal}:{schema}"] = not (
                    {"SELECT", "MODIFY", "READ_VOLUME", "WRITE_VOLUME"} & raw_privileges
                )
    return checks


def _stop_and_verify(client: Any, warehouse_id: str) -> str:
    from databricks.sdk.service.sql import State

    current = client.warehouses.get(warehouse_id)
    if current.state not in {State.STOPPED, State.STOPPING}:
        client.warehouses.stop(warehouse_id).result(timeout=timedelta(minutes=3))
    stopped = client.warehouses.wait_get_warehouse_stopped(
        warehouse_id, timeout=timedelta(minutes=3)
    )
    require(stopped.state == State.STOPPED, "Warehouse did not reach STOPPED state")
    return "STOPPED"


def _warehouse_acl_checks(client: Any, warehouse_id: str) -> dict[str, bool]:
    permissions = client.warehouses.get_permissions(warehouse_id)
    by_group = {
        entry.group_name: {
            permission.permission_level.value
            for permission in (entry.all_permissions or [])
            if permission.permission_level is not None
        }
        for entry in (permissions.access_control_list or [])
        if entry.group_name in GROUPS
    }
    required = {
        "retail_hp_admins": "CAN_MANAGE",
        "retail_hp_engineers": "CAN_MONITOR",
        "retail_hp_viewers": "CAN_USE",
        "retail_hp_app_runtime": "CAN_USE",
    }
    checks = {
        f"warehouse_acl:{group}": level in by_group.get(group, set())
        for group, level in required.items()
    }
    checks["engineers_not_manager"] = not (
        {"CAN_MANAGE", "IS_OWNER"} & by_group.get("retail_hp_engineers", set())
    )
    for group in GROUPS[2:]:
        checks[f"consumer_not_monitor_or_manager:{group}"] = not (
            {"CAN_MONITOR", "CAN_MANAGE", "IS_OWNER"} & by_group.get(group, set())
        )
    return checks


def apply_warehouse_acl(context: CloudContext) -> dict[str, Any]:
    """Apply group-only least-privilege access without starting the warehouse."""
    from databricks.sdk.service.sql import (
        State,
        WarehouseAccessControlRequest,
        WarehousePermissionLevel,
    )

    require(context.apply, "Warehouse ACL apply requires explicit apply context")
    client = context.client
    candidates = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(candidates) == 1, "Exactly one stopped project warehouse is required")
    warehouse_id = candidates[0].id
    require(bool(warehouse_id), "Project warehouse ID is missing")
    warehouse = client.warehouses.get(str(warehouse_id))
    _verify_warehouse_contract(warehouse)
    require(warehouse.state == State.STOPPED, "Warehouse ACL changes require STOPPED compute")
    client.warehouses.update_permissions(str(warehouse_id), access_control_list=[
        WarehouseAccessControlRequest(
            group_name="retail_hp_admins",
            permission_level=WarehousePermissionLevel.CAN_MANAGE,
        ),
        WarehouseAccessControlRequest(
            group_name="retail_hp_engineers",
            permission_level=WarehousePermissionLevel.CAN_MONITOR,
        ),
        WarehouseAccessControlRequest(
            group_name="retail_hp_viewers",
            permission_level=WarehousePermissionLevel.CAN_USE,
        ),
        WarehouseAccessControlRequest(
            group_name="retail_hp_app_runtime",
            permission_level=WarehousePermissionLevel.CAN_USE,
        ),
    ])
    checks = _warehouse_acl_checks(client, str(warehouse_id))
    require(all(checks.values()), "Warehouse least-privilege ACL verification failed")
    return {
        "status": "PASS", "warehouse_final_state": "STOPPED",
        "checks_passed": len(checks), "checks_failed": 0,
        "project_group_entries": 4, "identifiers_recorded": False,
        "cloud_mutations_performed": True,
    }


def run_paid_test(context: CloudContext) -> dict[str, Any]:
    """Create/reuse one guarded serverless warehouse, test it, and always stop it."""
    from databricks.sdk.service.sql import (
        CreateWarehouseRequestWarehouseType,
        EndpointTagPair,
        EndpointTags,
        State,
        StatementState,
    )

    require(context.apply, "Paid test requires explicit apply context")
    _require_runtime_approval()
    plan = paid_test_plan()
    budget = _verify_budget(context)
    client = context.client
    candidates = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(candidates) <= 1, "Duplicate project warehouse requires manual review")
    warehouse_id: str | None = None
    stop_event = threading.Event()
    controller_fired = threading.Event()
    controller_stop_failed = threading.Event()
    started_at = time.monotonic()

    def deadline_stop() -> None:
        if stop_event.wait(MAX_RUNTIME_MINUTES * 60):
            return
        controller_fired.set()
        if warehouse_id:
            try:
                client.warehouses.stop(warehouse_id)
            except Exception:
                # The finally path and native idle auto-stop remain active fallbacks.
                controller_stop_failed.set()

    controller = threading.Thread(target=deadline_stop, name="retail-hp-deadline-stop", daemon=True)
    controller.start()
    result: dict[str, Any] = {
        "status": "FAIL",
        "scope_verified": True,
        "plan": plan,
        "budget_admission": budget,
        "warehouse_created": False,
        "warehouse_final_state": "UNKNOWN",
        "controller_fired": False,
        "sql_smoke_test": "NOT_RUN",
        "identity_effective_grant_tests": "NOT_RUN",
        "representative_principal_authentication": "NOT_RUN_NO_CLIENT_WORKLOAD_CREDENTIAL",
    }
    try:
        if candidates:
            warehouse_id = candidates[0].id
            require(bool(warehouse_id), "Existing warehouse ID is missing")
            current = client.warehouses.get(str(warehouse_id))
            _verify_warehouse_contract(current)
            if current.state != State.RUNNING:
                client.warehouses.start(str(warehouse_id)).result(timeout=timedelta(minutes=8))
        else:
            waiter = client.warehouses.create(
                name=WAREHOUSE_NAME,
                cluster_size=WAREHOUSE_SIZE,
                warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
                enable_serverless_compute=True,
                enable_photon=True,
                min_num_clusters=1,
                max_num_clusters=1,
                auto_stop_mins=AUTO_STOP_MINUTES,
                tags=EndpointTags(custom_tags=[
                    EndpointTagPair(key=key, value=value)
                    for key, value in sorted(REQUIRED_TAGS.items())
                ]),
            )
            warehouse_id = getattr(waiter.response, "id", None)
            require(bool(warehouse_id), "Created warehouse ID is missing")
            waiter.result(timeout=timedelta(minutes=8))
            result["warehouse_created"] = True
        running = client.warehouses.get(str(warehouse_id))
        _verify_warehouse_contract(running)
        require(running.state == State.RUNNING, "Warehouse is not RUNNING for smoke test")
        require(
            time.monotonic() - started_at < MAX_RUNTIME_MINUTES * 60,
            "Runtime deadline reached",
        )
        statement = client.statement_execution.execute_statement(
            "SELECT current_catalog(), current_schema(), 1",
            str(warehouse_id),
            catalog=CATALOG,
            schema="serving",
            row_limit=1,
            byte_limit=4096,
            wait_timeout="50s",
        )
        status = statement.status
        require(status is not None, "SQL statement status is missing")
        require(status is not None and status.state == StatementState.SUCCEEDED,
                "SQL smoke test failed")
        result["sql_smoke_test"] = "PASS"
        identity_checks = _effective_identity_checks(client)
        require(all(identity_checks.values()), "Effective identity grant test failed")
        result["identity_effective_grant_tests"] = {
            "status": "PASS", "checks_passed": len(identity_checks), "checks_failed": 0
        }
        result["status"] = "PASS"
        return result
    finally:
        stop_event.set()
        controller.join(timeout=1)
        if warehouse_id:
            try:
                result["warehouse_final_state"] = _stop_and_verify(client, str(warehouse_id))
            except Exception as exc:
                result["warehouse_final_state"] = "STOP_VERIFICATION_FAILED"
                if isinstance(exc, SafetyError):
                    result["safe_stop_error"] = str(exc)
                result["status"] = "FAIL"
        result["controller_fired"] = controller_fired.is_set()
        result["controller_stop_failed"] = controller_stop_failed.is_set()
        elapsed = min(time.monotonic() - started_at, MAX_RUNTIME_MINUTES * 60)
        result["elapsed_seconds"] = round(elapsed, 2)
        result["estimated_elapsed_cost_inr_pre_tax"] = round(
            RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * elapsed / 3600, 4
        )
        result["hard_invoice_cap_guaranteed"] = False
        result["identifiers_recorded"] = False


def run_native_idle_shutdown_test(context: CloudContext) -> dict[str, Any]:
    """Observe the API-configured one-minute idle stop with a four-minute fallback."""
    from databricks.sdk.service.sql import State

    require(context.apply, "Idle shutdown test requires explicit apply context")
    _require_runtime_approval()
    budget = _verify_budget(context)
    client = context.client
    candidates = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(candidates) == 1, "Exactly one project warehouse is required")
    warehouse_id = candidates[0].id
    require(bool(warehouse_id), "Project warehouse ID is missing")
    warehouse = client.warehouses.get(str(warehouse_id))
    _verify_warehouse_contract(warehouse)
    require(warehouse.state == State.STOPPED, "Idle test must begin with STOPPED compute")
    fallback_fired = threading.Event()
    finished = threading.Event()
    started_at = time.monotonic()

    def fallback_stop() -> None:
        if finished.wait(IDLE_TEST_DEADLINE_MINUTES * 60):
            return
        fallback_fired.set()
        try:
            client.warehouses.stop(str(warehouse_id))
        except Exception:
            return

    controller = threading.Thread(target=fallback_stop, name="retail-hp-idle-fallback", daemon=True)
    controller.start()
    result: dict[str, Any] = {
        "status": "FAIL", "scope_verified": True, "budget_admission": budget,
        "auto_stop_minutes": AUTO_STOP_MINUTES,
        "fallback_deadline_minutes": IDLE_TEST_DEADLINE_MINUTES,
        "native_idle_stop_observed": False, "warehouse_final_state": "UNKNOWN",
    }
    try:
        client.warehouses.start(str(warehouse_id)).result(timeout=timedelta(minutes=3))
        stopped = client.warehouses.wait_get_warehouse_stopped(
            str(warehouse_id), timeout=timedelta(minutes=5)
        )
        require(stopped.state == State.STOPPED, "Native idle stop did not reach STOPPED")
        require(not fallback_fired.is_set(), "Native idle stop exceeded fallback deadline")
        result["native_idle_stop_observed"] = True
        result["status"] = "PASS"
        return result
    finally:
        finished.set()
        controller.join(timeout=1)
        result["fallback_fired"] = fallback_fired.is_set()
        result["warehouse_final_state"] = _stop_and_verify(client, str(warehouse_id))
        elapsed = min(time.monotonic() - started_at, IDLE_TEST_DEADLINE_MINUTES * 60)
        result["elapsed_seconds"] = round(elapsed, 2)
        result["estimated_elapsed_cost_inr_pre_tax"] = round(
            RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * elapsed / 3600, 4
        )
        result["hard_invoice_cap_guaranteed"] = False
        result["identifiers_recorded"] = False


def stop_project_warehouse(context: CloudContext) -> dict[str, Any]:
    """Emergency idempotent stop; exact project name only, never a broad stop."""
    client = context.client
    candidates = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(candidates) <= 1, "Duplicate project warehouse requires manual review")
    if not candidates:
        return {"status": "PASS", "matched": 0, "final_state": "ABSENT"}
    warehouse_id = candidates[0].id
    require(bool(warehouse_id), "Project warehouse ID is missing")
    final = _stop_and_verify(client, str(warehouse_id))
    return {"status": "PASS", "matched": 1, "final_state": final, "identifiers_recorded": False}
