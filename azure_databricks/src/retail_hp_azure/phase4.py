"""Phase 4 lakehouse contract, cost admission, and read-only inspection."""

# ruff: noqa: S608 -- SQL identifiers are derived only from sealed, closed constants.

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from retail_hp_azure.phase2 import CATALOG, CloudContext, inspect_compute
from retail_hp_azure.phase3 import LANDING_ROOT, REPO_ROOT, load_manifest, validate_manifest
from retail_hp_azure.safety import SafetyError, require

AZURE_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = AZURE_ROOT / "evidence" / "phase_04"
TRANSFER_VERSION = "retail_hp_transfer_v1"
PHASE4_VERSION = "retail_hp_lakehouse_v1"

SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "brands": ("BrandID",),
    "browsing_events": ("EventID",),
    "cart_events": ("CartEventID",),
    "customer_preferences": ("CustomerID",),
    "customer_product_audit": ("CustomerID", "ProductID"),
    "customers": ("CustomerID",),
    "inventory": ("InventoryID",),
    "product_categories": ("CategoryID",),
    "products": ("ProductID",),
    "promotions": ("PromotionID",),
    "recommendation_log": ("RecommendationID",),
    "recommendation_response": ("ResponseID",),
    "recommendation_snapshot": ("CustomerID", "Rank"),
    "regions": ("RegionID",),
    "sales_order_lines": ("OrderLineID",),
    "sales_orders": ("OrderID",),
    "search_events": ("SearchID",),
    "weather": ("WeatherID",),
    "wishlist": ("WishlistID",),
    "customer_profile_history": ("CustomerID", "ProfileVersion"),
}

FEATURE_TABLES = (
    f"{CATALOG}.features.customer_profile_features",
    f"{CATALOG}.features.customer_behavior_features",
    f"{CATALOG}.features.product_features",
)
GOLD_VIEWS = (
    f"{CATALOG}.gold.customer_360",
    f"{CATALOG}.gold.product_360",
    f"{CATALOG}.gold.current_inventory",
    f"{CATALOG}.gold.active_promotions",
    f"{CATALOG}.gold.eligible_product_catalog",
    f"{CATALOG}.gold.current_recommendations",
)
DICTIONARY_TABLE = f"{CATALOG}.gold.data_dictionary"
OBJECT_OWNER = "retail_hp_admins"


