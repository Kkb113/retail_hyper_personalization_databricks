"""Local MLflow ResponsesAgent and AgentServer acceptance; no cloud inference."""

import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from mlflow.types.responses import ResponsesAgentRequest
from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase9 import Plan, RetailAgent, Session
from retail_hp_azure.phase9_evaluation import Case, FixtureBackend
from retail_hp_azure.phase9_llm import AzureLunaPlanner
from retail_hp_azure.phase9_responses import (
    Binding,
    RetailResponsesAgent,
    create_agent_server,
    create_luna_agent,
)


def main():
    backend = FixtureBackend(Case("smoke", "known_customer", "recommend", "get_recommendations"))
    planner = Mock()
    planner.plan.return_value = Plan(
        action="get_recommendations", arguments='{"customer_id":"CUS000001","top_n":5}'
    )
    traces = []
    context = ToolContext(subject="local-synthetic-test", allowed_customers={"CUS000001"})
    agent = RetailAgent(
        planner,
        GovernedTools(backend, actor_secret=b"local-test" * 4, trace_sink=lambda _: None),
        traces.append,
    )
    adapter = RetailResponsesAgent(
        agent, lambda: Binding(context, Session(context.subject, "CUS000001"))
    )
    luna = create_luna_agent(
        token_provider=lambda: "synthetic-offline-token",
        ledger=Path("build/unused-offline-luna-ledger.json"),
        tools=agent.tools,
        binding=lambda: Binding(context, Session(context.subject, "CUS000001")),
        trace_sink=traces.append,
    )
    assert isinstance(luna.agent.planner, AzureLunaPlanner)
    assert luna.agent.planner.endpoint == "gpt-5.6-luna"
    payload = {"input": [{"role": "user", "content": "Recommend five products for me"}]}
    reply = adapter.predict(ResponsesAgentRequest.model_validate(payload))
    assert reply.custom_outputs["status"] == "ok"
    assert len(reply.custom_outputs["cards"]) == 5
    events = list(adapter.predict_stream(ResponsesAgentRequest.model_validate(payload)))
    assert len(events) == 1 and events[0].type == "response.output_item.done"
    attacked = {**payload, "custom_inputs": {"allowed_customers": ["CUS000002"]}}
    assert (
        adapter.predict(ResponsesAgentRequest.model_validate(attacked)).custom_outputs["status"]
        == "refused"
    )
    server = create_agent_server(adapter)
    with TestClient(server.app) as client:
        response = client.post("/invocations", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["custom_outputs"]["status"] == "ok"
        streamed = client.post("/invocations", json={**payload, "stream": True})
        assert streamed.status_code == 200
        assert "response.output_item.done" in streamed.text
        assert (
            client.post("/invocations", json=attacked).json()["custom_outputs"]["status"]
            == "refused"
        )
    assert "CUS000001" not in json.dumps(traces)
    assert "Recommend five products for me" not in json.dumps(traces)
    print(
        json.dumps(
            {
                "status": "PASS",
                "responses_predict": True,
                "safe_stream": True,
                "server_http": True,
                "custom_identity_rejected": True,
                "redacted_traces": len(traces),
                "cloud_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
