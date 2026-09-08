"""Cost-bounded Phase 8 control. Deployment creates no Azure resource or running endpoint."""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from databricks.sdk.service.compute import Environment
from databricks.sdk.service.jobs import JobEnvironment, JobSettings, NotebookTask, Task
from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language
from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase5 import inspect_registered_model
from retail_hp_azure.safety import require

ROOT = _find_repo_root()
SOURCE = ROOT / "azure_databricks/notebooks/phase8_semantic_build.py"
STATE = ROOT / "build/phase8-control.local.json"
EVIDENCE = ROOT / "azure_databricks/evidence/phase_08"
JOB = "retail-hp-poc-semantic-build"
TAG = "retail-hyper-personalization"
TIMEOUT = 120


def plan():
    # Published GTE rate: 1.857 DBU/1M input tokens. INR/DBU is a conservative
    # planning conversion, not an Azure invoice quote or guaranteed cap.
    attempts = [json.loads(p.read_text()) for p in EVIDENCE.glob("generation_*.json")]
    prior_compute = sum(item.get("estimated_job_cost_inr_pre_tax", 0) for item in attempts)
    unknown_tokens = (
        sum(
            item.get("status") != "PASS"
            and item.get("plan", {}).get("generation_job_timeout_seconds") == 180
            for item in attempts
        )
        * 500_000
    )
    # Reserve the entire input allowance for failed attempts with no usage output.
    prior_embedding_reserve = unknown_tokens * 1.857 * 44.91 / 1_000_000
    base = 16 * 44.91 * TIMEOUT / 3600 + 4.4588 * 3
    operator = EVIDENCE / "operator_embeddings.json"
    embedding_cost = (
        json.loads(operator.read_text())["estimated_embedding_cost_inr_pre_tax"]
        if operator.exists()
        else 0.5 * 1.857 * 44.91
    )
    validation_cost = sum(
        json.loads(p.read_text()).get("estimated_warehouse_cost_inr_pre_tax", 0)
        for p in EVIDENCE.glob("tool_validation_*.json")
    )
    total = (
        prior_compute + prior_embedding_reserve + embedding_cost + validation_cost + base * 2 + 1
    )
    return {
        "phase": 8,
        "new_azure_resources": 0,
        "new_dedicated_endpoints": 0,
        "generation_job_timeout_seconds": TIMEOUT,
        "schedule": None,
        "max_embedding_tokens": 500000,
        "max_products": 3000,
        "warehouse_max_minutes": 3,
        "prior_compute_estimate_inr": round(prior_compute, 4),
        "prior_unreported_embedding_reserve_inr": round(prior_embedding_reserve, 4),
        "known_embedding_estimate_inr": embedding_cost,
        "guarded_estimate_inr_pre_tax": round(total, 2),
        "planning_allowance_inr": 250,
        "hard_invoice_cap_guaranteed": False,
    }


def stopped(context):
    inventory = inspect_compute(context)
    require(
        inventory["cluster_count"] == 0 and inventory["app_count"] == 0,
        "Unexpected compute detected",
    )
    require(
        all(w["state"] == "STOPPED" for w in inventory["project_warehouses"]),
        "Warehouse must be stopped",
    )
    endpoint = context.client.api_client.do(
        "GET", "/api/2.0/serving-endpoints/retail-hp-poc-recommender"
    )
    require(endpoint["state"].get("suspend") == "STOPPED", "Recommender must be stopped")
    require(not list(context.client.jobs.list_runs(active_only=True)), "Active job detected")
    return inventory


def deploy(context):
    import base64

    from databricks.sdk.errors import NotFound

    require(context.apply, "Apply context required")
    stopped(context)
    registry = inspect_registered_model(context)
    require(registry["aliases"].get("champion") == 3, "Champion version drift")
    client = context.client
    content = SOURCE.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    directory = "/Workspace/Shared/retail_hp_phase8"
    path = f"{directory}/semantic_build_{digest[:16]}"
    client.workspace.mkdirs(directory)
    try:
        remote = client.workspace.export(path, format=ExportFormat.SOURCE)
        require(
            base64.b64decode(remote.content).replace(b"\r\n", b"\n").rstrip()
            == content.replace(b"\r\n", b"\n").rstrip(),
            "Notebook source drift",
        )
    except NotFound:
        client.workspace.upload(
            path, content, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=False
        )
    jobs = list(client.jobs.list(name=JOB))
    require(len(jobs) <= 1, "Duplicate semantic job")
    if jobs:
        job_id = jobs[0].job_id
        detail = client.jobs.get(job_id).settings
        require(
            detail.tags.get("project") == TAG and detail.tags.get("phase") == "8",
            "Refusing unrecognized job",
        )
        prior = json.loads(STATE.read_text())
        require(
            prior["job_id"] == job_id
            and len(detail.tasks) == 1
            and detail.tasks[0].notebook_task.notebook_path == prior["path"],
            "Existing job differs from the recorded deployment",
        )
        task = detail.tasks[0]
        task.notebook_task.notebook_path = path
        task.max_retries = 0
        task.timeout_seconds = TIMEOUT
        task.disable_auto_optimization = True
        client.jobs.update(job_id, new_settings=JobSettings(tasks=[task], timeout_seconds=TIMEOUT))
    else:
        created = client.jobs.create(
            name=JOB,
            max_concurrent_runs=1,
            timeout_seconds=TIMEOUT,
            tags={"project": TAG, "environment": "poc", "phase": "8"},
            tasks=[
                Task(
                    task_key="semantic_build",
                    notebook_task=NotebookTask(notebook_path=path),
                    environment_key="poc",
                    timeout_seconds=TIMEOUT,
                    max_retries=0,
                    disable_auto_optimization=True,
                )
            ],
            environments=[
                JobEnvironment(
                    environment_key="poc",
                    spec=Environment(environment_version="5", dependencies=[]),
                )
            ],
        )
        job_id = created.job_id
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"job_id": job_id, "source_sha256": digest, "path": path}))
    return {
        "status": "PASS",
        "job": JOB,
        "timeout_seconds": TIMEOUT,
        "schedule": None,
        "compute_started": False,
        "new_azure_resources": 0,
    }


