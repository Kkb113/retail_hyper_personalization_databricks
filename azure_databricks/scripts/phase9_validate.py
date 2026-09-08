"""Non-admin live agent/tool smoke with a three-minute warehouse stop watchdog."""

import json
import os
import threading
import time
from datetime import timedelta
from uuid import uuid4

from phase8_control import stopped
from phase9_evaluate import EVIDENCE, luna_operator_planner
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import WAREHOUSE_NAME, _stop_and_verify, _verify_budget
from retail_hp_azure.phase2_identity import IDENTITY_NAME
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase8_backend import DatabricksToolBackend, EmbeddingClient
from retail_hp_azure.phase8_semantic import VOLUME, SemanticIndex
from retail_hp_azure.phase9 import RetailAgent, Session
from retail_hp_azure.safety import require


def validate():
    require(os.environ.get("RETAIL_HP_PHASE9_CEILING_INR") == "250", "Execution gate required")
    context = CloudContext(apply=True)
    client = context.client
    stopped(context)
    require(_verify_budget(context)["current_spend_inr"] < 9000, "Monthly target reached")
    prior = sum(
        json.loads(p.read_text()).get("estimated_warehouse_cost_inr_pre_tax", 0)
        for p in EVIDENCE.glob("live_*.json")
    )
    require(2 * (100 + prior + 3 * 4.4588) + 10 < 250, "Validation allowance exhausted")
    principals = [p for p in client.service_principals.list() if p.display_name == IDENTITY_NAME]
    require(len(principals) == 1 and principals[0].active, "Workload identity unavailable")
    warehouses = [w for w in client.warehouses.list() if w.name == WAREHOUSE_NAME]
    require(len(warehouses) == 1 and warehouses[0].state.value == "STOPPED", "Warehouse drift")
    warehouse_id = warehouses[0].id
    generation_dir = _find_repo_root() / "azure_databricks/evidence/phase_08"
    successful = [
        json.loads(p.read_text())
        for p in generation_dir.glob("generation_*.json")
        if json.loads(p.read_text())["status"] == "PASS"
    ]
    require(len(successful) == 1, "Ambiguous semantic snapshot")
    version = successful[0]["generation"]["snapshot_version"]
    workload, secret_id, principal_id = _workload_client(context, principals[0])
    report = {
        "status": "FAIL",
        "non_admin_identity": True,
        "non_admin_identity_scope": "Databricks governed tools only",
        "llm_identity": "azure_operator_validation_only",
        "llm_model": "gpt-5.6-luna",
        "deployed_workload_azure_identity_verified": False,
        "new_azure_resources": 0,
        "checks": [],
        "new_endpoints": 0,
    }
    started = None
    finished = threading.Event()

    def stop_on_deadline():
        if not finished.wait(180):
            client.warehouses.stop(warehouse_id)

    try:
        response = workload.files.download(f"{VOLUME}/{version}.json")
        with response.contents as stream:
            data = stream.read(80_000_001)
        require(len(data) <= 80_000_000, "Snapshot too large")
        embeddings = EmbeddingClient(workload, max_calls=2)
        backend = DatabricksToolBackend(
            workload, warehouse_id, index=SemanticIndex(json.loads(data)), embeddings=embeddings
        )
        traces = []
        planner = luna_operator_planner(context)
        agent = RetailAgent(
            planner,
            GovernedTools(backend, actor_secret=os.urandom(32), trace_sink=lambda _: None),
            traces.append,
        )
        actor = ToolContext(subject="phase9-live-synthetic", allowed_customers={"CUS000001"})
        session = Session(actor.subject, "CUS000001")

        def call(prompt):
            return agent.run(
                prompt, context=actor, session=session, request_id=f"phase9-{uuid4().hex}"
            )

        denied = call("Show CUS000002's recommendations")
        require(denied.status == "refused", "Unauthorized customer not denied")
        scenario = call("What if my price sensitivity were High?")
        require(
            scenario.status == "unavailable" and scenario.action == "simulate_scenario",
            "Stopped scenario service must fail closed",
        )
        report["checks"].extend(["unauthorized_customer_denied", "stopped_scenario_not_started"])
        started = time.monotonic()
        threading.Thread(target=stop_on_deadline, daemon=True).start()
        client.warehouses.start(warehouse_id).result(timeout=timedelta(minutes=3))
        recs = call("Recommend five products for me")
        require(recs.status == "ok" and len(recs.cards) == 5, "Live recommendations failed")
        require([r["rank"] for r in recs.cards] == [1, 2, 3, 4, 5], "Ranking changed")
        require(
            all(r["available_qty"] > 0 and r["base_price"] >= 0 for r in recs.cards),
            "Live product facts missing",
        )
        compare = call("Compare the first two")
        require(compare.status == "ok" and len(compare.cards) == 2, "Live follow-up failed")
        require(
            {r["product_id"] for r in compare.cards} == {r["product_id"] for r in recs.cards[:2]},
            "Comparison context changed",
        )
        search = call("Search for a warm jacket for winter")
        require(search.status == "ok" and search.cards, "Live semantic search failed")
        require(
            any(
                "jacket" in r["product_name"].lower() or "coat" in r["product_name"].lower()
                for r in search.cards
            ),
            "Semantic result relevance failed",
        )
        report["checks"].extend(
            ["live_batch_rank_and_facts", "live_multiturn_comparison", "live_semantic_search"]
        )
        report.update(status="PASS", traces=traces, embedding_tokens=embeddings.tokens)
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
        report["final_compute"] = stopped(context)
        (EVIDENCE / f"live_{uuid4().hex[:8]}.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
