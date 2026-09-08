"""Governed Delta fallback for Phase 7 operational events."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

from retail_hp_azure.phase7 import (
    CATALOG,
    EXPORT_SCHEMA,
    EXPORT_TABLE,
    OPERATIONAL_SCHEMA,
    OPERATIONAL_TABLES,
    OWNER,
    RUNTIME_PRINCIPAL,
    ActorContext,
    OperationalEvent,
    WriteResult,
    canonical_payload_hash,
    pseudonymize_actor,
)
from retail_hp_azure.safety import SafetyError, require

CONTRACT_VERSION = "retail_hp_operational_delta_v1"
_WRITER_LOCK = threading.RLock()  # Single-process POC only; not a distributed lock.


def _stored_event(table: str, row: dict[str, Any]) -> OperationalEvent:
    def timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    return OperationalEvent(
        table=table, event_id=row["event_id"], actor_hash=row["actor_hash"],
        idempotency_key=row["idempotency_key"], correlation_id=row["correlation_id"],
        payload=json.loads(row["payload_json"]), created_at=timestamp(row["created_at"]),
        expires_at=timestamp(row["expires_at"]),
    )


def table_name(table: str) -> str:
    require(table in OPERATIONAL_TABLES, "Unknown operational table")
    return f"{CATALOG}.{OPERATIONAL_SCHEMA}.{table}"


def migration_statements() -> tuple[str, ...]:
    columns = """(
      event_id STRING NOT NULL,
      actor_hash STRING NOT NULL,
      idempotency_key STRING NOT NULL,
      correlation_id STRING NOT NULL,
      payload_json STRING NOT NULL,
      payload_sha256 STRING NOT NULL,
      created_at TIMESTAMP NOT NULL,
      expires_at TIMESTAMP NOT NULL
    ) USING DELTA
    TBLPROPERTIES (
      'delta.appendOnly' = 'true',
      'retail_hp.contract_version' = 'retail_hp_operational_delta_v1',
      'retail_hp.data_classification' = 'synthetic_poc_operational'
    )"""
    statements: list[str] = []
    for table in OPERATIONAL_TABLES:
        full_name = table_name(table)
        statements.extend(
            (
                f"CREATE TABLE IF NOT EXISTS {full_name} {columns}",  # noqa: S608
                f"ALTER TABLE {full_name} OWNER TO `{OWNER}`",  # noqa: S608
                f"GRANT SELECT, MODIFY ON TABLE {full_name} TO `{RUNTIME_PRINCIPAL}`",  # noqa: S608
            )
        )
    export = f"{CATALOG}.{EXPORT_SCHEMA}.{EXPORT_TABLE}"
    statements.extend(
        (
            f"""CREATE TABLE IF NOT EXISTS {export} (
              event_id STRING NOT NULL,
              actor_hash STRING NOT NULL,
              correlation_id STRING NOT NULL,
              sentiment STRING,
              reason_code STRING,
              product_id STRING,
              created_at TIMESTAMP NOT NULL,
              exported_at TIMESTAMP NOT NULL
            ) USING DELTA
            TBLPROPERTIES (
              'delta.appendOnly' = 'true',
              'retail_hp.contract_version' = 'retail_hp_feedback_export_v1',
              'retail_hp.data_classification' = 'synthetic_poc_analytics'
            )""",  # noqa: S608
            f"ALTER TABLE {export} OWNER TO `{OWNER}`",  # noqa: S608
            f"GRANT SELECT ON TABLE {export} TO `retail_hp_engineers`",  # noqa: S608
            f"GRANT USE_SCHEMA ON SCHEMA {CATALOG}.{OPERATIONAL_SCHEMA} "
            f"TO `{RUNTIME_PRINCIPAL}`",  # noqa: S608
        )
    )
    return tuple(statements)


def feedback_export_statement() -> str:
    source = table_name("feedback")
    target = f"{CATALOG}.{EXPORT_SCHEMA}.{EXPORT_TABLE}"
    return f"""MERGE INTO {target} AS target
    USING (
      SELECT event_id, actor_hash, correlation_id,
             get_json_object(payload_json, '$.sentiment') AS sentiment,
             get_json_object(payload_json, '$.reason_code') AS reason_code,
             get_json_object(payload_json, '$.product_id') AS product_id,
             created_at, current_timestamp() AS exported_at
      FROM {source}
      WHERE expires_at > current_timestamp()
    ) AS source
    ON target.event_id = source.event_id
    WHEN NOT MATCHED THEN INSERT *"""  # noqa: S608


def _execute(
    client: Any,
    warehouse_id: str,
    statement: str,
    *,
    parameters: list[Any] | None = None,
    deadline_seconds: float = 45,
) -> Any:
    from retail_hp_azure.phase2_compute import _verify_warehouse_contract

    warehouse = client.warehouses.get(warehouse_id)
    _verify_warehouse_contract(warehouse)
    require(
        getattr(getattr(warehouse, "state", None), "value", None) == "RUNNING",
        "Operational SQL requires explicitly started compute; use ephemeral mode while stopped",
    )
    deadline = time.monotonic() + deadline_seconds
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        catalog=CATALOG,
        schema=OPERATIONAL_SCHEMA,
        parameters=parameters,
        row_limit=1000,
        byte_limit=1_048_576,
        wait_timeout="10s",
    )
    while response.status is not None and response.status.state.value in {"PENDING", "RUNNING"}:
        if time.monotonic() >= deadline:
            client.statement_execution.cancel_execution(response.statement_id)
            raise SafetyError("Operational SQL statement timed out")
        time.sleep(0.5)
        response = client.statement_execution.get_statement(response.statement_id)
    state = getattr(getattr(response, "status", None), "state", None)
    require(getattr(state, "value", None) == "SUCCEEDED", "Operational SQL statement failed")
    return response


def _rows(response: Any) -> list[dict[str, Any]]:
    manifest = getattr(response, "manifest", None)
    require(not getattr(manifest, "truncated", False), "Operational SQL result was truncated")
    schema = getattr(manifest, "schema", None)
    names = [column.name for column in (getattr(schema, "columns", None) or [])]
    data = getattr(getattr(response, "result", None), "data_array", None) or []
    return [dict(zip(names, row, strict=True)) for row in data]


class DeltaOperationalStore:
    """Single-writer POC event store using parameterized, insert-only Delta MERGE."""

    def __init__(self, client: Any, warehouse_id: str) -> None:
        require(bool(warehouse_id), "Warehouse identifier is required")
        self.client = client
        self.warehouse_id = warehouse_id

    def write(self, event: OperationalEvent) -> WriteResult:
        with _WRITER_LOCK:
            return self._write(event.model_copy(deep=True))

    def _write(self, event: OperationalEvent) -> WriteResult:
        from databricks.sdk.service.sql import StatementParameterListItem

        full_name = table_name(event.table)
        payload = json.dumps(
            event.payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        payload_hash = canonical_payload_hash(event.payload)
        params = [
            StatementParameterListItem(name="event_id", value=event.event_id, type="STRING"),
            StatementParameterListItem(name="actor_hash", value=event.actor_hash, type="STRING"),
            StatementParameterListItem(
                name="idempotency_key", value=event.idempotency_key, type="STRING"
            ),
            StatementParameterListItem(
                name="correlation_id", value=event.correlation_id, type="STRING"
            ),
            StatementParameterListItem(name="payload_json", value=payload, type="STRING"),
            StatementParameterListItem(name="payload_sha256", value=payload_hash, type="STRING"),
            StatementParameterListItem(
                name="created_at",
                value=event.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
                type="TIMESTAMP",
            ),
            StatementParameterListItem(
                name="expires_at",
                value=event.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
                type="TIMESTAMP",
            ),
        ]
        before = self._get(event.table, event.event_id, event.actor_hash)
        if before:
            require(len(before) == 1, "Duplicate operational event detected")
            require(before[0]["payload_sha256"] == payload_hash, "Idempotency payload conflict")
            return WriteResult(event=_stored_event(event.table, before[0]), replayed=True)
        _execute(
            self.client,
            self.warehouse_id,
            f"""MERGE INTO {full_name} AS target
            USING (SELECT :event_id AS event_id, :actor_hash AS actor_hash,
                          :idempotency_key AS idempotency_key,
                          :correlation_id AS correlation_id, :payload_json AS payload_json,
                          :payload_sha256 AS payload_sha256,
                          CAST(:created_at AS TIMESTAMP) AS created_at,
                          CAST(:expires_at AS TIMESTAMP) AS expires_at) AS source
            ON target.event_id = source.event_id
            WHEN NOT MATCHED THEN INSERT *""",  # noqa: S608
            parameters=params,
        )
        after = self._get(event.table, event.event_id, event.actor_hash)
        require(len(after) == 1, "Operational write did not produce exactly one event")
        require(after[0]["payload_sha256"] == payload_hash, "Idempotency payload conflict")
        return WriteResult(event=_stored_event(event.table, after[0]), replayed=False)

    def _get(self, table: str, event_id: str, actor_hash: str) -> list[dict[str, Any]]:
        from databricks.sdk.service.sql import StatementParameterListItem

        full_name = table_name(table)
        response = _execute(
            self.client,
            self.warehouse_id,
            f"""SELECT * FROM {full_name}
            WHERE event_id = :event_id AND actor_hash = :actor_hash""",  # noqa: S608
            parameters=[
                StatementParameterListItem(name="event_id", value=event_id, type="STRING"),
                StatementParameterListItem(name="actor_hash", value=actor_hash, type="STRING"),
            ],
        )
        return _rows(response)

    def count_for_actor(self, table: str, actor_hash: str) -> int:
        from databricks.sdk.service.sql import StatementParameterListItem

        full_name = table_name(table)
        response = _execute(
            self.client,
            self.warehouse_id,
            f"""SELECT count(*) AS count FROM {full_name}
            WHERE actor_hash = :actor_hash AND expires_at > current_timestamp()""",  # noqa: S608
            parameters=[
                StatementParameterListItem(name="actor_hash", value=actor_hash, type="STRING")
            ],
        )
        rows = _rows(response)
        require(len(rows) == 1, "Operational count failed")
        return int(rows[0]["count"])

    def list_for_actor(
        self, *, table: str, actor: ActorContext, secret: bytes,
    ) -> tuple[OperationalEvent, ...]:
        """Trusted server API, not database RLS; fail rather than silently truncate."""
        from databricks.sdk.service.sql import StatementParameterListItem

        full_name = table_name(table)
        response = _execute(
            self.client, self.warehouse_id,
            f"""SELECT * FROM {full_name}
            WHERE actor_hash = :actor_hash AND expires_at > current_timestamp()
            ORDER BY created_at, event_id""",  # noqa: S608
            parameters=[StatementParameterListItem(
                name="actor_hash", value=pseudonymize_actor(actor, secret), type="STRING"
            )],
        )
        return tuple(_stored_event(table, row) for row in _rows(response))
