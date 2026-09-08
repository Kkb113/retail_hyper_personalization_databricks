"""Deploy, trigger and inspect the single unscheduled Phase 6 batch job."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from databricks.sdk.service.compute import Environment
from databricks.sdk.service.jobs import JobEnvironment, JobSettings, NotebookTask, Task
from databricks.sdk.service.workspace import ImportFormat, Language
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase5_runtime import DEPENDENCIES

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["deploy", "run", "inspect"])
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
state_file = root / "build/phase6-control.local.json"
state_file.parent.mkdir(exist_ok=True)
state = json.loads(state_file.read_text()) if state_file.exists() else {}
context = CloudContext(apply=args.command != "inspect")
client = context.client
name = "retail-hp-poc-batch-recommendations"

if args.command == "deploy":
    source = (root / "azure_databricks/notebooks/phase6_batch.py").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    notebook = "/Shared/retail_hp_phase6/batch_" + digest[:16]
    client.workspace.mkdirs("/Shared/retail_hp_phase6")
    client.workspace.upload(
        notebook, source, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True
    )
    jobs = list(client.jobs.list(name=name, limit=25))
    assert len(jobs) <= 1
    settings = dict(
        name=name,
        max_concurrent_runs=1,
        timeout_seconds=180,
        description="Synthetic POC candidate v3 demo100 batch; manual only; 3-minute timeout.",
        tags={"project": "retail-hyper-personalization", "environment": "poc"},
        tasks=[
            Task(
                task_key="score_and_publish",
                notebook_task=NotebookTask(notebook_path=notebook),
                environment_key="poc",
                timeout_seconds=180,
                max_retries=0,
                disable_auto_optimization=True,
            )
        ],
        environments=[
            JobEnvironment(
                environment_key="poc",
                spec=Environment(environment_version="5", dependencies=list(DEPENDENCIES)),
            )
        ],
    )
    if jobs:
        assert jobs[0].job_id == state["job_id"], "Refuse unrelated job mutation"
        prior = client.jobs.get_run(state["run_id"])
        assert prior.state.result_state, "Previous run is still active"
        assert state["notebook_sha256"] != digest, "No unreviewed repeat run"
        state.setdefault("run_history", [])
        if state.get("prior_run_id") and state["prior_run_id"] not in state["run_history"]:
            state["run_history"].append(state["prior_run_id"])
        state["prior_run_id"] = state.pop("run_id")
        client.jobs.reset(state["job_id"], JobSettings(**settings))
    else:
        created = client.jobs.create(**settings)
        state["job_id"] = created.job_id
    state.update(notebook_sha256=digest)
    print(json.dumps({"status": "DEPLOYED", "schedule": None, "timeout_seconds": 180}))
elif args.command == "run":
    _verify_budget(context)
    assert "run_id" not in state, "Prior run must be reconciled before another paid attempt"
    job = client.jobs.get(state["job_id"])
    assert job.settings.name == name and job.settings.schedule is None
    assert job.settings.timeout_seconds == 180
    # This is a deterministic duplicate-prevention key, not an authentication secret.
    run = client.jobs.run_now(
        state["job_id"], idempotency_token="retail-hp-phase6-" + state["notebook_sha256"][:16]
    )
    state["run_id"] = run.run_id
    print(json.dumps({"status": "SUBMITTED", "planning_compute_inr_pre_tax": 35.92536}))
else:
    run = client.jobs.get_run(state["run_id"])
    report = {
        "state": run.state.as_dict(),
        "duration_seconds": ((run.end_time or time.time() * 1000) - run.start_time) / 1000,
    }
    if run.state.result_state:
        output = client.jobs.get_run_output(run.tasks[0].run_id)
        if output.notebook_output and output.notebook_output.result:
            report["validation"] = json.loads(output.notebook_output.result)
        elif output.error:
            report["error"] = output.error[:1500]
        target = root / "azure_databricks/evidence/phase_06/batch_run.json"
        target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
if args.command != "inspect":
    state_file.write_text(json.dumps(state))
