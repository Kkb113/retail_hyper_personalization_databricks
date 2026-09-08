from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from retail_hp_azure.phase7 import (
    ActorContext,
    FeedbackPayload,
    InMemoryOperationalStore,
    StoreMode,
    build_event,
    fallback_decision,
    pseudonymize_actor,
)
from retail_hp_azure.phase7_delta import (
    feedback_export_statement,
    migration_statements,
    table_name,
)
from retail_hp_azure.phase7_runtime import cost_plan
from retail_hp_azure.safety import SafetyError

SECRET = b"phase7-test-key-is-not-a-real-secret-000"
NOW = datetime(2026, 9, 8, tzinfo=UTC)


def event(actor: str, key: str, payload: dict[str, object] | None = None):
    return build_event(
        table="feedback",
        actor=ActorContext(subject=actor),
        secret=SECRET,
        idempotency_key=key,
        correlation_id="corr-phase7-0001",
        payload=payload or {"sentiment": "positive", "product_id": "P0001"},
        now=NOW,
    )


def test_actor_is_pseudonymized_and_raw_subject_is_not_persisted():
    created = event("demo-user@example.test", "feedback-key-0001")
    assert created.actor_hash == pseudonymize_actor(
        ActorContext(subject="demo-user@example.test"), SECRET
    )
    assert "demo-user" not in created.model_dump_json()


def test_feedback_write_is_idempotent_and_conflicting_replay_fails():
    store = InMemoryOperationalStore()
    first = store.write(event("user-a", "feedback-key-0001"))
    replay = store.write(event("user-a", "feedback-key-0001"))
    assert first.replayed is False
    assert replay.replayed is True
    with pytest.raises(SafetyError, match="different payload"):
        store.write(event("user-a", "feedback-key-0001", {"sentiment": "negative"}))


def test_conversation_state_cannot_cross_actor_boundary():
    store = InMemoryOperationalStore()
    store.write(event("user-a", "feedback-key-0001"))
    user_a = store.list_for_actor(
        table="feedback", actor=ActorContext(subject="user-a"), secret=SECRET
    )
    assert len(user_a) == 1
    assert store.list_for_actor(
        table="feedback", actor=ActorContext(subject="user-b"), secret=SECRET
    ) == ()


def test_read_only_failure_mode_rejects_writes_without_affecting_reads():
    store = InMemoryOperationalStore(mode=StoreMode.READ_ONLY)
    with pytest.raises(SafetyError, match="write path is disabled"):
        store.write(event("user-a", "feedback-key-0001"))
    assert store.list_for_actor(
        table="feedback", actor=ActorContext(subject="user-a"), secret=SECRET
    ) == ()


def test_retention_purge_is_explicit_and_bounded():
    store = InMemoryOperationalStore()
    store.write(event("user-a", "feedback-key-0001"))
    assert store.list_for_actor(
        table="feedback",
        actor=ActorContext(subject="user-a"),
        secret=SECRET,
        now=NOW + timedelta(days=91),
    ) == ()
    assert store.purge_expired(now=NOW + timedelta(days=89)) == 0
    assert store.purge_expired(now=NOW + timedelta(days=91)) == 1


def test_contract_rejects_unsafe_keys_large_payloads_and_short_secrets():
    with pytest.raises(ValidationError):
        event("user-a", "short")
    with pytest.raises((ValidationError, SafetyError)):
        event("user-a", "feedback-key-0001", {"text": "x" * 17_000})
    with pytest.raises(SafetyError, match="32 bytes"):
        pseudonymize_actor(ActorContext(subject="user-a"), b"short")


def test_feedback_payload_is_typed_bounded_and_rejects_unknown_fields():
    payload = FeedbackPayload(
        sentiment="positive",
        reason_code="relevant",
        product_id="P0001",
        recommendation_request_id="request-0001",
    )
    assert payload.sentiment.value == "positive"
    with pytest.raises(ValidationError):
        FeedbackPayload(
            sentiment="invented",
            reason_code="relevant",
            product_id="P0001",
            recommendation_request_id="request-0001",
        )
    with pytest.raises(ValidationError):
        FeedbackPayload(
            sentiment="positive",
            reason_code="relevant",
            product_id="P0001",
            recommendation_request_id="request-0001",
            customer_email="forbidden@example.test",
        )


def test_store_selection_never_silently_creates_lakebase():
    decision = fallback_decision(lakebase_projects=0, lakebase_enabled=False)
    assert decision["selected_store"] == "delta_ephemeral_hybrid"
    assert decision["new_paid_resource_required"] is False
    with pytest.raises(SafetyError, match="Unmanaged"):
        fallback_decision(lakebase_projects=1, lakebase_enabled=False)


def test_delta_migration_declares_all_tables_append_only_and_least_privilege():
    statements = migration_statements()
    ddl = "\n".join(statements)
    for table in (
        "conversation",
        "message",
        "feedback",
        "recommendation_impression",
        "scenario_session",
        "notification",
        "idempotency_key",
        "app_audit_event",
    ):
        assert table_name(table) in ddl
    assert ddl.count("'delta.appendOnly' = 'true'") == 9
    assert "GRANT SELECT, MODIFY" in ddl
    assert "retail_hp_app_runtime" in ddl
    assert "CREATE TABLE" not in next(
        item for item in statements if "GRANT USE_SCHEMA" in item
    )


def test_feedback_export_is_insert_only_and_does_not_contain_message_payloads():
    statement = feedback_export_statement()
    assert "WHEN NOT MATCHED THEN INSERT" in statement
    assert "WHEN MATCHED" not in statement
    assert ".agent.feedback" in statement
    assert ".agent.message" not in statement


def test_phase7_cost_plan_creates_no_resource_and_stays_below_ceiling():
    plan = cost_plan()
    assert plan["new_azure_resources"] == 0
    assert plan["lakebase_enabled"] is False
    assert plan["export_job_schedule"] is None
    assert plan["warehouse_auto_stop_minutes"] == 1
    assert plan["guarded_estimate_inr_pre_tax"] < plan["phase_ceiling_inr"] == 250
