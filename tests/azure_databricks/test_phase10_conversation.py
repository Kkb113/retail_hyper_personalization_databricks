"""Offline conversational regressions; no paid inference or Azure calls."""

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase9 import Plan, Session
from retail_hp_azure.phase9_evaluation import PROVENANCE, Case, FixtureBackend
from retail_hp_azure.phase10_conversation import ConversationAgent, Narrative, Section
from retail_hp_azure.phase10_runtime import RunningBackend
from retail_hp_azure.safety import SafetyError


class Backend(FixtureBackend):
    def read(self, tool, arguments, request_id):
        if tool == "get_customer_360":
            return [
                {
                    "customer_id": arguments["customer_id"],
                    "customer_segment": "Regular",
                    "loyalty_tier": "Silver",
                    "preferred_channel": "Online",
                    "region_id": "R1",
                    "behavior_as_of": "2026-01-01",
                    "purchase_count": 4,
                    "browse_count": 10,
                }
            ], PROVENANCE
        if tool == "get_opportunities":
            return [], PROVENANCE
        return super().read(tool, arguments, request_id)


def setup(action="get_recommendations"):
    backend = Backend(Case("chat", "known_customer", "recommend", action))
    planner = Mock()
    planner.plan.return_value = Plan(action=action, arguments="{}")
    planner.compose.return_value = Narrative(
        summary="Verified customer summary",
        sections=[Section(heading="Recommendations", text="Detailed grounded explanation.")],
    )
    agent = ConversationAgent(
        planner,
        GovernedTools(backend, actor_secret=b"test-only" * 8, trace_sink=lambda _: None),
        lambda _: None,
    )
    context = ToolContext(subject="test", allowed_customers={"CUS000001", "CUS000002"})
    session = Session(subject="test")
    return agent, planner, context, session


def run(agent, context, session, text):
    return agent.run(text, context=context, session=session, request_id="test-chat")


def test_quoted_multipart_prompt_resolves_customer_and_hydrates_five_cards():
    agent, planner, context, session = setup()
    reply = run(
        agent,
        context,
        session,
        '"For cus000001 recommend five personalized products, explain signals '
        'and consider promotions and discovery outside usual choices."',
    )
    assert reply.status == "ok"
    assert session.selected_customer == "CUS000001"
    assert len(reply.cards) == 5
    assert [row["rank"] for row in reply.cards] == [1, 2, 3, 4, 5]
    assert all(row.get("product_name") for row in reply.cards)
    assert reply.sections
    packet = planner.compose.call_args.args[1]
    assert any(r["tool"] == "get_customer_360" for r in packet["evidence"])
    assert len(packet["evidence"]) <= 8


def test_followup_remembers_verified_facts_and_customer_change_clears_them():
    agent, planner, context, session = setup()
    run(agent, context, session, "Recommend for CUS000001")
    planner.plan.return_value = Plan(action="retail_advice")
    run(agent, context, session, "Explain why the first one fits")
    packet = planner.compose.call_args.args[1]
    assert packet["selected_customer"] == "CUS000001"
    assert packet["previous_verified_evidence"]
    assert packet["history"]
    run(agent, context, session, "Tell me about CUS000002")
    packet = planner.compose.call_args.args[1]
    assert not packet["previous_verified_evidence"]
    assert not packet["history"]


def test_general_retail_does_not_require_customer_or_sql():
    agent, planner, context, session = setup("retail_advice")
    reply = run(agent, context, session, "How should a retailer design a loyalty program?")
    assert reply.status == "ok"
    assert planner.compose.call_args.args[1]["evidence"] == []
    assert session.selected_customer is None


def test_unauthorized_customer_is_rejected_before_any_llm_call():
    agent, planner, context, session = setup()
    assert run(agent, context, session, "Recommend for CUS999999").status == "refused"
    planner.plan.assert_not_called()
    planner.compose.assert_not_called()


def test_missing_customer_gets_specific_clarification():
    agent, _, context, session = setup()
    reply = run(agent, context, session, "Recommend five products for me")
    assert reply.status == "clarify"
    assert "customer" in reply.text


def test_provider_failure_keeps_actual_recommendations_without_generic_safety_error():
    agent, planner, context, session = setup()
    planner.plan.side_effect = RuntimeError("provider unavailable")
    planner.compose.side_effect = RuntimeError("provider unavailable")
    reply = run(agent, context, session, "Recommend for CUS000001")
    assert reply.status == "ok" and len(reply.cards) == 5
    assert "verified" in reply.text
    assert "safely complete" not in reply.text


def test_customer_comparison_uses_distinct_profiles_and_revocation_clears_memory():
    agent, planner, context, session = setup()
    run(agent, context, session, "Compare CUS000001 and CUS000002")
    packet = planner.compose.call_args.args[1]
    assert {r["rows"][0]["customer_id"] for r in packet["evidence"]} == {
        "CUS000001",
        "CUS000002",
    }
    planner.plan.return_value = Plan(action="retail_advice")
    restricted = ToolContext(subject="test", allowed_customers={"CUS000001"})
    run(agent, restricted, session, "Explain that comparison")
    assert planner.compose.call_args.args[1]["previous_verified_evidence"] == []
    assert planner.compose.call_args.args[1]["history"] == []


def test_invented_product_argument_never_executes():
    agent, planner, context, session = setup()
    planner.plan.return_value = Plan(
        action="get_product_details",
        arguments=json.dumps({"product_id": "PRO999999"}),
    )
    assert run(agent, context, session, "Explain the first product").status == "clarify"
    planner.compose.assert_not_called()


def test_warehouse_wake_is_bounded_to_active_lease(monkeypatch):
    client = Mock()

    def warehouse(state):
        return SimpleNamespace(name="retail-hp-poc-sql", state=SimpleNamespace(value=state))

    client.warehouses.get.side_effect = [warehouse("STOPPED"), warehouse("RUNNING")]
    backend = RunningBackend(client, "fixed")
    backend.lease_expires = time.time() + 300
    monkeypatch.setattr("retail_hp_azure.phase10_runtime.time.sleep", lambda _: None)
    backend.check_running()
    client.warehouses.start.assert_called_once_with("fixed")


def test_warehouse_near_deadline_never_wakes():
    client = Mock()
    client.warehouses.get.return_value.name = "retail-hp-poc-sql"
    client.warehouses.get.return_value.state.value = "STOPPED"
    backend = RunningBackend(client, "fixed")
    backend.lease_expires = time.time() + 20
    with pytest.raises(SafetyError, match="DEMO_WINDOW_ENDING"):
        backend.check_running()
    client.warehouses.start.assert_not_called()