def run(context):
    require(os.environ.get("RETAIL_HP_PHASE8_CEILING_INR") == "250", "Execution gate required")
    stopped(context)
    require(
        plan()["guarded_estimate_inr_pre_tax"] < 250,
        "Cumulative guarded cost plan exceeds allowance",
    )
    budget = _verify_budget(context)
    require(budget["current_spend_inr"] < 9000, "Internal monthly spending target reached")
    state = json.loads(STATE.read_text())
    require(
        hashlib.sha256(SOURCE.read_bytes()).hexdigest() == state["source_sha256"],
        "Source changed since deployment",
    )
    client = context.client
    settings = client.jobs.get(state["job_id"]).settings
    require(
        settings.name == JOB
        and settings.schedule is None
        and settings.trigger is None
        and settings.continuous is None
        and settings.timeout_seconds == TIMEOUT
        and settings.max_concurrent_runs == 1
        and len(settings.tasks) == 1
        and (settings.tasks[0].max_retries or 0) == 0
        and settings.tasks[0].disable_auto_optimization is True
        and settings.tasks[0].timeout_seconds == TIMEOUT
        and settings.tasks[0].notebook_task.notebook_path == state["path"],
        "Job drift",
    )
    started = time.monotonic()
    waiter = client.jobs.run_now(state["job_id"], idempotency_token=f"phase8-{uuid4().hex}")
    state["run_id"] = waiter.run_id
    STATE.write_text(json.dumps(state))
    report = {"status": "FAIL", "plan": plan()}
    try:
        completed = waiter.result(timeout=timedelta(minutes=4))
        require(completed.state.result_state.value == "SUCCESS", "Generation failed")
        output = client.jobs.get_run_output(completed.tasks[0].run_id)
        result = json.loads(output.notebook_output.result)
        require(result["status"] == "PASS", "Generation acceptance failed")
        report.update(status="PASS", generation=result)
    finally:
        final = client.jobs.get_run(waiter.run_id)
        if final.state.life_cycle_state.value not in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            client.jobs.cancel_run(waiter.run_id).result(timeout=timedelta(minutes=2))
        elapsed = time.monotonic() - started
        report["elapsed_seconds"] = round(elapsed, 2)
        report["estimated_job_cost_inr_pre_tax"] = round(16 * 44.91 * elapsed / 3600, 4)
        report["final_inventory"] = stopped(context)
        report["captured_at"] = datetime.now(UTC).isoformat()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / f"generation_{waiter.run_id}.json").write_text(json.dumps(report, indent=2))
    return report


def harden_retries(context):
    require(context.apply, "Apply context required")
    stopped(context)
    checked = []
    for name, key in (
        ("retail-hp-poc-batch-recommendations", "score_and_publish"),
        ("retail-hp-poc-feedback-export", "export_feedback"),
    ):
        jobs = list(context.client.jobs.list(name=name))
        require(len(jobs) == 1, "Expected prior-phase job missing")
        settings = context.client.jobs.get(jobs[0].job_id).settings
        require(
            settings.tags.get("project") == TAG
            and settings.schedule is None
            and settings.trigger is None
            and settings.continuous is None
            and settings.timeout_seconds == 180
            and len(settings.tasks) == 1
            and settings.tasks[0].task_key == key,
            "Prior-phase job contract drift",
        )
        task = settings.tasks[0]
        task.max_retries = 0
        task.disable_auto_optimization = True
        context.client.jobs.update(jobs[0].job_id, new_settings=JobSettings(tasks=[task]))
        verified = context.client.jobs.get(jobs[0].job_id).settings.tasks[0]
        require(verified.disable_auto_optimization is True, "Retry hardening failed")
        checked.append({"job": name, "disable_auto_optimization": True, "max_retries": 0})
    result = {"status": "PASS", "jobs": checked, "compute_started": False}
    (EVIDENCE / "retry_hardening.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "inspect", "deploy", "run", "harden-retries"])
    command = parser.parse_args().command
    if command == "plan":
        report = plan()
    else:
        context = CloudContext(apply=command in {"deploy", "run", "harden-retries"})
        report = {
            "inspect": stopped,
            "deploy": deploy,
            "run": run,
            "harden-retries": harden_retries,
        }[command](context)
    print(json.dumps(report, indent=2))
