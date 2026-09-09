"""Live caller-identity runtime checks, not a substitute for browser sign-in QA."""

import json
import secrets
import time
from pathlib import Path

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.phase8 import Customer, ToolContext
from retail_hp_azure.phase10_runtime import RunningBackend, WorkbenchRuntime
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def validate(context):
    state = json.loads((ROOT / "build/phase10-live.local.json").read_text())
    require(time.time() < state["deadline"] - 180, "Existing window required")
    principal = context.client.service_principals.get(state["tester_id"])
    workload, secret, principal_id = _workload_client(context, principal)
    report = {
        "status": "FAIL",
        "release": state["release"],
        "inference_calls": 0,
        "browser_sign_in_tested": False,
        "customers": [],
    }
    try:
        subject = state["operator_id"]
        runtime = WorkbenchRuntime(
            app_client=workload,
            warehouse_id=state["warehouse_id"],
            entitlements={subject: ToolContext(subject=subject)},
            actor_secret=secrets.token_bytes(32),
            ledger=ROOT / "build/cohort-check.local.json",
            index=None,
            lease_expires=state["deadline"],
            trace_sink=lambda event: None,
            user_client_factory=lambda token: context.client,
            cohort_subjects=frozenset({subject}),
        )
        caller = runtime.authenticate("injected-existing-operator-client")
        migration = json.loads(
            (
                ROOT / "azure_databricks/evidence/phase_10/customer_context_migration.json"
            ).read_text()
        )
        require(
            len(caller.allowed_customers) == migration["ready_customers"] > 2,
            "Published active cohort drift",
        )
        report["authorized_published_customers"] = len(caller.allowed_customers)
        backend = RunningBackend(context.client, state["warehouse_id"])
        backend.lease_expires = state["deadline"]
        for customer_id in ["CUS003101", "CUS000151"]:
            require(customer_id in caller.allowed_customers, "Demo customer not authorized")
            rows, _ = backend.read("get_customer_360", {"customer_id": customer_id}, "cohort-qa")
            customer = Customer.model_validate(rows[0])
            require(customer.evidence_version == "customer_context_v2", "Old customer view")
            require(customer.recent_purchases, "Expected purchase evidence missing")
            rows, _ = backend.read(
                "get_recommendations", {"customer_id": customer_id, "top_n": 10}, "cohort-qa"
            )
            require(
                len(rows) == len({row["product_id"] for row in rows}) == 10,
                "Incomplete distinct recommendations",
            )
            report["customers"].append(
                {
                    "customer_id": customer_id,
                    "purchase_entries": customer.purchase_count,
                    "recent_purchase_products": len(customer.recent_purchases),
                    "distinct_recommendations": len(rows),
                }
            )
        report["status"] = "PASS_OPERATOR_RUNTIME"
    finally:
        context.client.service_principal_secrets_proxy.delete(principal_id, secret)
        report["temporary_oauth_secret_revoked"] = True
        (ROOT / "azure_databricks/evidence/phase_10/cohort_validation.json").write_text(
            json.dumps(report, indent=2)
        )
    return report


if __name__ == "__main__":
    print(json.dumps(validate(CloudContext(apply=True, direct_operator_token=True)), indent=2))
