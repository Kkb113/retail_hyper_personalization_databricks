"""One-time, budget-admitted Phase 4 serverless lakehouse build."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase3 import AZURE_ROOT, inspect_remote
from retail_hp_azure.phase4 import inspect_lakehouse, record_evidence, validate_local_features
from retail_hp_azure.safety import SafetyError, require

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient


NOTEBOOK_SOURCE = AZURE_ROOT / "notebooks" / "phase4_build.py"
WORKSPACE_ROOT = "/Workspace/Shared/retail_hp_phase4"
ENVIRONMENT_VERSION = "5"
MAX_RUNTIME_MINUTES = 5.0
CONTROLLER_DEADLINE_MINUTES = 7.0
PLANNING_MAX_DBU_PER_HOUR = 16
AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR = 44.91
RISK_MULTIPLIER = 2.0
PHASE4_CEILING_INR = 250.0


def build_job_plan() -> dict[str, Any]:
    base = (
        AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR * PLANNING_MAX_DBU_PER_HOUR * MAX_RUNTIME_MINUTES / 60
    )
    guarded = base * RISK_MULTIPLIER
    require(guarded < PHASE4_CEILING_INR, "Guarded Phase 4 estimate exceeds ceiling")
    return {
        "version": "azure_phase4_job_plan_v1",
        "compute": "automated_serverless_cpu",
        "persistent_job_created": False,
        "schedule_created": False,
        "continuous_pipeline_created": False,
        "gpu": False,
        "environment_version": ENVIRONMENT_VERSION,
        "dependency_count": 0,
        "task_timeout_minutes": MAX_RUNTIME_MINUTES,
        "controller_deadline_minutes": CONTROLLER_DEADLINE_MINUTES,
        "planning_max_dbu_per_hour": PLANNING_MAX_DBU_PER_HOUR,
        "retail_rate_inr_per_dbu_hour": AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR,
        "base_estimate_inr_pre_tax": round(base, 4),
        "risk_multiplier": RISK_MULTIPLIER,
        "guarded_estimate_inr_pre_tax": round(guarded, 4),
        "phase4_ceiling_inr": PHASE4_CEILING_INR,
        "hard_invoice_cap_guaranteed": False,
        "source": "Microsoft Azure Retail Prices API snapshot, West US, 2026-09-07",
    }


def _require_approval() -> None:
    require(
        os.environ.get("RETAIL_HP_PHASE4_CEILING_INR") == "250",
        "Exact Phase 4 execution ceiling approval is required",
    )


def _notebook_path() -> tuple[str, bytes]:
    content = NOTEBOOK_SOURCE.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    return f"{WORKSPACE_ROOT}/phase4_build_{digest[:16]}", content


def _ensure_notebook(client: WorkspaceClient) -> tuple[str, str]:
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language

    path, content = _notebook_path()
    expected = hashlib.sha256(content).hexdigest()
    try:
        client.workspace.get_status(path)
    except NotFound:
        client.workspace.mkdirs(WORKSPACE_ROOT)
        client.workspace.upload(
            path,
            content,
            format=ImportFormat.SOURCE,
            language=Language.PYTHON,
            overwrite=False,
        )
        return path, "CREATED"
    with client.workspace.download(path, format=ExportFormat.SOURCE) as stream:
        observed = stream.read()
    normalized = observed.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
    require(
        hashlib.sha256(normalized).hexdigest() == expected,
        "Versioned Phase 4 notebook content drift",
    )
    return path, "NO_CHANGE"


def _parse_output(client: WorkspaceClient, run: Any) -> dict[str, Any]:
    from databricks.sdk.service.jobs import RunResultState

    require(
        run.state is not None and run.state.result_state == RunResultState.SUCCESS,
        "Phase 4 Databricks run did not succeed",
    )
    tasks = run.tasks or []
    require(len(tasks) == 1 and tasks[0].run_id is not None, "Phase 4 output is missing")
    output = client.jobs.get_run_output(tasks[0].run_id)
    text = getattr(getattr(output, "notebook_output", None), "result", None)
    require(isinstance(text, str) and len(text) < 40_000, "Phase 4 output is unavailable")
    if not isinstance(text, str):  # pragma: no cover
        raise SafetyError("Phase 4 output is unavailable")
    value = json.loads(text)
    require(
        isinstance(value, dict) and value.get("status") == "PASS", "Phase 4 quality report failed"
    )
    return cast(dict[str, Any], value)


def run_build(context: CloudContext) -> dict[str, Any]:
    from databricks.sdk.service.compute import Environment
    from databricks.sdk.service.jobs import (
        JobEnvironment,
        NotebookTask,
        RunLifeCycleState,
        Source,
        SubmitTask,
    )

    require(context.apply, "Phase 4 build requires explicit apply context")
    _require_approval()
    plan = build_job_plan()
    budget = _verify_budget(context)
    require(
        12_000 - float(budget["current_spend_inr"]) > plan["guarded_estimate_inr_pre_tax"],
        "Azure budget has insufficient Phase 4 headroom",
    )
    before = inspect_compute(context)
    require(
        before["cluster_count"] == 0 and before["job_count"] == 0,
        "Unexpected cluster or persistent job before Phase 4",
    )
    require(
        all(item["state"] == "STOPPED" for item in before["project_warehouses"]),
        "Project warehouse must remain stopped",
    )
    transfer = inspect_remote(context)
    require(
        transfer["status"] == "PASS"
        and transfer["matching_count"] == 46
        and transfer["matching_seal_count"] == 2,
        "Sealed Phase 3 transfer must pass before Phase 4",
    )
    notebook_path, notebook_operation = _ensure_notebook(context.client)
    source_hash = hashlib.sha256(NOTEBOOK_SOURCE.read_bytes()).hexdigest()
    started = time.monotonic()
    finished = threading.Event()
    deadline_fired = threading.Event()
    wait = context.client.jobs.submit(
        run_name="retail-hp-phase4-lakehouse-build",
        idempotency_token=f"retail-hp-phase4-{source_hash[:32]}",
        timeout_seconds=int(MAX_RUNTIME_MINUTES * 60),
        environments=[
            JobEnvironment(
                environment_key="default",
                spec=Environment(environment_version=ENVIRONMENT_VERSION, dependencies=[]),
            )
        ],
        tasks=[
            SubmitTask(
                task_key="build_lakehouse",
                notebook_task=NotebookTask(notebook_path=notebook_path, source=Source.WORKSPACE),
                environment_key="default",
                timeout_seconds=int(MAX_RUNTIME_MINUTES * 60),
            )
        ],
    )
    run_id = wait.run_id

    def cancel_at_deadline() -> None:
        if not finished.wait(CONTROLLER_DEADLINE_MINUTES * 60):
            deadline_fired.set()
            context.client.jobs.cancel_run(run_id)

    controller = threading.Thread(target=cancel_at_deadline, daemon=True)
    controller.start()
    try:
        run = wait.result(timeout=timedelta(minutes=CONTROLLER_DEADLINE_MINUTES + 1))
        report = _parse_output(context.client, run)
    finally:
        finished.set()
        controller.join(timeout=1)
        final = context.client.jobs.get_run(run_id=run_id)
        if final.state is not None and final.state.life_cycle_state not in {
            RunLifeCycleState.TERMINATED,
            RunLifeCycleState.SKIPPED,
            RunLifeCycleState.INTERNAL_ERROR,
        }:
            context.client.jobs.cancel_run(run_id).result(timeout=timedelta(minutes=2))

    elapsed = min(time.monotonic() - started, MAX_RUNTIME_MINUTES * 60)
    estimated = AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR * PLANNING_MAX_DBU_PER_HOUR * elapsed / 3600
    local = validate_local_features()
    remote_parity = cast(dict[str, Any], report.get("feature_parity", {}))
    comparable = {
        key: value
        for key, value in local.items()
        if key not in {"version", "status", "customer_identifiers_recorded"}
    }
    for key, expected in comparable.items():
        observed = remote_parity.get(key)
        if isinstance(expected, float):
            require(
                isinstance(observed, int | float) and abs(float(observed) - expected) <= 0.01,
                f"Feature parity failed for {key}",
            )
        else:
            require(observed == expected, f"Feature parity failed for {key}")
    metadata = inspect_lakehouse(context)
    require(metadata["status"] == "PASS", "Phase 4 metadata inspection failed")
    after = inspect_compute(context)
    require(
        after["cluster_count"] == 0 and after["job_count"] == 0,
        "Phase 4 cleanup left cluster or persistent job",
    )
    require(
        all(item["state"] == "STOPPED" for item in after["project_warehouses"]),
        "Phase 4 changed the project warehouse state",
    )
    return {
        **report,
        "notebook_operation": notebook_operation,
        "notebook_sha256": source_hash,
        "metadata_inspection": "PASS",
        "local_golden_feature_parity": "PASS",
        "persistent_job_created": False,
        "schedule_created": False,
        "continuous_pipeline_created": False,
        "serverless_run_terminated": True,
        "deadline_fired": deadline_fired.is_set(),
        "elapsed_seconds": round(elapsed, 2),
        "estimated_elapsed_cost_inr_pre_tax": round(estimated, 4),
        "planning_guarded_ceiling_inr": plan["guarded_estimate_inr_pre_tax"],
        "hard_invoice_cap_guaranteed": False,
        "budget_admission": budget,
        "final_cluster_count": after["cluster_count"],
        "final_persistent_job_count": after["job_count"],
        "project_warehouse_state": after["project_warehouses"][0]["state"],
        "new_azure_resources": 0,
        "run_identifier_recorded": False,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run"])
    args = parser.parse_args()
    try:
        result = build_job_plan() if args.command == "plan" else run_build(CloudContext(apply=True))
        if args.command == "run":
            record_evidence("databricks_build.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(f"Phase 4 runtime failed: {type(exc).__name__}; {detail}") from None
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
