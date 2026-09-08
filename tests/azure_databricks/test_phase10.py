"""App admission, customer isolation and explicit-write regressions; no cloud calls."""

import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from retail_hp_azure.phase7 import InMemoryOperationalStore
from retail_hp_azure.phase8 import GovernedTools, Provenance, ToolContext
from retail_hp_azure.phase10_app import create_app

REC = {
    "product_id": "PRO000001",
    "rank": 1,
    "score": 0.5,
    "route": "cold_only",
    "reason_codes": "route:cold_only",
}
PROVENANCE = Provenance(
    source="fixture", source_timestamp="2026-01-01", data_version="fixture", model_version="3"
)
ACTION = {"action": "get_recommendations", "arguments": {"customer_id": "CUS000001"}}
FEEDBACK = {
    "product_id": "PRO000001",
    "sentiment": "positive",
    "reason_code": "relevant",
    "confirmed": True,
}
HEADERS = {"x-retail-request": "workbench-v1", "x-forwarded-access-token": "test-token"}


def suite():
    backend, adapter = Mock(), Mock()
    backend.read.return_value = ([REC], PROVENANCE)
    backend.write_feedback.side_effect = InMemoryOperationalStore().write
    adapter.authenticate.return_value = ToolContext(
        subject="test-user", allowed_customers={"CUS000001"}
    )
    adapter.tools.return_value = GovernedTools(
        backend, actor_secret=b"s" * 32, trace_sink=lambda _: None
    )
    adapter.dependencies.return_value = {"warehouse": "STOPPED"}
    now = [time.time()]
    client = TestClient(
        create_app(adapter, lease_expires=now[0] + 300, clock=lambda: now[0]),
        base_url="https://testserver",
    )
    client.headers.update(HEADERS)
    response = client.post("/api/session", json={})
    assert response.status_code == 200
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return client, adapter, backend, now


def test_preview_is_disconnected_and_health_never_starts_cloud():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.post("/api/session", json={}, headers=HEADERS).status_code == 503
        assert client.get("/version").json()["model"] == "gpt-5.6-luna"


def test_authorized_action_and_secure_cookie():
    client, _, backend, _ = suite()
    response = client.post("/api/action", json=ACTION)
    assert response.status_code == 200
    assert response.json()["rows"][0]["product_id"] == "PRO000001"
    assert backend.read.call_count == 1
    response = client.post("/api/session", json={})
    assert all(
        x in response.headers["set-cookie"] for x in ("HttpOnly", "Secure", "SameSite=strict")
    )
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_customer_grants_cannot_be_supplied_by_browser():
    client, _, backend, _ = suite()
    assert (
        client.post("/api/action", json={**ACTION, "allowed_customers": ["CUS000002"]}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/action", json={**ACTION, "arguments": {"customer_id": "CUS000002"}}
        ).status_code
        == 503
    )
    backend.read.assert_not_called()


@pytest.mark.parametrize(
    "header,value,status",
    [
        ("x-csrf-token", "wrong", 403),
        ("x-retail-request", "", 403),
        ("sec-fetch-site", "cross-site", 403),
        ("x-forwarded-access-token", "", 401),
    ],
)
def test_request_boundary(header, value, status):
    client, _, backend, _ = suite()
    assert client.post("/api/action", json=ACTION, headers={header: value}).status_code == status
    backend.read.assert_not_called()


def test_revalidate_identity_and_entitlements_each_request():
    client, adapter, backend, _ = suite()
    adapter.authenticate.return_value = ToolContext(subject="different-user")
    assert client.post("/api/action", json=ACTION).status_code == 401
    backend.read.assert_not_called()


def test_expired_lease_blocks_every_operation_before_adapter():
    client, adapter, backend, now = suite()
    adapter.reset_mock()
    now[0] += 301
    assert client.post("/api/action", json=ACTION).status_code == 503
    assert client.get("/health/ready").status_code == 503
    adapter.authenticate.assert_not_called()
    backend.read.assert_not_called()


def test_request_quota_is_global_not_reset_by_new_browser_session():
    client, _, backend, _ = suite()
    for _ in range(100):
        assert client.post("/api/action", json=ACTION).status_code == 200
    response = client.post("/api/session", json={})
    client.headers["x-csrf-token"] = response.json()["csrf"]
    assert client.post("/api/action", json=ACTION).status_code == 503
    assert backend.read.call_count == 100


