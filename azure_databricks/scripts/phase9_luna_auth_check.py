"""Read-only Luna identity diagnostics. Never logs bearer tokens or principal identifiers."""

import base64
import json
import re

import requests
from phase9_azure_openai import ACCOUNT, request
from retail_hp_azure.phase2 import CloudContext


def main():
    context = CloudContext(apply=False)
    account = request(context, "GET", "")
    token = context.az_json(
        [
            "account",
            "get-access-token",
            "--subscription",
            context.subscription,
            "--resource",
            "https://ai.azure.com",
        ]
    )["accessToken"]
    encoded = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    roles = context.az_json(
        [
            "role",
            "assignment",
            "list",
            "--scope",
            account["id"],
            "--include-inherited",
            "--fill-principal-name",
            "false",
            "--subscription",
            context.subscription,
        ]
    )
    response = requests.get(
        f"https://{ACCOUNT}.openai.azure.com/openai/v1/models",
        headers={"Authorization": "Bearer " + token},
        timeout=30,
        allow_redirects=False,
    )
    error = response.json().get("error", {}) if response.status_code != 200 else {}
    message = str(error.get("message", ""))[:1500]
    message = re.sub(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", "<identifier>", message
    )
    message = re.sub(r"[\w.+-]+@[\w.-]+", "<email>", message)
    print(
        json.dumps(
            {
                "audience": claims.get("aud"),
                "tenant_matches": claims.get("tid") == context.account["tenantId"],
                "caller_has_openai_user": any(
                    r.get("principalId") == claims.get("oid")
                    and r.get("roleDefinitionId", "").endswith(
                        "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
                    )
                    for r in roles
                ),
                "public_network_access": account["properties"].get("publicNetworkAccess"),
                "network_default_action": account["properties"]
                .get("networkAcls", {})
                .get("defaultAction"),
                "status": response.status_code,
                "error": message,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
