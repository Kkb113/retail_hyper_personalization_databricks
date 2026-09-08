"""Fail-closed Phase 3 transfer to versioned Unity Catalog volume paths."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import SafetyError, require

if TYPE_CHECKING:
    from databricks.sdk.service.files import FilesAPI



def _find_repo_root() -> Path:
    """Locate the operator checkout without coupling paths to an editable install."""
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (
            (candidate / "azure_databricks" / "contracts" / "transfer_manifest.json").is_file()
            and (candidate / "pyproject.toml").is_file()
        ):
            return candidate
    return current


REPO_ROOT = _find_repo_root()
AZURE_ROOT = REPO_ROOT / "azure_databricks"
MANIFEST_PATH = AZURE_ROOT / "contracts" / "transfer_manifest.json"
EVIDENCE_ROOT = AZURE_ROOT / "evidence" / "phase_03"
LANDING_ROOT = "/Volumes/intellify_databricks_demo/bronze/transfer_landing/retail_hp_transfer_v1"
MODEL_ROOT = "/Volumes/intellify_databricks_demo/ml/model_assets/retail_hp_transfer_v1"
ROOTS = (LANDING_ROOT, MODEL_ROOT)
CONTROL_MANIFEST = "_control/transfer_manifest.json"
CONTROL_SEAL = "_control/transfer_seal.json"
RUNTIME_COMPAT_SOURCE = AZURE_ROOT / "runtime_compat" / "src" / "recommender_utils.py"
RUNTIME_COMPAT_DESTINATION = f"{MODEL_ROOT}/_runtime/src/recommender_utils.py"
ALLOWED_SUFFIXES = {".json", ".joblib", ".npz", ".parquet"}
FORBIDDEN_SUFFIXES = {
    ".bat", ".cmd", ".com", ".dll", ".exe", ".msi", ".ps1", ".scr", ".sh",
}
FORBIDDEN_PII_COLUMNS = {
    "address", "creditcard", "dateofbirth", "dob", "email", "emailaddress",
    "firstname", "lastname", "passport", "phone", "phonenumber", "ssn",
}
OPTIONAL_NULL_COLUMNS = {
    "product_categories": {"ParentCategoryID"},
    "recommendation_log": {"SessionID", "PromotionID"},
    "recommendation_response": {"ResponseTime", "OrderID"},
    "sales_order_lines": {"PromotionID"},
    "search_events": {"ClickedProductID"},
    "wishlist": {"RemovedDate"},
}
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "brands": ("BrandID",),
    "browsing_events": ("EventID",),
    "cart_events": ("CartEventID",),
    "customer_preferences": ("CustomerID",),
    "customer_product_audit": ("CustomerID", "ProductID"),
    "customers": ("CustomerID",),
    "inventory": ("InventoryID",),
    "product_categories": ("CategoryID",),
    "products": ("ProductID",),
    "promotions": ("PromotionID",),
    "recommendation_log": ("RecommendationID",),
    "recommendation_response": ("ResponseID",),
    "recommendation_snapshot": ("CustomerID", "Rank"),
    "regions": ("RegionID",),
    "sales_order_lines": ("OrderLineID",),
    "sales_orders": ("OrderID",),
    "search_events": ("SearchID",),
    "weather": ("WeatherID",),
    "wishlist": ("WishlistID",),
    "customer_profile_history": ("CustomerID", "ProfileVersion"),
}
MODEL_JSONS = {
    "known_ranker.json", "lowhistory_ranker.json", "shared_ranker.json",
    "cold_start_ranker.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "Expected a JSON object")
    return cast(dict[str, Any], value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _path_digest(entries: Iterable[Mapping[str, Any]], role: str | None = None) -> str:
    rows = []
    for entry in entries:
        if role is None or role in set(entry["roles"]):
            rows.append(f"{entry['path']}:{entry['sha256']}")
    text = "".join(f"{row}\n" for row in sorted(rows))
    return hashlib.sha256(text.encode()).hexdigest()


def load_manifest() -> dict[str, Any]:
    return _read_json(MANIFEST_PATH)


def _source_path(relative: str) -> Path:
    posix = PurePosixPath(relative)
    require(not posix.is_absolute() and ".." not in posix.parts, "Unsafe source path")
    path = (REPO_ROOT / Path(*posix.parts)).resolve()
    require(path.is_relative_to(REPO_ROOT.resolve()), "Source path escapes repository")
    require(path.is_file() and not path.is_symlink(), "Transfer source is missing or linked")
    return path


def _validate_destination(destination: str, roles: set[str]) -> None:
    path = PurePosixPath(destination)
    require(path.is_absolute() and ".." not in path.parts, "Unsafe destination path")
    under_landing = destination.startswith(LANDING_ROOT + "/data/")
    under_model = destination.startswith(MODEL_ROOT + "/artifacts/")
    require(under_landing or under_model, "Destination is outside approved version roots")
    require(not under_landing or "data" in roles, "Non-data asset targets data landing")
    require(not under_model or "model" in roles, "Non-model asset targets model landing")


@contextmanager
def _runtime_compat_import_path() -> Iterator[None]:
    """Resolve the approved pickle dependency from the isolated migration shim."""
    root = str(RUNTIME_COMPAT_SOURCE.parents[1])
    sys.path.insert(0, root)
    try:
        yield
    finally:
        sys.path.remove(root)


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    require(manifest.get("manifest_version") == "azure_phase0_transfer_v1",
            "Unexpected transfer manifest version")
    require(manifest.get("classification") == "synthetic_data",
            "Only synthetic data is approved")
    require(manifest.get("production_approved") is False, "Payload is not production approved")
    require(manifest.get("destination") == {
        "workspace": "intellify-databricks-demo",
        "catalog": "intellify_databricks_demo",
        "transfer_landing_root": LANDING_ROOT,
        "model_assets_root": MODEL_ROOT,
    }, "Transfer destination drift")
    files_value = manifest.get("files")
    require(isinstance(files_value, list) and len(files_value) == 45,
            "Expected 45 unique files")
    files = cast(list[dict[str, Any]], files_value)
    paths: set[str] = set()
    destinations: set[str] = set()
    for raw in files:
        require(isinstance(raw, dict), "Invalid transfer entry")
        relative_value = raw.get("path")
        require(isinstance(relative_value, str) and relative_value not in paths,
                "Duplicate source path")
        if not isinstance(relative_value, str):  # pragma: no cover - require raises
            raise SafetyError("Invalid source path")
        relative = relative_value
        paths.add(relative)
        roles = {str(value) for value in cast(list[Any], raw.get("roles", []))}
        require(bool(roles) and roles <= {"data", "model"}, "Invalid transfer roles")
        require(Path(relative).suffix.lower() in ALLOWED_SUFFIXES, "Unapproved file type")
        require(Path(relative).suffix.lower() not in FORBIDDEN_SUFFIXES, "Executable is forbidden")
        declared_destinations = raw.get("destinations")
        require(isinstance(declared_destinations, list) and bool(declared_destinations),
                "Transfer entry has no destination")
        for destination in cast(list[Any], declared_destinations):
            require(isinstance(destination, str) and destination not in destinations,
                    "Duplicate or invalid destination")
            if not isinstance(destination, str):  # pragma: no cover - require raises
                raise SafetyError("Invalid destination")
            _validate_destination(destination, roles)
            destinations.add(destination)
    require(len(destinations) == 46, "Expected 46 destination copies")
    require(_path_digest(files) == manifest.get("bundle_sha256"), "Bundle digest mismatch")
    require(_path_digest(files, "data") == manifest.get("data_bundle_sha256"),
            "Data bundle digest mismatch")
    require(_path_digest(files, "model") == manifest.get("model_bundle_sha256"),
            "Model bundle digest mismatch")


def _validate_data_file(entry: Mapping[str, Any], source: Path) -> dict[str, Any]:
    import pyarrow.parquet as parquet

    contract = entry["data_contract"]
    name = str(contract["name"])
    file = parquet.ParquetFile(source)  # type: ignore[no-untyped-call]
    columns = list(file.schema_arrow.names)
    required = [str(value) for value in contract["required_columns"]]
    require(file.metadata.num_rows == int(contract["expected_rows"]), "Data row drift")
    require(not (set(required) - set(columns)), "Required data column missing")
    normalized = {column.replace("_", "").lower() for column in columns}
    require(not (normalized & FORBIDDEN_PII_COLUMNS), "Direct PII column is forbidden")
    keys = PRIMARY_KEYS.get(name)
    require(keys is not None, "Primary-key contract missing")
    if keys is None:  # pragma: no cover - require raises; this narrows third-party types
        raise SafetyError("Primary-key contract missing")
    require(set(keys) <= set(columns), "Primary-key contract missing")
    table = file.read(columns=sorted(set(required) | set(keys)))  # type: ignore[no-untyped-call]
    optional = OPTIONAL_NULL_COLUMNS.get(name, set())
    nulls = {column: table.column(column).null_count for column in required}
    require(all(count == 0 for column, count in nulls.items() if column not in optional),
            "Required non-null data constraint failed")
    key_rows = zip(*(table.column(key).to_pylist() for key in keys), strict=True)
    observed = list(key_rows)
    require(all(all(value is not None for value in row) for row in observed),
            "Primary key contains null")
    require(len(observed) == len(set(observed)), "Primary key contains duplicates")
    return {
        "name": name,
        "rows": int(file.metadata.num_rows),
        "columns": len(columns),
        "primary_key": list(keys),
        "optional_null_columns_observed": sorted(
            column for column, count in nulls.items() if count
        ),
        "status": "PASS",
    }


def _validate_model_file(entry: Mapping[str, Any], source: Path) -> dict[str, Any]:
    suffix = source.suffix.lower()
    if suffix == ".json":
        value = json.loads(source.read_text(encoding="utf-8"))
        require(isinstance(value, dict | list) and bool(value), "Empty model JSON")
        kind = "json"
        if source.name in MODEL_JSONS:
            import xgboost

            model = xgboost.Booster()
            model.load_model(source)
            require(model.num_boosted_rounds() > 0, "XGBoost model has no trees")
            kind = "xgboost_booster"
    elif suffix == ".npz":
        import numpy

        with numpy.load(source, allow_pickle=False) as arrays:
            require(bool(arrays.files), "NPZ artifact has no arrays")
            for key in arrays.files:
                array = arrays[key]
                require(array.dtype.kind != "O" and array.size > 0, "Unsafe or empty NPZ array")
        kind = "numpy_npz"
    elif suffix == ".joblib":
        import joblib  # type: ignore[import-untyped]

        # Loading pickle-compatible content is permitted only after its approved hash passed.
        with _runtime_compat_import_path():
            value = joblib.load(source)
        require(value is not None, "Joblib artifact did not load")
        kind = f"joblib:{type(value).__module__}.{type(value).__name__}"
    elif suffix == ".parquet":
        kind = "parquet_cross_role"
    else:  # pragma: no cover - manifest allowlist makes this unreachable
        raise SafetyError("Unsupported model artifact type")
    return {"logical_path": entry["logical_path"], "loader": kind, "status": "PASS"}


def validate_local() -> dict[str, Any]:
    manifest = load_manifest()
    validate_manifest(manifest)
    data_reports = []
    model_reports = []
    total = 0
    for entry in manifest["files"]:
        source = _source_path(entry["path"])
        size = source.stat().st_size
        require(size == int(entry["size_bytes"]), "Local transfer size mismatch")
        require(_sha256_file(source) == entry["sha256"], "Local transfer hash mismatch")
        total += size
        if "data" in entry["roles"]:
            data_reports.append(_validate_data_file(entry, source))
        if "model" in entry["roles"]:
            model_reports.append(_validate_model_file(entry, source))
    require(len(data_reports) == 20 and len(model_reports) == 26,
            "Local role validation count mismatch")
    return {
        "version": "azure_phase3_local_validation_v1",
        "status": "PASS",
        "classification": "synthetic_data",
        "production_approved": False,
        "unique_file_count": 45,
        "destination_copy_count": 46,
        "data_file_count": len(data_reports),
        "model_file_count": len(model_reports),
        "data_rows": sum(item["rows"] for item in data_reports),
        "total_unique_bytes": total,
        "bundle_sha256": manifest["bundle_sha256"],
        "direct_pii_columns": 0,
        "executable_files": 0,
        "data": data_reports,
        "models": model_reports,
        "cloud_mutations_performed": False,
        "compute_started": False,
    }


def _not_found(operation: Any) -> bool:
    from databricks.sdk.errors import NotFound

    try:
        operation()
    except NotFound:
        return True
    return False


def _remote_file(files: FilesAPI, path: str) -> tuple[str, int] | None:
    from databricks.sdk.errors import NotFound

    try:
        metadata = files.get_metadata(path)
    except NotFound:
        return None
    response = files.download(path)
    stream = response.contents
    require(stream is not None, "Remote file response has no content")
    if stream is None:  # pragma: no cover - require raises; this narrows SDK types
        raise SafetyError("Remote file response has no content")
    with stream:
        digest, size = _sha256_stream(stream)
    declared_size = getattr(metadata, "content_length", None)
    if declared_size is not None:
        require(int(declared_size) == size, "Remote metadata/content size mismatch")
    return digest, size


def _put_immutable(files: FilesAPI, path: str, content: bytes) -> str:
    current = _remote_file(files, path)
    expected = hashlib.sha256(content).hexdigest(), len(content)
    if current is not None:
        require(current == expected, "Immutable destination drift; overwrite refused")
        return "NO_CHANGE"
    parent = path.rsplit("/", 1)[0]
    files.create_directory(parent)
    files.upload(path, io.BytesIO(content), overwrite=False)
    require(_remote_file(files, path) == expected, "Uploaded file verification failed")
    return "CREATED"


def inspect_remote(context: CloudContext) -> dict[str, Any]:
    manifest = load_manifest()
    validate_manifest(manifest)
    present = 0
    matching = 0
    drift = 0
    missing = 0
    control_drift = 0
    control_manifest_present = 0
    control_manifest_matching = 0
    sealed_roots = 0
    matching_seals = 0
    for entry in manifest["files"]:
        for destination in entry["destinations"]:
            remote = _remote_file(context.client.files, destination)
            if remote is None:
                missing += 1
            else:
                present += 1
                if remote == (entry["sha256"], int(entry["size_bytes"])):
                    matching += 1
                else:
                    drift += 1
    manifest_expected = _sha256_file(MANIFEST_PATH), MANIFEST_PATH.stat().st_size
    runtime_expected = _sha256_file(RUNTIME_COMPAT_SOURCE), RUNTIME_COMPAT_SOURCE.stat().st_size
    runtime_remote = _remote_file(context.client.files, RUNTIME_COMPAT_DESTINATION)
    seal_content = _seal_bytes(manifest, {
        "status": "PASS",
        "bundle_sha256": manifest["bundle_sha256"],
        "remote_hashes_verified": 46,
        "data_files_parsed": 20,
        "model_files_loaded": 26,
        "runtime_compatibility_loaded": True,
    })
    seal_expected = hashlib.sha256(seal_content).hexdigest(), len(seal_content)
    for root in ROOTS:
        control_remote = _remote_file(context.client.files, f"{root}/{CONTROL_MANIFEST}")
        control_manifest_present += int(control_remote is not None)
        control_manifest_matching += int(control_remote == manifest_expected)
        control_drift += int(control_remote is not None and control_remote != manifest_expected)
        seal_remote = _remote_file(context.client.files, f"{root}/{CONTROL_SEAL}")
        sealed_roots += int(seal_remote is not None)
        matching_seals += int(seal_remote == seal_expected)
        control_drift += int(seal_remote is not None and seal_remote != seal_expected)
    control_drift += int(runtime_remote is not None and runtime_remote != runtime_expected)
    return {
        "version": "azure_phase3_remote_inspection_v1",
        "status": "PASS" if (
            drift == 0 and control_drift == 0 and missing == 0
            and control_manifest_matching == len(ROOTS)
        ) else "FAIL",
        "scope_verified": True,
        "expected_destination_count": 46,
        "present_count": present,
        "matching_count": matching,
        "missing_count": missing,
        "drift_count": drift,
        "control_drift_count": control_drift,
        "control_manifest_present_count": control_manifest_present,
        "control_manifest_matching_count": control_manifest_matching,
        "runtime_compatibility_present": runtime_remote is not None,
        "runtime_compatibility_matching": runtime_remote == runtime_expected,
        "sealed_root_count": sealed_roots,
        "matching_seal_count": matching_seals,
        "cloud_mutations_performed": False,
        "compute_started": False,
    }


def apply_transfer(context: CloudContext) -> dict[str, Any]:
    require(context.apply, "Transfer upload requires explicit apply context")
    local = validate_local()
    files = context.client.files
    existing_seals = [
        not _not_found(lambda root=root: files.get_metadata(f"{root}/{CONTROL_SEAL}"))
        for root in ROOTS
    ]
    require(not any(existing_seals), "Package is sealed; uploads are refused")
    created = 0
    unchanged = 0
    for entry in load_manifest()["files"]:
        content = _source_path(entry["path"]).read_bytes()
        for destination in entry["destinations"]:
            operation = _put_immutable(files, destination, content)
            created += operation == "CREATED"
            unchanged += operation == "NO_CHANGE"
    manifest_content = MANIFEST_PATH.read_bytes()
    for root in ROOTS:
        operation = _put_immutable(files, f"{root}/{CONTROL_MANIFEST}", manifest_content)
        created += operation == "CREATED"
        unchanged += operation == "NO_CHANGE"
    remote = inspect_remote(context)
    require(remote["status"] == "PASS" and remote["matching_count"] == 46,
            "Remote transfer verification failed")
    return {
        "version": "azure_phase3_transfer_apply_v1",
        "status": "PASS",
        "scope_verified": True,
        "local_validation": "PASS",
        "created_count": created,
        "unchanged_count": unchanged,
        "payload_destination_count": 46,
        "control_manifest_count": 2,
        "uploaded_bytes_maximum": local["total_unique_bytes"] + int(
            next(entry["size_bytes"] for entry in load_manifest()["files"]
                 if len(entry["destinations"]) == 2)
        ) + 2 * MANIFEST_PATH.stat().st_size,
        "remote_matching_count": remote["matching_count"],
        "sealed": False,
        "new_azure_resources": 0,
        "compute_started": False,
        "cloud_mutations_performed": created > 0,
    }


def _seal_bytes(manifest: Mapping[str, Any], validation: Mapping[str, Any]) -> bytes:
    require(validation.get("status") == "PASS", "Databricks validation did not pass")
    require(validation.get("bundle_sha256") == manifest["bundle_sha256"],
            "Databricks validation used a different bundle")
    require(validation.get("remote_hashes_verified") == 46,
            "Databricks did not verify every destination")
    require(validation.get("data_files_parsed") == 20, "Databricks data validation incomplete")
    require(validation.get("model_files_loaded") == 26, "Databricks model validation incomplete")
    require(validation.get("runtime_compatibility_loaded") is True,
            "Databricks runtime compatibility dependency was not loaded")
    seal = {
        "seal_version": "azure_phase3_transfer_seal_v1",
        "transfer_version": "retail_hp_transfer_v1",
        "classification": "synthetic_data",
        "production_approved": False,
        "manifest_sha256": _sha256_file(MANIFEST_PATH),
        "bundle_sha256": manifest["bundle_sha256"],
        "data_bundle_sha256": manifest["data_bundle_sha256"],
        "model_bundle_sha256": manifest["model_bundle_sha256"],
        "unique_file_count": 45,
        "destination_copy_count": 46,
        "data_file_count": 20,
        "model_file_count": 26,
        "runtime_dependency_count": 1,
        "runtime_dependency_sha256": _sha256_file(RUNTIME_COMPAT_SOURCE),
        "databricks_validation": "PASS",
        "mutation_policy": "NO_OVERWRITE_FAIL_ON_DRIFT",
        "storage_worm_guaranteed": False,
    }
    return (json.dumps(seal, indent=2, sort_keys=True) + "\n").encode()


def seal_transfer(context: CloudContext, validation: Mapping[str, Any]) -> dict[str, Any]:
    require(context.apply, "Transfer seal requires explicit apply context")
    manifest = load_manifest()
    remote = inspect_remote(context)
    require(
        remote["status"] == "PASS"
        and remote["matching_count"] == 46
        and remote["control_manifest_matching_count"] == 2
        and remote["runtime_compatibility_matching"] is True,
        "All remote payload and control artifacts must match before sealing",
    )
    content = _seal_bytes(manifest, validation)
    operations = [
        _put_immutable(context.client.files, f"{root}/{CONTROL_SEAL}", content)
        for root in ROOTS
    ]
    final = inspect_remote(context)
    require(final["sealed_root_count"] == 2 and final["matching_seal_count"] == 2,
            "Both transfer roots must contain the expected seal")
    return {
        "version": "azure_phase3_transfer_seal_result_v1",
        "status": "PASS",
        "scope_verified": True,
        "seal_sha256": hashlib.sha256(content).hexdigest(),
        "created_count": operations.count("CREATED"),
        "unchanged_count": operations.count("NO_CHANGE"),
        "sealed_root_count": 2,
        "storage_worm_guaranteed": False,
        "immutability_control": "versioned paths + approved hashes + no-overwrite client + seals",
        "compute_started": False,
        "new_azure_resources": 0,
        "cloud_mutations_performed": "CREATED" in operations,
    }


def record_evidence(filename: str, value: Mapping[str, Any]) -> None:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    document = {**value, "captured_at": datetime.now(UTC).isoformat()}
    (EVIDENCE_ROOT / filename).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate-local", "inspect-remote", "apply-transfer"])
    args = parser.parse_args()
    try:
        if args.command == "validate-local":
            result = validate_local()
            record_evidence("local_validation.json", result)
        elif args.command == "inspect-remote":
            result = inspect_remote(CloudContext())
            record_evidence("remote_inspection.json", result)
        else:
            require(os.environ.get("RETAIL_HP_PHASE3_TRANSFER_APPROVED") == "retail_hp_transfer_v1",
                    "Exact Phase 3 transfer approval is required")
            result = apply_transfer(CloudContext(apply=True))
            record_evidence("transfer_apply.json", result)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, SafetyError) else "raw values suppressed"
        raise SystemExit(f"Phase 3 operation failed: {type(exc).__name__}; {detail}") from None
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
