"""Stage native assets without compute; reconcile only within the approved live lease."""
# ruff: noqa: S608 -- fixed internal SQL names only; metric payload parameterized.

import argparse
import json
import time
from pathlib import Path

from databricks.sdk.service.dashboards import Dashboard
from databricks.sdk.service.workspace import ImportFormat
from phase10_live import STATE, automation
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_delta import _execute, _rows
from retail_hp_azure.phase10_runtime import RunningBackend
from retail_hp_azure.phase11 import (
    DASHBOARD_NAME,
    PREFIX,
    WAREHOUSE,
    dashboard_spec,
    incident_findings,
    monitoring_queries,
    opportunity_ddl,
    opportunity_refresh,
    opportunity_select,
)
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "azure_databricks/evidence/phase_11"
CONTROL = ROOT / "build/phase11-control.local.json"
PARENT = "/Shared/retail_hp_phase11"


def stage(context):
    require(context.apply, "Apply required")
    client = context.client
    client.workspace.mkdirs(PARENT)
    sql_path = PARENT + "/refresh_opportunities.sql"
    client.workspace.upload(
        sql_path, opportunity_refresh().encode(), format=ImportFormat.AUTO, overwrite=True
    )
    existing = [d for d in client.lakeview.list() if d.display_name == DASHBOARD_NAME]
    require(len(existing) <= 1, "Ambiguous dashboard")
    spec = json.dumps(dashboard_spec())
    if existing:
        item = client.lakeview.get(existing[0].dashboard_id)
        require(item.parent_path == PARENT, "Dashboard ownership scope mismatch")
        item.serialized_dashboard, item.warehouse_id = spec, WAREHOUSE
        dashboard = client.lakeview.update(item.dashboard_id, item)
    else:
        dashboard = client.lakeview.create(
            Dashboard(
                display_name=DASHBOARD_NAME,
                parent_path=PARENT,
                warehouse_id=WAREHOUSE,
                serialized_dashboard=spec,
            )
        )
    job_name = "retail-hp-poc-opportunity-refresh"
    jobs = list(client.jobs.list(name=job_name))
    require(len(jobs) <= 1, "Ambiguous opportunity job")
    settings = {
        "name": job_name,
        "max_concurrent_runs": 1,
        "timeout_seconds": 600,
        "tags": {"project": "retail-hp-poc", "phase": "11", "trigger": "manual-only"},
        "tasks": [
            {
                "task_key": "refresh_opportunities",
                "timeout_seconds": 480,
                "max_retries": 0,
                "sql_task": {
                    "warehouse_id": WAREHOUSE,
                    "file": {"path": sql_path, "source": "WORKSPACE"},
                },
            }
        ],
    }
    if jobs:
        job_id = jobs[0].job_id
        require(client.jobs.get(job_id).settings.tags.get("phase") == "11", "Job scope drift")
        client.api_client.do(
            "POST", "/api/2.1/jobs/reset", body={"job_id": job_id, "new_settings": settings}
        )
    else:
        job_id = client.api_client.do("POST", "/api/2.1/jobs/create", body=settings)["job_id"]
    state = {
        "dashboard_id": dashboard.dashboard_id,
        "job_id": job_id,
        "warehouse_id": WAREHOUSE,
        "compute_started": False,
        "schedules_created": 0,
        "status": "STAGED_NOT_PUBLISHED",
    }
    CONTROL.write_text(json.dumps(state))
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "stage.json").write_text(json.dumps(state, indent=2))
    return state


def evaluation_rows():
    source = ROOT / "azure_databricks/evidence/phase_09/full_gpt-5.6-luna_20260908T081333.json"
    report = json.loads(source.read_text())
    require(report["status"] == "PASS" and report["endpoint"] == "gpt-5.6-luna", "Evaluation drift")
    rows = [
        {
            "metric": key,
            "value": value,
            "scope": "Historical Phase 9 fixtures; not Phase 10 chat",
            "source": source.name,
            "observed_at": "2026-09-08T08:13:33Z",
        }
        for key, value in report["summary"]["metrics"].items()
    ]
    for metric in ["ndcg", "recall", "hit_rate", "purchase_hit_rate"]:
        rows.append(
            {
                "metric": metric,
                "value": None,
                "scope": "Unavailable: no qualified future holdout for current release",
                "source": "Not measured",
                "observed_at": None,
            }
        )
    for filename, field, metric in [
        ("cold_resume.json", "elapsed_seconds", "historical_endpoint_resume_seconds"),
        ("live_inference.json", "warm_p95_seconds", "historical_endpoint_warm_p95_seconds"),
    ]:
        measured = json.loads((ROOT / "azure_databricks/evidence/phase_06" / filename).read_text())
        require(measured["status"] == "PASS", "Serving measurement not accepted")
        rows.append(
            {
                "metric": metric,
                "value": measured[field],
                "scope": "Historical Phase 6 test; not current App or idle timer latency",
                "source": filename,
                "observed_at": None,
            }
        )
    return rows


