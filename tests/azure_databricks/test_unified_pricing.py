"""Offline business routing and authorization checks; no cloud compute."""

from unittest.mock import Mock

import pytest
from retail_hp_azure.unified_pricing import UnifiedAgent, pricing_sections
from test_phase10_conversation import run, setup


def unified(plan, allowed=True):
    original, planner, context, session = setup()
    planner._request.return_value = plan
    pricing = Mock()
    pricing.lookup.return_value = []
    pricing.recommend.return_value = {
        "product_id": "PRO000001",
        "product_name": "Test product",
        "store_id": "STO000001",
        "channel": "Web",
        "current_price": 10,
        "suggested_price": 11,
        "reason": "Evaluated business rules.",
        "as_of": "2025-12-31",
        "manual_review": False,
    }
    agent = UnifiedAgent(planner, original.tools, lambda _: None, pricing, pricing_allowed=allowed)
    return agent, planner, context, session, pricing


@pytest.mark.parametrize(
    "text,plan",
    [
        ("Price PRO000001 STO000001 Web", {"action": "pricing", "product_ids": ["PRO999999"]}),
        (
            "Price PRO000001 Web",
            {"action": "pricing", "product_ids": ["PRO000001"], "store_id": "STO999999"},
        ),
        ("Price PRO000001 STO000001", {"action": "pricing", "channel": "Web"}),
        ("Price PRO000001 STO000001 Web", {"action": "pricing", "as_of": "2025-01-01"}),
        ("Price PRO000001 STO000001 Web", {"action": "pricing", "candidate_price": 123.45}),
        ("Price PRO000001 5% lower", {"action": "pricing", "candidate_price": 5}),
        ("Price PRO000001 in INR", {"action": "pricing"}),
    ],
)
def test_ungrounded_scenarios_never_score(text, plan):
    agent, _, context, session, pricing = unified(plan)
    assert run(agent, context, session, text).status == "clarify"
    pricing.recommend.assert_not_called()


def test_exact_business_request_uses_deterministic_price():
    agent, _, context, session, pricing = unified(
        {
            "action": "pricing",
            "product_ids": ["PRO000001"],
            "store_id": "STO000001",
            "channel": "Web",
        }
    )
    reply = run(agent, context, session, "Price PRO000001 at STO000001 for Web")
    assert reply.status == "ok"
    assert "11.00" in str(reply.sections)
    pricing.recommend.assert_called_once_with("PRO000001", "STO000001", "Web", None, None)


def test_exact_ids_survive_llm_queue_failure():
    agent, planner, context, session, pricing = unified({"action": "pricing"})
    planner._request.side_effect = RuntimeError("LLM queue is busy")
    reply = run(
        agent, context, session, "Recommend a price for PRO000001 at STO000001 through Web."
    )
    assert reply.status == "ok"
    pricing.recommend.assert_called_once_with("PRO000001", "STO000001", "Web", None, None)


def test_pricing_role_required():
    agent, _, context, session, pricing = unified({"action": "pricing"}, allowed=False)
    assert run(agent, context, session, "Recommend a price").status == "unavailable"
    pricing.recommend.assert_not_called()


def test_fresh_discovery_does_not_inherit_previous_filters():
    agent, _, context, session, pricing = unified({"action": "lookup"})
    session.pricing_state = {
        "product_ids": ["PRO000001"],
        "store_id": "STO000001",
        "channel": "Web",
    }
    assert run(agent, context, session, "Show available pricing scenarios").status == "ok"
    pricing.lookup.assert_called_once_with([], None, None, "")


def test_cross_session_refused():
    agent, planner, context, session, pricing = unified({"action": "pricing"})
    session.subject = "other-user"
    assert run(agent, context, session, "Recommend a price").status == "refused"
    pricing.recommend.assert_not_called()


def test_interpretation_failure_does_not_change_simulation_to_recommendation():
    agent, planner, context, session, pricing = unified({"action": "pricing"})
    planner._request.side_effect = RuntimeError("offline")
    assert run(agent, context, session, "What if PRO000001 costs 15?").status == "clarify"
    pricing.recommend.assert_not_called()


def test_manual_review_has_no_actionable_price():
    sections = pricing_sections(
        [
            {
                "product_id": "PRO000001",
                "product_name": "Test",
                "store_id": "STO000001",
                "channel": "Web",
                "as_of": "2025-12-31",
                "suggested_price": None,
            }
        ]
    )
    assert "no actionable price" in str(sections)
