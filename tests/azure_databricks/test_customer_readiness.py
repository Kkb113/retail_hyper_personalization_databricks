"""Coverage, identity and business-grounding regressions. No Azure or LLM calls."""

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from retail_hp_azure.business_explanations import product_reason
from retail_hp_azure.config import HOST
from retail_hp_azure.customer_context import customer_view_sql
from retail_hp_azure.demo_cohort import select_demo_customers
from retail_hp_azure.phase8 import Customer, ToolContext
from retail_hp_azure.phase8_backend import DatabricksToolBackend
from retail_hp_azure.phase10_runtime import RunningBackend, WorkbenchRuntime
from retail_hp_azure.safety import SafetyError


def runtime(tmp_path, *, dynamic=True):
    app = Mock()
    app.config.host, app.config.auth_type = HOST, "oauth-m2m"
    caller = Mock()
    caller.current_user.me.return_value = SimpleNamespace(id="owner", active=True)
    engine = WorkbenchRuntime(
        app_client=app,
        warehouse_id="fixed",
        entitlements={"owner": ToolContext(subject="owner", allowed_customers={"CUS000001"})},
        actor_secret=b"a" * 64,
        ledger=tmp_path / "ledger.json",
        index=None,
        lease_expires=time.time() + 600,
        trace_sink=lambda _: None,
        user_client_factory=lambda token: caller,
        cohort_subjects=frozenset({"owner"}) if dynamic else frozenset(),
    )
    return engine, caller


def test_published_cohort_replaces_numeric_range_using_caller_identity(tmp_path, monkeypatch):
    engine, caller = runtime(tmp_path)
    seen = []

    def ready(backend):
        seen.append(backend.client)
        return ["CUS000051", "CUS004951"]

    monkeypatch.setattr(RunningBackend, "ready_customers", ready)
    context = engine.authenticate("caller-token")
    assert context.allowed_customers == {"CUS000051", "CUS004951"}
    assert seen == [caller]
    assert engine.entitlements["owner"].allowed_customers == {"CUS000001"}


def test_fixed_tester_never_inherits_cohort_access(tmp_path, monkeypatch):
    engine, _ = runtime(tmp_path, dynamic=False)
    query = Mock(side_effect=AssertionError("Must not query broader cohort"))
    monkeypatch.setattr(RunningBackend, "ready_customers", query)
    assert engine.authenticate("token").allowed_customers == {"CUS000001"}
    query.assert_not_called()


def test_metadata_authentication_never_starts_sql(tmp_path, monkeypatch):
    engine, _ = runtime(tmp_path)
    query = Mock(side_effect=AssertionError("Health must not query SQL"))
    monkeypatch.setattr(RunningBackend, "ready_customers", query)
    assert engine.authenticate("token", metadata_only=True).subject == "owner"
    query.assert_not_called()


def test_cohort_lookup_allowance_blocks_before_query(tmp_path, monkeypatch):
    engine, _ = runtime(tmp_path)
    engine._cohort_reads = 200
    query = Mock()
    monkeypatch.setattr(RunningBackend, "ready_customers", query)
    with pytest.raises(SafetyError, match="allowance exhausted"):
        engine.authenticate("token")
    query.assert_not_called()


def test_unknown_actor_is_rejected_before_cohort_query(tmp_path, monkeypatch):
    engine, caller = runtime(tmp_path)
    caller.current_user.me.return_value.id = "unknown"
    query = Mock()
    monkeypatch.setattr(RunningBackend, "ready_customers", query)
    with pytest.raises(SafetyError):
        engine.authenticate("token")
    query.assert_not_called()


def test_cohort_refresh_never_retains_removed_customers(tmp_path, monkeypatch):
    engine, _ = runtime(tmp_path)
    query = Mock(side_effect=[["CUS000051"], []])
    monkeypatch.setattr(RunningBackend, "ready_customers", query)
    assert engine.authenticate("token").allowed_customers == {"CUS000051"}
    assert not engine.authenticate("token").allowed_customers


def test_cohort_requires_complete_unique_published_recommendations():
    backend = DatabricksToolBackend(Mock(), "warehouse")
    backend._query = Mock(return_value=[{"customer_id": "CUS004951"}])
    assert backend.ready_customers() == ["CUS004951"]
    sql, values = backend._query.call_args.args
    assert "count(DISTINCT r.product_id) = 10" in sql
    assert "count(DISTINCT r.rank) = 10" in sql
    assert "count(DISTINCT r.batch_id) = 1" in sql
    assert "registered_model_version = '3'" in sql
    assert values == {}
    backend._query.return_value = [{"customer_id": "CUS000001"}] * 2
    with pytest.raises(SafetyError, match="Duplicate"):
        backend.ready_customers()


def test_view_bounds_history_and_does_not_expose_order_identifiers():
    sql = customer_view_sql()
    assert "o.order_date < c.behavior_as_of" in sql
    assert "WHERE rn <= 10" in sql
    assert "lower(o.order_status) != 'cancelled'" in sql
    assert "synthetic_profile_not_verified_preference" in sql
    assert "'order_id'" not in sql
    assert "CREATE TABLE" not in sql


def test_evidence_contract_is_bounded_and_unknown_fields_fail():
    base = dict(
        customer_id="CUS000051",
        customer_segment="Fitness",
        loyalty_tier="Bronze",
        preferred_channel="Store",
        region_id="R1",
        behavior_as_of="2026-01-01",
        purchase_count=3,
        browse_count=2,
    )
    purchase = dict(
        position=1,
        product_id="PRO000001",
        product_name="Boots",
        category_name="Footwear",
        brand_name="Brand",
        last_purchased_at="2025-01-01",
        purchase_count=1,
    )
    assert Customer(**base, recent_purchases=[purchase]).recent_purchases[0].product_name == "Boots"
    with pytest.raises(ValueError):
        Customer(**base, recent_purchases=[purchase] * 11)
    with pytest.raises(ValueError):
        Customer(**base, recent_purchases=[{**purchase, "email": "not-permitted"}])


def test_explanations_use_product_history_not_scores_or_invented_preferences():
    product = dict(product_id="PRO000001", category_name="Footwear", brand_name="Brand", score=1)
    assert "does not establish" in product_reason(product, {})
    customer = {
        "recent_purchases": [
            {"product_id": "PRO000099", "category_name": "Footwear", "brand_name": "Other"}
        ]
    }
    assert "Past purchases include Footwear" in product_reason(product, customer)
    assert "Previously purchased" in product_reason(product, {"recent_purchases": [product]})
    assert "demo customer profile" in product_reason(product, {"favorite_brand_name": "Brand"})
    assert "score" not in product_reason(product, customer)


def test_customer_json_is_parsed_without_unbounded_payload():
    backend = DatabricksToolBackend(Mock(), "warehouse")
    backend._query = Mock(
        side_effect=[
            [{"behavior_as_of": "2026-01-01", "recent_purchases_json": json.dumps([])}],
            [{"source_at": "2026-01-01"}],
        ]
    )
    rows, _ = backend.read("get_customer_360", {"customer_id": "CUS000051"}, "test")
    assert rows[0]["recent_purchases"] == []
    assert "recent_purchases_json" not in rows[0]


def test_cohort_policy_matches_published_snapshot_and_is_order_independent():
    population = [f"CUS{i:06d}" for i in range(1, 5001)]
    assert select_demo_customers(population) == population[::50]
    assert select_demo_customers(list(reversed(population))) == population[::50]
    with pytest.raises(SafetyError):
        select_demo_customers(["CUS000001", "CUS000001"])
