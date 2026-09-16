"""Twenty-four offline business cases; live LLM/browser acceptance is separate."""

import pytest
from test_phase10_conversation import run
from test_unified_pricing import unified


@pytest.mark.parametrize("case", range(4))
def test_recommendation_business_cases(case):
    agent, planner, context, session, pricing = unified({"action": "retail"})
    text = [
        "Recommend products for CUS000001",
        "Recommend five for CUS000002",
        "Suggest personalized products for CUS000001",
        "Show recommendations for CUS000002",
    ][case]
    reply = run(agent, context, session, text)
    assert reply.status == "ok" and len(reply.cards) == 5
    pricing.recommend.assert_not_called()


@pytest.mark.parametrize("channel", ["Store", "Web", "Mobile", "Kiosk"])
def test_pricing_business_cases(channel):
    agent, _, context, session, pricing = unified(
        {
            "action": "pricing",
            "product_ids": ["PRO000001"],
            "store_id": "STO000001",
            "channel": channel,
        }
    )
    reply = run(agent, context, session, f"Price PRO000001 at STO000001 for {channel}")
    assert reply.status == "ok"
    assert pricing.recommend.call_args.args[2] == channel


@pytest.mark.parametrize("channel", ["Store", "Web", "Mobile", "Kiosk"])
def test_combined_business_cases(channel):
    agent, _, context, session, pricing = unified(
        {"action": "combined", "store_id": "STO000001", "channel": channel}
    )
    reply = run(
        agent,
        context,
        session,
        f"Recommend and price products for CUS000001 at STO000001 for {channel}",
    )
    assert reply.status == "ok" and reply.action == "combined"
    assert len(reply.cards) == 5 and pricing.recommend.call_count == 5


@pytest.mark.parametrize(
    "phrase",
    ["Use the first", "Price the first scenario", "Use the first instead", "Same first scenario"],
)
def test_followup_business_cases(phrase):
    agent, _, context, session, pricing = unified({"action": "pricing"})
    session.pricing_state = {
        "options": [
            {
                "product_id": "PRO000001",
                "store_id": "STO000001",
                "channel": "Web",
                "as_of": "2025-12-31",
            }
        ]
    }
    reply = run(agent, context, session, phrase)
    assert reply.status == "ok"
    pricing.recommend.assert_called_once_with("PRO000001", "STO000001", "Web", "2025-12-31", None)


@pytest.mark.parametrize("channel", ["Store", "Web", "Mobile", "Kiosk"])
def test_manual_review_business_cases(channel):
    agent, _, context, session, pricing = unified(
        {
            "action": "pricing",
            "product_ids": ["PRO000001"],
            "store_id": "STO000001",
            "channel": channel,
        }
    )
    pricing.recommend.return_value["suggested_price"] = None
    pricing.recommend.return_value["manual_review"] = True
    reply = run(agent, context, session, f"Price PRO000001 at STO000001 for {channel}")
    assert "no actionable price" in str(reply.sections)
    assert "consider 11.00" not in str(reply.sections)


@pytest.mark.parametrize("channel", ["Store", "Web", "Mobile", "Kiosk"])
def test_unsupported_business_cases(channel):
    agent, _, context, session, pricing = unified(
        {
            "action": "pricing",
            "product_ids": ["PRO999999"],
            "store_id": "STO000001",
            "channel": channel,
        }
    )
    pricing.recommend.side_effect = ValueError("NO_SUPPORTED_PRODUCT_STORE_CHANNEL")
    reply = run(agent, context, session, f"Price PRO999999 at STO000001 for {channel}")
    assert reply.status == "unavailable"
    assert "11.00" not in str(reply.sections)
