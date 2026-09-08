"""MLflow boundary. Authentication/session bindings are supplied by server code only."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import mlflow
from mlflow.pyfunc.model import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase9 import RetailAgent, Session
from retail_hp_azure.phase9_llm import AzureLunaPlanner


@dataclass
class Binding:
    context: ToolContext
    session: Session


def create_luna_agent(
    *,
    token_provider: Callable[[], str],
    ledger: Path,
    tools: GovernedTools,
    binding: Callable[[], Binding],
    trace_sink: Callable[[dict[str, Any]], None],
) -> RetailResponsesAgent:
    """Build the exact Luna agent. Credentials and entitlements come from the server.

    No model-selection fallback and no CLI/developer credential discovery occurs here.
    The deployment host must supply a verified Entra workload token provider; local
    validation may explicitly supply the operator provider instead.
    """
    planner = AzureLunaPlanner(token_provider, ledger)
    return RetailResponsesAgent(RetailAgent(planner, tools, trace_sink), binding)


class RetailResponsesAgent(ResponsesAgent):  # type: ignore[misc]
    def __init__(self, agent: RetailAgent, binding: Callable[[], Binding]) -> None:
        # ResponsesAgent/AgentServer otherwise automatically capture full payloads.
        # Explicit redacted evaluation traces are emitted by the evaluation driver.
        mlflow.tracing.disable()
        self.agent, self.binding = agent, binding

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        data = request.model_dump()
        try:
            if data.get("custom_inputs") or len(data.get("input", [])) != 1:
                raise ValueError("Server-managed context required")
            message = data["input"][0]
            if message.get("role") != "user":
                raise ValueError("Only a current user message is accepted")
            content = message.get("content")
            if isinstance(content, list):
                if len(content) != 1 or content[0].get("type") != "input_text":
                    raise ValueError("Only text input is supported")
                content = content[0]["text"]
            if not isinstance(content, str):
                raise ValueError("Text required")
            trusted = self.binding()
            result = self.agent.run(
                content,
                context=trusted.context,
                session=trusted.session,
                request_id=f"agent-{uuid4().hex}",
            )
            text, extra = result.text, result.model_dump(exclude={"text"})
        except Exception:
            text, extra = (
                "This request needs a valid authenticated session and a single text message.",
                {"status": "refused"},
            )
        return ResponsesAgentResponse.model_validate(
            {
                "output": [self.create_text_output_item(text=text, id=f"msg-{uuid4().hex}")],
                "custom_outputs": extra,
            }
        )

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[Any, None, None]:
        result = self.predict(request)
        # Only validated final content is streamed, never draft tokens or hidden reasoning.
        for item in result.output:
            yield ResponsesAgentStreamEvent.model_validate(
                {"type": "response.output_item.done", "item": item}
            )


def create_agent_server(agent: RetailResponsesAgent) -> Any:
    from mlflow.genai.agent_server import AgentServer, invoke, stream

    mlflow.tracing.disable()

    @invoke()  # type: ignore[misc]
    async def invocation(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        return agent.predict(ResponsesAgentRequest.model_validate(request))

    @stream()  # type: ignore[misc]
    async def streaming(request: ResponsesAgentRequest) -> Any:
        for event in agent.predict_stream(ResponsesAgentRequest.model_validate(request)):
            yield event

    return AgentServer("ResponsesAgent", enable_chat_proxy=False)
