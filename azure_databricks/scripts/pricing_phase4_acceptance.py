"""Bounded non-admin HTTP validation; no compute start, no public chat records."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]


def validate(context):
    state = json.loads((ROOT / "build/phase10-live.local.json").read_text())
    require(time.time() < state["deadline"] - 300, "Insufficient existing validation window")
    principal = context.client.service_principals.get(state["tester_id"])
    workload, secret, principal_id = _workload_client(context, principal)
    report = {"status": "FAIL", "checks": {}, "browser_acceptance": "USER_MANUAL_PENDING"}
    sessions = []
    headers = {**workload.config.authenticate(), "x-retail-request": "workbench-v1"}

    def post(session, path, body):
        require(time.time() < state["deadline"] - 100, "Validation deadline")
        response = session.post(
            state["url"].rstrip("/") + path, json=body, timeout=90, allow_redirects=False
        )
        require(response.status_code == 200, f"App HTTP {response.status_code}")
        return response.json()

    def new_session():
        session = requests.Session()
        sessions.append(session)
        session.headers.update(headers)
        result = post(session, "/api/session", {})
        require(result["customers"] == ["CUS000001"], "Non-admin entitlement drift")
        session.headers["x-csrf-token"] = result["csrf"]
        return session

    def chat(session, text):
        started = time.monotonic()
        result = post(session, "/api/chat", {"text": text})
        return result, round(time.monotonic() - started, 3)

    try:
        session = new_session()
        cases = [
            ("pricing", "Recommend a price for PRO002461 at STO000037 through Store.", "ok"),
            ("explain", "Explain that pricing suggestion.", "ok"),
            ("simulation", "What if the price for the same product is 55.71 source units?", "ok"),
            ("unauthorized_customer", "Recommend and price products for CUS000002.", "refused"),
            ("retail", "Recommend five products for CUS000001.", "ok"),
            ("combined", "Recommend products for CUS000001 and suggest their prices.", "ok"),
            ("discovery", "Show available pricing scenarios.", "ok"),
            ("selected_scenario", "Use the first pricing scenario.", "ok"),
        ]
        for label, prompt, expected in cases:
            reply, elapsed = chat(session, prompt)
            (ROOT / f"build/pricing-phase4-{label}.local.json").write_text(json.dumps(reply))
            report["checks"][label] = {
                "status": reply["status"],
                "seconds": elapsed,
                "passed": reply["status"] == expected,
            }
            if label == "pricing":
                # Exact accepted-model result for the pinned historical scenario.
                report["checks"][label]["numeric_parity"] = "61.28" in json.dumps(reply["sections"])
                report["checks"][label]["passed"] &= report["checks"][label]["numeric_parity"]
            if label == "retail":
                report["checks"][label]["passed"] &= len(reply["cards"]) == 5
        parallel_sessions = [new_session() for _ in range(5)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(
                pool.map(
                    lambda s: chat(
                        s, "Recommend a price for PRO002461 at STO000037 through Store."
                    ),
                    parallel_sessions,
                )
            )
        times = sorted(elapsed for _, elapsed in results)
        report["five_concurrent_sessions"] = {
            "successful": sum(r["status"] == "ok" for r, _ in results),
            "seconds": times,
            "p95_seconds": times[-1],
            "target_seconds": 30,
        }
        require(all(c["passed"] for c in report["checks"].values()), "Business acceptance failed")
        require(all(r["status"] == "ok" for r, _ in results), "Concurrency acceptance failed")
        require(times[-1] <= 30, "End-to-end latency target not met")
        report["status"] = "PASS_AUTOMATED_HTTP"
    finally:
        for session in sessions:
            session.close()
        context.client.service_principal_secrets_proxy.delete(principal_id, secret)
        report["temporary_secret_revoked"] = True
        (ROOT / "build/pricing-phase4-http.local.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(validate(CloudContext(apply=True, direct_operator_token=True)), indent=2))
