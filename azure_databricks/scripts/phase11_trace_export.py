"""Manual redacted trace export; SQL resumes only inside the approved shutdown lease."""
# ruff: noqa: S608 -- fixed target and schema, request rows parameterized.

import json
import os
import re
import sys
import time
from pathlib import Path

from databricks.sdk.service.sql import StatementParameterListItem
from phase10_live import STATE, automation
from retail_hp_azure.config import HOST
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_delta import _execute, _rows
from retail_hp_azure.phase10_runtime import RunningBackend
from retail_hp_azure.phase11 import PREFIX, WAREHOUSE, safe_trace
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def export(context):
    state = json.loads(STATE.read_text())
    require(time.time() < state["deadline"] - 90, "Existing validation window required")
    require(
        automation(context, "GET", "/jobs/" + state["stop_job"])["properties"]["status"]
        == "Running",
        "Independent shutdown must be running",
    )
    backend = RunningBackend(context.client, WAREHOUSE)
    backend.lease_expires = state["deadline"]
    backend.check_running()
    root = "/Volumes/intellify_databricks_demo/agent/app_launches/observability/" + state["ticket"]
    records, summaries = [], []
    for entry in context.client.files.list_directory_contents(root):
        require(len(summaries) < 200, "Trace export bound")
        require(
            entry.path.startswith(root + "/")
            and re.fullmatch(r"[0-9a-f]{64}\.json", entry.path.rsplit("/", 1)[1]),
            "Unexpected archive path",
        )
        with context.client.files.download(entry.path).contents as stream:
            payload = stream.read(100001)
        require(len(payload) <= 100000, "Trace archive too large")
        events = json.loads(payload)["events"]
        require(0 < len(events) <= 40, "Trace event bound")
        for event in events:
            clean = safe_trace(event)
            clean.pop("observed_at")
            require(
                {k: v for k, v in event.items() if k != "observed_at"} == clean,
                "Trace contains non-allowlisted fields",
            )
        request = [e for e in events if e.get("event") == "agent_request"]
        require(len(request) == 1, "Exactly one completed request per archive")
        request = request[0]
        require(
            all(e["request_hash"] == request["request_hash"] for e in events),
            "Broken trace correlation",
        )
        usage = [e for e in events if e.get("event") == "llm_call"]
        summaries.append(
            {
                "request_hash": request["request_hash"],
                "model_version": request["model_version"],
                "status": request["status"],
                "elapsed_ms": request["elapsed_ms"],
                "observed_at": request["observed_at"],
                "input_tokens": (
                    sum(e["input_tokens"] for e in usage)
                    if all("input_tokens" in e for e in usage)
                    else None
                ),
                "output_tokens": (
                    sum(e["output_tokens"] for e in usage)
                    if all("output_tokens" in e for e in usage)
                    else None
                ),
                "cost_estimate_inr": (
                    sum(e["cost_estimate_inr"] for e in usage)
                    if all("cost_estimate_inr" in e for e in usage)
                    else None
                ),
            }
        )
        records.extend(events)
    require(summaries, "No deployed request archives found")
    acceptance = json.loads(
        (ROOT / "azure_databricks/evidence/phase_10/chat_validation.json").read_text()
    )
    require(acceptance["release"] == state["release"], "Chat evidence release drift")
    require(
        len(acceptance.get("request_hashes", [])) == 4
        and set(acceptance["request_hashes"]) <= {r["request_hash"] for r in summaries},
        "Every evaluated request must have an archived correlation",
    )
    schema = (
        "ARRAY<STRUCT<request_hash:STRING,model_version:STRING,status:STRING,elapsed_ms:DOUBLE,"
    )
    schema += (
        "input_tokens:BIGINT,output_tokens:BIGINT,cost_estimate_inr:DOUBLE,observed_at:TIMESTAMP>>"
    )
    statement = f"""MERGE INTO {PREFIX}.phase11_requests t USING
      (SELECT m.* FROM (SELECT explode(from_json(:payload,'{schema}')) AS m)) s
      ON t.request_hash=s.request_hash WHEN NOT MATCHED THEN INSERT *"""
    _execute(
        context.client,
        WAREHOUSE,
        statement,
        parameters=[
            StatementParameterListItem(name="payload", value=json.dumps(summaries), type="STRING")
        ],
        deadline_seconds=30,
    )
    rows = _rows(
        _execute(
            context.client,
            WAREHOUSE,
            f"SELECT count(*) AS n FROM {PREFIX}.phase11_requests WHERE request_hash IN "
            "(SELECT explode(from_json(:ids,'ARRAY<STRING>')))",
            parameters=[
                StatementParameterListItem(
                    name="ids",
                    value=json.dumps([r["request_hash"] for r in summaries]),
                    type="STRING",
                )
            ],
            deadline_seconds=30,
        )
    )
    require(int(rows[0]["n"]) == len(summaries), "Request export reconciliation failed")
    # Replay observed redacted events into the EXISTING MLflow experiment. Never autolog prompts.
    import mlflow
    from mlflow import MlflowClient

    os.environ["DATABRICKS_HOST"] = HOST
    os.environ["DATABRICKS_AUTH_TYPE"] = "azure-cli"
    mlflow.set_tracking_uri("databricks")
    publication = json.loads(
        (ROOT / "azure_databricks/evidence/phase_09/publication.json").read_text()
    )
    experiment_id = publication["mlflow_experiment_id"]
    client = MlflowClient()
    previous = client.search_runs(
        [experiment_id], filter_string=f"tags.phase11_launch = '{state['ticket']}'"
    )
    require(len(previous) <= 1, "Ambiguous observed runs")
    if previous:
        run_id = previous[0].info.run_id
        recorded = client.get_run(run_id)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": recorded.info.status,
                    "metrics": recorded.data.metrics,
                    "expected_requests": len(summaries),
                    "expected_events": len(records),
                }
            )
        )
        require(
            recorded.info.status == "FINISHED"
            and recorded.data.metrics.get("observed_requests") == len(summaries)
            and recorded.data.metrics.get("redacted_events") == len(records),
            "Existing run incomplete; no duplicate export allowed",
        )
    else:
        with mlflow.start_run(
            experiment_id=experiment_id,
            run_name="phase11-observed-requests",
            tags={"phase11_launch": state["ticket"]},
        ) as run:
            mlflow.tracing.enable()
            with mlflow.start_span(name="observed_request_replay") as root_span:
                root_span.set_attribute(
                    "record_type", "redacted_observed_replay_not_live_span_timing"
                )
                for event in records:
                    with mlflow.start_span(name=event.get("event", "tool_call")) as span:
                        span.set_attributes(event)
            mlflow.log_metrics(
                {"observed_requests": len(summaries), "redacted_events": len(records)}
            )
            run_id = run.info.run_id
    mlflow.flush_trace_async_logging()
    traces = client.search_traces(
        experiment_ids=[experiment_id],
        filter_string=f"metadata.mlflow.sourceRun = '{run_id}'",
        max_results=10,
    )
    require(len(traces) == 1, "MLflow trace readback failed")
    report = {
        "status": "PASS_OBSERVED_TRACE_EXPORT",
        "request_count": len(summaries),
        "event_count": len(records),
        "all_correlations_verified": True,
        "raw_payloads_recorded": False,
        "mlflow_run_id": run_id,
        "mlflow_trace_id": traces[0].info.trace_id,
        "replay_not_live_span_timing": True,
    }
    (ROOT / "azure_databricks/evidence/phase_11/trace_validation.json").write_text(
        json.dumps(report, indent=2)
    )
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(export(CloudContext(apply=True, direct_operator_token=True)), indent=2))
