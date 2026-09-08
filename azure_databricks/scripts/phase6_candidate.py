"""Register the deterministic runtime as Candidate; never promote before live gates."""

import json
import sys
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient
from retail_hp_azure.mlflow_model import RetailHyperPersonalizationModel
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase5 import REGISTERED_MODEL, golden_inputs
from retail_hp_azure.phase5_runtime import DEPENDENCIES
from retail_hp_azure.phase6 import configure_mlflow_identity
from retail_hp_azure.recommender import RUNTIME_VERSION, AdaptiveRetailRecommender

sys.stdout.reconfigure(encoding="utf-8")
context = CloudContext(apply=True)
configure_mlflow_identity()
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment("/Shared/retail_hp_phase5/functional_recommender_experiment")
root = Path(__file__).resolve().parents[2]
evidence = root / "azure_databricks/evidence/phase_06"
local = json.loads((evidence / "local_determinism.json").read_text())
assert local["status"] == "PASS" and local["runtime_version"] == RUNTIME_VERSION
tracking = MlflowClient()
versions = list(tracking.search_model_versions(f"name='{REGISTERED_MODEL}'"))
assert len(versions) == 2, "Reconcile an existing candidate rather than duplicate it"
signature = mlflow.models.get_model_info(f"models:/{REGISTERED_MODEL}/2").signature
runtime = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
with mlflow.start_run(run_name="phase6-deterministic-ranking-candidate"):
    info = mlflow.pyfunc.log_model(
        name="functional_recommender",
        python_model=RetailHyperPersonalizationModel(),
        artifacts={
            "data": str(root / "migration_assets"),
            "models": str(root / "migration_assets/artifacts"),
        },
        code_paths=[str(root / "azure_databricks/src/retail_hp_azure")],
        signature=signature,
        input_example=golden_inputs(runtime).head(1),
        pip_requirements=["mlflow==3.16.0", *DEPENDENCIES],
        metadata={
            "classification": "synthetic_data",
            "production_approved": False,
            "runtime_version": RUNTIME_VERSION,
            "trained_weights_changed": False,
        },
    )
    registered = mlflow.register_model(info.model_uri, REGISTERED_MODEL)
version = str(registered.version)
assert version == "3"
tracking.set_model_version_tag(
    REGISTERED_MODEL, version, "retail_hp_poc_status", "PENDING_LIVE_PARITY"
)
tracking.set_model_version_tag(
    REGISTERED_MODEL, version, "retail_hp_production_status", "HOLD_PENDING_FUTURE_HOLDOUT"
)
tracking.set_model_version_tag(
    REGISTERED_MODEL, "2", "retail_hp_poc_status", "HOLD_SERVING_PARITY_FAILED"
)
context.client.registered_models.set_alias(REGISTERED_MODEL, "Candidate", 3)
report = {
    "status": "CANDIDATE_REGISTERED",
    "version": 3,
    "runtime_version": RUNTIME_VERSION,
    "trained_weights_changed": False,
    "production_approved": False,
    "live_tests_required_before_champion": True,
}
(evidence / "candidate_v3.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
