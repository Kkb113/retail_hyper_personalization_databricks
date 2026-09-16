"""Create only the scoped, idle Basic Automation account; never starts compute."""

import argparse
import json
import re

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import REQUIRED_TAGS, require

ACCOUNT = "retail-hp-poc-shutdown"


def bootstrap(context):
    require(context.apply, "Explicit apply required")
    token = context.az_json([
        "account", "get-access-token", "--subscription", context.subscription,
        "--resource", "https://management.azure.com/",
    ])["accessToken"]
    url = ("https://management.azure.com" + context.group["id"]
           + "/providers/Microsoft.Automation/automationAccounts/" + ACCOUNT
           + "?api-version=2024-10-23")
    headers = {"Authorization": "Bearer " + token}
    result = requests.get(url, headers=headers, timeout=30, allow_redirects=False)
    if result.status_code == 404:
        result = requests.put(url, headers=headers, json={
            "location": "westus", "identity": {"type": "SystemAssigned"},
            "tags": {**REQUIRED_TAGS, "phase": "10", "purpose": "bounded-poc-shutdown"},
            "properties": {"sku": {"name": "Basic"}, "disableLocalAuth": True,
                           "publicNetworkAccess": False},
        }, timeout=45, allow_redirects=False)
    if result.status_code not in {200, 201}:
        code = result.json().get("error", {}).get("code", "Unknown")
        code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(code)) else "Unknown"
        raise RuntimeError(f"Automation account HTTP {result.status_code}: {code}")
    account = result.json()
    require(account["location"] == "westus", "Automation location drift")
    require(account.get("tags", {}).get("purpose") == "bounded-poc-shutdown",
            "Automation ownership drift")
    require(account["properties"]["sku"]["name"] == "Basic", "Automation SKU drift")
    require(account["identity"]["type"] == "SystemAssigned", "Identity drift")
    return {"account": ACCOUNT, "sku": "Basic", "identity_created": True,
            "runbook_deployed": False, "compute_started": False,
            "state": account["properties"].get("state", "Unknown")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(bootstrap(CloudContext(apply=True)), indent=2))
