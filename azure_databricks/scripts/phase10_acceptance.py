"""Exercise deployed App APIs with existing non-admin OAuth; never starts compute."""

import json
import time
from pathlib import Path

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def validate(context):
    state = json.loads((ROOT / "build/phase10-live.local.json").read_text())
    require(time.time() < state["deadline"] - 90, "Not enough safe test time")
    principal = context.client.service_principals.get(state["tester_id"])
    workload, secret, principal_id = _workload_client(context, principal)
    report = {"status": "FAIL", "checks": {}, "compute_started": False}
    session = requests.Session()
    session.headers.update(workload.config.authenticate())
    session.headers["x-retail-request"] = "workbench-v1"

    def post(path, body):
        require(time.time() < state["deadline"] - 45, "Acceptance deadline reached")
        response = session.post(
            state["url"].rstrip("/") + path, json=body, timeout=75, allow_redirects=False
        )
        require(response.status_code == 200, f"{path} HTTP {response.status_code}")
        return response.json()

    try:
        result = post("/api/session", {})
        require(result["customers"] == ["CUS000001"], "Non-admin entitlement mismatch")
        session.headers["x-csrf-token"] = result["csrf"]
        report["checks"]["authenticated_non_admin_session"] = True
        reply = post(
            "/api/chat", {"text": "Recommend 3 products for me", "customer_id": "CUS000001"}
        )
        require(reply["status"] == "ok" and len(reply["cards"]) > 0, "Live agent unavailable")
        report["checks"]["luna_app_identity_chat"] = True
        report["checks"]["recommendation_cards"] = len(reply["cards"])
        denied = session.post(
            state["url"].rstrip("/") + "/api/chat",
            json={"text": "Recommend products", "customer_id": "CUS000002"},
            timeout=30,
            allow_redirects=False,
        )
        require(denied.status_code == 403, "Cross-customer request not denied")
        report["checks"]["cross_customer_denied"] = True
        semantic = post(
            "/api/action",
            {"action": "search_products", "arguments": {"query": "comfortable shoes", "top_n": 3}},
        )
        require(semantic["rows"], "No semantic results")
        report["checks"]["semantic_discovery"] = True
        batch = post(
            "/api/action",
            {
                "action": "get_recommendations",
                "arguments": {"customer_id": "CUS000001", "top_n": 3},
            },
        )
        feedback = {
            "product_id": batch["rows"][0]["product_id"],
            "sentiment": "positive",
            "reason_code": "relevant",
            "confirmed": True,
        }
        saved = post("/api/feedback", feedback)
        replay = post("/api/feedback", feedback)
        require(
            saved["rows"][0]["replayed"] is False
            and replay["rows"][0]["replayed"] is True
            and saved["rows"][0]["event_id"] == replay["rows"][0]["event_id"],
            "Feedback idempotent replay failed",
        )
        report["checks"]["feedback_write_and_replay"] = True
        scenario = post(
            "/api/action",
            {
                "action": "simulate_scenario",
                "arguments": {
                    "customer_id": "CUS000001",
                    "scenario_id": "app-live-test",
                    "top_n": 3,
                    "price_sensitivity": "High",
                },
            },
        )
        require(scenario["rows"], "Scenario returned no products")
        report["checks"]["realtime_scenario"] = True
        report["status"] = "PASS_API_ONLY"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["safe_failure"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        raise
    finally:
        session.close()
        context.client.service_principal_secrets_proxy.delete(principal_id, secret)
        report["temporary_oauth_secret_revoked"] = True
        (ROOT / "azure_databricks/evidence/phase_10/live_api_validation.json").write_text(
            json.dumps(report, indent=2)
        )
    return report


if __name__ == "__main__":
    print(json.dumps(validate(CloudContext(apply=True, direct_operator_token=True)), indent=2))
