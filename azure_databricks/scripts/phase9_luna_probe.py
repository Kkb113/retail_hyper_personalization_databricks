"""One bounded, synthetic operator-identity Luna probe; never starts Databricks compute."""

import json
import os
from pathlib import Path

from phase9_azure_openai import DEPLOYMENT, VERSION, request
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase9_llm import AzureLunaPlanner
from retail_hp_azure.safety import require


def main():
    require(os.environ.get("RETAIL_HP_PHASE9_CEILING_INR") == "250", "Execution gate required")
    context = CloudContext(apply=False)
    deployment = request(context, "GET", f"/deployments/{DEPLOYMENT}")
    require(deployment["properties"]["provisioningState"] == "Succeeded", "Not ready")
    require(deployment["properties"]["model"]["version"] == VERSION, "Version drift")
    require(deployment["sku"]["name"] == "GlobalStandard", "Paid SKU drift")

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

    planner = AzureLunaPlanner(token, Path("build/phase9-spend.local.json"))
    try:
        plan = planner.plan("Find a lightweight jacket", {}, timeout=30)
        print(
            json.dumps(
                {
                    "action": plan.action,
                    "usage": planner.last_usage,
                    "identity": "operator_validation_only",
                }
            )
        )
    except Exception as exc:
        # Only our safe HTTP status is printable; never provider bodies/tokens.
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error_type": type(exc).__name__,
                    "safe_error": str(exc)
                    if str(exc).startswith("LLM_HTTP_")
                    else "Luna probe failed; raw error suppressed",
                    "usage": planner.last_usage,
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
