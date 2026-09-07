"""One-time, budget-admitted Phase 5 MLflow registration and validation run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase3 import MODEL_ROOT, REPO_ROOT, _put_immutable
from retail_hp_azure.phase4 import inspect_lakehouse
from retail_hp_azure.phase5 import (
    AZURE_ROOT,
    canonical_output_sha,
    ensure_registered_model_owner,
    golden_inputs,
    inspect_local_artifacts,
    inspect_registered_model,
    record_evidence,
)
from retail_hp_azure.recommender import AdaptiveRetailRecommender
from retail_hp_azure.safety import SafetyError, require

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

NOTEBOOK_SOURCE = AZURE_ROOT / "notebooks" / "phase5_register.py"
WORKSPACE_ROOT = "/Workspace/Shared/retail_hp_phase5"
RUNTIME_ROOT = f"{MODEL_ROOT}/_runtime/phase5"
ENVIRONMENT_VERSION = "5"
DEPENDENCIES = (
    "joblib==1.5.3",
    "numpy==2.5.1",
    "pandas==3.0.3",
    "pyarrow==24.0.0",
    "scikit-learn==1.9.0",
    "scipy==1.18.1",
    "xgboost==3.3.0",
)
MAX_RUNTIME_MINUTES = 3.5
CONTROLLER_DEADLINE_MINUTES = 5.5
PLANNING_MAX_DBU_PER_HOUR = 16
AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR = 44.91
RISK_MULTIPLIER = 2.0
PHASE5_CEILING_INR = 250.0


def registration_job_plan() -> dict[str, Any]:
    base = (
        AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR * PLANNING_MAX_DBU_PER_HOUR * MAX_RUNTIME_MINUTES / 60
    )
    guarded = base * RISK_MULTIPLIER
    require(guarded < PHASE5_CEILING_INR, "Guarded Phase 5 estimate exceeds ceiling")
    return {
        "version": "azure_phase5_job_plan_v1",
        "compute": "automated_serverless_cpu",
        "persistent_job_created": False,
        "schedule_created": False,
        "serving_endpoint_created": False,
        "gpu": False,
        "environment_version": ENVIRONMENT_VERSION,
        "dependency_count": len(DEPENDENCIES) + 1,
        "task_timeout_minutes": MAX_RUNTIME_MINUTES,
        "controller_deadline_minutes": CONTROLLER_DEADLINE_MINUTES,
        "planning_max_dbu_per_hour": PLANNING_MAX_DBU_PER_HOUR,
        "retail_rate_inr_per_dbu_hour": AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR,
        "base_estimate_inr_pre_tax": round(base, 4),
        "risk_multiplier": RISK_MULTIPLIER,
        "guarded_estimate_inr_pre_tax": round(guarded, 4),
        "phase5_ceiling_inr": PHASE5_CEILING_INR,
        "hard_invoice_cap_guaranteed": False,
        "source": "Microsoft Azure Retail Prices API snapshot, West US, 2026-09-07",
    }


def _require_approval() -> None:
    require(
        os.environ.get("RETAIL_HP_PHASE5_CEILING_INR") == "250",
        "Exact Phase 5 execution ceiling approval is required",
    )


def _build_wheel() -> tuple[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="retail-hp-phase5-") as directory:
        command = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            directory,
        ]
        completed = subprocess.run(  # noqa: S603 -- closed argv invokes the pinned interpreter
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        require(completed.returncode == 0, "Phase 5 wheel build failed")
        wheels = list(Path(directory).glob("*.whl"))
        require(len(wheels) == 1, "Expected exactly one Phase 5 wheel")
        return wheels[0].name, wheels[0].read_bytes()


def _ensure_notebook(client: WorkspaceClient) -> tuple[str, str]:
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language

    content = NOTEBOOK_SOURCE.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    path = f"{WORKSPACE_ROOT}/phase5_register_{digest[:16]}"
    try:
        client.workspace.get_status(path)
    except NotFound:
        client.workspace.mkdirs(WORKSPACE_ROOT)
        client.workspace.upload(
            path, content, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=False
        )
        return path, "CREATED"
    with client.workspace.download(path, format=ExportFormat.SOURCE) as stream:
        observed = stream.read().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
    require(hashlib.sha256(observed).hexdigest() == digest, "Phase 5 notebook drift")
    return path, "NO_CHANGE"


def _parse_output(client: WorkspaceClient, run: Any) -> dict[str, Any]:
    from databricks.sdk.service.jobs import RunResultState

    require(
        run.state is not None and run.state.result_state == RunResultState.SUCCESS,
        "Phase 5 Databricks run did not succeed",
    )
    tasks = run.tasks or []
    require(len(tasks) == 1 and tasks[0].run_id is not None, "Phase 5 output is missing")
    output = client.jobs.get_run_output(tasks[0].run_id)
    raw = getattr(getattr(output, "notebook_output", None), "result", None)
    require(isinstance(raw, str) and len(raw) < 40_000, "Phase 5 output is unavailable")
    if not isinstance(raw, str):  # pragma: no cover
        raise SafetyError("Phase 5 output is unavailable")
    result = json.loads(raw)
    require(isinstance(result, dict) and result.get("status") == "PASS", "Phase 5 failed")
    return cast(dict[str, Any], result)


def local_golden_validation() -> dict[str, Any]:
    runtime = AdaptiveRetailRecommender(
        REPO_ROOT / "migration_assets", REPO_ROOT / "migration_assets" / "artifacts"
    )
    inputs = golden_inputs(runtime)
    output = runtime.predict(inputs)
    require(output.groupby("request_id").size().eq(10).all(), "Local golden coverage failed")
    require(output.product_id.isin(runtime.eligible_ids).all(), "Local inventory validity failed")
    return {
        "version": "azure_phase5_local_golden_v1",
        "status": "PASS",
        "request_count": len(inputs),
        "output_rows": len(output),
        "output_sha256": canonical_output_sha(output),
        "inventory_valid_pct": 100.0,
        "identifiers_recorded": False,
    }


def run_registration(context: CloudContext) -> dict[str, Any]:
    from databricks.sdk.service.compute import Environment
    from databricks.sdk.service.jobs import (
        JobEnvironment,
        NotebookTask,
        RunLifeCycleState,
        Source,
        SubmitTask,
    )

    require(context.apply, "Phase 5 registration requires explicit apply context")
    _require_approval()
    plan = registration_job_plan()
    budget = _verify_budget(context)
    require(
        12_000 - float(budget["current_spend_inr"]) > plan["guarded_estimate_inr_pre_tax"],
        "Azure budget has insufficient Phase 5 headroom",
    )
    before = inspect_compute(context)
    require(before["cluster_count"] == 0 and before["job_count"] == 0, "Unexpected compute")
    require(
        all(item["state"] == "STOPPED" for item in before["project_warehouses"]),
        "Project warehouse must remain stopped",
    )
    require(inspect_lakehouse(context)["status"] == "PASS", "Phase 4 lakehouse is incomplete")
    artifacts = inspect_local_artifacts()
    local = local_golden_validation()
    wheel_name, wheel = _build_wheel()
    wheel_hash = hashlib.sha256(wheel).hexdigest()
    wheel_path = f"{RUNTIME_ROOT}/{wheel_hash[:16]}/{wheel_name}"
    wheel_operation = _put_immutable(context.client.files, wheel_path, wheel)
    notebook_path, notebook_operation = _ensure_notebook(context.client)
    notebook_hash = hashlib.sha256(NOTEBOOK_SOURCE.read_bytes()).hexdigest()

    started = time.monotonic()
    finished = threading.Event()
    deadline_fired = threading.Event()
    wait = context.client.jobs.submit(
        run_name="retail-hp-phase5-functional-mlflow",
        idempotency_token=f"retail-hp-phase5-{notebook_hash[:16]}-{wheel_hash[:16]}",
        timeout_seconds=int(MAX_RUNTIME_MINUTES * 60),
        environments=[
            JobEnvironment(
                environment_key="default",
                spec=Environment(
                    environment_version=ENVIRONMENT_VERSION,
                    dependencies=[wheel_path, *DEPENDENCIES],
                ),
            )
        ],
        tasks=[
            SubmitTask(
                task_key="register_and_validate",
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
    require(report["golden_output_sha256"] == local["output_sha256"], "Local/Azure parity failed")
    owner_control = ensure_registered_model_owner(context)
    require(owner_control["status"] == "PASS", "Registered model owner control failed")
    registry = inspect_registered_model(context)
    require(
        registry["status"] == "PASS"
        and registry["owner_valid"]
        and registry["candidate_present"]
        and not registry["champion_present"],
        "Registered model governance failed",
    )
    after = inspect_compute(context)
    require(after["cluster_count"] == 0 and after["job_count"] == 0, "Compute cleanup failed")
    require(
        all(item["state"] == "STOPPED" for item in after["project_warehouses"]),
        "Warehouse state changed",
    )
    return {
        **report,
        "artifact_inspection": artifacts["status"],
        "local_azure_golden_parity": "PASS",
        "notebook_operation": notebook_operation,
        "notebook_sha256": notebook_hash,
        "wheel_operation": wheel_operation,
        "wheel_sha256": wheel_hash,
        "registry_inspection": "PASS",
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
        "run_identifier_recorded": False,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "local-validate", "apply"])
    args = parser.parse_args()
    if args.command == "plan":
        result = registration_job_plan()
    elif args.command == "local-validate":
        result = local_golden_validation()
        record_evidence("local_validation.json", result)
    else:
        result = run_registration(CloudContext(apply=True))
        record_evidence("registration.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
