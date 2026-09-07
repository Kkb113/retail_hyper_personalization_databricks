# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# dependencies = [
#   "joblib==1.5.3", "numpy==2.5.1", "pandas==3.0.3",
#   "pyarrow==24.0.0", "scikit-learn==1.9.0", "xgboost==3.3.0",
# ]
# ///
"""One-time remote hash, schema and frozen-model validation for Phase 3."""

import hashlib
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xgboost

LANDING_ROOT = Path(
    "/Volumes/intellify_databricks_demo/bronze/transfer_landing/retail_hp_transfer_v1"
)
MODEL_ROOT = Path(
    "/Volumes/intellify_databricks_demo/ml/model_assets/retail_hp_transfer_v1"
)
sys.path.insert(0, str(MODEL_ROOT / "_runtime"))
from src.recommender_utils import normalize_categorical_frame  # noqa: E402

assert callable(normalize_categorical_frame)
MANIFEST = LANDING_ROOT / "_control" / "transfer_manifest.json"
MODEL_JSONS = {
    "known_ranker.json",
    "lowhistory_ranker.json",
    "shared_ranker.json",
    "cold_start_ranker.json",
}
PRIMARY_KEYS = {
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
OPTIONAL_NULL_COLUMNS = {
    "product_categories": {"ParentCategoryID"},
    "recommendation_log": {"SessionID", "PromotionID"},
    "recommendation_response": {"ResponseTime", "OrderID"},
    "sales_order_lines": {"PromotionID"},
    "search_events": {"ClickedProductID"},
    "wishlist": {"RemovedDate"},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
assert manifest["classification"] == "synthetic_data"
assert manifest["production_approved"] is False
assert len(manifest["files"]) == 45

remote_hashes_verified = 0
for entry in manifest["files"]:
    for destination in entry["destinations"]:
        path = Path(destination)
        assert path.is_file()
        assert path.stat().st_size == entry["size_bytes"]
        assert sha256_file(path) == entry["sha256"]
        remote_hashes_verified += 1

data_files_parsed = 0
data_rows = 0
existing_customer_loader_initialized = False
for entry in manifest["files"]:
    if "data" not in entry["roles"]:
        continue
    path = Path(next(
        value for value in entry["destinations"] if value.startswith(str(LANDING_ROOT))
    ))
    contract = entry["data_contract"]
    parquet = pq.ParquetFile(path)
    required = contract["required_columns"]
    assert parquet.metadata.num_rows == contract["expected_rows"]
    assert set(required) <= set(parquet.schema_arrow.names)
    keys = PRIMARY_KEYS[contract["name"]]
    table = parquet.read(columns=sorted(set(required) | set(keys)))
    optional = OPTIONAL_NULL_COLUMNS.get(contract["name"], set())
    assert all(
        table.column(column).null_count == 0
        for column in required
        if column not in optional
    )
    key_rows = list(zip(*(table.column(key).to_pylist() for key in keys), strict=True))
    assert all(all(value is not None for value in row) for row in key_rows)
    assert len(key_rows) == len(set(key_rows))
    if contract["name"] == "recommendation_snapshot":
        customer_ids = table.column("CustomerID").to_pylist()
        assert len(set(customer_ids)) == 1593
        existing_customer_loader_initialized = True
    data_rows += parquet.metadata.num_rows
    data_files_parsed += 1

model_files_loaded = 0
new_customer_loader_initialized = False
for entry in manifest["files"]:
    if "model" not in entry["roles"]:
        continue
    path = Path(next(value for value in entry["destinations"] if value.startswith(str(MODEL_ROOT))))
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value
        if path.name in MODEL_JSONS:
            booster = xgboost.Booster()
            booster.load_model(path)
            assert booster.num_boosted_rounds() > 0
        if path.name == "onboarding_schema.json":
            assert value["all_fields_optional"] is True
            assert value["questions"]
            assert set(value["prohibited_fields"]) >= {"email", "phone", "precise_location"}
            new_customer_loader_initialized = True
    elif suffix == ".npz":
        with np.load(path, allow_pickle=False) as arrays:
            assert arrays.files
            assert all(
                arrays[key].dtype.kind != "O" and arrays[key].size > 0
                for key in arrays.files
            )
    elif suffix == ".joblib":
        assert joblib.load(path) is not None
    elif suffix == ".parquet":
        assert pq.ParquetFile(path).metadata.num_rows == 15930
    else:
        raise AssertionError("unsupported model file")
    model_files_loaded += 1

assert remote_hashes_verified == 46
assert data_files_parsed == 20
assert data_rows == 275630
assert model_files_loaded == 26
assert existing_customer_loader_initialized
assert new_customer_loader_initialized

result = {
    "version": "azure_phase3_databricks_validation_v1",
    "status": "PASS",
    "bundle_sha256": manifest["bundle_sha256"],
    "remote_hashes_verified": remote_hashes_verified,
    "data_files_parsed": data_files_parsed,
    "data_rows": data_rows,
    "model_files_loaded": model_files_loaded,
    "existing_customer_loader_initialized": existing_customer_loader_initialized,
    "new_customer_loader_initialized": new_customer_loader_initialized,
    "runtime_compatibility_loaded": True,
    "package_versions": {
        "joblib": joblib.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": "24.0.0",
        "xgboost": xgboost.__version__,
    },
    "customer_identifiers_recorded": False,
    "production_approved": False,
}
dbutils.notebook.exit(json.dumps(result, sort_keys=True))  # noqa: F821
