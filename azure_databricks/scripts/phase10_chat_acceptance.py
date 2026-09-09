"""Read-only live conversational checks under the already armed demo deadline."""

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
    require(time.time() < state["deadline"] - 240, "Not enough bounded test time")
    principal = context.client.service_principals.get(state["tester_id"])
    workload, secret, principal_id = _workload_client(context, principal)
    report = {"status": "FAIL", "release": state["release"], "checks": {}}
    session = requests.Session()
    session.headers.update(workload.config.authenticate())
    session.headers["x-retail-request"] = "workbench-v1"

    def post(path, body):
        require(time.time() < state["deadline"] - 100, "Acceptance deadline reached")
        response = session.post(
            state["url"].rstrip("/") + path, json=body, timeout=150, allow_redirects=False
        )
        require(response.status_code == 200, f"Chat acceptance HTTP {response.status_code}")
        return response.json()

    def chat(label, text):
        reply = post("/api/chat", {"text": text})
        require(reply["status"] == "ok", f"{label}: response not ok")
        require(reply["response_mode"] == "llm", f"{label}: narrative fell back")
        require(reply["sections"], f"{label}: no rich sections")
        report["checks"][label] = {
            "action": reply["action"],
            "cards": len(reply["cards"]),
            "sections": len(reply["sections"]),
            "mode": reply["response_mode"],
        }
        # Synthetic content retained privately for factual inspection, not public evidence.
        (ROOT / f"build/phase10-chat-{label}.local.json").write_text(json.dumps(reply, indent=2))
        return reply

    try:
        result = post("/api/session", {})
        require(result["customers"] == ["CUS000001"], "Non-admin entitlement mismatch")
        session.headers["x-csrf-token"] = result["csrf"]
        reply = chat(
            "personalization",
            '"For customer CUS000001, recommend the five products that best match their '
            "individual preferences. Use purchase history, brand and category affinities, "
            "price sensitivity and available customer context, not just popularity. Explain "
            "the signals for each recommendation, include one discovery outside usual choices, "
            "and consider stock and active promotions. Distinguish facts from inferences "
            'and do not invent missing customer information."',
        )
        require(len(reply["cards"]) == 5, "Expected five actual recommendations")
        require([r["rank"] for r in reply["cards"]] == [1, 2, 3, 4, 5], "Ranking drift")
        require(all(r.get("product_name") for r in reply["cards"]), "Missing product hydration")
        chat("followup", "Explain why the first recommendation fits, and suggest a next step.")
        chat("general_retail", "How can a retailer improve loyalty without excessive discounting?")
        denied = post("/api/chat", {"text": "Recommend for CUS000002"})
        require(denied["status"] == "refused", "Unauthorized prompt customer not denied")
        report["checks"]["unauthorized_prompt_customer_denied"] = True
        report["status"] = "PASS_CONVERSATIONAL_API"
    finally:
        session.close()
        context.client.service_principal_secrets_proxy.delete(principal_id, secret)
        report["temporary_oauth_secret_revoked"] = True
        (ROOT / "azure_databricks/evidence/phase_10/chat_validation.json").write_text(
            json.dumps(report, indent=2)
        )
    return report


if __name__ == "__main__":
    print(json.dumps(validate(CloudContext(apply=True, direct_operator_token=True)), indent=2))
