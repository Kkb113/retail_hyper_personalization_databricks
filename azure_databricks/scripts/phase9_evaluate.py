"""Explicit, quota-reserved pay-token benchmark. Does not start any warehouse or endpoint."""

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from phase8_control import stopped
from phase9_azure_openai import DEPLOYMENT, VERSION, request
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase3 import _find_repo_root
from retail_hp_azure.phase9 import SYSTEM_PROMPT
from retail_hp_azure.phase9_evaluation import cases, evaluate_case, evaluate_multiturn, summarize
from retail_hp_azure.phase9_llm import RATES, AzureLunaPlanner, DatabricksPlanner
from retail_hp_azure.safety import require

ROOT = _find_repo_root()
EVIDENCE = ROOT / "azure_databricks/evidence/phase_09"
LEDGER = ROOT / "build/phase9-spend.local.json"


def luna_operator_planner(context):
    """Explicit local validation identity; never used as a deployed workload credential."""
    deployment = request(context, "GET", f"/deployments/{DEPLOYMENT}")
    require(
        deployment and deployment["properties"]["provisioningState"] == "Succeeded",
        "Luna deployment unavailable",
    )
    require(
        deployment["properties"]["model"]["name"] == DEPLOYMENT
        and deployment["properties"]["model"]["version"] == VERSION
        and deployment["sku"]["name"] == "GlobalStandard",
        "Luna deployment drift",
    )

    def token():
        return context.az_json(
            [
                "account",
                "get-access-token",
                "--subscription",
                context.subscription,
                "--resource",
                "https://ai.azure.com",
            ]
        )["accessToken"]

    return AzureLunaPlanner(token, LEDGER)


def write_report(path, report):
    # Atomic replacement avoids partially truncated evidence under OneDrive synchronization.
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", choices=[DEPLOYMENT, *RATES], default=DEPLOYMENT)
    parser.add_argument("--mode", choices=["probe", "screen", "full"], required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    require(os.environ.get("RETAIL_HP_PHASE9_CEILING_INR") == "250", "Execution gate required")
    context = CloudContext(apply=False)
    inventory = stopped(context)
    require(_verify_budget(context)["current_spend_inr"] < 9000, "Monthly target reached")
    if args.endpoint == DEPLOYMENT:
        planner = luna_operator_planner(context)
        model = f"azure.openai.{DEPLOYMENT}.{VERSION}"
    else:
        endpoint = context.client.serving_endpoints.get(args.endpoint)
        entities = endpoint.config.served_entities if endpoint.config else []
        expected = {
            "databricks-gpt-oss-20b": "system.ai.gpt-oss-20b",
            "databricks-meta-llama-3-1-8b-instruct": "system.ai.meta_llama_v3_1_8b_instruct",
        }
        require(
            len(entities or []) == 1 and entities[0].foundation_model is not None,
            "Expected existing shared foundation endpoint",
        )
        model = entities[0].foundation_model.name
        require(model.lower() == expected[args.endpoint], f"Model identity drift: {model}")
        planner = DatabricksPlanner(context.client, args.endpoint, LEDGER)
    dataset = cases()
    if args.case_id:
        dataset = [case for case in dataset if case.id == args.case_id]
        require(bool(dataset), "Unknown case ID")
    if args.mode == "probe":
        dataset = dataset[:1]
    if args.mode == "screen":
        counts = Counter()
        selected = []
        for case in dataset:
            if counts[case.category] < 2:
                selected.append(case)
                counts[case.category] += 1
        dataset = selected
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"{args.mode}_{args.endpoint}_{stamp}.json"
    report = {
        "status": "RUNNING",
        "endpoint": args.endpoint,
        "model_identity": model,
        "llm_identity": "operator_validation_only"
        if args.endpoint == DEPLOYMENT
        else "databricks_operator",
        "mode": args.mode,
        "new_azure_resources": 0,
        "new_endpoints": 0,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "initial_compute": inventory,
        "rows": [],
    }
    if args.resume:
        previous = json.loads(args.resume.read_text())
        require(
            previous["endpoint"] == args.endpoint
            and previous["mode"] == args.mode
            and previous["prompt_sha256"] == report["prompt_sha256"],
            "Resume drift",
        )
        report["rows"] = previous["rows"]
        report["resumed_from"] = args.resume.name
        completed = {row["case_id"] for row in report["rows"]}
        dataset = [case for case in dataset if case.id not in completed]
    try:
        for case in dataset:
            planner.last_usage = {}
            row = evaluate_case(case, planner)
            row["usage"] = planner.last_usage
            report["rows"].append(row)
            write_report(path, report)
            print(
                json.dumps(
                    {
                        k: row[k]
                        for k in ("case_id", "action", "status", "selection_pass", "behavior_pass")
                    }
                ),
                flush=True,
            )
        report["summary"] = summarize(report["rows"])
        report["status"] = "PASS" if report["summary"]["passed"] else "FAIL"
        if args.mode == "full" and not args.case_id:
            report["multiturn"] = evaluate_multiturn(planner)
            if not all(row["passed"] for row in report["multiturn"]):
                report["status"] = "FAIL"
    finally:
        report["final_compute"] = stopped(context)
        report["cumulative_llm_ledger"] = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
        write_report(path, report)
    print(
        json.dumps(
            {"report": path.name, "status": report["status"], "summary": report.get("summary")}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
