import json
from types import SimpleNamespace

import pytest
from retail_hp_azure.phase11 import (
    TRACE_REQUEST,
    TraceArchive,
    dashboard_spec,
    incident_findings,
    monitoring_queries,
    opportunity_refresh,
    opportunity_select,
    safe_trace,
    sample_request,
)


def test_trace_drops_payload_identity_and_nonfinite():
    record = safe_trace(
        {
            "request_hash": "a" * 64,
            "text": "secret customer",
            "actor_hash": "b" * 64,
            "arguments": {"customer_id": "CUS000001"},
            "elapsed_ms": float("nan"),
            "input_tokens": True,
            "error_type": "Bearer secret",
            "cost_estimate_inr": -1,
            "output_tokens": 12,
        }
    )
    assert record["request_hash"] == "a" * 64 and record["output_tokens"] == 12
    assert (
        not {
            "text",
            "actor_hash",
            "arguments",
            "elapsed_ms",
            "input_tokens",
            "error_type",
            "cost_estimate_inr",
        }
        & record.keys()
    )


def test_archive_correlates_and_bounds():
    stored = []
    files = SimpleNamespace(
        create_directory=lambda path: None,
        upload=lambda path, data, overwrite: stored.append((path, json.load(data), overwrite)),
    )
    archive = TraceArchive(SimpleNamespace(files=files), "a" * 32)
    token = TRACE_REQUEST.set("b" * 64)
    try:
        archive({"tool": "get_recommendations", "status": "SUCCESS"})
        archive({"event": "agent_request", "status": "ok"})
    finally:
        TRACE_REQUEST.reset(token)
    assert len(stored) == 1 and stored[0][2] is False
    assert all(e["request_hash"] == "b" * 64 for e in stored[0][1]["events"])
    assert not archive.pending
    archive.written = 200
    archive({"request_hash": "c" * 64, "event": "agent_request"})
    assert len(stored) == 1


def test_archive_failure_does_not_break_chat(capsys):
    def fail(path):
        raise RuntimeError("credential should never be logged")

    archive = TraceArchive(SimpleNamespace(files=SimpleNamespace(create_directory=fail)), "a" * 32)
    archive({"request_hash": "c" * 64, "event": "agent_request"})
    output = capsys.readouterr().out
    assert "trace_archive_failed" in output and "credential" not in output


def test_sampling_consistent_and_limits():
    assert sample_request("x", 100) and not sample_request("x", 0)
    assert sample_request("x", 10) == sample_request("x", 10)
    with pytest.raises(ValueError):
        sample_request("x", 101)


def test_rules_are_deterministic_historical_and_not_fake_events():
    sql = opportunity_select()
    assert "sha2" in sql and "behavior_as_of" in sql
    assert "purchase_count=0" in sql and "available_qty>0" in sql
    assert "registered_model_version='3'" in sql and "customer_status='Active'" in sql
    assert "BACK_IN_STOCK" not in sql and "PRICE_CHANGE" not in sql
    assert opportunity_refresh().startswith("INSERT OVERWRITE")


def test_dashboard_has_explicit_fields_no_embedded_credentials_or_schedules():
    spec = dashboard_spec()
    queries = monitoring_queries()
    assert {d["name"] for d in spec["datasets"]} == set(queries)
    widgets = spec["pages"][0]["layout"]
    assert "multilineTextboxSpec" in widgets[0]["widget"]
    visual_types = [item["widget"].get("spec", {}).get("widgetType") for item in widgets]
    assert visual_types.count("counter") == 3
    assert visual_types.count("bar") == 3
    assert visual_types.count("table") == 4
    for item in widgets[1:]:
        widget = item["widget"]
        fields = widget["queries"][0]["query"]["fields"]
        assert fields and all(f["expression"] != "*" for f in fields)
        if widget["spec"]["widgetType"] == "table":
            assert widget["spec"]["version"] == 2
            columns = widget["spec"]["encodings"]["columns"]
            assert len(columns) == len(fields)
            assert all(column.get("displayName") for column in columns)
    assert "NULL AS DOUBLE" in queries["feedback"]
    assert "try_divide" in queries["coverage"]


@pytest.mark.parametrize(
    "quality,coverage,expected",
    [
        (
            {"invalid_customer_batches": 0},
            {"inventory_validity": 1, "active_published_customers": 95},
            [],
        ),
        (
            {"invalid_customer_batches": 1},
            {"inventory_validity": None, "active_published_customers": 0},
            ["BLOCK_DEMO_INVALID_BATCH", "REVIEW_INVENTORY_ELIGIBILITY", "BLOCK_DEMO_NO_CUSTOMERS"],
        ),
    ],
)
def test_incidents(quality, coverage, expected):
    assert incident_findings(quality, coverage) == expected
