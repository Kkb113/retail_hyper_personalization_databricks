# Databricks notebook source
# ruff: noqa: E501, F821
"""Register and validate one immutable functional recommender candidate."""

import hashlib
import inspect
import json
from pathlib import Path

import mlflow
import retail_hp_azure
from mlflow.models import ModelSignature
from mlflow.tracking import MlflowClient
from mlflow.types import ColSpec, Schema
from retail_hp_azure.mlflow_model import RetailHyperPersonalizationModel
from retail_hp_azure.phase5 import INPUT_COLUMNS, canonical_output_sha, golden_inputs
from retail_hp_azure.recommender import (
    MODEL_VERSION,
    POLICY_VERSION,
    RUNTIME_VERSION,
    AdaptiveRetailRecommender,
)

CATALOG = "intellify_databricks_demo"
MODEL_NAME = f"{CATALOG}.ml.adaptive_recommender"
DATA_ROOT = f"/Volumes/{CATALOG}/bronze/transfer_landing/retail_hp_transfer_v1/data"
MODEL_ROOT = f"/Volumes/{CATALOG}/ml/model_assets/retail_hp_transfer_v1/artifacts"
OWNER = "retail_hp_admins"
EXPERIMENT = "/Shared/retail_hp_phase5/functional_recommender_experiment"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


runtime_source = Path(inspect.getfile(retail_hp_azure)).parent
release_key = hashlib.sha256(
    (runtime_source / "recommender.py").read_bytes()
    + (runtime_source / "mlflow_model.py").read_bytes()
    + (Path(MODEL_ROOT) / "model_release_v1" / "release_manifest.json").read_bytes()
).hexdigest()

runtime = AdaptiveRetailRecommender(DATA_ROOT, MODEL_ROOT)
golden = golden_inputs(runtime)
local = runtime.predict(golden)
require(local.groupby("request_id").size().eq(10).all(), "Golden coverage failed")
require(
    local.product_id.isin(runtime.eligible_ids).all(), "Local output contains an ineligible product"
)
require(
    local.groupby("request_id").product_id.nunique().eq(10).all(),
    "Local output contains duplicates",
)

signature = ModelSignature(
    inputs=Schema(
        [
            ColSpec("string", "request_id"),
            ColSpec("string", "request_type"),
            ColSpec("string", "customer_id", required=False),
            ColSpec("string", "scenario_id", required=False),
            ColSpec("long", "top_n"),
            ColSpec("string", "as_of", required=False),
            *[ColSpec("string", name, required=False) for name in INPUT_COLUMNS[6:16]],
            ColSpec("double", "category_affinity_score", required=False),
            ColSpec("double", "brand_affinity_score", required=False),
            ColSpec("string", "session_events_json", required=False),
            ColSpec("string", "excluded_product_ids_json", required=False),
        ]
    ),
    outputs=Schema(
        [
            ColSpec("string", "request_id"),
            ColSpec("string", "customer_id", required=False),
            ColSpec("string", "scenario_id", required=False),
            ColSpec("long", "rank"),
            ColSpec("string", "product_id"),
            ColSpec("double", "score"),
            ColSpec("string", "route"),
            ColSpec("double", "cold_weight"),
            ColSpec("double", "warm_weight"),
            ColSpec("double", "behavioral_confidence"),
            ColSpec("double", "metadata_confidence"),
            ColSpec("string", "candidate_sources"),
            ColSpec("string", "reason_codes"),
            ColSpec("string", "fallback_reason"),
            ColSpec("string", "inventory_snapshot_at"),
            ColSpec("string", "model_version"),
            ColSpec("string", "policy_version"),
            ColSpec("string", "runtime_version"),
        ]
    ),
)
requirements = [
    f"mlflow=={mlflow.__version__}",
    "joblib==1.5.3",
    "numpy==2.5.1",
    "pandas==3.0.3",
    "pyarrow==24.0.0",
    "scikit-learn==1.9.0",
    "scipy==1.18.1",
    "xgboost==3.3.0",
]

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(EXPERIMENT)
client = MlflowClient(registry_uri="databricks-uc")
existing = None
versions = list(client.search_model_versions(f"name='{MODEL_NAME}'"))
operation = "CREATED"
try:
    existing = client.get_model_version_by_alias(MODEL_NAME, "Candidate")
    operation = "REUSED"
