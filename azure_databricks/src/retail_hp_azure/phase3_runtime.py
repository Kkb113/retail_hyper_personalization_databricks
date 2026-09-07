"""One-time bounded Databricks runtime validation and Phase 3 sealing."""

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
from retail_hp_azure.phase3 import (
    AZURE_ROOT,
    RUNTIME_COMPAT_DESTINATION,
    RUNTIME_COMPAT_SOURCE,
    _put_immutable,
    inspect_remote,
    record_evidence,
    seal_transfer,
)
from retail_hp_azure.safety import SafetyError, require

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

NOTEBOOK_SOURCE = AZURE_ROOT / "notebooks" / "phase3_validate.py"
WORKSPACE_ROOT = "/Workspace/Shared/retail_hp_phase3"
ENVIRONMENT_VERSION = "5"
DEPENDENCIES = (
    "joblib==1.5.3",
    "numpy==2.5.1",
    "pandas==3.0.3",
    "pyarrow==24.0.0",
    "scikit-learn==1.9.0",
    "xgboost==3.3.0",
)
MAX_RUNTIME_MINUTES = 2.5
CONTROLLER_DEADLINE_MINUTES = 4
PLANNING_MAX_DBU_PER_HOUR = 16
AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR = 44.91
RISK_MULTIPLIER = 2.0
PHASE3_VALIDATION_CEILING_INR = 250.0


def validation_job_plan() -> dict[str, Any]:
    base = (
        AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR
        * PLANNING_MAX_DBU_PER_HOUR
        * MAX_RUNTIME_MINUTES
        / 60
    )
    guarded = base * RISK_MULTIPLIER
    require(guarded < PHASE3_VALIDATION_CEILING_INR, "Guarded validation estimate exceeds ceiling")
    return {
        "version": "azure_phase3_validation_job_plan_v1",
        "compute": "automated_serverless_cpu",
        "persistent_job_created": False,
        "schedule_created": False,
        "gpu": False,
        "environment_version": ENVIRONMENT_VERSION,
        "dependency_count": len(DEPENDENCIES),
        "task_timeout_minutes": MAX_RUNTIME_MINUTES,
        "controller_deadline_minutes": CONTROLLER_DEADLINE_MINUTES,
        "planning_max_dbu_per_hour": PLANNING_MAX_DBU_PER_HOUR,
        "retail_rate_inr_per_dbu_hour": AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR,
        "base_estimate_inr_pre_tax": round(base, 4),
        "risk_multiplier": RISK_MULTIPLIER,
        "guarded_estimate_inr_pre_tax": round(guarded, 4),
        "phase3_validation_ceiling_inr": PHASE3_VALIDATION_CEILING_INR,
        "hard_invoice_cap_guaranteed": False,
        "source": "Microsoft Azure Retail Prices API, West US, 2026-09-07",
    }


def _require_approval() -> None:
    require(
        os.environ.get("RETAIL_HP_PHASE3_VALIDATION_CEILING_INR") == "250",
        "Exact Phase 3 validation ceiling approval is required",
    )


def _notebook_path() -> tuple[str, bytes]:
    content = NOTEBOOK_SOURCE.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    return f"{WORKSPACE_ROOT}/phase3_validate_{digest[:16]}", content


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
            path, content, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=False
        )
        return path, "CREATED"
    with client.workspace.download(path, format=ExportFormat.SOURCE) as stream:
        observed = stream.read()
    # Source exports can normalize one final newline but may not otherwise differ.
    normalized = observed.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
    require(hashlib.sha256(normalized).hexdigest() == expected,
            "Versioned validation notebook content drift")
    return path, "NO_CHANGE"


def _parse_output(client: WorkspaceClient, run: Any) -> dict[str, Any]:
    from databricks.sdk.service.jobs import RunResultState

    require(run.state is not None and run.state.result_state == RunResultState.SUCCESS,
            "Databricks validation run did not succeed")
    tasks = run.tasks or []
    require(len(tasks) == 1 and tasks[0].run_id is not None, "Validation task output is missing")
    output = client.jobs.get_run_output(tasks[0].run_id)
    text = getattr(getattr(output, "notebook_output", None), "result", None)
    require(isinstance(text, str) and len(text) < 20_000, "Validation output is unavailable")
    if not isinstance(text, str):  # pragma: no cover - require raises; narrows SDK Any
        raise SafetyError("Validation output is unavailable")
    value = json.loads(text)
    require(isinstance(value, dict) and value.get("status") == "PASS",
            "Databricks validation report failed")
    return cast(dict[str, Any], value)


