"""Fail-closed source and resolved-bundle guards. This is not Azure RBAC."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from retail_hp_azure.config import CLI_VERSION, HOST, ROOT_PATH, PocConfig, load_config

REQUIRED_TAGS = {
    "auto-stop": "true",
    "cost-profile": "low",
    "environment": "poc",
    "managed-by": "codex",
    "project": "retail-hyper-personalization",
    "workspace": "intellify-databricks-demo",
}
FINGERPRINTS = {
    "subscription": "abbac062fec075143b421090466e4ca3164b706288e5226ec80eab5a660a3198",
    "tenant": "0e6ba1a9be18e7f8aa8e4b32ad33977a5acbb56d124564c652d4e591e1ba4d88",
    "resource_group_id": "ee1324cb0d1b0da7f79793920baeffd2789f06898df71ad91bc4dc6e15287e1c",
    "workspace_arm_id": "b0ce410ef4a0a1d64b8e02ddd6473919f2c93f39b7b116fc5f3be3d1abedac12",
    "workspace_id": "fd16be8323389de2c04a63069229d6fb8b12be8c69e7f3861568ec7c92f9ab8e",
}


class SafetyError(ValueError):
    """A source, identity, or cost boundary failed. Values are deliberately redacted."""


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently choosing the last value."""


def _unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)  # type: ignore[no-untyped-call]
        if not isinstance(key, str) or key in result:
            raise SafetyError("Bundle has duplicate or non-string keys")
        result[key] = loader.construct_object(value_node)  # type: ignore[no-untyped-call]
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafetyError(message)


def check_fingerprint(key: str, value: str) -> None:
    digest = hashlib.sha256(value.strip().lower().encode()).hexdigest()
    require(digest == FINGERPRINTS[key], f"Azure scope mismatch: {key}")


def bundle_variables(config: PocConfig) -> dict[str, str]:
    return {
        "catalog": config.scope.catalog,
        **config.names.model_dump(),
        **{f"enable_{key}": "false" for key in config.features.model_dump()},
    }


def expected_bundle(config: PocConfig) -> dict[str, Any]:
    return {
        "bundle": {
            "name": "retail-hp-azure",
            "databricks_cli_version": f">= {CLI_VERSION}, <= {CLI_VERSION}",
            "engine": "direct",
        },
        "variables": {key: {"default": value} for key, value in bundle_variables(config).items()},
        "sync": {"paths": ["bundle_files"]},
        "resources": {},
        "targets": {"poc": {"default": True, "workspace": {
            "host": HOST, "root_path": ROOT_PATH,
        }}},
    }


def check_environment(environment: Mapping[str, str]) -> None:
    # Reject overrides, profiles, tokens, alternate roots/engines and lookup variables.
    allowed = {"DATABRICKS_HOST": HOST, "DATABRICKS_AUTH_TYPE": "azure-cli"}
    for key, value in environment.items():
        upper = key.upper()
        if upper.startswith("DATABRICKS_"):
            require(upper in allowed and allowed[upper] == value,
                    "Databricks environment override is not permitted")


def preflight(root: Path, environment: Mapping[str, str]) -> PocConfig:
    root = root.resolve()
    require(root.name == "azure_databricks", "Use the Azure subtree, not the legacy bundle")
    config = load_config(root / "config" / "poc.json")
    require(config.tags == REQUIRED_TAGS, "Required low-cost ownership tags are missing or changed")
    check_environment(environment)
    require(not list((root / ".databricks").rglob("variable-overrides.json")),
            "Local bundle variable overrides are forbidden")
    source = root / "databricks.yml"
    require(not source.is_symlink(), "Bundle symlinks are forbidden")
    document = yaml.load(source.read_text(encoding="utf-8"), Loader=UniqueLoader)  # noqa: S506
    require(document == expected_bundle(config),
            "Bundle differs from the resource-free Phase 1 contract")
    sync_root = root / "bundle_files"
    require(not sync_root.is_symlink(), "Sync directory symlinks are forbidden")
    entries = list(sync_root.rglob("*"))
    require(len(entries) == 1 and entries[0].name == "README.md"
            and entries[0].is_file() and not entries[0].is_symlink(),
            "Only the foundation marker may be eligible for synchronization")
    require(entries[0].read_text(encoding="utf-8") ==
            "# Retail HP Azure foundation\n\n"
            "Phase 1 marker only. No runtime, model, data or credentials are synchronized.\n",
            "The synchronization marker has unexpected content")
    return config


def validate_resolved(document: dict[str, Any], config: PocConfig) -> None:
    require(document.get("workspace", {}).get("host") == HOST, "Resolved host is not approved")
    username = document.get("workspace", {}).get("current_user", {}).get("userName")
    require(isinstance(username, str) and bool(username) and "/" not in username
            and "\\" not in username and username not in {".", ".."},
            "Resolved workspace principal is invalid")
    expected_root = ROOT_PATH.replace("${workspace.current_user.userName}", username)
    require(document.get("workspace", {}).get("root_path") == expected_root,
            "Resolved bundle root is not approved")
    require(document.get("bundle", {}).get("target") == "poc", "Resolved target is not poc")
    require(not document.get("resources"), "Resolved bundle contains resources")
    for key, value in bundle_variables(config).items():
        require(document.get("variables", {}).get(key, {}).get("value") == value,
                "Resolved variable differs from approved configuration")


def validate_proposed_mutation(resource: dict[str, Any], config: PocConfig) -> None:
    """A future deployment helper must call this before any write, even for free SKUs."""
    require(resource.get("resource_group") == config.scope.resource_group, "Wrong resource group")
    require(resource.get("workspace_host") == HOST, "Wrong workspace")
    require(resource.get("tags") == REQUIRED_TAGS, "Missing cost tags")
    raise SafetyError("All cloud mutations are blocked in Phase 1, including free resources")


def plan(config: PocConfig, root: Path) -> dict[str, Any]:
    return {
        "schema_version": "azure_phase1_plan_v1",
        "phase": 1,
        "target": config.target,
        "scope": config.scope.model_dump(),
        "future_names_only": config.names.model_dump(),
        "features": config.features.model_dump(),
        "actions": [],
        "cloud_mutations_allowed": False,
        "deployment_allowed": False,
        "new_billable_resources": 0,
        "incremental_compute_cost_inr": 0,
        "budget": config.cost.model_dump(),
        "source_hashes": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("config/poc.json", "databricks.yml")
        },
        "note": "Read-only foundation; budget, shutdown, model and app deployment are NOT active.",
    }


def json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"
