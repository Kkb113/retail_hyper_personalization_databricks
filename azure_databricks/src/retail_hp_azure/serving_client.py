"""Validated, bounded POC inference client with no implicit compute startup."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retail_hp_azure.config import HOST
from retail_hp_azure.phase6 import ENDPOINT_NAME


class ServingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1, max_length=128)
    request_type: Literal["existing_customer", "new_customer", "scenario"]
    customer_id: str | None = None
    scenario_id: str | None = None
    top_n: int = Field(default=10, ge=1, le=50, strict=True)
    as_of: str = "2026-01-01T00:00:00Z"
    region_id: str | None = None
    state: str | None = None
    climate_zone: str | None = None
    customer_segment: str | None = None
    loyalty_tier: str | None = None
    preferred_channel: str | None = None
    favorite_category_id: str | None = None
    favorite_brand_id: str | None = None
    price_sensitivity: str | None = None
    color_preference: str | None = None
    category_affinity_score: float | None = None
    brand_affinity_score: float | None = None
    session_events_json: str = Field(default="[]", max_length=32000)
    excluded_product_ids_json: str = Field(default="[]", max_length=32000)

    @model_validator(mode="after")
    def validate_context(self) -> ServingRequest:
        if self.request_type == "existing_customer" and not self.customer_id:
            raise ValueError("customer_id is required")
        if self.request_type == "scenario" and not self.scenario_id:
            raise ValueError("scenario_id is required")
        for value in (self.session_events_json, self.excluded_product_ids_json):
            if not isinstance(json.loads(value), list):
                raise ValueError("Session and exclusion fields must be JSON lists")
        if not all(isinstance(item, str) for item in json.loads(self.excluded_product_ids_json)):
            raise ValueError("Excluded product identifiers must be strings")
        for affinity in (self.category_affinity_score, self.brand_affinity_score):
            if affinity is not None and not math.isfinite(affinity):
                raise ValueError("Affinity scores must be finite")
        return self


class ServingError(RuntimeError):
    """Safe service error; raw upstream bodies and credentials are never exposed."""


class Transport(Protocol):
    def __call__(self, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]: ...


class RecommendationClient:
    def __init__(self, transport: Transport, *, timeout_seconds: float = 120) -> None:
        if not 0 < timeout_seconds <= 120:
            raise ValueError("Timeout must be between zero and 120 seconds")
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def predict(self, request: ServingRequest, *, eligible_ids: set[str]) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self.timeout_seconds
        payload = {"dataframe_records": [request.model_dump()]}
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServingError("INFERENCE_TIMEOUT")
            try:
                status, body = self.transport(payload, remaining)
            except TimeoutError as exc:
                raise ServingError("INFERENCE_TIMEOUT") from exc
            if status in {429, 503} and attempt == 0:
                time.sleep(min(1.0, max(0, deadline - time.monotonic())))
                continue
            if status != 200:
                raise ServingError(f"INFERENCE_HTTP_{status}")
            rows = body.get("predictions")
            if not isinstance(rows, list) or len(rows) > request.top_n:
                raise ServingError("INVALID_RESPONSE")
            seen: set[str] = set()
            excluded = set(json.loads(request.excluded_product_ids_json))
            safe: list[dict[str, Any]] = []
            for rank, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    raise ServingError("INVALID_RESPONSE")
                product = row.get("product_id")
                score = row.get("score")
                if (
                    row.get("request_id") != request.request_id
                    or type(row.get("rank")) is not int
                    or row.get("rank") != rank
                    or not isinstance(product, str)
                    or product in seen
                    or not isinstance(score, float | int)
                    or isinstance(score, bool)
                    or not math.isfinite(score)
                ):
                    raise ServingError("INVALID_RESPONSE")
                seen.add(product)
                if product in eligible_ids and product not in excluded:
                    safe.append({**row, "rank": len(safe) + 1})
            return safe
        raise ServingError("RETRY_EXHAUSTED")


def authenticated_transport(authenticate: Callable[[], dict[str, str]]) -> Transport:
    """Use identity-generated headers; never persist a PAT or enable redirects."""
    import requests

    def send(payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
        try:
            response = requests.post(
                f"{HOST}/serving-endpoints/{ENDPOINT_NAME}/invocations",
                headers=authenticate(),
                json=payload,
                timeout=(min(5.0, timeout), timeout),
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise TimeoutError("Inference deadline") from exc
        except requests.RequestException:
            raise ServingError("INFERENCE_CONNECTION_FAILED") from None
        if len(response.content) > 2_000_000:
            raise ServingError("RESPONSE_TOO_LARGE")
        try:
            body = response.json() if response.status_code == 200 else {}
        except ValueError:
            raise ServingError("INVALID_RESPONSE") from None
        if not isinstance(body, dict):
            raise ServingError("INVALID_RESPONSE")
        return int(response.status_code), body

    return send


def read_batch_recommendations(
    client: Any, customer_id: str, *, top_n: int = 10
) -> list[dict[str, Any]]:
    """Read the governed snapshot; require an explicitly started demo warehouse."""
    from databricks.sdk.service.sql import StatementParameterListItem

    request = ServingRequest(
        request_id="batch-read",
        request_type="existing_customer",
        customer_id=customer_id,
        top_n=top_n,
    )
    warehouses = [w for w in client.warehouses.list() if w.name == "retail-hp-poc-sql"]
    if len(warehouses) != 1 or str(warehouses[0].state.value) != "RUNNING":
        raise ServingError("DEMO_WAREHOUSE_NOT_RUNNING")
    response = client.statement_execution.execute_statement(
        warehouse_id=warehouses[0].id,
        statement=(
            "SELECT product_id, rank, score, route, candidate_sources, reason_codes, "
            "batch_id, registered_model_version, release_scope "
            "FROM intellify_databricks_demo.serving.customer_recommendations "
            "WHERE customer_id = :customer AND rank <= :top_n ORDER BY rank"
        ),
        parameters=[
            StatementParameterListItem(name="customer", value=request.customer_id, type="STRING"),
            StatementParameterListItem(name="top_n", value=str(request.top_n), type="INT"),
        ],
        wait_timeout="10s",
    )
    deadline = time.monotonic() + 30
    while response.status.state.value in {"PENDING", "RUNNING"}:
        if time.monotonic() >= deadline:
            client.statement_execution.cancel_execution(response.statement_id)
            raise ServingError("BATCH_READ_TIMEOUT")
        time.sleep(0.5)
        response = client.statement_execution.get_statement(response.statement_id)
    if response.status.state.value != "SUCCEEDED":
        raise ServingError("BATCH_READ_FAILED")
    names = [column.name for column in response.manifest.schema.columns]
    records = [dict(zip(names, row, strict=True)) for row in response.result.data_array or []]
    for record in records:
        record["rank"] = int(record["rank"])
        record["score"] = float(record["score"])
    return records
