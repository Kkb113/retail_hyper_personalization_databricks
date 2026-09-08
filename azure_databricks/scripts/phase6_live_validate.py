"""Bounded live inference validation; always request shutdown after tests."""

import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import REGISTERED_MODEL, canonical_output_sha, golden_inputs
from retail_hp_azure.phase6 import ENDPOINT_NAME
from retail_hp_azure.recommender import AdaptiveRetailRecommender
from retail_hp_azure.serving_client import (
    RecommendationClient,
    ServingRequest,
    authenticated_transport,
)

context = CloudContext(apply=True)
client = context.client
root = Path(__file__).resolve().parents[2]
runtime = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
endpoint = client.serving_endpoints.get(ENDPOINT_NAME)
assert endpoint.config and not endpoint.pending_config, "Wait for stable deployment first"
assert endpoint.config.served_entities[0].entity_name == REGISTERED_MODEL
assert endpoint.config.served_entities[0].entity_version == "3"
assert endpoint.state.ready.value == "READY"
report = {"status": "FAIL", "registered_version": "3"}
try:
    transport = authenticated_transport(client.config.authenticate)
    inputs = golden_inputs(runtime)
    expected = runtime.predict(inputs)
    start = time.monotonic()
    status, response = transport(
        {"dataframe_records": json.loads(inputs.to_json(orient="records"))}, 120
    )
    assert status == 200
    actual = pd.DataFrame(response["predictions"])
    assert canonical_output_sha(actual) == canonical_output_sha(expected)
    report["first_four_request_seconds"] = round(time.monotonic() - start, 3)
    checkpoint = client.files.download(
        "/Volumes/intellify_databricks_demo/ml/model_assets/_phase6/poc_candidate_3_demo100_20260101/shard_00000.parquet"
    )
    batch = pd.read_parquet(io.BytesIO(checkpoint.contents.read()))
    cohort = sorted(runtime.profiles.index.astype(str))[::50]
    records = [
        {
            "request_id": "batch-" + customer,
            "request_type": "existing_customer",
            "customer_id": customer,
            "top_n": 10,
            "as_of": "2026-01-01T00:00:00Z",
        }
        for customer in cohort
    ]
    status, response = transport({"dataframe_records": records}, 120)
    assert status == 200
    served_batch = pd.DataFrame(response["predictions"])
    report["azure_batch_endpoint_exact_parity"] = canonical_output_sha(
        batch
    ) == canonical_output_sha(served_batch)
    assert report["azure_batch_endpoint_exact_parity"], "Azure batch/endpoint parity failed"
    report["batch_parity_rows"] = len(served_batch)
    typed = RecommendationClient(transport)
    timings = []
    for i in range(12):
        raw = json.loads(inputs.iloc[[i % 4]].to_json(orient="records"))[0]
        request = ServingRequest(**{key: value for key, value in raw.items() if value is not None})
        started = time.monotonic()
        rows = typed.predict(request, eligible_ids=runtime.eligible_ids)
        timings.append(time.monotonic() - started)
        assert len(rows) == 10
        assert len({row["product_id"] for row in rows}) == 10
    status, _ = transport({"dataframe_records": [{"request_type": "invalid"}]}, 30)
    assert 400 <= status < 500
    p95 = float(np.percentile(timings, 95))
    assert p95 <= 5, "Warm inference misses the 5-second POC SLO"
    report.update(
        status="PASS",
        exact_golden_parity=True,
        warm_requests=12,
        warm_p95_seconds=round(p95, 3),
        inventory_valid_pct=100,
        invalid_request_status=status,
        explicit_stop_requested=True,
    )
finally:
    client.api_client.do("POST", f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}/config:stop")
    (root / "azure_databricks/evidence/phase_06/live_inference.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report))
