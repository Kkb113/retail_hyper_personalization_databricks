"""Register a dependency-corrected package without retraining or Azure compute."""

import json
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient
from retail_hp_azure.mlflow_model import RetailHyperPersonalizationModel
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import REGISTERED_MODEL, canonical_output_sha, golden_inputs
from retail_hp_azure.phase5_runtime import DEPENDENCIES
from retail_hp_azure.phase6 import configure_mlflow_identity

context = CloudContext(apply=True)
configure_mlflow_identity()
root = Path(__file__).resolve().parents[2]
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment("/Shared/retail_hp_phase5/functional_recommender_experiment")
tracking = MlflowClient()
assert len(list(tracking.search_model_versions(f"name='{REGISTERED_MODEL}'"))) == 1
old = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL}/1")
inputs = golden_inputs(old.unwrap_python_model().model)
expected = old.predict(inputs)
with mlflow.start_run(run_name="phase6-serving-dependency-repair"):
    info = mlflow.pyfunc.log_model(
        name="functional_recommender",
        python_model=RetailHyperPersonalizationModel(),
        artifacts={
            "data": str(root / "migration_assets"),
            "models": str(root / "migration_assets/artifacts"),
        },
        code_paths=[str(root / "azure_databricks/src/retail_hp_azure")],
        signature=old.metadata.signature,
        input_example=inputs.head(1),
        pip_requirements=["mlflow==3.16.0", *DEPENDENCIES],
        metadata={
            "classification": "synthetic_data",
            "production_approved": False,
            "packaging_repair_of_version": "1",
        },
    )
    registered = mlflow.register_model(info.model_uri, REGISTERED_MODEL)
version = str(registered.version)
loaded = mlflow.pyfunc.load_model(f"models:/{REGISTERED_MODEL}/{version}")
assert canonical_output_sha(loaded.predict(inputs)) == canonical_output_sha(expected)
tracking.set_model_version_tag(
    REGISTERED_MODEL, version, "retail_hp_poc_status", "APPROVED_SYNTHETIC_ONLY"
)
tracking.set_model_version_tag(
    REGISTERED_MODEL, version, "retail_hp_production_status", "HOLD_PENDING_FUTURE_HOLDOUT"
)
context.client.registered_models.set_alias(REGISTERED_MODEL, "Champion", int(version))
report = {
    "status": "PASS",
    "registered_version": version,
    "previous_version": "1",
    "dependency_repair": "mlflow 3.8.1 requires pandas<3; 3.16.0 resolves with pandas3",
    "golden_parity": True,
    "retrained": False,
    "production_approved": False,
}
(root / "azure_databricks/evidence/phase_06/repackage.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
