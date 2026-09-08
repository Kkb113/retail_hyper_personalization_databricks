"""Read-only post-session resource and release reconciliation."""

import json
from datetime import UTC, datetime
from pathlib import Path

from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase5 import inspect_registered_model
from retail_hp_azure.phase6 import ENDPOINT_NAME

context = CloudContext()
client = context.client
root = Path(__file__).resolve().parents[2]
state = json.loads((root / "build/phase6-control.local.json").read_text())
inventory = inspect_compute(context)
endpoint = client.serving_endpoints.get(ENDPOINT_NAME)
raw_endpoint = client.api_client.do("GET", f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}")
job = client.jobs.get(state["job_id"])
runs = []
run_ids = list(
    dict.fromkeys([*state.get("run_history", []), state.get("prior_run_id"), state.get("run_id")])
)
for run_id in run_ids:
    if run_id:
        run = client.jobs.get_run(run_id)
        runs.append(
            {
                "result": run.state.result_state.value if run.state.result_state else None,
                "duration_seconds": ((run.end_time or run.start_time) - run.start_time) / 1000,
            }
        )
tables = []
for name in (
    "gold.customer_recommendation_history",
    "gold.customer_recommendation_current",
    "serving.customer_recommendations",
):
    table = client.tables.get("intellify_databricks_demo." + name)
    tables.append({"name": name, "owner": table.owner, "type": table.table_type.value})
report = {
    "captured_at": datetime.now(UTC).isoformat(),
    "inventory": inventory,
    "registry": inspect_registered_model(context),
    "budget": _verify_budget(context),
    "endpoint_state": raw_endpoint.get("state"),
    "endpoint_config": {
        "version": endpoint.config.served_entities[0].entity_version,
        "scale_to_zero": endpoint.config.served_entities[0].scale_to_zero_enabled,
    }
    if endpoint.config
    else None,
    "active_job_runs": len(list(client.jobs.list_runs(job_id=state["job_id"], active_only=True))),
    "batch_runs": runs,
    "job_schedule": job.settings.schedule,
    "job_timeout_seconds": job.settings.timeout_seconds,
    "tables": tables,
    "hard_invoice_cap_guaranteed": False,
}
report["shutdown_verified"] = (
    raw_endpoint["state"].get("suspend") == "STOPPED"
    and all(w["state"] == "STOPPED" for w in inventory["project_warehouses"])
    and report["active_job_runs"] == 0
    and inventory["cluster_count"] == 0
    and job.settings.schedule is None
)
(root / "azure_databricks/evidence/phase_06/cloud_reconciliation.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
