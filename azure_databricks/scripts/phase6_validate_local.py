"""Broader deterministic synthetic POC validation; no Azure calls."""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from retail_hp_azure.phase5 import golden_inputs
from retail_hp_azure.recommender import AdaptiveRetailRecommender

root = Path(__file__).resolve().parents[2]
model = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
customers = sorted(model.profiles.index.astype(str))[::50]
records = pd.DataFrame(
    [
        {
            "request_id": f"validation-{i}",
            "request_type": "existing_customer",
            "customer_id": customer,
            "top_n": 10,
        }
        for i, customer in enumerate(customers)
    ]
)
records = pd.concat([records, golden_inputs(model)], ignore_index=True)
started = time.monotonic()
output = model.predict(records)
assert len(output) == len(records) * 10
assert output.product_id.isin(model.eligible_ids).all()
assert output.groupby("request_id").product_id.nunique().eq(10).all()
assert np.isfinite(output.score).all()
repeat = model.predict(records.iloc[:4])
pd.testing.assert_frame_equal(
    output.iloc[:40].reset_index(drop=True), repeat.reset_index(drop=True)
)
report = {
    "status": "PASS",
    "request_count": len(records),
    "rows": len(output),
    "elapsed_seconds": round(time.monotonic() - started, 3),
    "inventory_valid_pct": 100,
    "unique_products_per_request": 10,
    "finite_scores": True,
    "deterministic_repeat": True,
    "production_approved": False,
}
print(json.dumps(report), flush=True)
