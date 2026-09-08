"""Phase 7 operational-state contracts and a safe in-process POC fallback."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from retail_hp_azure.safety import SafetyError, require

CATALOG = "intellify_databricks_demo"
OPERATIONAL_SCHEMA = "agent"
EXPORT_SCHEMA = "monitoring"
RUNTIME_PRINCIPAL = "retail_hp_app_runtime"
OWNER = "retail_hp_admins"
EXPORT_JOB_NAME = "retail-hp-poc-feedback-export"

OPERATIONAL_TABLES = (
    "conversation",
    "message",
    "feedback",
    "recommendation_impression",
    "scenario_session",
    "notification",
    "idempotency_key",
    "app_audit_event",
)
EXPORT_TABLE = "feedback_event_export"

RETENTION_DAYS = {
    "conversation": 1,
    "message": 1,
    "feedback": 90,
    "recommendation_impression": 30,
    "scenario_session": 1,
    "notification": 7,
    "idempotency_key": 7,
    "app_audit_event": 30,
}

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class StoreMode(StrEnum):
    DELTA = "delta"
    EPHEMERAL = "ephemeral"
    READ_ONLY = "read_only"


class FeedbackSentiment(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class FeedbackReason(StrEnum):
    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    ALREADY_OWNED = "already_owned"
    TOO_EXPENSIVE = "too_expensive"
    OUT_OF_STOCK = "out_of_stock"
    OTHER = "other"


class FeedbackPayload(BaseModel):
    """Structured explicit feedback; never treat it as online-learning input."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    sentiment: FeedbackSentiment
    reason_code: FeedbackReason
    product_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    recommendation_request_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$"
    )
    reason_text: str | None = Field(default=None, max_length=500)


class ActorContext(BaseModel):
    """Authenticated subject supplied by the future Databricks App boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=255)


class OperationalEvent(BaseModel):
    """Small event envelope; raw authenticated identities are never persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    table: str
    event_id: str = Field(min_length=8, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)
    actor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    correlation_id: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("event_id", "idempotency_key", "correlation_id")
    @classmethod
    def validate_safe_key(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("must be an opaque safe identifier")
        return value

    @field_validator("table")
    @classmethod
    def validate_table(cls, value: str) -> str:
        if value not in OPERATIONAL_TABLES:
            raise ValueError("unknown operational table")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("payload exceeds 16 KiB")
        return value


@dataclass(frozen=True)
class WriteResult:
    event: OperationalEvent
    replayed: bool


def pseudonymize_actor(actor: ActorContext, secret: bytes) -> str:
    """Create a stable, non-reversible actor key using an uncommitted secret."""
    require(len(secret) >= 32, "Actor pseudonymization secret must contain at least 32 bytes")
    return hmac.new(secret, actor.subject.encode("utf-8"), hashlib.sha256).hexdigest()


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    require(len(encoded.encode("utf-8")) <= 16_384, "Payload exceeds 16 KiB")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_event(
    *,
    table: str,
    actor: ActorContext,
    secret: bytes,
    idempotency_key: str,
    correlation_id: str,
    payload: Mapping[str, Any],
    now: datetime | None = None,
) -> OperationalEvent:
    """Validate and bind one event to its authenticated actor and retention window."""
    require(table in OPERATIONAL_TABLES, "Unknown operational table")
    moment = now or datetime.now(UTC)
    require(moment.tzinfo is not None, "Event time must be timezone-aware")
    actor_hash = pseudonymize_actor(actor, secret)
    body = dict(payload)
    canonical_payload_hash(body)
    event_id = hashlib.sha256(
        f"{table}:{actor_hash}:{idempotency_key}".encode()
    ).hexdigest()
    return OperationalEvent(
        table=table,
        event_id=event_id,
        idempotency_key=idempotency_key,
        actor_hash=actor_hash,
        correlation_id=correlation_id,
        payload=body,
        created_at=moment.astimezone(UTC),
        expires_at=moment.astimezone(UTC) + timedelta(days=RETENTION_DAYS[table]),
    )


class InMemoryOperationalStore:
    """Thread-safe demo/session store mirroring Delta idempotency and user isolation."""

    def __init__(self, mode: StoreMode = StoreMode.EPHEMERAL) -> None:
        self.mode = mode
        self._events: dict[str, OperationalEvent] = {}
        self._payload_hashes: dict[str, str] = {}
        self._lock = threading.RLock()

    def write(self, event: OperationalEvent) -> WriteResult:
        if self.mode == StoreMode.READ_ONLY:
            raise SafetyError("Operational store unavailable; write path is disabled")
        digest = canonical_payload_hash(event.payload)
        with self._lock:
            current = self._events.get(event.event_id)
            if current is not None:
                require(
                    self._payload_hashes[event.event_id] == digest,
                    "Idempotency key was reused with a different payload",
                )
                return WriteResult(event=current.model_copy(deep=True), replayed=True)
            self._events[event.event_id] = event.model_copy(deep=True)
            self._payload_hashes[event.event_id] = digest
            return WriteResult(event=event.model_copy(deep=True), replayed=False)

    def list_for_actor(
        self,
        *,
        table: str,
        actor: ActorContext,
        secret: bytes,
        now: datetime | None = None,
    ) -> tuple[OperationalEvent, ...]:
        require(table in OPERATIONAL_TABLES, "Unknown operational table")
        actor_hash = pseudonymize_actor(actor, secret)
        moment = now or datetime.now(UTC)
        require(moment.tzinfo is not None, "Read time must be timezone-aware")
        with self._lock:
            return tuple(
                event.model_copy(deep=True)
                for event in self._events.values()
                if event.table == table
                and event.expires_at > moment
                and hmac.compare_digest(event.actor_hash, actor_hash)
            )

    def purge_expired(self, *, now: datetime | None = None) -> int:
        require(self.mode != StoreMode.READ_ONLY, "Read-only store cannot purge state")
        moment = now or datetime.now(UTC)
        require(moment.tzinfo is not None, "Purge time must be timezone-aware")
        with self._lock:
            expired = [key for key, event in self._events.items() if event.expires_at <= moment]
            for key in expired:
                self._events.pop(key)
                self._payload_hashes.pop(key)
            return len(expired)


def fallback_decision(*, lakebase_projects: int, lakebase_enabled: bool) -> dict[str, Any]:
    """Fail closed: Phase 7 never silently provisions a paid operational database."""
    require(lakebase_projects >= 0, "Invalid Lakebase inventory")
    require(not (lakebase_projects and not lakebase_enabled), "Unmanaged Lakebase project detected")
    selected = "lakebase" if lakebase_enabled else "delta_ephemeral_hybrid"
    return {
        "selected_store": selected,
        "new_paid_resource_required": lakebase_enabled and lakebase_projects == 0,
        "lakebase_project_count": lakebase_projects,
        "fallback_read_only_supported": True,
        "continuous_sync": False,
    }