def apply(context):
    require(context.apply, "Apply required")
    lease = json.loads(STATE.read_text())
    control = json.loads(CONTROL.read_text())
    require(time.time() < lease["deadline"] - 240, "Insufficient approved window")
    require(
        automation(context, "GET", "/jobs/" + lease["stop_job"])["properties"]["status"]
        == "Running",
        "Independent stop required",
    )
    backend = RunningBackend(context.client, WAREHOUSE)
    backend.lease_expires = lease["deadline"]

    def sql(statement, parameters=None):
        require(time.time() < lease["deadline"] - 90, "Validation deadline")
        backend.check_running()
        return _rows(
            _execute(
                context.client, WAREHOUSE, statement, parameters=parameters, deadline_seconds=30
            )
        )

    sql(opportunity_ddl())
    sql(
        f"CREATE TABLE IF NOT EXISTS {PREFIX}.phase11_requests "
        "(request_hash STRING, model_version STRING, status STRING, elapsed_ms DOUBLE, "
        "input_tokens BIGINT, output_tokens BIGINT, cost_estimate_inr DOUBLE, "
        "observed_at TIMESTAMP) USING DELTA"
    )
    sql(f"ALTER TABLE {PREFIX}.phase11_requests OWNER TO `retail_hp_admins`")
    sql(f"ALTER TABLE {PREFIX}.phase11_opportunities OWNER TO `retail_hp_admins`")
    sql(opportunity_refresh())
    first = sql(
        f"SELECT count(*) AS n, count(DISTINCT opportunity_id) AS distinct_n "
        f"FROM {PREFIX}.phase11_opportunities"
    )[0]
    sql(opportunity_refresh())
    second = sql(
        f"SELECT count(*) AS n, count(DISTINCT opportunity_id) AS distinct_n "
        f"FROM {PREFIX}.phase11_opportunities"
    )[0]
    require(first == second and first["n"] == first["distinct_n"], "Opportunity idempotency failed")
    comparison = sql(f"""WITH expected AS ({opportunity_select()})
        SELECT count(*) AS mismatches FROM (
        (SELECT opportunity_id FROM expected EXCEPT SELECT opportunity_id
          FROM {PREFIX}.phase11_opportunities)
        UNION ALL
        (SELECT opportunity_id FROM {PREFIX}.phase11_opportunities EXCEPT
          SELECT opportunity_id FROM expected))""")[0]
    require(int(comparison["mismatches"]) == 0, "Opportunity source reconciliation failed")
    sql(
        f"CREATE TABLE IF NOT EXISTS {PREFIX}.phase11_metrics "
        "(metric STRING, value DOUBLE, scope STRING, source STRING, observed_at STRING) USING DELTA"
    )
    from databricks.sdk.service.sql import StatementParameterListItem

    sql(
        f"INSERT OVERWRITE {PREFIX}.phase11_metrics SELECT m.* FROM "
        "(SELECT explode(from_json(:payload,'ARRAY<STRUCT<metric:STRING,value:DOUBLE,"
        "scope:STRING,source:STRING,observed_at:STRING>>')) AS m)",
        [
            StatementParameterListItem(
                name="payload", value=json.dumps(evaluation_rows()), type="STRING"
            )
        ],
    )
    sql(f"ALTER TABLE {PREFIX}.phase11_metrics OWNER TO `retail_hp_admins`")
    figures = {}
    for name, query in monitoring_queries().items():
        view = f"{PREFIX}.phase11_{name}_summary"
        sql(f"CREATE OR REPLACE VIEW {view} AS {query}")
        sql(f"ALTER VIEW {view} OWNER TO `retail_hp_admins`")
        figures[name] = sql(f"SELECT * FROM {view}")
    require(
        sum(int(r["recommendation_rows"]) for r in figures["routes"])
        == int(figures["coverage"][0]["published_rows"]),
        "Route reconciliation failed",
    )
    require(
        sum(int(r["recommendation_rows"]) for r in figures["candidate_sources"])
        == int(figures["coverage"][0]["published_rows"]),
        "Source reconciliation failed",
    )
    findings = incident_findings(figures["quality"][0], figures["coverage"][0])
    require(not findings, "Operational quality gate failed")
    context.client.lakeview.publish(
        control["dashboard_id"], embed_credentials=False, warehouse_id=WAREHOUSE
    )
    published = context.client.lakeview.get_published(control["dashboard_id"])
    require(published.embed_credentials is False, "Dashboard must use viewer permissions")
    result = {
        "status": "PASS_SQL_RECONCILIATION",
        "figures": figures,
        "opportunity_rows": first["n"],
        "opportunity_idempotent": True,
        "source_reconciliation": comparison,
        "findings": findings,
        "dashboard_id": control["dashboard_id"],
        "embed_credentials": False,
        "new_compute": 0,
        "schedules_created": 0,
        "browser_render_verified": False,
    }
    (EVIDENCE / "live_validation.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["stage", "apply"])
    args = parser.parse_args()
    ctx = CloudContext(apply=True, direct_operator_token=True)
    print(json.dumps(stage(ctx) if args.command == "stage" else apply(ctx), indent=2))
