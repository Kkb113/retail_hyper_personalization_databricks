from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd  # type: ignore[import-untyped]
import pytest
from retail_hp_azure.phase5 import ensure_registered_model_owner, inspect_registered_model
from retail_hp_azure.recommender import (
    MODEL_VERSION,
    POLICY_VERSION,
    AdaptiveRetailRecommender,
    RecommendationInputError,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def model() -> AdaptiveRetailRecommender:
    return AdaptiveRetailRecommender(
        ROOT / "migration_assets", ROOT / "migration_assets" / "artifacts"
    )


def _known_customer(model: AdaptiveRetailRecommender, *, low: bool = False) -> str:
    counts = model.events.groupby("CustomerID").size()
    return next(
        customer
        for customer in sorted(model.customer_map)
        if customer in model.profiles.index
        and ((1 <= counts.get(customer, 0) <= 10) if low else counts.get(customer, 0) > 10)
    )


def test_existing_customer_executes_composite_and_is_deterministic(
    model: AdaptiveRetailRecommender,
) -> None:
    request = pd.DataFrame(
        [
            {
                "request_id": "known-case",
                "request_type": "existing_customer",
                "customer_id": _known_customer(model),
                "top_n": 10,
                "as_of": "2026-01-01T00:00:00Z",
            }
        ]
    )
    first = model.predict(request)
    second = model.predict(request)
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 10
    assert first["route"].eq("adaptive_blend").all()
    assert first["candidate_sources"].str.contains("als").any()
    assert first["product_id"].isin(model.eligible_ids).all()
    assert first["product_id"].is_unique
    assert first["model_version"].eq(MODEL_VERSION).all()
    assert first["policy_version"].eq(POLICY_VERSION).all()


def test_low_history_and_new_customer_routes(model: AdaptiveRetailRecommender) -> None:
    low = _known_customer(model, low=True)
    profile = model.profiles.loc[low]
    request = pd.DataFrame(
        [
            {
                "request_id": "low-case",
                "request_type": "existing_customer",
                "customer_id": low,
                "top_n": 7,
            },
            {
                "request_id": "new-case",
                "request_type": "new_customer",
                "top_n": 7,
                "region_id": str(profile.RegionID),
                "customer_segment": str(profile.CustomerSegment),
                "favorite_category_id": str(profile.FavoriteCategoryID),
                "session_events_json": json.dumps(
                    [{"event_type": "browse", "product_id": str(model.products.iloc[0].ProductID)}]
                ),
            },
        ]
    )
    output = model.predict(request)
    assert output.groupby("request_id").size().to_dict() == {"low-case": 7, "new-case": 7}
    assert output[output.request_id.eq("new-case")].route.eq("cold_only").all()
    assert output[output.request_id.eq("new-case")].cold_weight.eq(1.0).all()
    assert output.product_id.isin(model.eligible_ids).all()


@pytest.mark.parametrize(
    "record,message",
    [
        ({"request_id": "x", "request_type": "bad"}, "request_type"),
        (
            {"request_id": "x", "request_type": "existing_customer"},
            "customer_id",
        ),
        (
            {
                "request_id": "x",
                "request_type": "new_customer",
                "top_n": 51,
            },
            "top_n",
        ),
        (
            {
                "request_id": "x",
                "request_type": "new_customer",
                "session_events_json": "not-json",
            },
            "session_events_json",
        ),
    ],
)
def test_typed_input_failures(
    model: AdaptiveRetailRecommender, record: dict[str, object], message: str
) -> None:
    with pytest.raises(RecommendationInputError, match=message):
        model.predict(pd.DataFrame([record]))


def test_phase5_source_has_no_ui_llm_or_cloud_dependency() -> None:
    source = (ROOT / "azure_databricks/src/retail_hp_azure/recommender.py").read_text()
    lowered = source.lower()
    assert "streamlit" not in lowered
    assert "openai" not in lowered
    assert "azure.identity" not in lowered
    assert "spark.sql" not in lowered


def test_registry_aliases_are_case_insensitive() -> None:
    context = MagicMock()
    context.client.registered_models.list.return_value = [
        SimpleNamespace(full_name="intellify_databricks_demo.ml.adaptive_recommender")
    ]
    context.client.registered_models.get.return_value = SimpleNamespace(
        owner="retail_hp_admins",
        aliases=[SimpleNamespace(alias_name="candidate", version_num=1)],
    )
    context.client.model_versions.list.return_value = [SimpleNamespace(version=1)]
    result = inspect_registered_model(context)
    assert result["candidate_present"] is True
    assert result["champion_present"] is False


def test_owner_control_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    context = MagicMock()
    context.apply = True
    snapshots = iter(
        [
            {"status": "PASS", "owner_valid": False},
            {
                "status": "PASS",
                "owner_valid": True,
                "candidate_present": True,
                "champion_present": False,
                "owner": "retail_hp_admins",
            },
        ]
    )
    monkeypatch.setattr(
        "retail_hp_azure.phase5.inspect_registered_model", lambda _: next(snapshots)
    )
    result = ensure_registered_model_owner(context)
    context.client.registered_models.update.assert_called_once_with(
        "intellify_databricks_demo.ml.adaptive_recommender", owner="retail_hp_admins"
    )
    assert result["operation"] == "UPDATED"
    assert result["compute_started"] is False
