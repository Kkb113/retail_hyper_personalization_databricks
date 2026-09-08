"""Replace only the proven-unbuildable, never-ready Phase 6 endpoint."""

import json
import time
from pathlib import Path

from databricks.sdk.errors import NotFound
from databricks.sdk.service.serving import EndpointCoreConfigInput
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import REGISTERED_MODEL
from retail_hp_azure.phase6 import ENDPOINT_NAME, admit_session, endpoint_configuration

context = CloudContext(apply=True)
client = context.client
endpoint = client.serving_endpoints.get(ENDPOINT_NAME)
assert endpoint.config is None, "Never delete a previously functional endpoint"
entity = endpoint.pending_config.served_entities[0]
assert entity.entity_name == REGISTERED_MODEL and entity.entity_version == "1"
path = f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}"
logs = client.api_client.do(
    "GET", path + "/served-models/retail-recommender/build-logs", query={"config_version": 1}
)["logs"]
assert "mlflow 3.8.1 depends on pandas<3" in logs and "Failed to create conda environment" in logs
admit_session(
    hourly_cost_inr=31.3392, active_minutes=15, prior_cost_inr=185, launch_cost_inr=2 * 7.8348
)
aliases = client.registered_models.get(REGISTERED_MODEL, include_aliases=True).aliases
assert any(
    alias.alias_name.casefold() == "champion" and str(alias.version_num) == "2" for alias in aliases
)
client.serving_endpoints.delete(ENDPOINT_NAME)
try:
    client.serving_endpoints.get(ENDPOINT_NAME)
except NotFound:
    client.serving_endpoints.create(
        ENDPOINT_NAME,
        config=EndpointCoreConfigInput.from_dict(endpoint_configuration(2)["config"]),
        description="Synthetic POC Champion2; Small CPU; explicitly stop after demonstrations.",
    )
else:
    raise RuntimeError("Wait for deletion before recreating; no additional endpoint created")
report = {
    "status": "SUBMITTED",
    "replaced_never_ready_version": 1,
    "new_version": 2,
    "reason": "Proven MLflow/pandas resolver conflict; update API refused while pending",
    "models_and_source_data_deleted": False,
    "creation_epoch": time.time(),
}
root = Path(__file__).resolve().parents[2]
(root / "azure_databricks/evidence/phase_06/endpoint_replacement.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
