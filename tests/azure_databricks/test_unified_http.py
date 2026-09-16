"""Five authenticated sessions share capacity, never conversation state."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from fastapi.testclient import TestClient
from retail_hp_azure.phase8 import ToolContext
from retail_hp_azure.phase9 import AgentReply
from retail_hp_azure.phase10_app import create_app


def test_five_http_sessions_are_isolated():
    adapter = Mock()
    adapter.authenticate.side_effect = lambda token, **kw: ToolContext(
        subject=token, allowed_customers=set()
    )
    barrier = threading.Barrier(5)

    def answer(text, *, context, session, **kwargs):
        assert context.subject == session.subject == text
        session.pricing_state["subject"] = text
        barrier.wait(timeout=5)
        assert session.pricing_state["subject"] == text
        return AgentReply(status="ok", text=text, action="pricing")

    adapter.agent.return_value.run.side_effect = answer
    app = create_app(adapter, lease_expires=time.time() + 300, max_concurrent=5)

    def one(index):
        with TestClient(app, base_url="https://testserver") as client:
            actor = f"actor-{index}"
            client.headers.update(
                {"x-forwarded-access-token": actor, "x-retail-request": "workbench-v1"}
            )
            session = client.post("/api/session", json={})
            assert session.status_code == 200
            client.headers["x-csrf-token"] = session.json()["csrf"]
            reply = client.post("/api/chat", json={"text": actor})
            assert reply.status_code == 200
            assert reply.json()["text"] == actor
            return actor

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert len(set(pool.map(one, range(5)))) == 5