def test_explicit_feedback_requires_prior_authoritative_recommendation():
    client, _, backend, _ = suite()
    assert client.post("/api/feedback", json=FEEDBACK).status_code == 403
    assert client.post("/api/action", json=ACTION).status_code == 200
    assert client.post("/api/feedback", json={**FEEDBACK, "confirmed": False}).status_code == 422
    assert (
        client.post("/api/feedback", json={**FEEDBACK, "customer_id": "CUS000002"}).status_code
        == 422
    )
    backend.write_feedback.assert_not_called()
    first = client.post("/api/feedback", json=FEEDBACK)
    second = client.post("/api/feedback", json=FEEDBACK)
    assert first.status_code == second.status_code == 200
    assert not first.json()["rows"][0]["replayed"]
    assert second.json()["rows"][0]["replayed"]
    assert first.json()["rows"][0]["event_id"] == second.json()["rows"][0]["event_id"]


def test_backend_failure_is_sanitized_and_feedback_is_not_reported_saved():
    client, _, backend, _ = suite()
    backend.read.side_effect = RuntimeError("secret/token/private-customer")
    response = client.post("/api/action", json=ACTION)
    assert response.status_code == 503
    assert "secret" not in response.text


def test_body_bound_and_unknown_actions():
    client, _, backend, _ = suite()
    assert client.post("/api/action", content=b"x" * 12_001).status_code == 413
    assert client.post("/api/action", json={"action": "execute_sql"}).status_code == 422
    backend.read.assert_not_called()


def test_chat_cannot_select_an_unauthorized_customer():
    client, adapter, _, _ = suite()
    assert (
        client.post("/api/chat", json={"text": "recommend", "customer_id": "CUS000002"}).status_code
        == 403
    )
    adapter.agent.assert_not_called()


def test_stopped_warehouse_refuses_sql_and_feedback_without_starting():
    from retail_hp_azure.phase10_runtime import RunningBackend
    from retail_hp_azure.safety import SafetyError

    client = Mock()
    client.warehouses.get.return_value.name = "retail-hp-poc-sql"
    client.warehouses.get.return_value.state.value = "STOPPED"
    backend = RunningBackend(client, "fixed-warehouse")
    with pytest.raises(SafetyError, match="WAREHOUSE_NOT_EXPLICITLY_RUNNING"):
        backend._query("SELECT 1", {})
    with pytest.raises(SafetyError, match="WAREHOUSE_NOT_EXPLICITLY_RUNNING"):
        backend.write_feedback(Mock())
    client.statement_execution.execute_statement.assert_not_called()
    client.warehouses.start.assert_not_called()


def test_runtime_rejects_operator_credentials_and_invalid_entitlements(tmp_path):
    from retail_hp_azure.config import HOST
    from retail_hp_azure.phase10_runtime import WorkbenchRuntime
    from retail_hp_azure.safety import SafetyError

    client = Mock()
    client.config.host, client.config.auth_type = HOST, "azure-cli"
    kwargs = dict(
        app_client=client,
        warehouse_id="fixed",
        entitlements={},
        actor_secret=b"s" * 32,
        ledger=tmp_path / "ledger.json",
        index=None,
        lease_expires=time.time() + 300,
        trace_sink=lambda _: None,
    )
    with pytest.raises(SafetyError, match="native workload OAuth"):
        WorkbenchRuntime(**kwargs)
    client.config.auth_type = "oauth-m2m"
    with pytest.raises(SafetyError, match="entitlements required"):
        WorkbenchRuntime(**kwargs)


def test_runtime_uses_verified_scim_id_not_forwarded_email(tmp_path):
    from retail_hp_azure.config import HOST
    from retail_hp_azure.phase10_runtime import WorkbenchRuntime
    from retail_hp_azure.safety import SafetyError

    app_client, obo_client = Mock(), Mock()
    app_client.config.host, app_client.config.auth_type = HOST, "oauth-m2m"
    context = ToolContext(subject="123", allowed_customers={"CUS000001"})
    runtime = WorkbenchRuntime(
        app_client=app_client,
        warehouse_id="fixed",
        entitlements={"123": context},
        actor_secret=b"s" * 32,
        ledger=tmp_path / "ledger.json",
        index=None,
        lease_expires=time.time() + 300,
        trace_sink=lambda _: None,
        user_client_factory=lambda token: obo_client,
    )
    obo_client.current_user.me.return_value.id = "123"
    obo_client.current_user.me.return_value.active = True
    assert runtime.authenticate("test-user-token") == context
    obo_client.current_user.me.return_value.id = "456"
    with pytest.raises(SafetyError, match="allowlist"):
        runtime.authenticate("another-token")
    app_client.current_user.me.assert_not_called()
