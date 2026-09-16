"""Bounded operator HTTP acceptance; uses existing identity, never widens grants."""

import json
import time
from pathlib import Path

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def validate(context):
    state = json.loads((ROOT / "build/phase10-live.local.json").read_text())
    require(time.time() < state["deadline"] - 240, "Insufficient existing window")
    report = {"status": "FAIL", "release": state["release"], "checks": {}}
    session = requests.Session()
    session.headers.update(context.client.config.authenticate())
    session.headers["x-retail-request"] = "workbench-v1"

    def post(path, body):
        require(time.time() < state["deadline"] - 90, "Acceptance deadline")
        response = session.post(
            state["url"].rstrip("/") + path, json=body, timeout=100, allow_redirects=False
        )
        report["last_http_status"] = response.status_code
        require(response.status_code == 200, f"Operator HTTP {response.status_code}")
        return response.json()

    try:
        identity = context.client.current_user.me()
        require(str(identity.id) == state["operator_id"], "Operator identity mismatch")
        result = post("/api/session", {})
        customers = result["customers"]
        require(len(customers) == len(set(customers)) > 2, "Published cohort not resolved")
        require("CUS003101" in customers and "CUS000151" in customers, "Demo customers missing")
        session.headers["x-csrf-token"] = result["csrf"]
        report["checks"]["authorized_published_customers"] = len(customers)
        for label, customer, count in [
            ("personalization", "CUS003101", 10),
            ("customer_switch", "CUS000151", 5),
        ]:
            reply = post(
                "/api/chat",
                {
                    "text": f"For customer {customer}, recommend {count} "
                    "products tailored to their shopping history and preferences. "
                    "Explain why each fits in business language and suggest a next step."
                },
            )
            (ROOT / f"build/phase10-operator-{label}.local.json").write_text(
                json.dumps(reply, indent=2)
            )
            require(reply["status"] == "ok", f"{label}: not ok")
            require(reply["response_mode"] == "llm", f"{label}: fallback")
            cards = reply["cards"]
            require(len(cards) == count, f"{label}: count mismatch")
            require(len({card["product_id"] for card in cards}) == count, "Duplicate products")
            require(all(card.get("business_reason") for card in cards), "Missing business reasons")
            require(customer in reply["text"], "Customer context mismatch")
            report["checks"][label] = {
                "customer": customer,
                "distinct_products": count,
                "business_reasons": True,
                "mode": reply["response_mode"],
            }
        report["status"] = "PASS_OPERATOR_HTTP"
    finally:
        session.close()
        (ROOT / "azure_databricks/evidence/phase_10/operator_validation.json").write_text(
            json.dumps(report, indent=2)
        )
    return report


if __name__ == "__main__":
    print(json.dumps(validate(CloudContext(apply=False, direct_operator_token=True)), indent=2))
