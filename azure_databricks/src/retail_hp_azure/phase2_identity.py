"""Minimal workload identity bootstrap and authenticated Phase 2 acceptance test."""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any

from retail_hp_azure.config import HOST
from retail_hp_azure.phase2 import CATALOG
from retail_hp_azure.phase2_compute import (
    APPROVED_CEILING_INR,
    DBU_PER_HOUR,
    RETAIL_DBU_HOURLY_INR,
    WAREHOUSE_NAME,
    _require_runtime_approval,
    _stop_and_verify,
    _verify_budget,
    _verify_warehouse_contract,
)
from retail_hp_azure.safety import require

if TYPE_CHECKING:
    from retail_hp_azure.phase2 import CloudContext

IDENTITY_NAME = "retail-hp-app-runtime"
IDENTITY_GROUP = "retail_hp_app_runtime"
IDENTITY_TEST_DEADLINE_MINUTES = 4
OAUTH_CREDENTIAL_LIFETIME_SECONDS = 3600


def _ensure_identity(context: CloudContext) -> tuple[Any, dict[str, Any]]:
    client = context.client
    matches = [
        principal for principal in client.service_principals.list()
        if principal.display_name == IDENTITY_NAME
    ]
    require(len(matches) <= 1, "Duplicate project workload identity requires manual review")
    created = False
    if matches:
        principal = matches[0]
    else:
        principal = client.service_principals.create(active=True, display_name=IDENTITY_NAME)
        created = True
    require(principal.active is True, "Project workload identity is inactive")
    require(
        bool(principal.id) and bool(principal.application_id),
        "Workload identity is incomplete",
    )
    require(not (principal.roles or []), "Workload identity has unexpected account roles")

    groups = client.api_client.do(
        "GET", "/api/2.0/account/scim/v2/Groups",
        query={"filter": f'displayName eq "{IDENTITY_GROUP}"'},
    ).get("Resources", [])
    require(len(groups) == 1 and str(groups[0].get("id", "")).isdigit(),
            "Application runtime group is unavailable")
    group_id = str(groups[0]["id"])
    detail = client.api_client.do("GET", "/api/2.0/account/scim/v2/Groups/" + group_id)
    member_ids = {str(member.get("value")) for member in detail.get("members", [])}
    membership_added = str(principal.id) not in member_ids
    if membership_added:
        client.api_client.do(
            "PATCH", "/api/2.0/account/scim/v2/Groups/" + group_id,
            body={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{
                    "op": "add", "path": "members",
                    "value": [{"value": str(principal.id)}],
                }],
            },
        )

    admin_groups = [group for group in client.groups.list() if group.display_name == "admins"]
    require(len(admin_groups) == 1 and bool(admin_groups[0].id),
            "Workspace admins group is unavailable")
    admins = client.groups.get(str(admin_groups[0].id))
    require(
        not any(member.value == principal.id for member in (admins.members or [])),
        "Workload identity must not be a workspace administrator",
    )
    return principal, {
        "identity_created": created,
        "runtime_group_membership_added": membership_added,
        "workspace_admin": False,
        "account_admin_roles": 0,
        "identity_cost_inr": 0,
    }


