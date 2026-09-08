"""Summarize retained Phase 9 evidence without cloud calls or additional inference."""

import json
import math

from phase9_evaluate import EVIDENCE, LEDGER


def main():
    release = json.loads((EVIDENCE / "release_evaluation.json").read_text())
    primary = json.loads((EVIDENCE / release["source_report"]).read_text())
    secondary_paths = list(EVIDENCE.glob("screen_databricks-meta-llama*.json"))
    secondary = json.loads(sorted(secondary_paths)[-1].read_text())
    models = []
    for report in (primary, secondary):
        measured = [r["usage"] for r in report["rows"] if r.get("usage")]
        latencies = sorted(u["latency_ms"] for u in measured if "latency_ms" in u)
        cost = sum(u.get("cost_estimate_inr", u.get("reserved_inr", 0)) for u in measured)
        successes = sum(
            r["selection_pass"] and r["arguments_pass"] and r["behavior_pass"]
            for r in report["rows"]
        )
        models.append(
            {
                "endpoint": report["endpoint"],
                "mode": report["mode"],
                "sample_count": len(report["rows"]),
                "paid_attempts": len(measured),
                "selection_rate": report["summary"]["metrics"]["selection_pass"],
                "input_tokens": sum(u.get("input_tokens", 0) for u in measured),
                "output_tokens": sum(u.get("output_tokens", 0) for u in measured),
                "p50_provider_latency_ms": latencies[math.ceil(len(latencies) * 0.5) - 1],
                "p95_provider_latency_ms": latencies[math.ceil(len(latencies) * 0.95) - 1],
                "cost_estimate_inr": round(cost, 4),
                "cost_per_successful_case_inr": round(cost / max(1, successes), 4),
            }
        )
    ledger = json.loads(LEDGER.read_text())
    live = [json.loads(p.read_text()) for p in EVIDENCE.glob("live_*.json")]
    warehouse_cost = sum(r["estimated_warehouse_cost_inr_pre_tax"] for r in live)
    embedding_cost = sum(r.get("embedding_tokens", 0) for r in live) * 1.857 * 44.91 / 1e6
    estimate = ledger["reserved_or_spent_inr"] + warehouse_cost + embedding_cost
    result = {
        "selected_primary": "databricks-gpt-oss-20b",
        "fallback": None,
        "selection_reason": "Cheaper published rates; release passes planned gates",
        "comparison_caveat": "Diagnostic screens/full test differ in sample count and prompt; "
        "not a controlled comparative quality experiment",
        "models": models,
        "cumulative_llm_ledger": ledger,
        "warehouse_cost_estimate_inr_pre_tax": warehouse_cost,
        "embedding_cost_estimate_inr_pre_tax": embedding_cost,
        "incremental_phase_estimate_inr_pre_tax": round(estimate, 2),
        "estimate_with_100_percent_margin_and_1_inr_storage_reserve": round(2 * estimate + 1, 2),
        "planning_allowance_inr": 250,
        "hard_invoice_cap_guaranteed": False,
        "existing_gateway_usage_tracking_enabled": True,
        "shared_gateway_rate_limits_modified": False,
        "inference_payload_logging_enabled_by_this_phase": False,
        "new_azure_resources": 0,
        "new_dedicated_endpoints": 0,
    }
    (EVIDENCE / "cost_and_model_selection.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
