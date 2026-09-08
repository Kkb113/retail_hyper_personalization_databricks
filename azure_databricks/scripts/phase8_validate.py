"""Bounded non-admin Phase 8 tool acceptance. Always stops the existing warehouse."""

import json
import os
import threading
import time
from datetime import timedelta
from uuid import uuid4

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import WAREHOUSE_NAME, _stop_and_verify, _verify_budget
from retail_hp_azure.phase2_identity import IDENTITY_NAME
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase8_backend import DatabricksToolBackend, EmbeddingClient
from retail_hp_azure.phase8_semantic import VOLUME, SemanticIndex
from retail_hp_azure.safety import SafetyError, require

ROOT = _find_repo_root()
EVIDENCE = ROOT / "azure_databricks/evidence/phase_08"


def validate():
    require(os.environ.get("RETAIL_HP_PHASE8_CEILING_INR") == "250", "Execution gate required")
    context = CloudContext(apply=True)
    client = context.client
    require(_verify_budget(context)["current_spend_inr"] < 9000, "Monthly target reached")
    generation = sorted(EVIDENCE.glob("generation_*.json"), key=lambda p: p.stat().st_mtime)[-1]
    built = json.loads(generation.read_text())
    require(built["status"] == "PASS", "Generation did not pass")
    attempts = [json.loads(p.read_text()) for p in EVIDENCE.glob("generation_*.json")]
    prior = sum(p.get("estimated_job_cost_inr_pre_tax", 0) for p in attempts)
    prior += (
        sum(
            p.get("status") != "PASS"
            and p.get("plan", {}).get("generation_job_timeout_seconds") == 180
            for p in attempts
        )
        * 0.5
        * 1.857
        * 44.91
    )
    prior += json.loads((EVIDENCE / "operator_embeddings.json").read_text())[
        "estimated_embedding_cost_inr_pre_tax"
    ]
    prior += sum(
        json.loads(p.read_text()).get("estimated_warehouse_cost_inr_pre_tax", 0)
        for p in EVIDENCE.glob("tool_validation_*.json")
    )
    require(prior + 2 * 3 * 4.4588 + 1 < 250, "Validation exceeds remaining phase allowance")
    details = built["generation"]
    version = details["snapshot_version"]
    require(
        len(version) == 64 and all(c in "0123456789abcdef" for c in version), "Invalid snapshot"
    )
    principal = [p for p in client.service_principals.list() if p.display_name == IDENTITY_NAME]
    require(len(principal) == 1 and principal[0].active, "Workload identity unavailable")
    warehouses = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(warehouses) == 1 and warehouses[0].state.value == "STOPPED", "Warehouse drift")
    warehouse_id = warehouses[0].id
    workload, secret_id, principal_id = _workload_client(context, principal[0])
    report = {"status": "FAIL", "non_admin_identity": True, "new_azure_resources": 0}
    started = None
    finished = threading.Event()

    def deadline_stop():
        if not finished.wait(3 * 60):
            client.warehouses.stop(warehouse_id)

    try:
        response = workload.files.download(f"{VOLUME}/{version}.json")
        with response.contents as stream:
            payload = stream.read(80_000_001)
        require(len(payload) <= 80_000_000, "Snapshot exceeds bound")
        index = SemanticIndex(json.loads(payload))
        embeddings = EmbeddingClient(workload, max_calls=8)
        backend = DatabricksToolBackend(workload, warehouse_id, index=index, embeddings=embeddings)
        traces = []
        tools = GovernedTools(backend, actor_secret=os.urandom(32), trace_sink=traces.append)
        actor = ToolContext(
            subject="phase8-synthetic-validation",
            allowed_customers={"CUS000001"},
            can_view_quality=True,
        )

        def call(name, args, confirm=False):
            return tools.execute(
                name,
                args,
                context=actor,
                request_id=f"phase8-{uuid4().hex}",
                write_confirmed=confirm,
            )

        # These checks must refuse access before paid compute is started.
        try:
            call("get_customer_360", {"customer_id": "CUS000002"})
            raise AssertionError("Unauthorized lookup succeeded")
        except SafetyError as exc:
            require("CUSTOMER_ACCESS_DENIED" in str(exc), "Wrong denial reason")
        try:
            call("simulate_scenario", {"customer_id": "CUS000001", "scenario_id": "phase8-test"})
            raise AssertionError("Stopped endpoint was invoked")
        except SafetyError as exc:
            require(
                "RECOMMENDER_NOT_EXPLICITLY_RUNNING" in str(exc), "Wrong stopped-endpoint error"
            )
        started = time.monotonic()
        threading.Thread(target=deadline_stop, daemon=True).start()
        client.warehouses.start(warehouse_id).result(timeout=timedelta(minutes=3))
        customer = call("get_customer_360", {"customer_id": "CUS000001"})
        require(len(customer.rows) == 1, "Customer tool failed")
        recommendations = call("get_recommendations", {"customer_id": "CUS000001"})
        require(len(recommendations.rows) == 10, "Batch recommendations incomplete")
        pid = recommendations.rows[0]["product_id"]
        require(call("get_product_details", {"product_id": pid}).rows, "Product lookup failed")
        require(
            call(
                "compare_products",
                {"product_ids": [r["product_id"] for r in recommendations.rows[:2]]},
            ).rows,
            "Comparison failed",
        )
        require(
            call("explain_recommendation", {"customer_id": "CUS000001", "product_id": pid}).rows,
            "Explanation failed",
        )
        quality = call("get_quality_summary", {})
        require(quality.rows[0]["batch_customers"] == 100, "Quality coverage drift")
        call("get_opportunities", {"customer_id": "CUS000001"})
        semantic_checks = []
        for query, terms in [
            ("warm jacket for cold winter weather", ("jacket", "coat")),
            ("running shoes for jogging", ("running", "sneaker")),
            ("headphones for listening to music", ("headphone", "earbud")),
        ]:
            result = call("search_products", {"query": query, "top_n": 5})
            relevant = any(
                any(term in r["product_name"].lower() for term in terms) for r in result.rows
            )
            require(relevant, "Semantic relevance acceptance failed")
            semantic_checks.append({"query": query, "relevant_in_top_5": relevant})
        filtered = call("search_products", {"query": "jacket", "max_price": 50, "top_n": 5})
        require(all(row["base_price"] <= 50 for row in filtered.rows), "Price filter failed")
        feedback = {
            "customer_id": "CUS000001",
            "idempotency_key": f"phase8-{uuid4().hex}",
            "feedback": {
                "sentiment": "positive",
                "reason_code": "relevant",
                "product_id": pid,
                "recommendation_request_id": recommendations.request_id,
            },
        }
        first = call("record_feedback", feedback, True)
        second = call("record_feedback", feedback, True)
        require(
            not first.rows[0]["replayed"] and second.rows[0]["replayed"], "Feedback replay failed"
        )
        report.update(
            status="PASS",
            checks={
                "unauthorized_customer_denied": True,
                "stopped_endpoint_not_invoked": True,
                "product_details_compare_explain": True,
                "batch_recommendations": True,
                "quality_and_opportunities": True,
                "feedback_idempotent": True,
                "price_filter": True,
                "semantic": semantic_checks,
            },
            embedding_tokens=embeddings.tokens,
            trace_count=len(traces),
            index_products=len(index.vectors),
            source_version=index.version,
        )
    finally:
        finished.set()
        try:
            client.service_principal_secrets_proxy.delete(principal_id, secret_id)
            report["temporary_secret_revoked"] = all(
                item.id != secret_id
                for item in client.service_principal_secrets_proxy.list(principal_id)
            )
        finally:
            report["warehouse_final_state"] = _stop_and_verify(client, warehouse_id)
        elapsed = time.monotonic() - started if started is not None else 0
        report["warehouse_elapsed_seconds"] = round(elapsed, 2)
        report["estimated_warehouse_cost_inr_pre_tax"] = round(elapsed * 4.4588 / 60, 4)
        report["hard_invoice_cap_guaranteed"] = False
        (EVIDENCE / f"tool_validation_{uuid4().hex[:8]}.json").write_text(
            json.dumps(report, indent=2)
        )
    return report


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