def run_validation(context: CloudContext) -> dict[str, Any]:
    from databricks.sdk.service.compute import Environment
    from databricks.sdk.service.jobs import (
        JobEnvironment,
        NotebookTask,
        RunLifeCycleState,
        Source,
        SubmitTask,
    )

    require(context.apply, "Runtime validation requires explicit apply context")
    _require_approval()
    plan = validation_job_plan()
    budget = _verify_budget(context)
    before = inspect_compute(context)
    require(before["cluster_count"] == 0 and before["job_count"] == 0,
            "Unexpected cluster or persistent job before validation")
    require(all(item["state"] == "STOPPED" for item in before["project_warehouses"]),
            "Project warehouse must remain stopped")
    remote = inspect_remote(context)
    require(
        remote["status"] == "PASS"
        and remote["matching_count"] == 46
        and remote["control_manifest_matching_count"] == 2,
        "Remote transfer and manifests must match before runtime validation",
    )
    require(remote["sealed_root_count"] == 0, "Transfer is already sealed")

    runtime_operation = _put_immutable(
        context.client.files, RUNTIME_COMPAT_DESTINATION, RUNTIME_COMPAT_SOURCE.read_bytes()
    )

    notebook_path, notebook_operation = _ensure_notebook(context.client)
    source_hash = hashlib.sha256(NOTEBOOK_SOURCE.read_bytes()).hexdigest()
    started = time.monotonic()
    finished = threading.Event()
    deadline_fired = threading.Event()
    wait = context.client.jobs.submit(
        run_name="retail-hp-phase3-immutable-transfer-validation",
        idempotency_token=f"retail-hp-phase3-{source_hash[:32]}",
        timeout_seconds=int(MAX_RUNTIME_MINUTES * 60),
        environments=[JobEnvironment(
            environment_key="default",
            spec=Environment(
                environment_version=ENVIRONMENT_VERSION,
                dependencies=list(DEPENDENCIES),
            ),
        )],
        tasks=[SubmitTask(
            task_key="validate_transfer",
            notebook_task=NotebookTask(notebook_path=notebook_path, source=Source.WORKSPACE),
            environment_key="default",
            timeout_seconds=int(MAX_RUNTIME_MINUTES * 60),
        )],
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
        validation = _parse_output(context.client, run)
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
    elapsed_estimate = (
        AUTOMATED_SERVERLESS_INR_PER_DBU_HOUR
        * PLANNING_MAX_DBU_PER_HOUR
        * elapsed
        / 3600
    )
    result = {
        **validation,
        "scope_verified": True,
        "notebook_operation": notebook_operation,
        "runtime_compatibility_operation": runtime_operation,
        "notebook_sha256": source_hash,
        "workspace_path_recorded": False,
        "persistent_job_created": False,
        "schedule_created": False,
        "serverless_run_terminated": True,
        "deadline_fired": deadline_fired.is_set(),
        "elapsed_seconds": round(elapsed, 2),
        "estimated_elapsed_cost_inr_pre_tax": round(elapsed_estimate, 4),
        "planning_guarded_ceiling_inr": plan["guarded_estimate_inr_pre_tax"],
        "hard_invoice_cap_guaranteed": False,
        "budget_admission": budget,
        "run_identifier_recorded": False,
    }
    return result


def complete_phase3(context: CloudContext) -> dict[str, Any]:
    validation = run_validation(context)
    record_evidence("databricks_validation.json", validation)
    seal = seal_transfer(context, validation)
    record_evidence("transfer_seal.json", seal)
    after = inspect_compute(context)
    require(after["cluster_count"] == 0 and after["job_count"] == 0,
            "Validation cleanup left cluster or persistent job")
    require(all(item["state"] == "STOPPED" for item in after["project_warehouses"]),
            "Project warehouse state changed during validation")
    return {
        "version": "azure_phase3_completion_v1",
        "status": "PASS",
        "transfer": "HASH_IDENTICAL",
        "databricks_validation": "PASS",
        "sealed_root_count": seal["sealed_root_count"],
        "persistent_job_count": after["job_count"],
        "cluster_count": after["cluster_count"],
        "project_warehouse_state": after["project_warehouses"][0]["state"],
        "new_azure_resources": 0,
        "production_approved": False,
        "estimated_validation_cost_inr_pre_tax": validation[
            "estimated_elapsed_cost_inr_pre_tax"
        ],
        "hard_invoice_cap_guaranteed": False,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run-and-seal"])
    args = parser.parse_args()
    try:
        result = validation_job_plan() if args.command == "plan" else complete_phase3(
            CloudContext(apply=True)
        )
        if args.command == "run-and-seal":
            record_evidence("completion.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(f"Phase 3 runtime failed: {type(exc).__name__}; {detail}") from None
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
