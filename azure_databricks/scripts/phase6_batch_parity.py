"""Validate the cloud-produced batch checkpoint locally without paid compute."""

import io
import json
from pathlib import Path

import pandas as pd
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import canonical_output_sha
from retail_hp_azure.recommender import AdaptiveRetailRecommender

client = CloudContext().client
root = Path(__file__).resolve().parents[2]
path = (
    "/Volumes/intellify_databricks_demo/ml/model_assets/_phase6/"
    "poc_candidate_3_demo100_20260101/shard_00000.parquet"
)
response = client.files.download(path)
actual = pd.read_parquet(io.BytesIO(response.contents.read()))
runtime = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
customers = sorted(runtime.profiles.index.astype(str))[::50]
inputs = pd.DataFrame(
    [
        {
            "request_id": "batch-" + customer,
            "request_type": "existing_customer",
            "customer_id": customer,
            "top_n": 10,
            "as_of": "2026-01-01T00:00:00Z",
        }
        for customer in customers
    ]
)
expected = runtime.predict(inputs)
differences = {}
exact = canonical_output_sha(actual) == canonical_output_sha(expected)
if not exact:
    for column in expected.columns:
        left = actual[column].reset_index(drop=True)
        right = expected[column].reset_index(drop=True)
        same = (left.eq(right) | (left.isna() & right.isna())).fillna(False)
        if not same.all():
            differences[column] = {
                "different_rows": int((~same).sum()),
                "max_numeric_error": float((left - right).abs().max())
                if pd.api.types.is_numeric_dtype(left)
                else None,
            }
    print(json.dumps({"differences": differences}), flush=True)
report = {
    "registered_version": "3",
    "status": "PASS" if exact else "WARN",
    "customers": 100,
    "rows": 1000,
    "exact_local_cloud_parity": exact,
    "differences": differences,
    "sha256": canonical_output_sha(actual),
    "azure_compute_started": False,
    "note": "Cross-platform diagnostic; Azure batch/endpoint parity is a separate required gate",
}
(root / "azure_databricks/evidence/phase_06/batch_parity.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
