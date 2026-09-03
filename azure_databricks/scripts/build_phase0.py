"""Build and verify the Azure Databricks Phase 0 evidence package.

The script is deliberately read-only with respect to Azure and Databricks. It
may write only sanitized evidence under the local azure_databricks folder.
Raw subscription, tenant, ARM resource, principal, and workspace identifiers
are represented by SHA-256 fingerprints in committed outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AZURE_ROOT = ROOT / "azure_databricks"
MIGRATION_ROOT = ROOT / "migration_assets"
PHASE_ROOT = AZURE_ROOT / "evidence" / "phase_00"
SCOPE_PATH = AZURE_ROOT / "config" / "phase0_scope.json"
TRANSFER_PATH = AZURE_ROOT / "contracts" / "transfer_manifest.json"
BASELINE_PATH = PHASE_ROOT / "local_baseline.json"
LIVE_INVENTORY_PATH = PHASE_ROOT / "live_inventory.json"
RESOURCE_INVENTORY_PATH = AZURE_ROOT / "resource_inventory.json"


class Phase0Error(RuntimeError):
    """Raised when a Phase 0 safety or integrity gate fails."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str, *, lower: bool = False) -> str:
    normalized = value.lower() if lower else value
    return _sha256_bytes(normalized.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _approved_model_hashes() -> dict[str, str]:
    """Read the self-contained approval map for the Azure migration payload."""

    contract = _read_json(AZURE_ROOT / "contracts" / "approved_model_hashes.json")
    assets = contract.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise Phase0Error("The approved model hash contract is missing or empty")
    return {str(key): str(value) for key, value in assets.items()}


def _parquet_shape(path: Path) -> tuple[int, list[str]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - environment guard
        raise Phase0Error("pyarrow is required to inspect the frozen parquet assets") from exc
    metadata = parquet.ParquetFile(path)
    return int(metadata.metadata.num_rows), list(metadata.schema_arrow.names)


def _bundle_digest(entries: Iterable[Mapping[str, Any]], role: str | None = None) -> str:
    rows: list[str] = []
    for entry in entries:
        roles = set(entry["roles"])
        if role is None or role in roles:
            rows.append(f"{entry['path']}:{entry['sha256']}")
    return _sha256_text("".join(f"{row}\n" for row in sorted(rows)))


def build_transfer_manifest(scope: Mapping[str, Any]) -> dict[str, Any]:
    inventory_path = AZURE_ROOT / "contracts" / "source_asset_inventory.json"
    inventory = _read_json(inventory_path)
    model_hashes = _approved_model_hashes()
    entries: dict[str, dict[str, Any]] = {}

    landing_root = scope["databricks"]["transfer_landing_root"]
    model_root = scope["databricks"]["model_assets_root"]

    for asset in inventory["data_assets"]:
        logical_path = str(asset["path"]).replace("\\", "/")
        relative = f"migration_assets/{logical_path}"
        source = MIGRATION_ROOT / logical_path
        if not source.is_file():
            raise Phase0Error(f"Required data asset is missing: {relative}")
        rows, columns = _parquet_shape(source)
        expected_rows = int(asset["expected_rows"])
        if rows != expected_rows:
            raise Phase0Error(
                f"Row-count mismatch for {relative}: expected {expected_rows}, observed {rows}"
            )
        required_columns = [str(item) for item in asset["required_columns"]]
        missing_columns = sorted(set(required_columns) - set(columns))
        if missing_columns:
            raise Phase0Error(f"Required columns missing from {relative}: {missing_columns}")
        entries[relative] = {
            "path": relative,
            "roles": ["data"],
            "sha256": _sha256_file(source),
            "size_bytes": source.stat().st_size,
            "logical_path": logical_path,
            "destinations": [f"{landing_root}/data/{logical_path}"],
            "data_contract": {
                "name": asset["name"],
                "domain": asset["domain"],
                "expected_rows": expected_rows,
                "observed_rows": rows,
                "required_columns": required_columns,
                "observed_column_count": len(columns),
                "capabilities": list(asset.get("capabilities", [])),
            },
        }

    for logical_path, expected_hash in sorted(model_hashes.items()):
        relative = f"migration_assets/{logical_path}"
        source = MIGRATION_ROOT / logical_path
        if not source.is_file():
            raise Phase0Error(f"Required model asset is missing: {relative}")
        observed_hash = _sha256_file(source)
        if observed_hash != expected_hash:
            raise Phase0Error(f"Frozen model hash mismatch: {relative}")
        entry = entries.setdefault(
            relative,
            {
                "path": relative,
                "logical_path": logical_path,
                "roles": [],
                "sha256": observed_hash,
                "size_bytes": source.stat().st_size,
                "destinations": [],
            },
        )
        if entry["sha256"] != observed_hash:
            raise Phase0Error(f"Cross-role hash mismatch: {relative}")
        entry["roles"] = sorted(set(entry["roles"]) | {"model"})
        entry["destinations"] = sorted(
            set(entry["destinations"]) | {f"{model_root}/{logical_path}"}
        )
        entry["model_contract"] = {
            "approved_sha256": expected_hash,
            "component": logical_path.split("/", 2)[1],
        }

    files = [entries[path] for path in sorted(entries)]
    data_count = sum("data" in item["roles"] for item in files)
    model_count = sum("model" in item["roles"] for item in files)
    overlap_count = sum(set(item["roles"]) == {"data", "model"} for item in files)
    manifest = {
        "manifest_version": "azure_phase0_transfer_v1",
        "release_id": scope["release"]["release_id"],
        "policy_version": scope["release"]["policy_version"],
        "classification": "synthetic_data",
        "production_approved": False,
        "generated_at": _utc_now(),
        "integrity_algorithm": "sha256-path-digest-v1",
        "source_contracts": [
            "azure_databricks/contracts/source_asset_inventory.json",
            "azure_databricks/contracts/approved_model_hashes.json",
        ],
        "destination": {
            "workspace": scope["databricks"]["workspace_name"],
            "catalog": scope["databricks"]["catalog"],
            "transfer_landing_root": landing_root,
            "model_assets_root": model_root,
        },
        "summary": {
            "unique_file_count": len(files),
            "data_file_count": data_count,
            "model_file_count": model_count,
            "cross_role_overlap_count": overlap_count,
            "declared_data_rows": sum(
                item.get("data_contract", {}).get("expected_rows", 0) for item in files
            ),
            "observed_data_rows": sum(
                item.get("data_contract", {}).get("observed_rows", 0) for item in files
            ),
            "total_unique_bytes": sum(int(item["size_bytes"]) for item in files),
            "missing_file_count": 0,
            "hash_mismatch_count": 0,
            "row_mismatch_count": 0,
        },
        "bundle_sha256": _bundle_digest(files),
        "data_bundle_sha256": _bundle_digest(files, "data"),
        "model_bundle_sha256": _bundle_digest(files, "model"),
        "files": files,
    }
    expected_summary = {
        "unique_file_count": 45,
        "data_file_count": 20,
        "model_file_count": 26,
        "cross_role_overlap_count": 1,
        "declared_data_rows": 275630,
    }
    for key, expected in expected_summary.items():
        observed = manifest["summary"][key]
        if observed != expected:
            raise Phase0Error(f"Transfer summary {key} expected {expected}, observed {observed}")
    return manifest


def _package_versions() -> dict[str, str]:
    packages = [
        "joblib",
        "numpy",
        "pandas",
        "pyarrow",
        "scikit-learn",
        "xgboost",
    ]
    result: dict[str, str] = {}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "not-installed"
    return result


def _git_output(arguments: list[str]) -> str:
    executable = shutil.which("git")
    if not executable:
        return "unavailable"
    result = subprocess.run(  # noqa: S603
        [executable, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def build_local_baseline(
    scope: Mapping[str, Any], transfer_manifest: Mapping[str, Any]
) -> dict[str, Any]:
    migration = _read_json(AZURE_ROOT / "contracts" / "model_migration_baseline.json")
    release = _read_json(AZURE_ROOT / "contracts" / "release_decision_baseline.json")
    tracked = [line for line in _git_output(["ls-files"]).splitlines() if line]
    return {
        "baseline_version": "azure_phase0_local_baseline_v1",
        "captured_at": _utc_now(),
        "source_commit": _git_output(["rev-parse", "HEAD"]),
        "source_branch": _git_output(["branch", "--show-current"]),
        "python_version": sys.version.split()[0],
        "package_versions": _package_versions(),
        "repository": {
            "tracked_file_count": len(tracked),
            "tracked_data_file_count": sum(
                path.startswith("migration_assets/data_cache/") for path in tracked
            ),
            "tracked_artifact_file_count": sum(
                path.startswith("migration_assets/artifacts/") for path in tracked
            ),
            "target_azure_subtree_initially_present": (ROOT / "azure_databricks").is_dir(),
        },
        "transfer": dict(transfer_manifest["summary"]),
        "selected_validation_metrics": migration["validation_metrics"][
            migration["selection_policy"]["selected_candidate"]
        ],
        "selected_candidate": migration["selection_policy"]["selected_candidate"],
        "release": {
            "decision": release["decision"],
            "release_approved": release["release_approved"],
            "blocking_reasons": release["blocking_reasons"],
            "synthetic_data_warning": release["synthetic_data_warning"],
        },
        "scope": {
            "resource_group": scope["azure"]["resource_group"],
            "workspace": scope["databricks"]["workspace_name"],
            "catalog": scope["databricks"]["catalog"],
        },
        "review_findings": {
            "legacy_catalog_hardcoding_present": True,
            "legacy_personal_identity_config_present": True,
            "legacy_free_edition_assumptions_present": True,
            "legacy_vector_search_enabled_by_default": True,
            "legacy_model_training_notebook_is_snapshot_wrapper": True,
            "new_functional_mlflow_package_required": True,
        },
    }


def _run_json(command: list[str], *, optional: bool = False) -> tuple[Any, str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        if optional:
            return None, type(result).__name__
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed"
        raise Phase0Error(f"Read-only command failed: {command[1]} ({detail})")
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError as exc:
        raise Phase0Error(f"Command returned invalid JSON: {command[1]}") from exc


def _safe_sdk_names(call: Callable[[], Iterable[Any]], *fields: str) -> dict[str, Any]:
    try:
        values: list[str] = []
        for item in call():
            for field in fields:
                value = getattr(item, field, None)
                if value:
                    values.append(str(value))
                    break
        return {"status": "available", "count": len(values), "names": sorted(set(values))}
    except Exception as exc:  # noqa: BLE001 - inventory must report optional API gaps
        return {
            "status": "unavailable",
            "count": None,
            "names": [],
            "error_type": type(exc).__name__,
        }


def collect_live_inventory(scope: Mapping[str, Any]) -> dict[str, Any]:
    az = shutil.which("az")
    if not az:
        raise Phase0Error("Azure CLI was not found")
    account, _ = _run_json([az, "account", "show", "--output", "json", "--only-show-errors"])
    group, _ = _run_json(
        [
            az,
            "group",
            "show",
            "--name",
            scope["azure"]["resource_group"],
            "--output",
            "json",
            "--only-show-errors",
        ]
    )
    workspace, _ = _run_json(
        [
            az,
            "resource",
            "show",
            "--resource-group",
            scope["azure"]["resource_group"],
            "--name",
            scope["databricks"]["workspace_name"],
            "--resource-type",
            "Microsoft.Databricks/workspaces",
            "--output",
            "json",
            "--only-show-errors",
        ]
    )
    resources, _ = _run_json(
        [
            az,
            "resource",
            "list",
            "--resource-group",
            scope["azure"]["resource_group"],
            "--output",
            "json",
            "--only-show-errors",
        ]
    )
    budgets, budget_error = _run_json(
        [
            az,
            "consumption",
            "budget",
            "list",
            "--resource-group",
            scope["azure"]["resource_group"],
            "--output",
            "json",
            "--only-show-errors",
        ],
        optional=True,
    )

    properties = workspace.get("properties", {})
    host = f"https://{properties['workspaceUrl']}"
    fingerprints = {
        "subscription": _sha256_text(str(account["id"]).lower()),
        "tenant": _sha256_text(str(account["tenantId"]).lower()),
        "resource_group_id": _sha256_text(str(group["id"]), lower=True),
        "workspace_arm_id": _sha256_text(str(workspace["id"]), lower=True),
        "workspace_id": _sha256_text(str(properties["workspaceId"])),
    }
    expected = scope["fingerprints"]
    fingerprint_checks = {
        key: fingerprints[key] == expected[key] for key in sorted(expected)
    }
    if not all(fingerprint_checks.values()):
        raise Phase0Error("The active Azure scope does not match the committed fingerprints")
    if str(group["name"]).casefold() != scope["azure"]["resource_group"].casefold():
        raise Phase0Error("The active resource group does not match the approved scope")
    if workspace["name"] != scope["databricks"]["workspace_name"]:
        raise Phase0Error("The active workspace does not match the approved scope")
    if workspace["location"].lower() != scope["databricks"]["workspace_region"]:
        raise Phase0Error("The active workspace region does not match the approved scope")
    if host != scope["databricks"]["workspace_host"]:
        raise Phase0Error("The active workspace host does not match the approved scope")

    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_AZURE_RESOURCE_ID"] = str(workspace["id"])
    os.environ["DATABRICKS_AUTH_TYPE"] = "azure-cli"
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:  # pragma: no cover - environment guard
        raise Phase0Error("databricks-sdk is required for live inventory") from exc
    client = WorkspaceClient()
    current_user = client.current_user.me()
    catalogs = sorted(item.name for item in client.catalogs.list() if item.name)
    expected_catalog = scope["databricks"]["catalog"]
    if expected_catalog not in catalogs:
        raise Phase0Error(f"Required catalog is not visible: {expected_catalog}")
    schemas = _safe_sdk_names(
        lambda: client.schemas.list(catalog_name=expected_catalog), "full_name", "name"
    )
    schema_names = [
        name.split(".")[-1] for name in schemas["names"] if name.split(".")[-1]
    ]
    volumes: list[str] = []
    volume_errors: list[str] = []
    for schema_name in schema_names:
        if schema_name == "information_schema":
            continue
        result = _safe_sdk_names(
            lambda value=schema_name: client.volumes.list(
                catalog_name=expected_catalog, schema_name=value
            ),
            "full_name",
            "name",
        )
        volumes.extend(result["names"])
        if result["status"] != "available":
            volume_errors.append(result["error_type"])
    volume_inventory = {
        "status": "available" if not volume_errors else "partial",
        "count": len(set(volumes)),
        "names": sorted(set(volumes)),
        "error_types": sorted(set(volume_errors)),
    }
    serving = _safe_sdk_names(lambda: client.serving_endpoints.list(), "name")
    serving_names = serving["names"]
    foundation_names = sorted(
        name for name in serving_names if name.startswith("databricks-")
    )
    custom_names = sorted(
        name for name in serving_names if not name.startswith("databricks-")
    )
    databricks_inventory = {
        "identity": {
            "active": bool(current_user.active),
            "group_names": sorted(
                {
                    str(group.display)
                    for group in (current_user.groups or [])
                    if getattr(group, "display", None)
                }
            ),
            "principal_name_redacted": True,
        },
        "catalogs": {"status": "available", "count": len(catalogs), "names": catalogs},
        "project_schemas": schemas,
        "project_volumes": volume_inventory,
        "jobs": _safe_sdk_names(lambda: client.jobs.list(), "name"),
        "apps": _safe_sdk_names(lambda: client.apps.list(), "name"),
        "sql_warehouses": _safe_sdk_names(
            lambda: client.warehouses.list(), "name", "id"
        ),
        "registered_models": _safe_sdk_names(
            lambda: client.registered_models.list(), "full_name", "name"
        ),
        "default_schema_functions": _safe_sdk_names(
            lambda: client.functions.list(
                catalog_name=expected_catalog, schema_name="default"
            ),
            "full_name",
            "name",
        ),
        "serving_endpoints": {
            "status": serving["status"],
            "total_count": serving["count"],
            "foundation_count": len(foundation_names),
            "foundation_names": foundation_names,
            "custom_count": len(custom_names),
            "custom_names": custom_names,
        },
    }

    budget_items = []
    if isinstance(budgets, list):
        for item in budgets:
            budget_items.append(
                {
                    "name": item.get("name"),
                    "amount": item.get("amount"),
                    "time_grain": item.get("timeGrain"),
                }
            )
    azure_resources = [
        {
            "name": item.get("name"),
            "type": item.get("type"),
            "location": item.get("location"),
            "sku_name": (item.get("sku") or {}).get("name"),
            "resource_id_fingerprint": _sha256_text(str(item.get("id", "")), lower=True),
        }
        for item in resources
    ]
    inventory = {
        "inventory_version": "azure_phase0_live_inventory_v1",
        "captured_at": _utc_now(),
        "collection_mode": "read_only",
        "raw_identifiers_committed": False,
        "scope_validation": {
            "all_fingerprints_match": all(fingerprint_checks.values()),
            "fingerprint_checks": fingerprint_checks,
            "resource_group_match": True,
            "workspace_match": True,
            "workspace_host_match": True,
            "workspace_region_match": True,
            "catalog_visible": True,
        },
        "azure": {
            "subscription_state": account.get("state"),
            "subscription_fingerprint": fingerprints["subscription"],
            "tenant_fingerprint": fingerprints["tenant"],
            "resource_group": {
                "name": group["name"],
                "location": group["location"],
                "provisioning_state": group.get("properties", {}).get("provisioningState"),
                "resource_id_fingerprint": fingerprints["resource_group_id"],
            },
            "workspace": {
                "name": workspace["name"],
                "location": workspace["location"],
                "sku": (workspace.get("sku") or {}).get("name"),
                "provisioning_state": properties.get("provisioningState"),
                "public_network_access": properties.get("publicNetworkAccess"),
                "compute_mode": properties.get("computeMode"),
                "unity_catalog_enabled": bool(properties.get("isUcEnabled")),
                "host": host,
                "arm_id_fingerprint": fingerprints["workspace_arm_id"],
                "workspace_id_fingerprint": fingerprints["workspace_id"],
            },
            "resource_count": len(azure_resources),
            "resources": sorted(azure_resources, key=lambda item: (item["type"], item["name"])),
            "budgets": {
                "query_status": "available" if budgets is not None else "unavailable",
                "error_type": budget_error or None,
                "count": len(budget_items),
                "items": budget_items,
                "notification_recipients_committed": False,
            },
        },
        "databricks": databricks_inventory,
        "cloud_mutations_performed": False,
    }
    return inventory


def build_resource_inventory(
    scope: Mapping[str, Any], live_inventory: Mapping[str, Any]
) -> dict[str, Any]:
    azure = live_inventory["azure"]
    return {
        "inventory_version": "azure_phase0_resource_inventory_v1",
        "captured_at": live_inventory["captured_at"],
        "scope": {
            "resource_group": scope["azure"]["resource_group"],
            "workspace": scope["databricks"]["workspace_name"],
        },
        "items": [
            {
                "name": azure["resource_group"]["name"],
                "kind": "azure_resource_group",
                "lifecycle": "existing_retain",
                "location": azure["resource_group"]["location"],
                "id_fingerprint": azure["resource_group"]["resource_id_fingerprint"],
            },
            {
                "name": azure["workspace"]["name"],
                "kind": "azure_databricks_workspace",
                "lifecycle": "existing_retain",
                "location": azure["workspace"]["location"],
                "sku": azure["workspace"]["sku"],
                "id_fingerprint": azure["workspace"]["arm_id_fingerprint"],
            },
        ],
        "new_resources_created_in_phase_0": 0,
        "cloud_mutations_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--collect-live",
        action="store_true",
        help="Run read-only Azure CLI and Databricks SDK inventory calls.",
    )
    arguments = parser.parse_args()
    scope = _read_json(SCOPE_PATH)
    transfer = build_transfer_manifest(scope)
    _write_json(TRANSFER_PATH, transfer)
    baseline = build_local_baseline(scope, transfer)
    _write_json(BASELINE_PATH, baseline)
    output = {
        "transfer_files": transfer["summary"]["unique_file_count"],
        "data_files": transfer["summary"]["data_file_count"],
        "model_files": transfer["summary"]["model_file_count"],
        "declared_rows": transfer["summary"]["declared_data_rows"],
        "live_inventory_collected": False,
    }
    if arguments.collect_live:
        live = collect_live_inventory(scope)
        _write_json(LIVE_INVENTORY_PATH, live)
        _write_json(RESOURCE_INVENTORY_PATH, build_resource_inventory(scope, live))
        output["live_inventory_collected"] = True
        output["azure_resource_count"] = live["azure"]["resource_count"]
        output["foundation_endpoint_count"] = live["databricks"]["serving_endpoints"][
            "foundation_count"
        ]
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