def snake_case(name: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    require(re.fullmatch(r"[a-z][a-z0-9_]*", value) is not None, "Unsafe column name")
    return value


def source_specs() -> list[dict[str, Any]]:
    """Derive the exact Phase 4 input contract from the sealed Phase 3 manifest."""
    manifest = load_manifest()
    validate_manifest(manifest)
    specs: list[dict[str, Any]] = []
    for entry in cast(list[dict[str, Any]], manifest["files"]):
        if "data" not in entry["roles"]:
            continue
        contract = cast(dict[str, Any], entry["data_contract"])
        name = str(contract["name"])
        require(name in SOURCE_KEYS, f"Phase 4 primary-key contract missing for {name}")
        specs.append(
            {
                "name": name,
                "logical_path": str(entry["logical_path"]),
                "volume_path": f"{LANDING_ROOT}/data/{entry['logical_path']}",
                "sha256": str(entry["sha256"]),
                "expected_rows": int(contract["expected_rows"]),
                "primary_key": list(SOURCE_KEYS[name]),
                "required_columns": [str(value) for value in contract["required_columns"]],
            }
        )
    require(len(specs) == 20, "Phase 4 requires all 20 approved data sources")
    require(
        sum(item["expected_rows"] for item in specs) == 275_630, "Phase 4 source row contract drift"
    )
    return sorted(specs, key=lambda item: item["name"])


def lakehouse_plan() -> dict[str, Any]:
    specs = source_specs()
    return {
        "version": "azure_phase4_plan_v1",
        "phase4_version": PHASE4_VERSION,
        "status": "PASS",
        "scope": {
            "catalog": CATALOG,
            "classification": "synthetic_data",
            "production_approved": False,
        },
        "source": {
            "transfer_version": TRANSFER_VERSION,
            "table_count": len(specs),
            "row_count": sum(item["expected_rows"] for item in specs),
            "sealed_phase3_required": True,
        },
        "artifacts": {
            "bronze_tables": len(specs),
            "silver_tables": len(specs),
            "feature_tables": list(FEATURE_TABLES),
            "gold_views": list(GOLD_VIEWS),
            "data_dictionary": DICTIONARY_TABLE,
        },
        "semantics": {
            "bronze": "append-only by transfer version and source hash",
            "silver": "fail-on-source-drift conditional MERGE",
            "feature_time": "strictly-prior events; Unity Catalog TIMESERIES keys",
            "money_currency": "UNSPECIFIED; source contract contains no currency",
            "gold": "deterministic views as of the latest source snapshot",
        },
        "compute": {
            "one_time_serverless_cpu": True,
            "persistent_job": False,
            "schedule": False,
            "continuous_pipeline": False,
            "gpu": False,
            "new_azure_resources": 0,
        },
    }


def validate_local_features() -> dict[str, Any]:
    """Build aggregate golden metrics independently with pandas; record no identifiers."""
    import pandas as pd  # type: ignore[import-untyped]

    manifest = load_manifest()
    paths = {
        str(entry["data_contract"]["name"]): REPO_ROOT / str(entry["path"])
        for entry in cast(list[dict[str, Any]], manifest["files"])
        if "data" in entry["roles"]
    }

    def read(name: str) -> Any:
        return pd.read_parquet(paths[name])

    browsing = read("browsing_events")
    cart = read("cart_events")
    orders = read("sales_orders")
    lines = read("sales_order_lines")
    wishlist = read("wishlist")
    search = read("search_events")
    valid_orders = orders[orders["OrderStatus"].str.lower().ne("cancelled")]
    purchases = lines.merge(valid_orders[["OrderID", "CustomerID", "OrderDate"]], on="OrderID")

    events = pd.concat(
        [
            pd.DataFrame(
                {
                    "customer_id": browsing["CustomerID"],
                    "event_time": browsing["EventTime"],
                    "browse": 1,
                    "cart_add": 0,
                    "purchase": 0,
                    "spend": 0.0,
                    "wishlist": 0,
                    "search": 0,
                    "search_click": 0,
                }
            ),
            pd.DataFrame(
                {
                    "customer_id": cart["CustomerID"],
                    "event_time": cart["EventTime"],
                    "browse": 0,
                    "cart_add": cart["Action"].str.lower().eq("add").astype(int),
                    "purchase": 0,
                    "spend": 0.0,
                    "wishlist": 0,
                    "search": 0,
                    "search_click": 0,
                }
            ),
            pd.DataFrame(
                {
                    "customer_id": purchases["CustomerID"],
                    "event_time": purchases["OrderDate"],
                    "browse": 0,
                    "cart_add": 0,
                    "purchase": 1,
                    "spend": purchases["LineAmount"].astype(float),
                    "wishlist": 0,
                    "search": 0,
                    "search_click": 0,
                }
            ),
            pd.DataFrame(
                {
                    "customer_id": wishlist["CustomerID"],
                    "event_time": wishlist["AddedDate"],
                    "browse": 0,
                    "cart_add": 0,
                    "purchase": 0,
                    "spend": 0.0,
                    "wishlist": 1,
                    "search": 0,
                    "search_click": 0,
                }
            ),
            pd.DataFrame(
                {
                    "customer_id": search["CustomerID"],
                    "event_time": search["EventTime"],
                    "browse": 0,
                    "cart_add": 0,
                    "purchase": 0,
                    "spend": 0.0,
                    "wishlist": 0,
                    "search": 1,
                    "search_click": search["ClickedProductID"].notna().astype(int),
                }
            ),
        ],
        ignore_index=True,
    )
    events["feature_timestamp"] = pd.to_datetime(events["event_time"]).dt.floor("D") + pd.Timedelta(
        days=1
    )
    metrics = ["browse", "cart_add", "purchase", "spend", "wishlist", "search", "search_click"]
    daily = events.groupby(["customer_id", "feature_timestamp"], as_index=False)[metrics].sum()
    daily = daily.sort_values(["customer_id", "feature_timestamp"])
    daily[metrics] = daily.groupby("customer_id")[metrics].cumsum()
    latest = daily.groupby("customer_id", as_index=False).tail(1)

    product_rows = read("products")
    return {
        "version": "azure_phase4_local_golden_v1",
        "status": "PASS",
        "customer_behavior_feature_rows": int(len(daily)),
        "customer_behavior_customer_count": int(daily["customer_id"].nunique()),
        "behavior_input_event_count": int(len(events)),
        "latest_browse_count": int(latest["browse"].sum()),
        "latest_cart_add_count": int(latest["cart_add"].sum()),
        "latest_purchase_count": int(latest["purchase"].sum()),
        "latest_purchase_spend": round(float(latest["spend"].sum()), 6),
        "latest_wishlist_count": int(latest["wishlist"].sum()),
        "latest_search_count": int(latest["search"].sum()),
        "latest_search_click_count": int(latest["search_click"].sum()),
        "customer_profile_feature_rows": int(len(read("customer_profile_history"))),
        "product_feature_rows": int(len(product_rows)),
        "product_total_units_sold": int(purchases["Qty"].sum()),
        "product_total_revenue": round(float(purchases["LineAmount"].sum()), 6),
        "product_total_browse_events": int(len(browsing)),
        "customer_identifiers_recorded": False,
    }


def inspect_lakehouse(context: CloudContext) -> dict[str, Any]:
    """Inspect catalog metadata without starting SQL or Spark compute."""
    expected: dict[str, set[str]] = {
        "bronze": {f"{CATALOG}.bronze.{item['name']}" for item in source_specs()},
        "silver": {f"{CATALOG}.silver.{item['name']}" for item in source_specs()},
        "features": set(FEATURE_TABLES),
        "gold": set(GOLD_VIEWS) | {DICTIONARY_TABLE},
    }
    observed: dict[str, set[str]] = {}
    owner_mismatches: list[str] = []
    for schema in expected:
        items = list(context.client.tables.list(catalog_name=CATALOG, schema_name=schema))
        observed[schema] = {str(item.full_name) for item in items if item.full_name}
        owner_mismatches.extend(
            str(item.full_name)
            for item in items
            if item.full_name in expected[schema] and str(item.owner) != OBJECT_OWNER
        )
    missing = sorted(
        name for schema, names in expected.items() for name in names - observed[schema]
    )
    unexpected_project = sorted(
        name
        for schema, names in observed.items()
        for name in names - expected[schema]
    )
    return {
        "version": "azure_phase4_metadata_inspection_v1",
        "status": (
            "PASS" if not missing and not unexpected_project and not owner_mismatches else "FAIL"
        ),
        "scope_verified": True,
        "expected_object_count": sum(len(value) for value in expected.values()),
        "observed_expected_count": sum(len(expected[s] & observed[s]) for s in expected),
        "missing": missing,
        "owner_mismatches": sorted(owner_mismatches),
        "required_owner": OBJECT_OWNER,
        "unexpected_project_objects": unexpected_project,
        "cloud_mutations_performed": False,
        "compute_started": False,
    }


def repair_lakehouse_owners(context: CloudContext) -> dict[str, Any]:
    """Transfer only the exact Phase 4 objects to the approved admin group."""
    require(context.apply, "Phase 4 owner repair requires explicit apply context")
    require(
        os.environ.get("RETAIL_HP_PHASE4_OWNER_REPAIR") == OBJECT_OWNER,
        "Exact Phase 4 owner-repair approval is required",
    )
    before_compute = inspect_compute(context)
    require(
        before_compute["cluster_count"] == 0 and before_compute["job_count"] == 0,
        "Owner repair requires zero clusters and persistent jobs",
    )
    require(
        all(item["state"] == "STOPPED" for item in before_compute["project_warehouses"]),
        "Owner repair requires the project warehouse to be stopped",
    )
    before = inspect_lakehouse(context)
    require(not before["missing"], "Owner repair refuses an incomplete Lakehouse")
    require(
        not before["unexpected_project_objects"],
        "Owner repair refuses unexpected project objects",
    )
    targets = cast(list[str], before["owner_mismatches"])
    for full_name in targets:
        context.client.tables.update(full_name, owner=OBJECT_OWNER)
    after = inspect_lakehouse(context)
    require(after["status"] == "PASS", "Phase 4 owner repair did not converge")
    after_compute = inspect_compute(context)
    require(
        after_compute["cluster_count"] == 0 and after_compute["job_count"] == 0,
        "Owner repair changed compute inventory",
    )
    require(
        all(item["state"] == "STOPPED" for item in after_compute["project_warehouses"]),
        "Owner repair changed warehouse state",
    )
    return {
        "version": "azure_phase4_ownership_repair_v1",
        "status": "PASS",
        "objects_updated": len(targets),
        "required_owner": OBJECT_OWNER,
        "expected_object_count": after["expected_object_count"],
        "remaining_owner_mismatches": len(after["owner_mismatches"]),
        "compute_started": False,
        "new_azure_resources": 0,
        "cluster_count": after_compute["cluster_count"],
        "persistent_job_count": after_compute["job_count"],
        "project_warehouse_state": after_compute["project_warehouses"][0]["state"],
        "identifiers_recorded": False,
    }


def validation_sql() -> str:  # noqa: S608 -- identifiers are closed-source constants
    """Return one bounded, read-only reconciliation query over all Phase 4 objects."""
    specs = source_specs()
    bronze_counts = " UNION ALL ".join(
        f"SELECT count(*) AS n FROM {CATALOG}.bronze.{item['name']} "
        f"WHERE transfer_version = '{TRANSFER_VERSION}'"
        for item in specs
    )
    silver_counts = " UNION ALL ".join(
        f"SELECT count(*) AS n FROM {CATALOG}.silver.{item['name']}" for item in specs
    )
    duplicate_counts = " UNION ALL ".join(
        "SELECT count(*) AS n FROM (SELECT "
        + ", ".join(snake_case(key) for key in item["primary_key"])
        + f" FROM {CATALOG}.silver.{item['name']} GROUP BY "
        + ", ".join(snake_case(key) for key in item["primary_key"])
        + " HAVING count(*) > 1) AS duplicate_groups"
        for item in specs
    )
    return f"""
SELECT
  (SELECT sum(n) FROM ({bronze_counts}) AS bronze_counts) AS bronze_rows,
  (SELECT sum(n) FROM ({silver_counts}) AS silver_counts) AS silver_rows,
  (SELECT sum(n) FROM ({duplicate_counts}) AS duplicate_counts) AS duplicate_key_groups,
  (SELECT count(*) FROM {CATALOG}.features.customer_profile_features) AS profile_rows,
  (SELECT count(*) FROM {CATALOG}.features.customer_behavior_features) AS behavior_rows,
  (SELECT count(DISTINCT customer_id)
     FROM {CATALOG}.features.customer_behavior_features) AS behavior_customers,
  (SELECT count(*) FROM {CATALOG}.features.product_features) AS product_rows,
  (SELECT count(*) FROM {CATALOG}.gold.customer_360) AS customer_360_rows,
  (SELECT count(*) FROM {CATALOG}.gold.product_360) AS product_360_rows,
  (SELECT count(*) FROM {CATALOG}.gold.eligible_product_catalog) AS eligible_product_rows,
  (SELECT count(*) FROM {CATALOG}.gold.current_recommendations) AS recommendation_rows,
  (SELECT count(*) FROM {CATALOG}.gold.data_dictionary) AS dictionary_rows,
  (SELECT count(*) FROM {CATALOG}.features.customer_behavior_features
    WHERE max_source_event_time >= feature_timestamp) AS future_leakage_rows,
  (SELECT count(*) FROM {CATALOG}.silver.products
    WHERE base_price < 0 OR cost_price < 0 OR margin_pct NOT BETWEEN 0 AND 100)
    AS invalid_product_rows,
  (SELECT count(*) FROM {CATALOG}.silver.inventory
    WHERE on_hand_qty < 0 OR reserved_qty < 0 OR available_qty < 0)
    AS invalid_inventory_rows,
  (SELECT count(*) FROM {CATALOG}.silver.promotions
    WHERE discount_pct NOT BETWEEN 0 AND 100 OR end_date < start_date)
    AS invalid_promotion_rows,
  (SELECT count(*) FROM {CATALOG}.silver.sales_orders
    WHERE gross_amount < 0 OR discount_amount < 0 OR net_amount < 0)
    AS invalid_order_rows,
  (SELECT count(*) FROM {CATALOG}.silver.weather
    WHERE humidity_pct NOT BETWEEN 0 AND 100) AS invalid_weather_rows,
  (SELECT count(*) FROM {CATALOG}.silver.products c LEFT ANTI JOIN
    {CATALOG}.silver.product_categories p ON c.category_id = p.category_id)
    AS orphan_product_category_rows,
  (SELECT count(*) FROM {CATALOG}.silver.products c LEFT ANTI JOIN
    {CATALOG}.silver.brands p ON c.brand_id = p.brand_id) AS orphan_product_brand_rows,
  (SELECT count(*) FROM {CATALOG}.silver.customers c LEFT ANTI JOIN
    {CATALOG}.silver.regions p ON c.region_id = p.region_id) AS orphan_customer_region_rows,
  (SELECT count(*) FROM {CATALOG}.silver.sales_order_lines c LEFT ANTI JOIN
    {CATALOG}.silver.sales_orders p ON c.order_id = p.order_id) AS orphan_order_line_rows,
  (SELECT count(*) FROM {CATALOG}.silver.browsing_events c LEFT ANTI JOIN
    {CATALOG}.silver.customers p ON c.customer_id = p.customer_id) AS orphan_event_customer_rows,
  (SELECT count(*) FROM {CATALOG}.silver.browsing_events c LEFT ANTI JOIN
    {CATALOG}.silver.products p ON c.product_id = p.product_id) AS orphan_event_product_rows,
  (SELECT count(*) FROM {CATALOG}.information_schema.table_constraints
    WHERE table_schema = 'features' AND constraint_type = 'PRIMARY KEY'
      AND table_name IN ('customer_profile_features', 'customer_behavior_features',
                         'product_features')) AS feature_primary_keys
""".strip()


def record_evidence(filename: str, value: Mapping[str, Any]) -> None:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    document = {**value, "captured_at": datetime.now(UTC).isoformat()}
    (EVIDENCE_ROOT / filename).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "inspect", "repair-owners"])
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = lakehouse_plan()
        elif args.command == "inspect":
            result = inspect_lakehouse(CloudContext())
            record_evidence("metadata_inspection.json", result)
        else:
            result = repair_lakehouse_owners(CloudContext(apply=True))
            record_evidence("ownership_repair.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(f"Phase 4 operation failed: {type(exc).__name__}; {detail}") from None
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
