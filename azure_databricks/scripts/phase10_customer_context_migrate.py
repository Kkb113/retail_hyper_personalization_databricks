"""Upgrade only the existing customer view, within an approved running window.

No compute starts, embedding rebuilds, new grants, or resources. Inspect the SQL
without Azure by omitting --apply. Retain rollback DDL locally before the change.
"""
# ruff: noqa: S608 -- only the fixed customer view is targeted.

import argparse
import json
import time
from pathlib import Path

from phase10_live import STATE, automation
from retail_hp_azure.customer_context import customer_view_sql
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_delta import _execute, _rows
from retail_hp_azure.phase8 import Customer
from retail_hp_azure.phase8_backend import CUSTOMER_VIEW, RECOMMENDATIONS, DatabricksToolBackend
from retail_hp_azure.safety import require


def migrate(context):
    state = json.loads(STATE.read_text())
    require(context.apply, "Apply required")
    require(time.time() < state["deadline"] - 240, "Insufficient approved window")
    require(
        automation(context, "GET", "/jobs/" + state["stop_job"])["properties"]["status"]
        == "Running",
        "Independent shutdown must be armed",
    )
    warehouse = context.client.warehouses.get(state["warehouse_id"])
    require(
        warehouse.name == "retail-hp-poc-sql" and warehouse.state.value == "RUNNING",
        "Existing approved warehouse must already be running",
    )

    def sql(statement):
        require(time.time() < state["deadline"] - 60, "Migration deadline")
        return _rows(
            _execute(context.client, state["warehouse_id"], statement, deadline_seconds=30)
        )

    # Do not grant wider data access: replace the same view, preserving its grants.
    previous = sql(f"SHOW CREATE TABLE {CUSTOMER_VIEW}")
    require(len(previous) == 1, "Previous view DDL unavailable")
    backup = Path("build") / f"customer-context-rollback-{int(time.time())}.local.json"
    backup.write_text(json.dumps(previous))
    require(
        context.client.tables.get(CUSTOMER_VIEW).owner == "retail_hp_admins",
        "Unexpected customer view owner",
    )
    sql(customer_view_sql())
    sql(f"ALTER VIEW {CUSTOMER_VIEW} OWNER TO `retail_hp_admins`")
    rows = sql(
        f"SELECT evidence_version, count(*) AS customers FROM {CUSTOMER_VIEW} "
        "GROUP BY evidence_version"
    )
    require(
        len(rows) == 1
        and rows[0]["evidence_version"] == "customer_context_v2"
        and int(rows[0]["customers"]) > 0,
        "Customer evidence migration validation failed",
    )
    backend = DatabricksToolBackend(context.client, state["warehouse_id"])
    ready = backend.ready_customers()
    expected = sql(
        f"SELECT count(DISTINCT r.customer_id) AS total FROM {RECOMMENDATIONS} r "
        f"JOIN {CUSTOMER_VIEW} c ON r.customer_id = c.customer_id "
        "WHERE r.registered_model_version = '3'"
    )
    require(0 < len(ready) == int(expected[0]["total"]), "Incomplete published customer coverage")
    samples = sorted({ready[0], ready[len(ready) // 2], ready[-1]})
    for customer in samples:
        require(time.time() < state["deadline"] - 180, "Readiness deadline")
        evidence, _ = backend.read("get_customer_360", {"customer_id": customer}, "readiness")
        require(len(evidence) == 1, "Missing customer evidence")
        validated = Customer.model_validate(evidence[0])
        require(validated.evidence_version == "customer_context_v2", "Evidence version drift")
    return {
        "status": "PASS_VIEW_UPGRADE",
        "rows": rows,
        "compute_started": False,
        "new_resources": 0,
        "new_grants": 0,
        "ready_customers": len(ready),
        "sample_customer_contracts_passed": samples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        result = migrate(CloudContext(apply=True, direct_operator_token=True))
        Path("azure_databricks/evidence/phase_10/customer_context_migration.json").write_text(
            json.dumps(result, indent=2)
        )
        print(json.dumps(result))
    else:
        print(customer_view_sql())
