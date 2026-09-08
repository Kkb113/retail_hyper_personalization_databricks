"""Private Databricks App HTTP boundary. No cloud calls occur at import or startup.

Runtime adapters must verify forwarded OAuth tokens, resolve entitlements on the
server, and refuse stopped dependencies. Local preview deliberately has no adapter.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from retail_hp_azure.phase8 import Contract, GovernedTools, ToolContext
from retail_hp_azure.phase9 import RetailAgent, Session

VERSION = "retail_workbench_v1"


class Adapter(Protocol):
    def authenticate(self, token: str) -> ToolContext: ...
    def dependencies(self) -> dict[str, str]: ...
    def tools(self, token: str) -> GovernedTools: ...
    def agent(self, token: str) -> RetailAgent: ...


class Action(Contract):
    action: Literal[
        "get_customer_360",
        "get_recommendations",
        "search_products",
        "get_product_details",
        "compare_products",
        "simulate_scenario",
        "get_opportunities",
        "get_quality_summary",
        "explain_recommendation",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class Chat(Contract):
    text: str = Field(min_length=1, max_length=1600)
    customer_id: str | None = Field(default=None, pattern=r"^CUS[0-9]{6}$")


class Feedback(Contract):
    product_id: str = Field(pattern=r"^PRO[0-9]{6}$")
    sentiment: Literal["positive", "negative", "neutral"]
    reason_code: Literal[
        "relevant", "not_relevant", "already_owned", "too_expensive", "out_of_stock", "other"
    ]
    confirmed: Literal[True]


@dataclass
class BrowserSession:
    context: ToolContext
    csrf: str
    expires: float
    agent_session: Session
    feedback_targets: dict[str, tuple[str, str, str]] = field(default_factory=dict)


def create_app(
    adapter: Adapter | None = None,
    *,
    static_dir: Path | None = None,
    lease_expires: float = 0,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """One worker only. Absolute external lease cannot be extended by browser traffic.

    Admission expiry is NOT a compute shutdown: deployment additionally needs an
    independently verified stop controller. Never expose this server outside the
    Databricks authenticated proxy (except loopback-only disconnected preview).
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions: dict[str, BrowserSession] = {}
    lock = threading.Lock()
    capacity = threading.BoundedSemaphore(1)
    remaining = 100

    @app.middleware("http")
    async def protect(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/") and request.method == "POST":
            if request.headers.get("x-retail-request") != "workbench-v1":
                return JSONResponse({"detail": "Same-origin application request required"}, 403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site request denied"}, 403)
            # Bound actual chunks, not merely attacker-controlled Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 12_000:
                    return JSONResponse({"detail": "Request too large"}, 413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    def trusted(request: Request, *, write: bool = False) -> tuple[BrowserSession, str]:
        if adapter is None or clock() >= lease_expires:
            raise HTTPException(
                503, "Demo is locked. Ask the operator to open a bounded demo window."
            )
        token = request.headers.get("x-forwarded-access-token", "")
        if not token or len(token) > 16_384:
            raise HTTPException(401, "Databricks user authorization is required.")
        try:
            context = adapter.authenticate(token)
        except Exception:
            raise HTTPException(
                401, "Databricks user authorization could not be verified."
            ) from None
        with lock:
            for key in list(sessions):
                if sessions[key].expires <= clock():
                    del sessions[key]
            session = sessions.get(request.cookies.get("retail_session", ""))
        if session is None or session.context != context:
            raise HTTPException(401, "Open a new authorized session.")
        if write and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session.csrf
        ):
            raise HTTPException(403, "Session confirmation is required.")
        return session, token

    def perform(fn: Callable[[], Any]) -> Any:
        nonlocal remaining
        if not capacity.acquire(blocking=False):
            raise HTTPException(429, "A request is already running. Please wait.")
        try:
            with lock:
                if remaining <= 0 or clock() >= lease_expires:
                    raise HTTPException(503, "Demo request allowance exhausted or expired.")
                remaining -= 1
            return fn()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                503, "This operation is unavailable or not authorized. No result was fabricated."
            ) from None
        finally:
            capacity.release()

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        enabled = adapter is not None and clock() < lease_expires
        return JSONResponse(
            {"status": "configured" if enabled else "locked"}, 200 if enabled else 503
        )

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": VERSION, "scope": "SYNTHETIC_POC_ONLY", "model": "gpt-5.6-luna"}

    @app.post("/api/session")
    def start(request: Request) -> JSONResponse:
        if adapter is None or clock() >= lease_expires:
            raise HTTPException(
                503, "Disconnected preview / demo locked. No Azure compute is started."
            )
        token = request.headers.get("x-forwarded-access-token", "")
        if not token or len(token) > 16_384:
            raise HTTPException(401, "Databricks user authorization is required.")
        try:
            context = adapter.authenticate(token)
        except Exception:
            raise HTTPException(401, "User authorization failed.") from None
        with lock:
            for key in list(sessions):
                if sessions[key].expires <= clock():
                    del sessions[key]
            if len(sessions) >= 20:
                raise HTTPException(429, "Demo session limit reached.")
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            sessions[sid] = BrowserSession(
                context, csrf, min(clock() + 900, lease_expires), Session(subject=context.subject)
            )
        response = JSONResponse(
            {
                "csrf": csrf,
                "customers": sorted(context.allowed_customers),
                "quality_allowed": context.can_view_quality,
                "expires_at": sessions[sid].expires,
            }
        )
        response.set_cookie(
            "retail_session", sid, secure=True, httponly=True, samesite="strict", max_age=900
        )
        return response

    @app.get("/health/dependencies")
    def dependencies(request: Request) -> dict[str, str]:
        trusted(request)
        assert adapter is not None
        return perform(adapter.dependencies)  # type: ignore[no-any-return]

    @app.post("/api/action")
    def action(body: Action, request: Request) -> Any:
        session, token = trusted(request, write=True)
        assert adapter is not None

        def execute() -> dict[str, Any]:
            result = adapter.tools(token).execute(
                body.action,
                body.arguments,
                context=session.context,
                request_id=f"app-{uuid4().hex}",
            )
            if (
                body.action == "get_recommendations"
                and body.arguments.get("mode", "batch") == "batch"
            ):
                session.feedback_targets = {
                    row["product_id"]: (
                        str(body.arguments["customer_id"]),
                        result.request_id,
                        uuid4().hex,
                    )
                    for row in result.rows
                }
            return result.model_dump(mode="json")

        return perform(execute)

    @app.post("/api/chat")
    def chat(body: Chat, request: Request) -> Any:
        session, token = trusted(request, write=True)
        assert adapter is not None
        if (
            body.customer_id is not None
            and body.customer_id not in session.context.allowed_customers
        ):
            raise HTTPException(403, "Customer is not authorized.")

        def execute() -> Any:
            if session.agent_session.selected_customer != body.customer_id:
                session.agent_session = Session(
                    subject=session.context.subject, selected_customer=body.customer_id
                )
            return (
                adapter.agent(token)
                .run(
                    body.text,
                    context=session.context,
                    session=session.agent_session,
                    request_id=f"app-{uuid4().hex}",
                )
                .model_dump(mode="json")
            )

        return perform(execute)

    @app.post("/api/feedback")
    def feedback(body: Feedback, request: Request) -> Any:
        session, token = trusted(request, write=True)
        assert adapter is not None
        target = session.feedback_targets.get(body.product_id)
        if target is None:
            raise HTTPException(
                403, "Load this customer's batch recommendations before giving feedback."
            )
        customer, correlation, key = target
        return perform(
            lambda: adapter.tools(token)
            .execute(
                "record_feedback",
                {
                    "customer_id": customer,
                    "idempotency_key": key,
                    "feedback": {
                        "product_id": body.product_id,
                        "sentiment": body.sentiment,
                        "reason_code": body.reason_code,
                        "recommendation_request_id": correlation,
                    },
                },
                context=session.context,
                request_id=f"feedback-{uuid4().hex}",
                write_confirmed=True,
            )
            .model_dump(mode="json")
        )

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    return app
