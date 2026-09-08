"""Grant scoped project access to the POC endpoint and inspect the result."""

import json

from databricks.sdk.service.serving import (
    ServingEndpointAccessControlRequest,
    ServingEndpointPermissionLevel,
)
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase6 import ENDPOINT_NAME

client = CloudContext(apply=True).client
endpoint = client.serving_endpoints.get(ENDPOINT_NAME)
assert endpoint.id
client.serving_endpoints.update_permissions(
    endpoint.id,
    access_control_list=[
        ServingEndpointAccessControlRequest(
            group_name="retail_hp_admins",
            permission_level=ServingEndpointPermissionLevel.CAN_MANAGE,
        ),
        ServingEndpointAccessControlRequest(
            group_name="retail_hp_engineers",
            permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
        ),
        ServingEndpointAccessControlRequest(
            group_name="retail_hp_app_runtime",
            permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
        ),
    ],
)
permissions = client.serving_endpoints.get_permissions(endpoint.id)
groups = {
    item.group_name: [p.permission_level.value for p in item.all_permissions]
    for item in permissions.access_control_list
    if item.group_name
}
assert "CAN_QUERY" in groups["retail_hp_app_runtime"]
assert "CAN_MANAGE" in groups["retail_hp_admins"]
print(
    json.dumps(
        {
            "status": "PASS",
            "project_groups": {k: v for k, v in groups.items() if k.startswith("retail_hp_")},
        }
    )
)
