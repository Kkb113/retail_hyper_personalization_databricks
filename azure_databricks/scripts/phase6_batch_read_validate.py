"""One bounded SQL client smoke test with unconditional warehouse shutdown."""

import json
import time
from datetime import timedelta
from pathlib import Path

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.recommender import AdaptiveRetailRecommender
from retail_hp_azure.serving_client import read_batch_recommendations

context = CloudContext(apply=True)
client = context.client
warehouses = [w for w in client.warehouses.list() if w.name == "retail-hp-poc-sql"]
assert len(warehouses) == 1 and warehouses[0].state.value == "STOPPED"
warehouse = warehouses[0]
details = client.warehouses.get(warehouse.id)
assert details.cluster_size == "2X-Small" and details.auto_stop_mins == 1
root = Path(__file__).resolve().parents[2]
runtime = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
customer = sorted(runtime.profiles.index.astype(str))[0]
report = {"status": "FAIL"}
try:
    client.warehouses.start(warehouse.id).result(timeout=timedelta(minutes=2))
    started = time.monotonic()
    rows = read_batch_recommendations(client, customer)
    assert len(rows) == 10 and len({row["product_id"] for row in rows}) == 10
    assert all(row["registered_model_version"] == "3" for row in rows)
    first_latency = time.monotonic() - started
    started = time.monotonic()
    repeated = read_batch_recommendations(client, customer)
    warm_latency = time.monotonic() - started
    assert repeated == rows
    assert warm_latency <= 5, "Warm batch read misses the 5-second POC smoke target"
    report = {
        "status": "PASS",
        "rows": 10,
        "parameterized_query": True,
        "registered_version": "3",
        "first_read_seconds": round(first_latency, 3),
        "warm_read_seconds": round(warm_latency, 3),
        "repeat_identical": True,
    }
finally:
    client.warehouses.stop(warehouse.id)
    (root / "azure_databricks/evidence/phase_06/batch_read.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report))
