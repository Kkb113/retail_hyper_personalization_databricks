"""Read-only identity verification and bundle validation. Never prints raw CLI responses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from retail_hp_azure.config import CLI_VERSION, HOST, PocConfig
from retail_hp_azure.safety import (
    REQUIRED_TAGS,
    SafetyError,
    check_fingerprint,
    require,
    validate_resolved,
)

Runner = Callable[[list[str]], Any]


def verify_azure(config: PocConfig, run: Runner, az: str) -> dict[str, Any]:
    def azure(arguments: list[str]) -> Any:
        return run([az, *arguments, "--only-show-errors", "--output", "json"])

    account = azure(["account", "show"])
    # No other Azure or workspace reads until both identity fingerprints match.
    check_fingerprint("subscription", account["id"])
    check_fingerprint("tenant", account["tenantId"])
    require(account["state"] == "Enabled", "Azure subscription is not enabled")
    boundary = ["--subscription", account["id"], "--resource-group", config.scope.resource_group]
    group = azure(["group", "show", "--subscription", account["id"],
                   "--name", config.scope.resource_group])
    check_fingerprint("resource_group_id", group["id"])
    require(group["name"] == config.scope.resource_group, "Azure resource group differs")
    workspace = azure(["resource", "show", *boundary, "--name", config.scope.workspace,
                       "--resource-type", "Microsoft.Databricks/workspaces"])
    check_fingerprint("workspace_arm_id", workspace["id"])
    properties = workspace["properties"]
    check_fingerprint("workspace_id", str(properties["workspaceId"]))
    require(f"https://{properties['workspaceUrl']}" == HOST, "Azure workspace host differs")
    require(workspace["name"] == config.scope.workspace, "Azure workspace name differs")
    require(workspace["location"].lower() == config.scope.region, "Azure workspace region differs")
    require(properties.get("provisioningState") == "Succeeded", "Workspace is not provisioned")
    resources = azure(["resource", "list", *boundary])
    budgets = azure(["consumption", "budget", "list", *boundary])
    return {
        "scope_fingerprints_match": True,
        "subscription_enabled": True,
        "workspace_sku_existing": workspace["sku"]["name"],
        "resource_group_resource_count": len(resources),
        "budget_count": len(budgets),
        "existing_workspace_tags_match_policy": all(
            workspace.get("tags", {}).get(key) == value for key, value in REQUIRED_TAGS.items()
        ),
        "existing_tags_modified": False,
    }


def validate_plan_output(document: dict[str, Any]) -> None:
    """The pinned CLI must produce no resource actions; unknown shapes fail closed."""
    require(isinstance(document, dict), "Unexpected bundle plan output")
    require(set(document) == {"plan_version", "cli_version", "plan"},
            "Unknown bundle plan fields")
    require(type(document["plan_version"]) is int and document["plan_version"] == 2,
            "Unknown bundle plan format version")
    require(document["cli_version"] == CLI_VERSION, "Bundle plan CLI version differs")
    require(document.get("plan") == {}, "Bundle plan is not empty or has an unknown shape")


def collect_live(root: Path, config: PocConfig, executable: Path) -> dict[str, Any]:
    az = shutil.which("az")
    require(az is not None, "Azure CLI is required; authenticate with az login")
    require(executable.is_file(), "Pinned Databricks CLI executable is missing")
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("DATABRICKS_")}
    environment.update({"DATABRICKS_HOST": HOST, "DATABRICKS_AUTH_TYPE": "azure-cli",
                        "DATABRICKS_CONFIG_FILE": os.devnull})

    def execute(arguments: list[str]) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed read-only commands, no shell
                arguments, cwd=root, env=environment, capture_output=True, text=True,
                encoding="utf-8", timeout=90, check=True,
            )
            return result.stdout
        except (subprocess.SubprocessError, OSError) as exc:
            stage = " ".join(arguments[1:3])
            raise SafetyError(f"Read-only check failed at {stage}; "
                              "verify login, access and CLI version. "
                              "Raw output suppressed to protect identifiers.") from exc

    def run(arguments: list[str]) -> Any:
        try:
            return json.loads(execute(arguments))
        except json.JSONDecodeError as exc:
            raise SafetyError("CLI returned unexpected JSON; raw response suppressed") from exc

    azure = verify_azure(config, run, str(az))
    cli = str(executable.resolve())
    require(execute([cli, "version"]).strip() == f"Databricks CLI v{CLI_VERSION}",
            "Databricks CLI version does not match toolchain pin")
    catalog = run([cli, "catalogs", "get", config.scope.catalog, "-o", "json"])
    require(catalog.get("name") == config.scope.catalog, "Approved catalog is not accessible")
    resolved = run([cli, "bundle", "validate", "-t", "poc", "--strict", "-o", "json"])
    validate_resolved(resolved, config)
    proposed = run([cli, "bundle", "plan", "-t", "poc", "-o", "json"])
    validate_plan_output(proposed)
    return {
        "mode": "live_read_only",
        "azure": azure,
        "catalog_accessible": True,
        "bundle_validation": "PASS_STRICT",
        "resolved_scope_validation": "PASS",
        "bundle_plan": "PASS_NO_RESOURCE_ACTIONS",
        "cli_version": CLI_VERSION,
        "cloud_mutations_performed": False,
        "raw_identifiers_or_recipients_recorded": False,
    }
