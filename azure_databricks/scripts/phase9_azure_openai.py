"""Explicitly approved, fixed-scope Luna account/deployment bootstrap; no inference."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import REQUIRED_TAGS, require

ACCOUNT = "retail-hp-poc-openai-4073b6c9"
DEPLOYMENT = "gpt-5.6-luna"
VERSION = "2026-07-09"
API = "2025-06-01"


def request(context: CloudContext, method: str, suffix: str, body: Any = None) -> Any:
    require(suffix in {"", "/deployments", f"/deployments/{DEPLOYMENT}"}, "Unapproved route")
    require(method == "GET" or (method == "PUT" and context.apply), "Write not approved")
    require(method != "PUT" or suffix != "/deployments", "Collection writes forbidden")
    path = context.group["id"] + "/providers/Microsoft.CognitiveServices/accounts/" + ACCOUNT
    token = context.az_json(
        [
            "account",
            "get-access-token",
            "--subscription",
            context.subscription,
            "--resource",
            "https://management.azure.com/",
        ]
    )["accessToken"]
    response = requests.request(
        method,
        f"https://management.azure.com{path}{suffix}?api-version={API}",
        headers={"Authorization": "Bearer " + token},
        json=body,
        timeout=45,
        allow_redirects=False,
    )
    if response.status_code == 404 and method == "GET":
        return None
    if not 200 <= response.status_code < 300:
        code = response.json().get("error", {}).get("code", "Unknown")
        code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(code)) else "Unknown"
        raise RuntimeError(f"Azure OpenAI HTTP {response.status_code}: {code}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    context = CloudContext(apply=args.apply)
    account = request(context, "GET", "")
    if account is None and args.apply:
        account = request(
            context,
            "PUT",
            "",
            {
                "location": "westus",
                "kind": "OpenAI",
                "sku": {"name": "S0"},
                "tags": {**REQUIRED_TAGS, "phase": "9", "billing": "pay-per-token"},
                "properties": {
                    "customSubDomainName": ACCOUNT,
                    "disableLocalAuth": True,
                    "publicNetworkAccess": "Enabled",
                },
            },
        )
    require(account is not None, "Account is not yet created")
    require(
        account["kind"] == "OpenAI"
        and account["location"] == "westus"
        and account["sku"]["name"] == "S0",
        "Account configuration drift",
    )
    require(account["properties"].get("disableLocalAuth") is True, "Keys must be disabled")
    state = account["properties"].get("provisioningState")
    deployment = None
    if args.deploy:
        require(state == "Succeeded", "Account provisioning is not complete")
        deployment = request(context, "GET", f"/deployments/{DEPLOYMENT}")
        if deployment is None and args.apply:
            deployment = request(
                context,
                "PUT",
                f"/deployments/{DEPLOYMENT}",
                {
                    "sku": {"name": "GlobalStandard", "capacity": 10},
                    "properties": {
                        "model": {"format": "OpenAI", "name": DEPLOYMENT, "version": VERSION},
                        "versionUpgradeOption": "NoAutoUpgrade",
                    },
                },
            )
        require(deployment is not None, "Deployment does not exist")
        require(
            deployment["sku"]["name"] == "GlobalStandard" and deployment["sku"]["capacity"] == 10,
            "Unexpected paid capacity",
        )
        require(
            deployment["properties"]["model"]["name"] == DEPLOYMENT
            and deployment["properties"]["model"]["version"] == VERSION,
            "Requested Luna version was not deployed",
        )
        require(
            deployment["properties"].get("versionUpgradeOption") == "NoAutoUpgrade",
            "Automatic model upgrades are forbidden",
        )
    report = {
        "account": ACCOUNT,
        "resource_group": "Databricks",
        "region": "westus",
        "account_state": state,
        "local_auth_disabled": True,
        "deployment": DEPLOYMENT if deployment else None,
        "deployment_state": deployment["properties"].get("provisioningState")
        if deployment
        else None,
        "inference_calls": 0,
        "provisioned_capacity": False,
        "integration_and_evaluation_complete": False,
    }
    Path("build").mkdir(exist_ok=True)
    Path("build/phase9-azure-openai.local.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
