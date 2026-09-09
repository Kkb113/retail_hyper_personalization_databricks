"""Finish an interrupted metadata export after verifying persisted spans; no compute."""

import json
import os
import sys
from pathlib import Path

from mlflow import MlflowClient
from retail_hp_azure.config import HOST
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def recover():
    os.environ["DATABRICKS_HOST"] = HOST
    os.environ["DATABRICKS_AUTH_TYPE"] = "azure-cli"
    state = json.loads((ROOT / "build/phase10-live.local.json").read_text())
    acceptance = json.loads(
        (ROOT / "azure_databricks/evidence/phase_10/chat_validation.json").read_text()
    )
    require(acceptance["release"] == state["release"], "Release mismatch")
    publication = json.loads(
        (ROOT / "azure_databricks/evidence/phase_09/publication.json").read_text()
    )
    client = MlflowClient(tracking_uri="databricks")
    experiment = publication["mlflow_experiment_id"]
    runs = client.search_runs(
        [experiment], filter_string=f"tags.phase11_launch = '{state['ticket']}'"
    )
    require(len(runs) == 1, "Exactly one prior export required")
    run = runs[0]
    traces = client.search_traces(
        experiment_ids=[experiment],
        filter_string=f"metadata.mlflow.sourceRun = '{run.info.run_id}'",
        max_results=10,
    )
    require(len(traces) == 1, "Exactly one persisted replay required")
    trace = client.get_trace(traces[0].info.trace_id)
    events = [s.attributes for s in trace.data.spans if "request_hash" in s.attributes]
    requests = {e["request_hash"] for e in events if e.get("event") == "agent_request"}
    require(requests == set(acceptance["request_hashes"]), "Persisted request mismatch")
    require(len(events) == run.data.metrics.get("redacted_events"), "Persisted event mismatch")
    require(len(requests) == run.data.metrics.get("observed_requests"), "Request count mismatch")
    require(run.info.status in {"RUNNING", "FINISHED"}, "Unexpected prior run status")
    if run.info.status == "RUNNING":
        client.set_terminated(run.info.run_id, status="FINISHED")
    require(client.get_run(run.info.run_id).info.status == "FINISHED", "Run not finalized")
    report = {
        "status": "PASS_OBSERVED_TRACE_EXPORT",
        "request_count": len(requests),
        "event_count": len(events),
        "all_correlations_verified": True,
        "raw_payloads_recorded": False,
        "mlflow_run_id": run.info.run_id,
        "mlflow_trace_id": trace.info.trace_id,
        "replay_not_live_span_timing": True,
        "recovered_interrupted_finalization": True,
        "compute_started": False,
    }
    (ROOT / "azure_databricks/evidence/phase_11/trace_validation.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    recover()
