"""Publish immutable agent files and redacted MLflow evaluation records; no paid compute."""

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile

from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import ExportFormat, ImportFormat
from phase8_control import stopped
from phase9_evaluate import EVIDENCE, ROOT
from retail_hp_azure.config import HOST
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase9 import PROMPT_VERSION, SYSTEM_PROMPT, VERSION
from retail_hp_azure.phase9_evaluation import cases, summarize
from retail_hp_azure.safety import require


def publish(report_path: Path):
    import mlflow
    from mlflow import MlflowClient

    sys.stdout.reconfigure(encoding="utf-8")
    report = json.loads(report_path.read_text())
    require(report["status"] == "PASS" and report["mode"] == "full", "Full evaluation must pass")
    require(report["endpoint"] == "gpt-5.6-luna", "Current release requires Luna evaluation")
    require(
        report["prompt_sha256"] == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "Evaluation prompt does not match release",
    )
    require(
        {r["case_id"] for r in report["rows"]} == {c.id for c in cases()}
        and len(report["rows"]) == 135
        and summarize(report["rows"])["passed"],
        "Evaluation coverage/gates mismatch",
    )
    require(
        len(report.get("multiturn", [])) >= 8 and all(row["passed"] for row in report["multiturn"]),
        "Multi-turn acceptance required",
    )
    live = [json.loads(p.read_text()) for p in EVIDENCE.glob("live_*.json")]
    require(
        any(
            r["status"] == "PASS"
            and r.get("llm_model") == report["endpoint"]
            and r["warehouse_final_state"] == "STOPPED"
            and r["temporary_secret_revoked"]
            for r in live
        ),
        "Live acceptance required",
    )
    context = CloudContext(apply=True)
    stopped(context)
    wheels = list((ROOT / "build/phase9-dist").glob("*.whl"))
    require(len(wheels) == 1, "Build one release wheel first")
    with ZipFile(wheels[0]) as wheel:
        for source in (ROOT / "azure_databricks/src/retail_hp_azure").glob("*.py"):
            require(
                wheel.read(f"retail_hp_azure/{source.name}") == source.read_bytes(),
                "Wheel source drift; rebuild before publishing",
            )
    files = {
        wheels[0].name: wheels[0].read_bytes(),
        "system_prompt.txt": SYSTEM_PROMPT.encode(),
        "evaluation_dataset.json": json.dumps([asdict(c) for c in cases()], indent=2).encode(),
        "evaluation_report.json": report_path.read_bytes(),
        "agent.lock": (ROOT / "azure_databricks/environments/agent.lock").read_bytes(),
    }
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    release_path = f"/Shared/retail_hp_phase9/releases/{digest}"
    context.client.workspace.mkdirs(release_path)
    for name, data in files.items():
        path = f"{release_path}/{name}"
        try:
            with context.client.workspace.download(path, format=ExportFormat.AUTO) as stream:
                existing = stream.read()
            require(hashlib.sha256(existing).hexdigest() == manifest[name], "Release drift")
        except NotFound:
            context.client.workspace.upload(path, data, format=ImportFormat.AUTO, overwrite=False)
        with context.client.workspace.download(path, format=ExportFormat.AUTO) as stream:
            require(
                hashlib.sha256(stream.read()).hexdigest() == manifest[name], "Readback mismatch"
            )
    # Uses the existing Azure CLI identity in this operator process. No tokens are serialized.
    os.environ["DATABRICKS_HOST"] = HOST
    os.environ["DATABRICKS_AUTH_TYPE"] = "azure-cli"
    mlflow.set_tracking_uri("databricks")
    experiment = mlflow.set_experiment("/Shared/retail_hp_phase9/agent_evaluation")
    mlflow.tracing.enable()
    client = MlflowClient()
    existing = client.search_runs(
        [experiment.experiment_id], filter_string=f"params.release_sha256 = '{digest}'"
    )
    require(len(existing) <= 1, "Ambiguous release runs; reconcile manually")
    if existing:
        run_id = existing[0].info.run_id
    else:
        with mlflow.start_run(run_name=f"phase9-{digest[:12]}") as run:
            mlflow.log_params(
                {
                    "agent_version": VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "endpoint": report["endpoint"],
                    "release_sha256": digest,
                    "benchmark_tools": "synthetic_fixtures",
                    "fallback": "none",
                    "trace_layout": "one_replay_graph_v1",
                }
            )
            mlflow.log_metrics(report["summary"]["metrics"])
            mlflow.log_dict({"files": manifest, "workspace_path": release_path}, "release.json")
            mlflow.log_dict(report["summary"], "evaluation_summary.json")
            # One export graph avoids 143 independent authentication/export requests.
            # Child spans are recorded evaluation replays, never invented live calls.
            with mlflow.start_span(name="recorded_agent_evaluation", span_type="EVALUATOR"):
                for row in report["rows"] + report.get("multiturn", []):
                    with mlflow.start_span(name="recorded_case", span_type="EVALUATOR") as span:
                        span.set_inputs({"case_id": row["case_id"], "release_sha256": digest})
                        span.set_outputs(
                            {
                                "action": row["action"],
                                "status": row["status"],
                                "recorded_latency_ms": row["trace"]["elapsed_ms"],
                            }
                        )
                        span.set_attribute("retail.record_type", "redacted_evaluation_replay")
            run_id = run.info.run_id
    mlflow.flush_trace_async_logging()
    recorded = client.get_run(run_id)
    require(recorded.data.metrics["safety_pass"] == 1, "MLflow metrics readback failed")
    traces = client.search_traces(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"metadata.mlflow.sourceRun = '{run_id}'",
        max_results=200,
    )
    expected_count = len(report["rows"]) + len(report.get("multiturn", []))
    require(len(traces) == 1, "MLflow replay graph readback count mismatch")
    graph = client.get_trace(traces[0].info.trace_id)
    recorded_cases = [span for span in graph.data.spans if span.name == "recorded_case"]
    require(len(recorded_cases) == expected_count, "MLflow replay case count mismatch")
    require(
        {span.inputs["case_id"] for span in recorded_cases}
        == {row["case_id"] for row in report["rows"] + report.get("multiturn", [])},
        "MLflow replay case coverage mismatch",
    )
    result = {
        "status": "PASS",
        "release_path": release_path,
        "release_sha256": digest,
        "files": manifest,
        "mlflow_experiment_id": experiment.experiment_id,
        "mlflow_run_id": run_id,
        "redacted_evaluation_trace_count": len(traces),
        "redacted_evaluation_record_count": len(recorded_cases),
        "model": report["endpoint"],
        "deployed_app_identity_verified": False,
        "no_new_compute": True,
        "recommender_final_state": "STOPPED",
        "final_compute": stopped(context),
    }
    (EVIDENCE / "publication.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    publish(parser.parse_args().report)
