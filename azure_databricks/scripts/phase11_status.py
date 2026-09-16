"""Read-only project health and delayed cost report; never starts or stops resources."""

import json
from datetime import UTC, datetime
from pathlib import Path

from phase10_live import STATE, automation, inspect
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget

ROOT = Path(__file__).resolve().parents[2]


def report(context):
    result = {
        "observed_at": datetime.now(UTC).isoformat(),
        "cloud_mutations": 0,
        "state": inspect(context),
        "billing_is_delayed": True,
        "invoice_cap": False,
        "managed_resource_group_cost_coverage": "Not verified",
    }
    try:
        budget = _verify_budget(context)
        result["reported_rg_month_spend_inr"] = budget["current_spend_inr"]
    except Exception as exc:
        result["reported_rg_month_spend_inr"] = None
        result["cost_read_status"] = type(exc).__name__
    state = json.loads(STATE.read_text())
    deployment = result["state"].get("deployment") or {}
    result["state"]["deployment"] = {
        key: deployment[key]
        for key in ("deployment_id", "status", "create_time", "update_time")
        if key in deployment
    }
    result["shutdown_job_status"] = automation(context, "GET", "/jobs/" + state["stop_job"])[
        "properties"
    ]["status"]
    result["jobs"] = []
    for job in context.client.jobs.list():
        if job.settings and job.settings.name and job.settings.name.startswith("retail-hp-"):
            detailed = context.client.jobs.get(job.job_id).settings
            result["jobs"].append(
                {
                    "name": detailed.name,
                    "schedule_present": bool(detailed.schedule),
                    "continuous_present": bool(getattr(detailed, "continuous", None)),
                    "trigger_present": bool(getattr(detailed, "trigger", None)),
                    "timeout_seconds": detailed.timeout_seconds,
                }
            )
    warehouse = context.client.warehouses.get(state["warehouse_id"])
    result["warehouse_auto_stop_minutes"] = warehouse.auto_stop_mins
    result["phase11_reserved_allowance_inr"] = json.loads(
        (ROOT / "build/phase11-validation.local.json").read_text()
    )["reserved_inr"]
    result["allowance_is_not_measured_spend"] = True
    submitted = json.loads(
        (ROOT / "azure_databricks/evidence/phase_11/job_submission.json").read_text()
    )
    run = context.client.jobs.get_run(submitted["run_id"])
    if run.job_id != submitted["job_id"]:
        raise ValueError("Job run scope mismatch")
    result["opportunity_job_validation"] = {
        "run_id": run.run_id,
        "state": run.state.as_dict(),
        "run_page_url": run.run_page_url,
    }
    (ROOT / "azure_databricks/evidence/phase_11/operations_status.json").write_text(
        json.dumps(result, indent=2)
    )
    return result


if __name__ == "__main__":
    print(json.dumps(report(CloudContext(apply=False, direct_operator_token=True)), indent=2))