except Exception:
    if len(versions) == 1:
        # Recover the sole artifact from an interrupted post-registration validation.
        existing = versions[0]
        operation = "RECOVERED"

if existing is None:
    with mlflow.start_run(run_name=f"phase5-{release_key[:12]}") as run:
        mlflow.log_params(
            {
                "runtime_version": RUNTIME_VERSION,
                "model_version": MODEL_VERSION,
                "policy_version": POLICY_VERSION,
                "production_approved": False,
            }
        )
        model_info = mlflow.pyfunc.log_model(
            artifact_path="functional_recommender",
            python_model=RetailHyperPersonalizationModel(),
            artifacts={"data": DATA_ROOT, "models": MODEL_ROOT},
            code_paths=[str(runtime_source)],
            signature=signature,
            input_example=golden.head(1),
            pip_requirements=requirements,
            metadata={
                "classification": "synthetic_data",
                "production_approved": False,
                "transfer_version": "retail_hp_transfer_v1",
                "feature_version": "retail_hp_lakehouse_v1",
            },
        )
        registered = mlflow.register_model(model_info.model_uri, MODEL_NAME)
        version = str(registered.version)
else:
    version = str(existing.version)

model_uri = f"models:/{MODEL_NAME}/{version}"
loaded = mlflow.pyfunc.load_model(model_uri)
remote = loaded.predict(golden)
identity_columns = [
    "request_id",
    "rank",
    "product_id",
    "route",
    "candidate_sources",
    "reason_codes",
]
require(
    local[identity_columns].equals(remote[identity_columns]),
    "Registered top-10 or route parity failed",
)
score_error = float((local.score - remote.score).abs().max())
require(score_error <= 1e-12, "Registered score parity failed")
require(
    remote.product_id.isin(runtime.eligible_ids).all(),
    "Registered model returned ineligible products",
)
require(
    remote.model_version.eq(MODEL_VERSION).all() and remote.policy_version.eq(POLICY_VERSION).all(),
    "Registered lineage failed",
)

# Candidate is assigned only after the registered artifact has passed reload/parity checks.
client.set_model_version_tag(MODEL_NAME, version, "retail_hp_release_key", release_key)
client.set_model_version_tag(MODEL_NAME, version, "retail_hp_validation_status", "PASS")
client.set_model_version_tag(
    MODEL_NAME, version, "retail_hp_production_status", "HOLD_PENDING_FUTURE_HOLDOUT"
)
client.set_registered_model_alias(MODEL_NAME, "Candidate", version)

champion_present = True
try:
    client.get_model_version_by_alias(MODEL_NAME, "Champion")
except Exception:
    champion_present = False
require(not champion_present, "Champion alias must remain unset during Phase 5")

report = {
    "version": "azure_phase5_registration_v1",
    "status": "PASS",
    "operation": operation,
    "registered_model": MODEL_NAME,
    "registered_version": version,
    "candidate_alias": version,
    "champion_alias_present": False,
    # Ownership is enforced by the SDK controller after this notebook exits.
    "required_owner": OWNER,
    "release_key": release_key,
    "golden_request_count": len(golden),
    "golden_output_rows": len(remote),
    "route_parity_pct": 100.0,
    "top10_exact_parity_pct": 100.0,
    "maximum_score_error": score_error,
    "golden_output_sha256": canonical_output_sha(remote),
    "inventory_valid_pct": 100.0,
    "customer_coverage_pct": 100.0,
    "signature_present": True,
    "input_example_present": True,
    "dependency_count": len(requirements),
    "runtime_mlflow_version": mlflow.__version__,
    "model_version": MODEL_VERSION,
    "policy_version": POLICY_VERSION,
    "runtime_version": RUNTIME_VERSION,
    "production_approved": False,
    "production_status": "HOLD_PENDING_FUTURE_HOLDOUT",
    "persistent_job_created": False,
    "schedule_created": False,
    "serving_endpoint_created": False,
    "gpu": False,
    "new_azure_resources": 0,
    "identifiers_recorded": False,
}
dbutils.notebook.exit(json.dumps(report, sort_keys=True))
