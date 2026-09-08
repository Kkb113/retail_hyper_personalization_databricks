from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from retail_hp_azure.phase4 import (
    DICTIONARY_TABLE,
    FEATURE_TABLES,
    GOLD_VIEWS,
    inspect_lakehouse,
    lakehouse_plan,
    repair_lakehouse_owners,
    snake_case,
    source_specs,
    validate_local_features,
    validation_sql,
)
from retail_hp_azure.phase4_runtime import NOTEBOOK_SOURCE, build_job_plan
from retail_hp_azure.phase4_sql_validation import _validate_row, validation_plan


class FakeTables:
    def __init__(self, names: set[str]) -> None:
        self.names = names
        self.owner = "retail_hp_admins"

    def list(self, *, catalog_name: str, schema_name: str) -> list[Any]:
        prefix = f"{catalog_name}.{schema_name}."
        return [
            SimpleNamespace(full_name=name, owner=self.owner)
            for name in self.names
            if name.startswith(prefix)
        ]

    def update(self, full_name: str, *, owner: str) -> None:
        assert full_name in self.names
        self.owner = owner


def test_phase4_plan_is_complete_scoped_and_non_persistent() -> None:
    result = lakehouse_plan()
    assert result["status"] == "PASS"
    assert result["source"] == {
        "transfer_version": "retail_hp_transfer_v1",
        "table_count": 20,
        "row_count": 275630,
        "sealed_phase3_required": True,
    }
    assert result["artifacts"]["bronze_tables"] == 20
    assert result["artifacts"]["silver_tables"] == 20
    assert len(result["artifacts"]["feature_tables"]) == 3
    assert len(result["artifacts"]["gold_views"]) == 6
    assert result["compute"] == {
        "one_time_serverless_cpu": True,
        "persistent_job": False,
        "schedule": False,
        "continuous_pipeline": False,
        "gpu": False,
        "new_azure_resources": 0,
    }
    assert result["scope"]["production_approved"] is False


def test_source_contract_is_derived_from_the_sealed_manifest() -> None:
    specs = source_specs()
    assert len(specs) == 20
    assert sum(item["expected_rows"] for item in specs) == 275630
    assert all(
        item["volume_path"].startswith(
            "/Volumes/intellify_databricks_demo/bronze/transfer_landing/retail_hp_transfer_v1/data/"
        )
        for item in specs
    )
    assert all(len(item["sha256"]) == 64 for item in specs)
    assert all(item["primary_key"] for item in specs)
    assert snake_case("CustomerID") == "customer_id"
    assert snake_case("CategoryAffinityScore") == "category_affinity_score"


def test_local_golden_features_are_deterministic_and_identifier_free() -> None:
    first = validate_local_features()
    second = validate_local_features()
    assert first == second
    assert first["status"] == "PASS"
    assert first["customer_behavior_feature_rows"] == 33402
    assert first["customer_behavior_customer_count"] == 4697
    assert first["behavior_input_event_count"] == 103171
    assert first["latest_purchase_count"] == 29171
    assert first["product_total_units_sold"] == 39757
    assert first["product_total_revenue"] == 5616469.28
    assert first["customer_identifiers_recorded"] is False
    assert not any("customer_id" in key for key in first if key != "customer_identifiers_recorded")


def test_phase4_job_is_one_time_cpu_bounded_and_below_ceiling() -> None:
    plan = build_job_plan()
    assert plan["compute"] == "automated_serverless_cpu"
    assert plan["persistent_job_created"] is False
    assert plan["schedule_created"] is False
    assert plan["continuous_pipeline_created"] is False
    assert plan["gpu"] is False
    assert plan["dependency_count"] == 0
    assert plan["task_timeout_minutes"] == 5.0
    assert plan["controller_deadline_minutes"] == 7.0
    assert plan["guarded_estimate_inr_pre_tax"] < 250
    assert plan["hard_invoice_cap_guaranteed"] is False


def test_sql_fallback_is_one_read_only_query_and_stays_below_30_inr() -> None:
    plan = validation_plan()
    assert plan["read_only_query_count"] == 1
    assert plan["guarded_estimate_inr_pre_tax"] < 30
    query = validation_sql()
    assert query.startswith("SELECT")
    assert all(
        token not in query.upper()
        for token in ["CREATE ", "DROP ", "DELETE ", "INSERT ", "UPDATE ", "MERGE ", "ALTER "]
    )
    assert query.count("intellify_databricks_demo.bronze.") == 20
    assert query.count("intellify_databricks_demo.silver.") >= 20


