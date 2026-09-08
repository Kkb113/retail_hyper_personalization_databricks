"""Read only Champion requirements; never load a pickle, invoke or start compute."""

import hashlib
import json
from datetime import UTC, datetime

from retail_hp_azure.model_identity import REGISTERED_MODEL
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase5 import inspect_registered_model
from retail_hp_azure.safety import require


def verify():
    context = CloudContext()
    registry = inspect_registered_model(context)
    require(registry["aliases"].get("champion") == 3, "Champion drift")
    endpoint = context.client.api_client.do(
        "GET", "/api/2.0/serving-endpoints/retail-hp-poc-recommender"
    )
    entities = endpoint.get("config", {}).get("served_entities", [])
    require(len(entities) == 1 and entities[0]["entity_version"] == "3", "Endpoint version drift")
    require(endpoint["state"].get("suspend") == "STOPPED", "Endpoint is not stopped")
    response = context.client.files.download(
        f"/Models/{REGISTERED_MODEL.replace('.', '/')}/3/requirements.txt"
    )
    with response.contents as stream:
        payload = stream.read(65537)
    require(len(payload) <= 65536, "Requirements size exceeds bound")
    actual = [
        line.strip()
        for line in payload.decode().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    root = _find_repo_root()
    expected = [
        line.strip()
        for line in (root / "azure_databricks/environments/phase6_model_requirements.txt")
        .read_text()
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    require(sorted(actual) == sorted(expected), "Live model requirements differ from Phase 6")
    require("mlflow==3.16.0" in actual, "Expected repaired MLflow pin missing")
    report = {
        "status": "PASS",
        "captured_at": datetime.now(UTC).isoformat(),
        "champion_version": 3,
        "endpoint_version": 3,
        "endpoint_state": "STOPPED",
        "mlflow_version": "3.16.0",
        "matches_phase6_requirements": True,
        "requirements_sha256": hashlib.sha256(payload).hexdigest(),
        "compute_started": False,
        "model_loaded": False,
        "cloud_mutations": False,
    }
    (root / "azure_databricks/evidence/phase_08/runtime_security_verification.json").write_text(
        json.dumps(report, indent=2)
    )
    return report


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
