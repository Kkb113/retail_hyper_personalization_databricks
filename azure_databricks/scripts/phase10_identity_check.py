"""Non-inference test of the actual App principal's short-lived Luna credential.

Temporary Databricks OAuth credential exists only in memory and is revoked in finally.
No token values or personal identities are emitted.
"""

import json

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_runtime import _workload_client
from retail_hp_azure.phase10_runtime import SERVICE_CREDENTIAL
from retail_hp_azure.safety import require


def check():
    from databricks.sdk.service.catalog import GenerateTemporaryServiceCredentialAzureOptions

    context = CloudContext(apply=True)
    app = context.client.apps.get("retail-hp-poc-app")
    require(app.compute_status.state.value == "STOPPED", "App must be stopped")
    principal = context.client.service_principals.get(str(app.service_principal_id))
    workload, secret_id, principal_id = _workload_client(context, principal)
    result = {"status": "FAIL", "compute_started": False, "inference_calls": 0}
    try:
        temporary = workload.credentials.generate_temporary_service_credential(
            SERVICE_CREDENTIAL,
            azure_options=GenerateTemporaryServiceCredentialAzureOptions(
                resources=["https://ai.azure.com/.default"]
            ),
        )
        require(
            temporary.azure_aad is not None and temporary.azure_aad.aad_token,
            "No workload token returned",
        )
        response = requests.get(
            "https://retail-hp-poc-openai-4073b6c9.openai.azure.com/openai/models?api-version=2024-10-21",
            headers={"Authorization": "Bearer " + temporary.azure_aad.aad_token},
            timeout=30,
            allow_redirects=False,
        )
        result["provider_http_status"] = response.status_code
        require(response.status_code == 200, "Luna workload metadata access failed")
        result["status"] = "PASS"
    finally:
        context.client.service_principal_secrets_proxy.delete(principal_id, secret_id)
        result["temporary_oauth_secret_revoked"] = True
    return result


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