def test_sql_metric_validator_fails_closed() -> None:
    names = [
        "bronze_rows",
        "silver_rows",
        "duplicate_key_groups",
        "profile_rows",
        "behavior_rows",
        "behavior_customers",
        "product_rows",
        "customer_360_rows",
        "product_360_rows",
        "eligible_product_rows",
        "recommendation_rows",
        "dictionary_rows",
        "future_leakage_rows",
        "invalid_product_rows",
        "invalid_inventory_rows",
        "invalid_promotion_rows",
        "invalid_order_rows",
        "invalid_weather_rows",
        "orphan_product_category_rows",
        "orphan_product_brand_rows",
        "orphan_customer_region_rows",
        "orphan_order_line_rows",
        "orphan_event_customer_rows",
        "orphan_event_product_rows",
        "feature_primary_keys",
    ]
    values = [
        "275630",
        "275630",
        "0",
        "5000",
        "33402",
        "4697",
        "3000",
        "5000",
        "3000",
        "1",
        "15930",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "3",
    ]
    result = _validate_row(values, names)
    assert result["feature_primary_keys"] == 3


def test_notebook_contract_has_governed_layers_features_and_safety_controls() -> None:
    source = NOTEBOOK_SOURCE.read_text(encoding="utf-8")
    compile(source, str(NOTEBOOK_SOURCE), "exec")
    assert "delta.appendOnly" in source
    assert "TIMESERIES" in source
    assert "second_pass_changes == 0" in source
    assert "future_leakage_rows == 0" in source
    assert source.count("CREATE OR REPLACE VIEW") == 6
    assert '"currency_code": "UNSPECIFIED"' in source
    assert '"customer_identifiers_recorded": False' in source
    assert "dbutils.notebook.exit" in source
    assert "CREATE STREAMING TABLE" not in source
    assert "requests" not in source


def test_metadata_inspection_requires_every_phase4_object() -> None:
    sources = source_specs()
    names = (
        {f"intellify_databricks_demo.bronze.{item['name']}" for item in sources}
        | {f"intellify_databricks_demo.silver.{item['name']}" for item in sources}
        | set(FEATURE_TABLES)
        | set(GOLD_VIEWS)
        | {DICTIONARY_TABLE}
    )
    context = SimpleNamespace(client=SimpleNamespace(tables=FakeTables(names)))
    good = inspect_lakehouse(context)  # type: ignore[arg-type]
    assert good["status"] == "PASS"
    assert good["expected_object_count"] == 50
    context.client.tables.names.add(
        "intellify_databricks_demo.gold.customer_recommendation_current"
    )
    assert inspect_lakehouse(context)["status"] == "PASS"
    context.client.tables.names.add("intellify_databricks_demo.gold.unrecognized")
    assert inspect_lakehouse(context)["status"] == "FAIL"
    context.client.tables.names.remove("intellify_databricks_demo.gold.unrecognized")
    context.client.tables.names.remove(next(iter(FEATURE_TABLES)))
    bad = inspect_lakehouse(context)  # type: ignore[arg-type]
    assert bad["status"] == "FAIL"
    assert len(bad["missing"]) == 1


def test_owner_repair_is_exact_idempotent_and_compute_free(monkeypatch: Any) -> None:
    sources = source_specs()
    names = (
        {f"intellify_databricks_demo.bronze.{item['name']}" for item in sources}
        | {f"intellify_databricks_demo.silver.{item['name']}" for item in sources}
        | set(FEATURE_TABLES)
        | set(GOLD_VIEWS)
        | {DICTIONARY_TABLE}
    )
    tables = FakeTables(names)
    tables.owner = "creator"
    context = SimpleNamespace(
        apply=True,
        client=SimpleNamespace(tables=tables),
    )
    stopped = {
        "cluster_count": 0,
        "job_count": 0,
        "project_warehouses": [{"state": "STOPPED"}],
    }
    monkeypatch.setenv("RETAIL_HP_PHASE4_OWNER_REPAIR", "retail_hp_admins")
    monkeypatch.setattr("retail_hp_azure.phase4.inspect_compute", lambda _: stopped)
    result = repair_lakehouse_owners(context)  # type: ignore[arg-type]
    assert result["status"] == "PASS"
    assert result["objects_updated"] == 50
    assert result["compute_started"] is False


def test_phase4_files_remain_inside_the_azure_databricks_subtree() -> None:
    assert Path("azure_databricks/notebooks/phase4_build.py").is_file()
    assert Path("azure_databricks/src/retail_hp_azure/phase4.py").is_file()
    assert Path("azure_databricks/src/retail_hp_azure/phase4_runtime.py").is_file()
