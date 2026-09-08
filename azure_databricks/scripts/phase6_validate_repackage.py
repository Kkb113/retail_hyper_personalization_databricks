"""Recover validation after successful registration but interrupted client output."""

import json
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import REGISTERED_MODEL, canonical_output_sha, golden_inputs
from retail_hp_azure.phase6 import configure_mlflow_identity

context = CloudContext(apply=True)
configure_mlflow_identity()
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
loaded = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL}/2")
assert loaded.metadata.metadata["packaging_repair_of_version"] == "1"
old = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL}/1")
inputs = golden_inputs(old.unwrap_python_model().model)
assert canonical_output_sha(loaded.predict(inputs)) == canonical_output_sha(old.predict(inputs))
tracking = MlflowClient()
tracking.set_model_version_tag(
    REGISTERED_MODEL, "2", "retail_hp_poc_status", "APPROVED_SYNTHETIC_ONLY"
)
tracking.set_model_version_tag(
    REGISTERED_MODEL, "2", "retail_hp_production_status", "HOLD_PENDING_FUTURE_HOLDOUT"
)
context.client.registered_models.set_alias(REGISTERED_MODEL, "Champion", 2)
report = {
    "status": "PASS",
    "registered_version": "2",
    "previous_version": "1",
    "golden_parity": True,
    "retrained": False,
    "production_approved": False,
    "dependency_repair": "mlflow 3.16.0 is compatible with pandas 3.0.3",
}
root = Path(__file__).resolve().parents[2]
(root / "azure_databricks/evidence/phase_06/repackage.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
