"""Low-cost, read-only Phase 4 validation on the auto-stopped project warehouse."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import timedelta
from typing import Any

from retail_hp_azure.phase2 import CATALOG, CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import (
    DBU_PER_HOUR,
    RETAIL_DBU_HOURLY_INR,
    WAREHOUSE_NAME,
    _stop_and_verify,
    _verify_budget,
    _verify_warehouse_contract,
)
from retail_hp_azure.phase4 import record_evidence, validation_sql
from retail_hp_azure.safety import SafetyError, require

MAX_RUNTIME_MINUTES = 3.0
RISK_MULTIPLIER = 2.0
VALIDATION_CEILING_INR = 30.0


def validation_plan() -> dict[str, Any]:
    base = RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * MAX_RUNTIME_MINUTES / 60
    guarded = base * RISK_MULTIPLIER
    require(guarded < VALIDATION_CEILING_INR, "SQL validation estimate exceeds ceiling")
    return {
        "version": "azure_phase4_sql_validation_plan_v1",
        "warehouse": "existing_2X-Small_serverless",
        "read_only_query_count": 1,
        "max_runtime_minutes": MAX_RUNTIME_MINUTES,
        "auto_stop_minutes": 1,
        "persistent_job_created": False,
        "base_estimate_inr_pre_tax": round(base, 4),
        "risk_multiplier": RISK_MULTIPLIER,
        "guarded_estimate_inr_pre_tax": round(guarded, 4),
        "validation_ceiling_inr": VALIDATION_CEILING_INR,
        "hard_invoice_cap_guaranteed": False,
    }


def _validate_row(row: list[str | None], names: list[str]) -> dict[str, int]:
    require(len(row) == len(names), "SQL validation result shape drift")
    values = {name: int(value or 0) for name, value in zip(names, row, strict=True)}
    expected = {
        "bronze_rows": 275_630,
        "silver_rows": 275_630,
        "duplicate_key_groups": 0,
        "profile_rows": 5_000,
        "behavior_rows": 33_402,
        "behavior_customers": 4_697,
        "product_rows": 3_000,
        "customer_360_rows": 5_000,
        "product_360_rows": 3_000,
        "recommendation_rows": 15_930,
        "future_leakage_rows": 0,
        "invalid_product_rows": 0,
        "invalid_inventory_rows": 0,
        "invalid_promotion_rows": 0,
        "invalid_order_rows": 0,
        "invalid_weather_rows": 0,
        "orphan_product_category_rows": 0,
        "orphan_product_brand_rows": 0,
        "orphan_customer_region_rows": 0,
        "orphan_order_line_rows": 0,
        "orphan_event_customer_rows": 0,
        "orphan_event_product_rows": 0,
        "feature_primary_keys": 3,
    }
    for key, expected_value in expected.items():
        require(values.get(key) == expected_value, f"SQL validation failed for {key}")
    require(values.get("dictionary_rows", 0) > 0, "Data dictionary is empty")
    require(
        0 < values.get("eligible_product_rows", 0) <= 3_000,
        "Eligible product view is empty or oversized",
    )
    return values


def run_validation(context: CloudContext) -> dict[str, Any]:
    from databricks.sdk.service.sql import State, StatementState

    require(context.apply, "Phase 4 SQL validation requires explicit apply context")
    require(
        os.environ.get("RETAIL_HP_PHASE4_SQL_VALIDATION_CEILING_INR") == "30",
        "Exact Phase 4 SQL validation approval is required",
    )
    plan = validation_plan()
    budget = _verify_budget(context)
    before = inspect_compute(context)
    require(
        before["cluster_count"] == 0 and before["job_count"] == 0,
        "Unexpected compute before SQL validation",
    )
    candidates = [w for w in context.client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(candidates) == 1, "Project warehouse is unavailable")
    candidate_id = candidates[0].id
    require(candidate_id is not None and candidate_id != "", "Project warehouse has no ID")
    warehouse_id = str(candidate_id)
    warehouse = context.client.warehouses.get(warehouse_id)
    _verify_warehouse_contract(warehouse)
    require(warehouse.state == State.STOPPED, "SQL validation must begin with stopped warehouse")
    finished = threading.Event()
    deadline_fired = threading.Event()
    started = time.monotonic()

    def stop_at_deadline() -> None:
        if not finished.wait(MAX_RUNTIME_MINUTES * 60):
            deadline_fired.set()
            try:
                context.client.warehouses.stop(warehouse_id)
            except Exception:
                return

    controller = threading.Thread(target=stop_at_deadline, daemon=True)
    controller.start()
    final_state = "UNKNOWN"
    values: dict[str, int] | None = None
    try:
        context.client.warehouses.start(warehouse_id).result(timeout=timedelta(minutes=2))
        statement = context.client.statement_execution.execute_statement(
            validation_sql(),
            warehouse_id,
            catalog=CATALOG,
            schema="gold",
            row_limit=1,
            byte_limit=16_384,
            wait_timeout="50s",
        )
        require(
            statement.status is not None and statement.status.state == StatementState.SUCCEEDED,
            "Phase 4 SQL validation statement failed or exceeded its wait",
        )
        manifest = statement.manifest
        require(manifest is not None, "SQL validation manifest is unavailable")
        if manifest is None:  # pragma: no cover - narrowing after fail-closed require
            raise SafetyError("SQL validation manifest is unavailable")
        schema = manifest.schema
        require(schema is not None, "SQL validation schema is unavailable")
        if schema is None:  # pragma: no cover - narrowing after fail-closed require
            raise SafetyError("SQL validation schema is unavailable")
        columns = schema.columns or []
        names = [str(column.name) for column in columns]
        result = statement.result
        require(result is not None, "SQL validation result is unavailable")
        if result is None:  # pragma: no cover - narrowing after fail-closed require
            raise SafetyError("SQL validation result is unavailable")
        rows = result.data_array or []
        require(len(rows) == 1, "SQL validation row is unavailable")
        values = _validate_row(list(rows[0]), names)
    finally:
        finished.set()
        controller.join(timeout=1)
        final_state = _stop_and_verify(context.client, warehouse_id)
    elapsed = min(time.monotonic() - started, MAX_RUNTIME_MINUTES * 60)
    require(final_state == "STOPPED", "Warehouse did not stop after Phase 4 validation")
    require(values is not None, "SQL validation values are unavailable")
    return {
        "version": "azure_phase4_sql_validation_v1",
        "status": "PASS",
        "metrics": values,
        "query_count": 1,
        "read_only": True,
        "warehouse_final_state": final_state,
        "controller_fired": deadline_fired.is_set(),
        "elapsed_seconds": round(elapsed, 2),
        "estimated_elapsed_cost_inr_pre_tax": round(
            RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * elapsed / 3600, 4
        ),
        "guarded_estimate_inr_pre_tax": plan["guarded_estimate_inr_pre_tax"],
        "budget_admission": budget,
        "hard_invoice_cap_guaranteed": False,
        "identifiers_recorded": False,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run"])
    args = parser.parse_args()
    try:
        result = (
            validation_plan()
            if args.command == "plan"
            else run_validation(CloudContext(apply=True))
        )
        if args.command == "run":
            record_evidence("sql_validation.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(f"Phase 4 SQL validation failed: {type(exc).__name__}; {detail}") from None
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
