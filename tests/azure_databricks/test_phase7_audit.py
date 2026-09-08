"""Regression tests for readiness-audit findings; no cloud compute required."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from retail_hp_azure import phase7_delta
from retail_hp_azure.phase7 import ActorContext, InMemoryOperationalStore, build_event
from retail_hp_azure.phase7_delta import DeltaOperationalStore, _execute, _rows
from retail_hp_azure.safety import SafetyError


def make_event():
    return build_event(
        table="message", actor=ActorContext(subject="synthetic-a"), secret=b"x" * 32,
        idempotency_key="message-0001", correlation_id="request-0001",
        payload={"nested": {"text": "original"}}, now=datetime(2026, 9, 8, tzinfo=UTC),
    )


def test_phase7_operator_assets_resolve_from_checkout_not_installed_package():
    from retail_hp_azure import phase7_runtime as runtime

    checkout = Path(__file__).resolve().parents[2]
    assert runtime.AZURE_ROOT == checkout / "azure_databricks"
    assert runtime.NOTEBOOK_SOURCE.is_file()


@pytest.mark.parametrize("state", ["STOPPED", "STOPPING", "STARTING", "UNKNOWN"])
def test_operational_sql_never_submits_when_not_running(monkeypatch, state):
    client = Mock()
    client.warehouses.get.return_value = SimpleNamespace(state=SimpleNamespace(value=state))
    monkeypatch.setattr(
        "retail_hp_azure.phase2_compute._verify_warehouse_contract", lambda _: None
    )
    with pytest.raises(SafetyError, match="explicitly started"):
        _execute(client, "warehouse", "SELECT 1")
    client.statement_execution.execute_statement.assert_not_called()
    client.warehouses.start.assert_not_called()


def test_truncated_operational_results_fail_closed():
    with pytest.raises(SafetyError, match="truncated"):
        _rows(SimpleNamespace(manifest=SimpleNamespace(truncated=True)))


def test_memory_store_does_not_expose_mutable_payload():
    store = InMemoryOperationalStore()
    original = make_event()
    result = store.write(original)
    original.payload["nested"]["text"] = "tampered"
    result.event.payload["nested"]["text"] = "tampered-again"
    replay = store.write(make_event())
    assert replay.event.payload["nested"]["text"] == "original"


def test_delta_replay_returns_persisted_timestamp_and_correlation(monkeypatch):
    original = make_event()
    incoming = original.model_copy(update={
        "created_at": original.created_at + timedelta(hours=1),
        "correlation_id": "request-0002",
    })
    row = {
        **original.model_dump(mode="json", exclude={"table", "payload"}),
        "payload_json": '{"nested":{"text":"original"}}',
        "payload_sha256": phase7_delta.canonical_payload_hash(original.payload),
    }
    store = DeltaOperationalStore(Mock(), "warehouse")
    monkeypatch.setattr(store, "_get", lambda *args: [row])
    result = store.write(incoming)
    assert result.replayed
    assert result.event.created_at == original.created_at
    assert result.event.correlation_id == original.correlation_id


def test_duplicate_stored_event_fails_closed(monkeypatch):
    store = DeltaOperationalStore(Mock(), "warehouse")
    monkeypatch.setattr(store, "_get", lambda *args: [{}, {}])
    with pytest.raises(SafetyError, match="Duplicate"):
        store.write(make_event())


def test_repeated_export_invocations_use_distinct_run_tokens(monkeypatch):
    from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState
    from retail_hp_azure import phase7_runtime as runtime

    client = Mock()
    client.jobs.list.return_value = [SimpleNamespace(job_id=7)]
    path = (
        f"{runtime.WORKSPACE_ROOT}/feedback_export_"
        f"{hashlib.sha256(runtime.NOTEBOOK_SOURCE.read_bytes()).hexdigest()[:16]}"
    )
    settings = SimpleNamespace(
        schedule=None, trigger=None, continuous=None, timeout_seconds=180,
        max_concurrent_runs=1, tasks=[SimpleNamespace(
            max_retries=0, timeout_seconds=180,
            notebook_task=SimpleNamespace(notebook_path=path),
        )],
    )
    client.jobs.get.return_value = SimpleNamespace(settings=settings)
    client.jobs.list_runs.return_value = []
    client.warehouses.list.return_value = [SimpleNamespace(
        name=runtime.WAREHOUSE_NAME, state=SimpleNamespace(value="STOPPED")
    )]
    run = SimpleNamespace(
        state=SimpleNamespace(result_state=RunResultState.SUCCESS,
                              life_cycle_state=RunLifeCycleState.TERMINATED),
        tasks=[SimpleNamespace(run_id=8)],
    )
    client.jobs.run_now.return_value.result.return_value = run
    client.jobs.get_run.return_value = run
    client.jobs.get_run_output.return_value = SimpleNamespace(
        notebook_output=SimpleNamespace(result=
            '{"status":"PASS","idempotent":true,"second_pass_changes":0}')
    )
    monkeypatch.setattr(runtime, "_require_approval", lambda: None)
    monkeypatch.setattr(runtime, "_verify_budget", lambda _: {})
    context = SimpleNamespace(apply=True, client=client)
    runtime.run_export_job(context)
    runtime.run_export_job(context)
    tokens = [call.kwargs["idempotency_token"] for call in client.jobs.run_now.call_args_list]
    assert len(set(tokens)) == 2
    assert all(len(token) <= 64 for token in tokens)
