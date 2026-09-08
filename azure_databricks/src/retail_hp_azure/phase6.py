"""Phase 6 release admission and bounded CPU endpoint configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retail_hp_azure.model_identity import MODEL_OWNER, REGISTERED_MODEL
from retail_hp_azure.safety import require

ENDPOINT_NAME = "retail-hp-poc-recommender"


def configure_mlflow_identity() -> None:
    """Bind local MLflow operations to the same scoped Azure CLI identity."""
    import os

    from retail_hp_azure.config import HOST

    os.environ["DATABRICKS_HOST"] = HOST
    os.environ["DATABRICKS_AUTH_TYPE"] = "azure-cli"


def resolve_release(
    registry: Mapping[str, Any], *, candidate_demo_approved: bool = False
) -> tuple[int, str]:
    """Pin a version once; a Candidate exception never promotes Champion."""
    require(registry.get("owner") == MODEL_OWNER, "Registered model owner drift")
    aliases = {str(key).casefold(): value for key, value in registry.get("aliases", {}).items()}
    alias = "candidate" if candidate_demo_approved else "champion"
    require(alias in aliases, f"Required {alias} release is unavailable")
    version = aliases[alias]
    require(type(version) is int and version > 0, "Invalid registered model version")
    return int(version), "synthetic_demo_only" if candidate_demo_approved else "champion"


def endpoint_configuration(version: int) -> dict[str, Any]:
    require(type(version) is int and version > 0, "Invalid registered model version")
    return {
        "name": ENDPOINT_NAME,
        "config": {
            "served_entities": [
                {
                    "name": "retail-recommender",
                    "entity_name": REGISTERED_MODEL,
                    "entity_version": str(version),
                    "workload_type": "CPU",
                    "workload_size": "Small",
                    "scale_to_zero_enabled": True,
                }
            ]
        },
    }


def admit_session(
    *,
    hourly_cost_inr: float,
    active_minutes: float,
    prior_cost_inr: float,
    ceiling_inr: float = 250.0,
    launch_cost_inr: float = 0.0,
) -> float:
    """Reserve the native idle tail and a 2x margin before a paid session."""
    import math

    values = (hourly_cost_inr, active_minutes, prior_cost_inr, ceiling_inr, launch_cost_inr)
    require(all(math.isfinite(v) for v in values), "Cost inputs must be finite")
    require(hourly_cost_inr > 0 and active_minutes > 0, "Missing runtime cost estimate")
    require(prior_cost_inr >= 0 and 0 < ceiling_inr <= 400, "Invalid session allowance")
    require(launch_cost_inr >= 0, "Invalid launch cost")
    reserved = (hourly_cost_inr * (active_minutes + 30) / 60 + launch_cost_inr) * 2
    require(prior_cost_inr + reserved <= ceiling_inr, "Insufficient phase cost headroom")
    return reserved
