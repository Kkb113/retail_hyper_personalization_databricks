"""Typed, bounded retail tools. Identity and write consent are server inputs, never LLM inputs."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, create_model

from retail_hp_azure.phase7 import (
    ActorContext,
    FeedbackPayload,
    OperationalEvent,
    WriteResult,
    build_event,
    pseudonymize_actor,
)
from retail_hp_azure.safety import require

VERSION = "retail_hp_tools_v1"
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
CustomerID = Annotated[str, Field(pattern=r"^CUS[0-9]{6}$")]
ProductID = Annotated[str, Field(pattern=r"^PRO[0-9]{6}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ToolContext(Contract):
    """Construct only after authenticating the caller and resolving server-side entitlements."""

    subject: str = Field(min_length=1, max_length=255)
    allowed_customers: frozenset[CustomerID] = frozenset()
    can_view_quality: bool = False


class CustomerArgs(Contract):
    customer_id: CustomerID


class RecommendationArgs(CustomerArgs):
    top_n: int = Field(default=10, ge=1, le=20, strict=True)
    mode: Literal["batch", "realtime"] = "batch"


class ExplainArgs(CustomerArgs):
    product_id: ProductID


class ProductArgs(Contract):
    product_id: ProductID


class CompareArgs(Contract):
    product_ids: list[ProductID] = Field(min_length=2, max_length=4)


class SearchArgs(Contract):
    query: str = Field(min_length=1, max_length=300)
    top_n: int = Field(default=5, ge=1, le=20, strict=True)
    category_id: Identifier | None = None
    max_price: float | None = Field(default=None, ge=0, le=1_000_000)
    promotion_only: bool = False
    region_id: Identifier | None = None


class ScenarioArgs(CustomerArgs):
    scenario_id: Identifier
    top_n: int = Field(default=10, ge=1, le=20, strict=True)
    favorite_category_id: Identifier | None = None
    price_sensitivity: Literal["Low", "Medium", "High"] | None = None
    excluded_product_ids: list[ProductID] = Field(default_factory=list, max_length=50)


class FeedbackArgs(CustomerArgs):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
    feedback: FeedbackPayload


class EmptyArgs(Contract):
    pass


class Product(Contract):
    product_id: ProductID
    product_name: str = Field(max_length=300)
    category_id: Identifier
    category_name: str = Field(max_length=100)
    brand_name: str = Field(max_length=100)
    base_price: float = Field(ge=0)
    currency_code: Literal["UNSPECIFIED"] = "UNSPECIFIED"
    available_qty: float = Field(gt=0)
    active_discount_pct: float = Field(default=0, ge=0, le=100)
    inventory_snapshot_at: str = Field(max_length=64)


class Customer(Contract):
    customer_id: CustomerID
    customer_segment: str = Field(max_length=100)
    loyalty_tier: str = Field(max_length=50)
    preferred_channel: str = Field(max_length=50)
    region_id: Identifier
    behavior_as_of: str = Field(max_length=64)
    purchase_count: int = Field(ge=0)
    browse_count: int = Field(ge=0)


class Recommendation(Contract):
    product_id: ProductID
    rank: int = Field(ge=1, le=50)
    score: float
    route: str = Field(max_length=100)
    reason_codes: str = Field(max_length=2000)


class Opportunity(Contract):
    product_id: ProductID
    opportunity_type: Literal["ACTIVE_PROMOTION"]
    discount_pct: float = Field(gt=0, le=100)


class Quality(Contract):
    model_version: str = Field(max_length=32)
    batch_customers: int = Field(ge=0)
    batch_rows: int = Field(ge=0)
    scope: Literal["SYNTHETIC_POC_ONLY"] = "SYNTHETIC_POC_ONLY"


class Receipt(Contract):
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    replayed: bool


class Provenance(Contract):
    source: str = Field(max_length=200)
    source_timestamp: str = Field(min_length=1, max_length=64)
    data_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)


class ToolResult(Contract):
    tool: str
    schema_version: Literal["retail_hp_tools_v1"] = "retail_hp_tools_v1"
    request_id: Identifier
    provenance: Provenance
    rows: list[dict[str, Any]] = Field(max_length=20)


CATALOG: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "get_customer_360": (CustomerArgs, Customer),
    "get_recommendations": (RecommendationArgs, Recommendation),
    "explain_recommendation": (ExplainArgs, Recommendation),
    "search_products": (SearchArgs, Product),
    "get_product_details": (ProductArgs, Product),
    "compare_products": (CompareArgs, Product),
    "simulate_scenario": (ScenarioArgs, Recommendation),
    "get_opportunities": (CustomerArgs, Opportunity),
    "record_feedback": (FeedbackArgs, Receipt),
    "get_quality_summary": (EmptyArgs, Quality),
}


def tool_schemas() -> dict[str, Any]:
    result = {}
    for name, (input_type, output_type) in CATALOG.items():
        envelope = create_model(
            f"{name}_result",
            __base__=ToolResult,
            rows=(list[output_type], Field(max_length=20)),  # type: ignore[valid-type]
        )
        result[name] = {
            "version": VERSION,
            "input": input_type.model_json_schema(),
            "output": envelope.model_json_schema(),
            "write": name == "record_feedback",
        }
    return result


class ToolBackend(Protocol):
    def read(
        self,
        tool: str,
        arguments: dict[str, Any],
        request_id: str,
    ) -> tuple[list[dict[str, Any]], Provenance]: ...

    def write_feedback(self, event: OperationalEvent) -> WriteResult: ...


class GovernedTools:
    """No LLM required. Backend receives only validated, authorized arguments."""

    def __init__(
        self,
        backend: ToolBackend,
        *,
        actor_secret: bytes,
        trace_sink: Callable[[dict[str, Any]], None],
    ) -> None:
        require(len(actor_secret) >= 32, "Actor secret must contain at least 32 bytes")
        self.backend, self.secret, self.trace_sink = backend, actor_secret, trace_sink

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
        request_id: str,
        write_confirmed: bool = False,
    ) -> ToolResult:
        started = time.monotonic()
        actor = ActorContext(subject=context.subject)
        trace = {
            "tool": tool if tool in CATALOG else "UNKNOWN",
            "version": VERSION,
            "actor_hash": pseudonymize_actor(actor, self.secret),
            "request_hash": hashlib.sha256(request_id.encode()).hexdigest(),
            "status": "DENIED_OR_FAILED",
            "arguments_recorded": False,
        }
        try:
            require(tool in CATALOG, "Unknown tool")
            require(
                len(json.dumps(arguments, allow_nan=False).encode()) <= 8192,
                "Tool request exceeds 8 KiB",
            )
            input_type, output_type = CATALOG[tool]
            validated = input_type.model_validate(arguments)
            customer = getattr(validated, "customer_id", None)
            if customer is not None:
                require(customer in context.allowed_customers, "CUSTOMER_ACCESS_DENIED")
            if tool == "get_quality_summary":
                require(context.can_view_quality, "QUALITY_ACCESS_DENIED")
            # Validate correlation before a backend call or write.
            from pydantic import TypeAdapter

            TypeAdapter(Identifier).validate_python(request_id)
            if tool == "record_feedback":
                require(write_confirmed, "EXPLICIT_WRITE_CONFIRMATION_REQUIRED")
                feedback = FeedbackArgs.model_validate(arguments)
                # Customer must have received this product in the authoritative batch.
                eligible, provenance = self.backend.read(
                    "explain_recommendation",
                    {
                        "customer_id": feedback.customer_id,
                        "product_id": feedback.feedback.product_id,
                    },
                    request_id,
                )
                require(bool(eligible), "FEEDBACK_REQUIRES_RECOMMENDED_PRODUCT")
                event = build_event(
                    table="feedback",
                    actor=actor,
                    secret=self.secret,
                    idempotency_key=feedback.idempotency_key,
                    correlation_id=request_id,
                    payload={
                        **feedback.feedback.model_dump(mode="json"),
                        "customer_id": feedback.customer_id,
                    },
                )
                receipt = self.backend.write_feedback(event)
                rows = [{"event_id": receipt.event.event_id, "replayed": receipt.replayed}]
            else:
                rows, provenance = self.backend.read(
                    tool, validated.model_dump(mode="json"), request_id
                )
            require(len(rows) <= 20, "Tool output exceeded row bound")
            safe = [output_type.model_validate(row).model_dump(mode="json") for row in rows]
            result = ToolResult(tool=tool, request_id=request_id, provenance=provenance, rows=safe)
            require(
                len(result.model_dump_json().encode()) <= 64_000, "Tool output exceeded byte bound"
            )
            trace["status"] = "SUCCESS"
            return result
        finally:
            trace["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            trace["observed_at"] = datetime.now(UTC).isoformat()
            self.trace_sink(trace)
