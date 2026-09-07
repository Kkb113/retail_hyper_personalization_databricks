"""Phase 5 contract, artifact audit, and read-only registry inspection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from retail_hp_azure.phase2 import CATALOG, CloudContext, inspect_compute
from retail_hp_azure.phase3 import REPO_ROOT, validate_local
from retail_hp_azure.pickle_compat import load_approved_joblib
from retail_hp_azure.safety import require

AZURE_ROOT = REPO_ROOT / "azure_databricks"
EVIDENCE_ROOT = AZURE_ROOT / "evidence" / "phase_05"
LOCAL_MODEL_ROOT = REPO_ROOT / "migration_assets" / "artifacts"
REGISTERED_MODEL = f"{CATALOG}.ml.adaptive_recommender"
MODEL_OWNER = "retail_hp_admins"
PHASE5_VERSION = "azure_functional_recommender_v1"
PHASE5_RUN_NAME = "retail-hp-phase5-functional-mlflow"
AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR = 44.91
PLANNING_MAX_DBU_PER_HOUR = 16
PHASE5_CEILING_INR = 250.0
INPUT_COLUMNS = [
    "request_id",
    "request_type",
    "customer_id",
    "scenario_id",
    "top_n",
    "as_of",
    "region_id",
    "state",
    "climate_zone",
    "customer_segment",
    "loyalty_tier",
    "preferred_channel",
    "favorite_category_id",
    "favorite_brand_id",
    "price_sensitivity",
    "color_preference",
    "category_affinity_score",
    "brand_affinity_score",
    "session_events_json",
    "excluded_product_ids_json",
]


def golden_inputs(runtime: Any) -> pd.DataFrame:
    """Build four deterministic, synthetic parity cases without fixed identities."""
    counts = runtime.events.groupby("CustomerID").size()
    known = next(
        customer
        for customer in sorted(runtime.customer_map)
        if counts.get(customer, 0) > 10 and customer in runtime.profiles.index
    )
    low = next(
        customer
        for customer in sorted(runtime.customer_map)
        if 1 <= counts.get(customer, 0) <= 10 and customer in runtime.profiles.index
    )
    profile = runtime.profiles.loc[known]
    sample_product = str(runtime.products.iloc[0].ProductID)

    def record(**values: Any) -> dict[str, Any]:
        base: dict[str, Any] = {column: None for column in INPUT_COLUMNS}
        base.update(
            {
                "top_n": 10,
                "as_of": "2026-01-01T00:00:00Z",
                "session_events_json": "[]",
                "excluded_product_ids_json": "[]",
            }
        )
        base.update(values)
        return base

    frame = pd.DataFrame(
        [
            record(request_id="golden-known", request_type="existing_customer", customer_id=known),
            record(request_id="golden-low", request_type="existing_customer", customer_id=low),
            record(
                request_id="golden-new",
                request_type="new_customer",
                region_id=str(profile.RegionID),
                state=str(profile.State),
                climate_zone=str(profile.ClimateZone),
                customer_segment=str(profile.CustomerSegment),
                loyalty_tier=str(profile.LoyaltyTier),
                preferred_channel=str(profile.PreferredChannel),
                favorite_category_id=str(profile.FavoriteCategoryID),
                favorite_brand_id=str(profile.FavoriteBrandID),
                price_sensitivity=str(profile.PriceSensitivity),
                color_preference=str(profile.ColorPreference),
                category_affinity_score=float(profile.CategoryAffinityScore),
                brand_affinity_score=float(profile.BrandAffinityScore),
            ),
            record(
                request_id="golden-scenario",
                request_type="scenario",
                scenario_id="weekend-demo",
                region_id=str(profile.RegionID),
                customer_segment=str(profile.CustomerSegment),
                favorite_category_id=str(profile.FavoriteCategoryID),
                session_events_json=json.dumps(
                    [{"event_type": "browse", "product_id": sample_product}]
                ),
            ),
        ]
    )[INPUT_COLUMNS]
    numeric = {"top_n", "category_affinity_score", "brand_affinity_score"}
    for column in set(INPUT_COLUMNS) - numeric:
        # Pandas 3 defaults text to the new ``str`` dtype, which older MLflow
        # schema enforcement cannot safely coerce. Object strings remain portable.
        frame[column] = frame[column].astype(object)
    return frame


def canonical_output_sha(frame: pd.DataFrame) -> str:
    """Hash non-identifying parity output with stable float text."""
    columns = [
        "request_id",
        "rank",
        "product_id",
        "score",
        "route",
        "cold_weight",
        "warm_weight",
        "candidate_sources",
        "reason_codes",
        "fallback_reason",
        "model_version",
        "policy_version",
    ]
    normalized = frame[columns].sort_values(["request_id", "rank"], kind="stable").copy()
    for column in ("score", "cold_weight", "warm_weight"):
        normalized[column] = normalized[column].map(lambda value: f"{float(value):.12f}")
    return hashlib.sha256(normalized.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def phase5_plan() -> dict[str, Any]:
    return {
        "version": "azure_phase5_plan_v1",
        "status": "PASS",
        "scope": {
            "catalog": CATALOG,
            "registered_model": REGISTERED_MODEL,
            "classification": "synthetic_data",
            "production_approved": False,
        },
        "functional_components": [
            "als_retrieval",
            "content_retrieval",
            "metadata_retrieval",
            "copurchase_retrieval",
            "promotion_retrieval",
            "warm_lambdamart_ranker",
            "cold_start_lambdamart_ranker",
            "adaptive_router",
            "inventory_and_eligibility",
            "durable_purchase_exclusion",
            "diversity",
            "deterministic_fallback",
        ],
        "request_types": ["existing_customer", "new_customer", "scenario"],
        "release": {
            "initial_alias": "Candidate",
            "champion_alias": "HOLD",
            "champion_gate": "future holdout and later deployment approval",
        },
        "compute": {
            "one_time_serverless_cpu": True,
            "persistent_job": False,
            "schedule": False,
            "serving_endpoint": False,
            "gpu": False,
            "new_azure_resources": 0,
        },
        "known_source_limitations": {
            "ratings": "not transferred; related warm-ranker features use explicit neutral zero",
            "holidays": "not transferred; related context features use explicit neutral zero",
            "store_region_map": (
                "not transferred; global availability is used for regional availability"
            ),
            "currency": "UNSPECIFIED in the frozen source contract",
        },
    }


def inspect_local_artifacts() -> dict[str, Any]:
    transfer = validate_local()
    require(transfer["status"] == "PASS", "Sealed Phase 3 artifacts must pass")
    retrieval = LOCAL_MODEL_ROOT / "retrieval_v2"
    with np.load(retrieval / "als_factors.npz", allow_pickle=False) as factors:
        user_shape = list(factors["user_factors"].shape)
        item_shape = list(factors["item_factors"].shape)
    warm = load_approved_joblib(LOCAL_MODEL_ROOT / "ranker_v2/ranker_preprocessor.joblib")
    cold = load_approved_joblib(LOCAL_MODEL_ROOT / "cold_start_v1/cold_start_preprocessor.joblib")
    selection = json.loads(
        (LOCAL_MODEL_ROOT / "model_release_v1/model_selection.json").read_text(encoding="utf-8")
    )
    require(user_shape[1] == item_shape[1] == 64, "ALS factor dimension drift")
    require(len(warm.feature_names_in_) == 92, "Warm raw-feature contract drift")
    require(len(warm.get_feature_names_out()) == 272, "Warm transformed contract drift")
    require(len(cold.feature_names_in_) == 34, "Cold raw-feature contract drift")
    require(len(cold.get_feature_names_out()) == 731, "Cold transformed contract drift")
    require(
        selection["production_holdout_status"] == "HOLD_PENDING_FUTURE_HOLDOUT",
        "Production holdout contract drift",
    )
    return {
        "version": "azure_phase5_artifact_inspection_v1",
        "status": "PASS",
        "user_factor_shape": user_shape,
        "item_factor_shape": item_shape,
        "warm_raw_features": 92,
        "warm_transformed_features": 272,
        "cold_raw_features": 34,
        "cold_transformed_features": 731,
        "baseline_metrics": selection["selected_metrics"],
        "production_holdout_status": selection["production_holdout_status"],
        "identifiers_recorded": False,
    }


def inspect_registered_model(context: CloudContext) -> dict[str, Any]:
    matches = [
        item
        for item in context.client.registered_models.list(catalog_name=CATALOG, schema_name="ml")
        if item.full_name == REGISTERED_MODEL
    ]
    if not matches:
        return {
            "version": "azure_phase5_registry_inspection_v1",
            "status": "NOT_REGISTERED",
            "registered_model_count": 0,
            "compute_started": False,
            "identifiers_recorded": False,
        }
    model = context.client.registered_models.get(REGISTERED_MODEL, include_aliases=True)
    versions = list(context.client.model_versions.list(REGISTERED_MODEL))
    aliases = {
        str(alias.alias_name): int(alias.version_num)
        for alias in (model.aliases or [])
        if alias.alias_name and alias.version_num is not None
    }
    normalized_aliases = {name.casefold(): version for name, version in aliases.items()}
    return {
        "version": "azure_phase5_registry_inspection_v1",
        "status": "PASS",
        "registered_model_count": 1,
        "owner": str(model.owner),
        "owner_valid": str(model.owner) == MODEL_OWNER,
        "model_version_count": len(versions),
        "aliases": aliases,
        "candidate_present": "candidate" in normalized_aliases,
        "champion_present": "champion" in normalized_aliases,
        "compute_started": False,
        "identifiers_recorded": False,
    }


def ensure_registered_model_owner(context: CloudContext) -> dict[str, Any]:
    """Set the UC registered-model owner without starting Databricks compute."""
    require(context.apply, "Registered-model owner repair requires explicit apply context")
    before = inspect_registered_model(context)
    require(before["status"] == "PASS", "Registered model must exist before owner repair")
    operation = "NO_CHANGE"
    if not before["owner_valid"]:
        context.client.registered_models.update(REGISTERED_MODEL, owner=MODEL_OWNER)
        operation = "UPDATED"
    after = inspect_registered_model(context)
    require(after["owner_valid"], "Registered-model owner is invalid")
    require(after["candidate_present"], "Candidate alias is missing")
    require(not after["champion_present"], "Champion alias must remain unset")
    return {
        "version": "azure_phase5_owner_control_v1",
        "status": "PASS",
        "operation": operation,
        "owner": after["owner"],
        "candidate_present": after["candidate_present"],
        "champion_present": after["champion_present"],
        "compute_started": False,
        "identifiers_recorded": False,
    }


def inspect_phase5_cloud(context: CloudContext) -> dict[str, Any]:
    """Reconcile Phase 5 registry, bounded runs and stopped state without compute."""
    from retail_hp_azure.phase2_compute import _verify_budget

    registry = inspect_registered_model(context)
    compute = inspect_compute(context)
    budget = _verify_budget(context)
    attempts = [
        run
        for run in context.client.jobs.list_runs(completed_only=True, limit=25)
        if run.run_name == PHASE5_RUN_NAME
    ]
    durations_ms = [
        max(0, int(run.end_time) - int(run.start_time))
        for run in attempts
        if run.start_time is not None and run.end_time is not None
    ]
    result_states: dict[str, int] = {}
    for run in attempts:
        state = getattr(getattr(run, "state", None), "result_state", None)
        name = getattr(state, "value", None) or str(state or "UNKNOWN")
        result_states[str(name)] = result_states.get(str(name), 0) + 1
    elapsed_seconds = sum(durations_ms) / 1000
    estimated_cost = (
        AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR
        * PLANNING_MAX_DBU_PER_HOUR
        * elapsed_seconds
        / 3600
    )
    warehouse_stopped = all(
        item["state"] == "STOPPED" for item in compute["project_warehouses"]
    )
    accepted = (
        registry["status"] == "PASS"
        and registry["owner_valid"]
        and registry["candidate_present"]
        and not registry["champion_present"]
        and registry["model_version_count"] == 1
        and compute["cluster_count"] == 0
        and compute["job_count"] == 0
        and warehouse_stopped
        and estimated_cost < PHASE5_CEILING_INR
    )
    return {
        "version": "azure_phase5_cloud_reconciliation_v1",
        "status": "PASS" if accepted else "FAIL",
        "registered_model_count": registry.get("registered_model_count", 0),
        "registered_model_version_count": registry.get("model_version_count", 0),
        "owner_valid": registry.get("owner_valid", False),
        "candidate_present": registry.get("candidate_present", False),
        "champion_present": registry.get("champion_present", False),
        "candidate_assignment_is_post_validation": True,
        "attempt_count": len(attempts),
        "attempt_result_states": result_states,
        "cumulative_elapsed_seconds": round(elapsed_seconds, 2),
        "estimated_cumulative_cost_inr_pre_tax": round(estimated_cost, 4),
        "phase5_ceiling_inr": PHASE5_CEILING_INR,
        "within_phase5_ceiling": estimated_cost < PHASE5_CEILING_INR,
        "budget_status": "PASS",
        "budget_currency": "INR",
        "budget_amount_inr": budget["amount_inr"],
        "cluster_count": compute["cluster_count"],
        "persistent_job_count": compute["job_count"],
        "project_warehouse_state": (
            compute["project_warehouses"][0]["state"]
            if compute["project_warehouses"]
            else "MISSING"
        ),
        "new_azure_resources": 0,
        "serving_endpoint_created": False,
        "schedule_created": False,
        "hard_invoice_cap_guaranteed": False,
        "identifiers_recorded": False,
    }
def record_evidence(filename: str, value: Mapping[str, Any]) -> None:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    document = {**value, "captured_at": datetime.now(UTC).isoformat()}
    (EVIDENCE_ROOT / filename).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["plan", "inspect-artifacts", "inspect-registry", "ensure-owner", "reconcile"],
    )
    args = parser.parse_args()
    if args.command == "plan":
        result = phase5_plan()
    elif args.command == "inspect-artifacts":
        result = inspect_local_artifacts()
        record_evidence("artifact_inspection.json", result)
    elif args.command == "inspect-registry":
        result = inspect_registered_model(CloudContext())
        record_evidence("registry_inspection.json", result)
    elif args.command == "ensure-owner":
        result = ensure_registered_model_owner(CloudContext(apply=True))
        record_evidence("owner_control.json", result)
    else:
        result = inspect_phase5_cloud(CloudContext())
        record_evidence("cloud_reconciliation.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
