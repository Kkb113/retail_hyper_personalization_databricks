"""Read-only Phase 8 metadata and final stopped-state audit; no SQL execution."""

import json
from datetime import UTC, datetime

from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase4 import inspect_lakehouse
from retail_hp_azure.phase5 import inspect_registered_model
from retail_hp_azure.phase8_backend import CUSTOMER_VIEW, OPPORTUNITIES, PRODUCT_VIEW, QUALITY_VIEW
from retail_hp_azure.phase8_semantic import TABLE

context = CloudContext()
client = context.client
objects = []
for name in [TABLE, CUSTOMER_VIEW, OPPORTUNITIES, PRODUCT_VIEW, QUALITY_VIEW]:
    item = client.tables.get(name)
    objects.append({"name": name, "owner": item.owner, "type": item.table_type.value})
volume = client.volumes.read("intellify_databricks_demo.serving.semantic_assets")
jobs = []
for job in client.jobs.list():
    if job.settings.name in {
        "retail-hp-poc-semantic-build",
        "retail-hp-poc-feedback-export",
        "retail-hp-poc-batch-recommendations",
    }:
        settings = client.jobs.get(job.job_id).settings
        jobs.append(
            {
                "name": settings.name,
                "timeout_seconds": settings.timeout_seconds,
                "manual_only": settings.schedule is None
                and settings.trigger is None
                and settings.continuous is None,
                "disable_auto_optimization": all(
                    t.disable_auto_optimization is True for t in settings.tasks
                ),
                "max_retries_zero": all((t.max_retries or 0) == 0 for t in settings.tasks),
                "active_runs": len(
                    list(client.jobs.list_runs(job_id=job.job_id, active_only=True))
                ),
            }
        )
inventory = inspect_compute(context)
endpoint = client.api_client.do("GET", "/api/2.0/serving-endpoints/retail-hp-poc-recommender")
lakehouse = inspect_lakehouse(context)
registry = inspect_registered_model(context)
passed = (
    all(item["owner"] == "retail_hp_admins" for item in objects)
    and volume.owner == "retail_hp_admins"
    and len(jobs) == 3
    and all(
        job["manual_only"]
        and job["disable_auto_optimization"]
        and job["max_retries_zero"]
        and job["active_runs"] == 0
        for job in jobs
    )
    and inventory["cluster_count"] == 0
    and inventory["app_count"] == 0
    and all(w["state"] == "STOPPED" for w in inventory["project_warehouses"])
    and endpoint["state"].get("suspend") == "STOPPED"
    and lakehouse["status"] == "PASS"
    and registry["aliases"].get("champion") == 3
)
report = {
    "status": "PASS" if passed else "INCOMPLETE",
    "captured_at": datetime.now(UTC).isoformat(),
    "objects": objects,
    "semantic_volume_owner": volume.owner,
    "jobs": jobs,
    "inventory": inventory,
    "recommender_state": endpoint["state"].get("suspend"),
    "lakehouse": lakehouse,
    "registry": registry,
    "compute_started": False,
}
path = _find_repo_root() / "azure_databricks/evidence/phase_08/cloud_reconciliation.json"
path.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
