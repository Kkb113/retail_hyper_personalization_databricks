"""Local-only ranking regression across CPU thread configurations."""

import json
import time
from pathlib import Path

import pandas as pd
from retail_hp_azure.phase5 import canonical_output_sha
from retail_hp_azure.recommender import RUNTIME_VERSION, AdaptiveRetailRecommender
from threadpoolctl import threadpool_limits

root = Path(__file__).resolve().parents[2]
model = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
customers = sorted(model.profiles.index.astype(str))[::50]
inputs = pd.DataFrame(
    [
        {
            "request_id": "check-" + customer,
            "request_type": "existing_customer",
            "customer_id": customer,
            "top_n": 10,
            "as_of": "2026-01-01T00:00:00Z",
        }
        for customer in customers
    ]
)
results = []
for threads in (1, 4):
    started = time.monotonic()
    with threadpool_limits(limits=threads):
        output = model.predict(inputs)
    assert len(output) == 1000 and output.product_id.isin(model.eligible_ids).all()
    results.append(
        {
            "threads": threads,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "sha256": canonical_output_sha(output),
        }
    )
    print(json.dumps(results[-1]), flush=True)
assert results[0]["sha256"] == results[1]["sha256"]
report = {
    "status": "PASS",
    "runtime_version": RUNTIME_VERSION,
    "customers": 100,
    "rows_per_test": 1000,
    "checks": results,
    "azure_compute_started": False,
    "azure_parity_retest_required": True,
    "deployed": False,
}
(root / "azure_databricks/evidence/phase_06/local_determinism.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