def apply_and_test_workload_identity(context: CloudContext) -> dict[str, Any]:
    """Authenticate as the project app identity, test access, revoke its test secret."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    from databricks.sdk.errors import NotFound, PermissionDenied
    from databricks.sdk.service.sql import State, StatementState

    require(context.apply, "Identity bootstrap requires explicit apply context")
    _require_runtime_approval()
    budget = _verify_budget(context)
    principal, identity = _ensure_identity(context)
    client = context.client
    warehouses = [warehouse for warehouse in client.warehouses.list()
                  if warehouse.name == WAREHOUSE_NAME]
    require(len(warehouses) == 1 and bool(warehouses[0].id),
            "Exactly one project warehouse is required")
    warehouse_id = str(warehouses[0].id)
    warehouse = client.warehouses.get(warehouse_id)
    _verify_warehouse_contract(warehouse)
    require(warehouse.state == State.STOPPED, "Identity test must begin with STOPPED compute")

    secret_id: str | None = None
    secret_value: str | None = None
    deadline_fired = threading.Event()
    finished = threading.Event()
    started_at = time.monotonic()

    def deadline_stop() -> None:
        if finished.wait(IDENTITY_TEST_DEADLINE_MINUTES * 60):
            return
        deadline_fired.set()
        try:
            client.warehouses.stop(warehouse_id)
        except Exception:
            return

    controller = threading.Thread(target=deadline_stop, name="retail-hp-identity-stop",
                                  daemon=True)
    controller.start()
    checks: dict[str, bool] = {}
    final_state = "UNKNOWN"
    secret_revoked = False
    try:
        secret = client.service_principal_secrets_proxy.create(
            str(principal.id), lifetime=f"{OAUTH_CREDENTIAL_LIFETIME_SECONDS}s"
        )
        secret_id, secret_value = secret.id, secret.secret
        require(bool(secret_id) and bool(secret_value), "OAuth test secret was not returned")
        workload_config = Config(**{  # type: ignore[arg-type]
            "host": HOST,
            "auth_type": "oauth-m2m",
            "client_id": str(principal.application_id),
            "client_" + "secret": str(secret_value),
            "config_file": os.devnull,
        })
        workload = WorkspaceClient(config=workload_config)
        me = workload.current_user.me()
        checks["authenticated_as_service_principal"] = (
            me.id == principal.id and me.user_name == principal.application_id
        )
        checks["runtime_group_membership"] = any(
            group.display == IDENTITY_GROUP for group in (me.groups or [])
        )
        serving = workload.schemas.get(f"{CATALOG}.serving")
        checks["serving_schema_allowed"] = serving.name == "serving"
        list(workload.tables.list(CATALOG, "serving"))
        checks["serving_table_listing_allowed"] = True
        try:
            workload.volumes.read(f"{CATALOG}.bronze.transfer_landing")
        except (PermissionDenied, NotFound):
            checks["raw_volume_metadata_denied"] = True
        else:
            checks["raw_volume_metadata_denied"] = False
        statement = workload.statement_execution.execute_statement(
            "SELECT 1 AS identity_probe",
            warehouse_id,
            catalog=CATALOG,
            schema="serving",
            row_limit=1,
            byte_limit=1024,
            wait_timeout="50s",
        )
        checks["sql_execution_allowed"] = (
            statement.status is not None
            and statement.status.state == StatementState.SUCCEEDED
        )
        require(all(checks.values()), "Authenticated workload identity check failed")
    finally:
        finished.set()
        controller.join(timeout=1)
        if secret_id and principal.id:
            client.service_principal_secrets_proxy.delete(str(principal.id), secret_id)
            secret_revoked = all(
                item.id != secret_id
                for item in client.service_principal_secrets_proxy.list(str(principal.id))
            )
        final_state = _stop_and_verify(client, warehouse_id)

    require(secret_revoked, "Temporary OAuth secret revocation could not be verified")
    require(final_state == "STOPPED", "Warehouse cleanup verification failed")
    elapsed = min(time.monotonic() - started_at, IDENTITY_TEST_DEADLINE_MINUTES * 60)
    estimated = RETAIL_DBU_HOURLY_INR * DBU_PER_HOUR * elapsed / 3600
    require(estimated < APPROVED_CEILING_INR, "Identity test estimate exceeds ceiling")
    return {
        "status": "PASS",
        "scope_verified": True,
        "identity": identity,
        "authenticated_checks_passed": len(checks),
        "authenticated_checks_failed": 0,
        "checks": checks,
        "temporary_oauth_secret_revoked": True,
        "oauth_secret_recorded": False,
        "identity_identifiers_recorded": False,
        "warehouse_final_state": final_state,
        "deadline_fired": deadline_fired.is_set(),
        "elapsed_seconds": round(elapsed, 2),
        "estimated_elapsed_cost_inr_pre_tax": round(estimated, 4),
        "budget_admission": budget,
        "hard_invoice_cap_guaranteed": False,
    }
