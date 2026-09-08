"""Phase 8 contract, authorization, grounding and cost-safety regression suite."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from retail_hp_azure.phase7 import InMemoryOperationalStore
from retail_hp_azure.phase8 import CATALOG, GovernedTools, Provenance, ToolContext, tool_schemas
from retail_hp_azure.phase8_backend import DatabricksToolBackend
from retail_hp_azure.phase8_semantic import (
    DOCUMENT_VERSION,
    EMBEDDING_MODEL,
    SemanticIndex,
    normalize,
    product_document,
)
from retail_hp_azure.safety import SafetyError

CONTEXT = ToolContext(
    subject="synthetic-user", allowed_customers={"CUS000001"}, can_view_quality=True
)
PROVENANCE = Provenance(
    source="synthetic-fixture",
    source_timestamp="2025-12-31",
    data_version="fixture-v1",
    model_version="3",
)
REC = {
    "product_id": "PRO000001",
    "rank": 1,
    "score": 0.5,
    "route": "cold_only",
    "reason_codes": "route:cold_only",
}
PRODUCT = {
    "product_id": "PRO000001",
    "product_name": "Winter jacket",
    "category_id": "CAT000001",
    "category_name": "Jackets",
    "brand_name": "Demo",
    "base_price": 30.0,
    "available_qty": 5,
    "inventory_snapshot_at": "2025-12-31",
}
FEEDBACK = {
    "customer_id": "CUS000001",
    "idempotency_key": "feedback-0001",
    "feedback": {
        "sentiment": "positive",
        "reason_code": "relevant",
        "product_id": "PRO000001",
        "recommendation_request_id": "request-0001",
    },
}


def suite():
    backend = Mock()
    backend.read.return_value = ([REC], PROVENANCE)
    backend.write_feedback.side_effect = InMemoryOperationalStore().write
    traces = []
    return GovernedTools(backend, actor_secret=b"s" * 32, trace_sink=traces.append), backend, traces


def test_all_ten_tools_have_closed_versioned_input_and_output_schemas():
    schemas = tool_schemas()
    assert len(schemas) == 10
    for schema in schemas.values():
        assert schema["input"]["additionalProperties"] is False
        assert schema["output"]["additionalProperties"] is False
        assert schema["output"]["properties"]["rows"]["maxItems"] == 20


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_customer_360", {}),
        ("get_recommendations", {}),
        ("explain_recommendation", {"product_id": "PRO000001"}),
        ("simulate_scenario", {"scenario_id": "scenario-1"}),
        ("get_opportunities", {}),
        ("record_feedback", FEEDBACK),
    ],
)
def test_customer_denied_before_any_backend_access(tool, args):
    tools, backend, traces = suite()
    with pytest.raises(SafetyError, match="CUSTOMER_ACCESS_DENIED"):
        tools.execute(
            tool,
            {**args, "customer_id": "CUS000002"},
            context=CONTEXT,
            request_id="request-1",
            write_confirmed=True,
        )
    backend.read.assert_not_called()
    backend.write_feedback.assert_not_called()
    assert traces[0]["status"] == "DENIED_OR_FAILED"
    assert "CUS000002" not in json.dumps(traces)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_products", {"query": "x" * 301}),
        ("search_products", {"query": "jacket", "max_price": float("inf")}),
        ("search_products", {"query": "jacket", "table": "secret"}),
        ("get_customer_360", {"customer_id": "CUS000001' OR 1=1--"}),
        ("get_recommendations", {"customer_id": "CUS000001", "top_n": 21}),
        ("compare_products", {"product_ids": ["PRO000001"] * 5}),
        ("record_feedback", {**FEEDBACK, "write_confirmed": True}),
    ],
)
def test_malformed_or_injected_requests_fail_before_backend(tool, args):
    tools, backend, _ = suite()
    with pytest.raises((ValidationError, ValueError, SafetyError)):
        tools.execute(tool, args, context=CONTEXT, request_id="request-1")
    backend.read.assert_not_called()


def test_feedback_requires_server_confirmation_and_is_idempotent():
    tools, backend, _ = suite()
    with pytest.raises(SafetyError, match="CONFIRMATION"):
        tools.execute("record_feedback", FEEDBACK, context=CONTEXT, request_id="request-1")
    backend.read.assert_not_called()
    first = tools.execute(
        "record_feedback", FEEDBACK, context=CONTEXT, request_id="request-1", write_confirmed=True
    )
    second = tools.execute(
        "record_feedback", FEEDBACK, context=CONTEXT, request_id="request-2", write_confirmed=True
    )
    assert first.rows[0]["replayed"] is False
    assert second.rows[0]["replayed"] is True
    assert first.rows[0]["event_id"] == second.rows[0]["event_id"]


def test_feedback_rejects_product_not_recommended():
    tools, backend, _ = suite()
    backend.read.return_value = ([], PROVENANCE)
    with pytest.raises(SafetyError, match="RECOMMENDED_PRODUCT"):
        tools.execute(
            "record_feedback",
            FEEDBACK,
            context=CONTEXT,
            request_id="request-1",
            write_confirmed=True,
        )
    backend.write_feedback.assert_not_called()


@pytest.mark.parametrize("confirmation", ["false", "true", 1, [True]])
def test_feedback_rejects_truthy_non_boolean_confirmation(confirmation):
    tools, backend, _ = suite()
    with pytest.raises(SafetyError, match="CONFIRMATION"):
        tools.execute(
            "record_feedback",
            FEEDBACK,
            context=CONTEXT,
            request_id="request-1",
            write_confirmed=confirmation,
        )
    backend.read.assert_not_called()
    backend.write_feedback.assert_not_called()


def test_feedback_rejects_mismatched_backend_product():
    tools, backend, _ = suite()
    backend.read.return_value = ([{**REC, "product_id": "PRO000002"}], PROVENANCE)
    with pytest.raises(SafetyError, match="RECOMMENDED_PRODUCT"):
        tools.execute(
            "record_feedback",
            FEEDBACK,
            context=CONTEXT,
            request_id="request-1",
            write_confirmed=True,
        )
    backend.write_feedback.assert_not_called()


def snapshot_fixture():
    return {
        "model": EMBEDDING_MODEL,
        "document_version": DOCUMENT_VERSION,
        "snapshot_version": "a" * 64,
        "generated_at": "2026-09-08T00:00:00+00:00",
        "rows": [{"product_id": "PRO000001", "embedding": [1.0] + [0.0] * 1023}],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_version", "not-a-version"),
        ("snapshot_version", None),
        ("generated_at", "2026-09-08"),
        ("generated_at", None),
        ("generated_at", "invalid"),
    ],
)
def test_semantic_snapshot_metadata_fails_closed(field, value):
    with pytest.raises((SafetyError, ValueError)):
        SemanticIndex({**snapshot_fixture(), field: value})


def test_semantic_index_rejects_bad_ids_and_missing_eligible_products():
    payload = snapshot_fixture()
    payload["rows"][0]["product_id"] = "arbitrary-id"
    with pytest.raises(SafetyError, match="identifier"):
        SemanticIndex(payload)
    index = SemanticIndex(snapshot_fixture())
    with pytest.raises(SafetyError, match="REBUILD_REQUIRED"):
        index.rank([1.0] + [0.0] * 1023, {"PRO000002"})


def test_stale_search_index_fails_before_paid_embedding(monkeypatch):
    embeddings = Mock()
    backend = DatabricksToolBackend(
        Mock(), "warehouse", index=SemanticIndex(snapshot_fixture()), embeddings=embeddings
    )
    monkeypatch.setattr(backend, "_query", lambda *_: [{"ids": '["PRO000002"]'}])
    with pytest.raises(SafetyError, match="REBUILD_REQUIRED"):
        backend.read("search_products", {"query": "jacket", "top_n": 5}, "request-1")
    embeddings.embed.assert_not_called()


def test_embedding_quota_is_atomic_under_concurrency(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from retail_hp_azure.phase8_backend import EmbeddingClient

    client = Mock()
    entity = Mock()
    entity.foundation_model.name = EMBEDDING_MODEL
    client.serving_endpoints.get.return_value.config.served_entities = [entity]
    response = Mock(status_code=200, content=b"ok")
    response.json.return_value = {
        "data": [{"index": 0, "embedding": [1.0] + [0.0] * 1023}],
        "usage": {"total_tokens": 1},
    }
    post = Mock(return_value=response)
    monkeypatch.setattr("requests.post", post)
    embeddings = EmbeddingClient(client, max_calls=1)

    def invoke(_):
        try:
            embeddings.embed("jacket")
            return True
        except SafetyError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(invoke, range(8))) == 1
    assert post.call_count == 1 and embeddings.remaining == 0 and embeddings.tokens == 1


def test_batch_query_rechecks_current_product_eligibility(monkeypatch):
    backend = DatabricksToolBackend(Mock(), "warehouse")
    calls = []
    monkeypatch.setattr(
        backend,
        "_query",
        lambda sql, args: calls.append((sql, args))
        or ([{"source_at": "2025-12-31"}] if "max(" in sql else []),
    )
    backend.read("get_recommendations", {"customer_id": "CUS000001", "top_n": 10}, "req")
    assert "JOIN intellify_databricks_demo.serving.tool_products" in calls[0][0]
    assert calls[0][1]["customer"] == "CUS000001"


@pytest.mark.parametrize("version", ["3.16.0", "3.8.1"])
def test_runtime_verifier_checks_artifact_without_loading_model(tmp_path, monkeypatch, version):
    import io
    import runpy

    root = Path(__file__).resolve().parents[2]
    verify = runpy.run_path(str(root / "azure_databricks/scripts/phase8_verify_runtime.py"))[
        "verify"
    ]
    directory = tmp_path / "azure_databricks/environments"
    directory.mkdir(parents=True)
    (directory / "phase6_model_requirements.txt").write_text("mlflow==3.16.0\n")
    (tmp_path / "azure_databricks/evidence/phase_08").mkdir(parents=True)
    context = Mock()
    context.client.api_client.do.return_value = {
        "config": {"served_entities": [{"entity_version": "3"}]},
        "state": {"suspend": "STOPPED"},
    }
    context.client.files.download.return_value.contents = io.BytesIO(
        f"mlflow=={version}\n".encode()
    )
    monkeypatch.setitem(verify.__globals__, "CloudContext", lambda: context)
    monkeypatch.setitem(
        verify.__globals__, "inspect_registered_model", lambda _: {"aliases": {"champion": 3}}
    )
    monkeypatch.setitem(verify.__globals__, "_find_repo_root", lambda: tmp_path)
    if version == "3.16.0":
        result = verify()
        assert result["matches_phase6_requirements"] and not result["model_loaded"]
        assert not result["compute_started"]
    else:
        with pytest.raises(SafetyError, match="requirements differ"):
            verify()
    context.client.files.download.assert_called_once_with(
        "/Models/intellify_databricks_demo/ml/adaptive_recommender/3/requirements.txt"
    )
    assert all(call.args[0] == "GET" for call in context.client.api_client.do.call_args_list)
    context.client.warehouses.start.assert_not_called()


def test_output_validation_and_redacted_audit():
    tools, backend, traces = suite()
    backend.read.return_value = ([{**PRODUCT, "credential": "should-not-leak"}], PROVENANCE)
    with pytest.raises(ValidationError):
        tools.execute(
            "get_product_details",
            {"product_id": "PRO000001"},
            context=CONTEXT,
            request_id="request-1",
        )
    assert "should-not-leak" not in json.dumps(traces)
    assert "synthetic-user" not in json.dumps(traces)


def test_all_tools_can_execute_without_an_llm():
    cases = {
        "get_customer_360": (
            {"customer_id": "CUS000001"},
            [
                {
                    "customer_id": "CUS000001",
                    "customer_segment": "Demo",
                    "loyalty_tier": "Gold",
                    "preferred_channel": "Web",
                    "region_id": "REG000001",
                    "behavior_as_of": "2025-12-31",
                    "purchase_count": 1,
                    "browse_count": 2,
                }
            ],
        ),
        "get_recommendations": ({"customer_id": "CUS000001"}, [REC]),
        "explain_recommendation": ({"customer_id": "CUS000001", "product_id": "PRO000001"}, [REC]),
        "search_products": ({"query": "winter jacket"}, [PRODUCT]),
        "get_product_details": ({"product_id": "PRO000001"}, [PRODUCT]),
        "compare_products": ({"product_ids": ["PRO000001", "PRO000002"]}, [PRODUCT]),
        "simulate_scenario": ({"customer_id": "CUS000001", "scenario_id": "demo"}, [REC]),
        "get_opportunities": (
            {"customer_id": "CUS000001"},
            [
                {
                    "product_id": "PRO000001",
                    "opportunity_type": "ACTIVE_PROMOTION",
                    "discount_pct": 10,
                }
            ],
        ),
        "record_feedback": (FEEDBACK, [REC]),
        "get_quality_summary": (
            {},
            [{"model_version": "3", "batch_customers": 100, "batch_rows": 1000}],
        ),
    }
    assert set(cases) == set(CATALOG)
    for tool, (args, rows) in cases.items():
        tools, backend, traces = suite()
        backend.read.return_value = (rows, PROVENANCE)
        result = tools.execute(
            tool, args, context=CONTEXT, request_id="request-1", write_confirmed=True
        )
        assert result.rows and result.provenance.data_version == "fixture-v1"
        assert traces[-1]["status"] == "SUCCESS"


def test_cosine_normalization_ties_and_eligibility():
    vector = [2.0] + [0.0] * 1023
    index = SemanticIndex(
        {
            "model": EMBEDDING_MODEL,
            "document_version": DOCUMENT_VERSION,
            "snapshot_version": "a" * 64,
            "generated_at": "2026-09-08T00:00:00+00:00",
            "rows": [
                {"product_id": pid, "embedding": vector}
                for pid in ["PRO000002", "PRO000001", "PRO000003"]
            ],
        }
    )
    assert index.rank(vector, {"PRO000001", "PRO000002"}) == ["PRO000001", "PRO000002"]
    with pytest.raises(SafetyError):
        normalize([0.0] * 1024)
    with pytest.raises(SafetyError):
        normalize([float("nan")] * 1024)


def test_region_filter_fails_without_inventing_stock_mapping():
    backend = DatabricksToolBackend(Mock(), "warehouse")
    with pytest.raises(SafetyError, match="REGIONAL_INVENTORY_UNAVAILABLE"):
        backend.read("search_products", {"query": "jacket", "region_id": "REG000001"}, "req")


def test_embedding_document_reuse_ignores_price_stock_but_detects_description_change():
    text, digest = product_document(PRODUCT)
    assert product_document({**PRODUCT, "base_price": 42, "available_qty": 1}) == (text, digest)
    assert product_document({**PRODUCT, "product_name": "Summer shirt"})[1] != digest
    assert "30.0" not in text


def test_backend_customer_query_is_parameterized(monkeypatch):
    backend = DatabricksToolBackend(Mock(), "warehouse")
    calls = []
    monkeypatch.setattr(
        backend,
        "_query",
        lambda sql, args: calls.append((sql, args))
        or ([{"source_at": "2025-12-31"}] if "max(" in sql else []),
    )
    backend.read("get_customer_360", {"customer_id": "CUS000001' OR 1=1--"}, "request-1")
    sql, args = calls[0]
    assert "OR 1=1" not in sql and ":customer" in sql
    assert args["customer"].endswith("OR 1=1--")


def test_cost_plan_reserves_unknown_generation_but_not_publish_tokens(tmp_path, monkeypatch):
    import runpy

    root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(root / "azure_databricks/scripts/phase8_control.py"))
    plan = namespace["plan"]
    monkeypatch.setitem(plan.__globals__, "EVIDENCE", tmp_path)
    for index, timeout in enumerate((180, 180, 90, 120)):
        (tmp_path / f"generation_{index}.json").write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "estimated_job_cost_inr_pre_tax": 10,
                    "plan": {"generation_job_timeout_seconds": timeout},
                }
            )
        )
    (tmp_path / "operator_embeddings.json").write_text(
        json.dumps(
            {
                "estimated_embedding_cost_inr_pre_tax": 6.2166,
            }
        )
    )
    report = plan()
    assert report["prior_compute_estimate_inr"] == 40
    assert report["prior_unreported_embedding_reserve_inr"] == 83.3979
    assert report["known_embedding_estimate_inr"] == 6.2166
    assert report["generation_job_timeout_seconds"] == 120
    assert not report["hard_invoice_cap_guaranteed"]


@pytest.mark.parametrize("scenario", [False, True])
def test_realtime_and_scenario_tool_payloads_execute_the_actual_model_locally(
    monkeypatch, scenario
):
    import pandas as pd
    from retail_hp_azure import phase8_backend
    from retail_hp_azure.recommender import AdaptiveRetailRecommender

    root = Path(__file__).resolve().parents[2]
    model = AdaptiveRetailRecommender(
        root / "migration_assets", root / "migration_assets/artifacts"
    )
    client = Mock()
    client.api_client.do.return_value = {
        "state": {"suspend": "NOT_SUSPENDED", "ready": "READY"},
        "config": {"served_entities": [{"entity_version": "3"}]},
    }
    backend = DatabricksToolBackend(client, "warehouse")
    monkeypatch.setattr(
        backend,
        "_query",
        lambda sql, args: [{"source_at": "2025-12-31"}]
        if "max(" in sql
        else [{"ids": json.dumps(sorted(model.eligible_ids))}],
    )

    def transport(payload, timeout):
        rows = model.predict(pd.DataFrame(payload["dataframe_records"]))
        return 200, {"predictions": rows.to_dict(orient="records")}

    monkeypatch.setattr(phase8_backend, "authenticated_transport", lambda _: transport)
    tools = GovernedTools(backend, actor_secret=b"s" * 32, trace_sink=lambda _: None)
    args = {"customer_id": "CUS000001", "top_n": 3}
    if scenario:
        args.update(scenario_id="phase8-local", favorite_category_id="CAT000011")
    else:
        args["mode"] = "realtime"
    result = tools.execute(
        "simulate_scenario" if scenario else "get_recommendations",
        args,
        context=CONTEXT,
        request_id="phase8-local",
    )
    assert len(result.rows) == 3
    assert all(row["product_id"] in model.eligible_ids for row in result.rows)
