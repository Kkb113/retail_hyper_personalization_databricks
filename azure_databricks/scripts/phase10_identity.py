"""Keyless Luna identity bootstrap, restricted to the approved RG and one App.

No deployment, inference, API key enablement or compute start is performed.
"""

import argparse
import json

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase10_runtime import SERVICE_CREDENTIAL
from retail_hp_azure.safety import REQUIRED_TAGS, require

CONNECTOR = "retail-hp-poc-luna-identity"
ACCOUNT = "retail-hp-poc-openai-4073b6c9"


def bootstrap(context):
    from databricks.sdk.service.catalog import (
        AzureManagedIdentity,
        CredentialPurpose,
        IsolationMode,
        PermissionsChange,
        Privilege,
        WorkspaceBinding,
        WorkspaceBindingBindingType,
    )

    require(context.apply, "Identity writes require explicit apply")
    client = context.client
    app = client.apps.get("retail-hp-poc-app")
    require(app.service_principal_client_id is not None, "App identity unavailable")
    require(app.compute_status.state.value == "STOPPED", "App must remain stopped")
    connector_path = (
        context.group["id"] + "/providers/Microsoft.Databricks/accessConnectors/" + CONNECTOR
    )
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
    url = f"https://management.azure.com{connector_path}?api-version=2024-05-01"
    headers = {"Authorization": "Bearer " + token}
    response = requests.get(url, headers=headers, timeout=30, allow_redirects=False)
    if response.status_code == 404:
        response = requests.put(
            url,
            headers=headers,
            json={
                "location": "westus",
                "identity": {"type": "SystemAssigned"},
                "tags": {**REQUIRED_TAGS, "phase": "10", "purpose": "luna-app-identity"},
                "properties": {},
            },
            timeout=45,
            allow_redirects=False,
        )
    require(response.status_code in {200, 201}, f"Access connector HTTP {response.status_code}")
    connector = response.json()
    require(
        connector["location"] == "westus" and connector["identity"]["type"] == "SystemAssigned",
        "Connector identity drift",
    )
    require(
        connector.get("tags", {}).get("purpose") == "luna-app-identity", "Connector ownership drift"
    )
    account_path = (
        context.group["id"] + "/providers/Microsoft.CognitiveServices/accounts/" + ACCOUNT
    )
    assigned = context.az_json(
        [
            "role",
            "assignment",
            "list",
            "--scope",
            account_path,
        ]
    )
    principal_id = connector["identity"]["principalId"]
    if not any(
        x.get("principalId") == principal_id
        and x.get("roleDefinitionName") == "Cognitive Services OpenAI User"
        for x in assigned
    ):
        context.az_json(
            [
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                principal_id,
                "--assignee-principal-type",
                "ServicePrincipal",
                "--role",
                "Cognitive Services OpenAI User",
                "--scope",
                account_path,
            ]
        )
    existing = [x for x in client.credentials.list_credentials() if x.name == SERVICE_CREDENTIAL]
    require(len(existing) <= 1, "Ambiguous credential")
    if not existing:
        credential = client.credentials.create_credential(
            SERVICE_CREDENTIAL,
            purpose=CredentialPurpose.SERVICE,
            azure_managed_identity=AzureManagedIdentity(access_connector_id=connector_path),
            comment="Keyless Luna access for the private retail POC App only.",
        )
    else:
        credential = client.credentials.get_credential(SERVICE_CREDENTIAL)
    require(
        credential.azure_managed_identity is not None
        and credential.azure_managed_identity.access_connector_id.lower() == connector_path.lower(),
        "Service credential target drift",
    )
    client.workspace_bindings.update_bindings(
        "credential",
        SERVICE_CREDENTIAL,
        add=[
            WorkspaceBinding(
                workspace_id=int(context.workspace["properties"]["workspaceId"]),
                binding_type=WorkspaceBindingBindingType.BINDING_TYPE_READ_WRITE,
            )
        ],
    )
    client.credentials.update_credential(
        SERVICE_CREDENTIAL, isolation_mode=IsolationMode.ISOLATION_MODE_ISOLATED
    )
    client.grants.update(
        "credential",
        SERVICE_CREDENTIAL,
        changes=[
            PermissionsChange(principal=app.service_principal_client_id, add=[Privilege.ACCESS])
        ],
    )
    return {
        "status": "CONFIGURED_NOT_INFERENCE_TESTED",
        "connector": CONNECTOR,
        "credential": SERVICE_CREDENTIAL,
        "scope": "approved_resource_group_only",
        "long_lived_azure_secrets_created": False,
        "compute_started": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(bootstrap(CloudContext(apply=True)), indent=2))
