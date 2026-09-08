from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound
from retail_hp_azure.phase3 import (
    CONTROL_MANIFEST,
    CONTROL_SEAL,
    LANDING_ROOT,
    MODEL_ROOT,
    ROOTS,
    RUNTIME_COMPAT_DESTINATION,
    RUNTIME_COMPAT_SOURCE,
    _put_immutable,
    _seal_bytes,
    apply_transfer,
    inspect_remote,
    load_manifest,
    seal_transfer,
    validate_local,
    validate_manifest,
)
from retail_hp_azure.phase3_runtime import NOTEBOOK_SOURCE, validation_job_plan
from retail_hp_azure.safety import SafetyError


class FakeDownload:
    def __init__(self, content: bytes) -> None:
        self.contents = io.BytesIO(content)


class FakeFiles:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.overwrite_values: list[bool | None] = []

    def get_metadata(self, path: str) -> Any:
        if path not in self.values:
            raise NotFound("missing")
        return SimpleNamespace(content_length=len(self.values[path]))

    def download(self, path: str) -> FakeDownload:
        if path not in self.values:
            raise NotFound("missing")
        return FakeDownload(self.values[path])

    def create_directory(self, _path: str) -> None:
        return None

    def upload(self, path: str, contents: io.BytesIO, *, overwrite: bool | None = None) -> None:
        self.overwrite_values.append(overwrite)
        if path in self.values and not overwrite:
            raise RuntimeError("overwrite refused")
        self.values[path] = contents.read()


class FakeContext:
    def __init__(self, files: FakeFiles, *, apply: bool = False) -> None:
        self.apply = apply
        self.client = SimpleNamespace(files=files)


def _populate_payload(files: FakeFiles) -> None:
    for entry in load_manifest()["files"]:
        content = Path(entry["path"]).read_bytes()
        for destination in entry["destinations"]:
            files.values[destination] = content
    manifest = Path("azure_databricks/contracts/transfer_manifest.json").read_bytes()
    for root in ROOTS:
        files.values[f"{root}/{CONTROL_MANIFEST}"] = manifest
    files.values[RUNTIME_COMPAT_DESTINATION] = RUNTIME_COMPAT_SOURCE.read_bytes()


def _validation() -> dict[str, Any]:
    manifest = load_manifest()
    return {
        "status": "PASS",
        "bundle_sha256": manifest["bundle_sha256"],
        "remote_hashes_verified": 46,
        "data_files_parsed": 20,
        "model_files_loaded": 26,
        "runtime_compatibility_loaded": True,
    }


def test_local_transfer_payload_is_complete_parseable_and_loadable() -> None:
    result = validate_local()
    assert result["status"] == "PASS"
    assert result["unique_file_count"] == 45
    assert result["destination_copy_count"] == 46
    assert result["data_file_count"] == 20
    assert result["model_file_count"] == 26
    assert result["data_rows"] == 275630
    assert result["total_unique_bytes"] == 12733215
    assert result["direct_pii_columns"] == 0
    assert result["executable_files"] == 0


def test_manifest_rejects_destination_outside_approved_roots() -> None:
    manifest = load_manifest()
    manifest["files"][0]["destinations"][0] = "/Volumes/other/schema/volume/file.json"
    with pytest.raises(SafetyError, match="outside approved"):
        validate_manifest(manifest)


def test_immutable_upload_creates_then_noops_and_never_overwrites() -> None:
    files = FakeFiles()
    path = f"{MODEL_ROOT}/artifacts/test.json"
    assert _put_immutable(files, path, b"safe") == "CREATED"
    assert _put_immutable(files, path, b"safe") == "NO_CHANGE"
    assert files.overwrite_values == [False]
    with pytest.raises(SafetyError, match="overwrite refused"):
        _put_immutable(files, path, b"changed")


def test_remote_inspection_detects_missing_matching_and_drift() -> None:
    files = FakeFiles()
    empty = inspect_remote(FakeContext(files))  # type: ignore[arg-type]
    assert empty["missing_count"] == 46
    assert empty["status"] == "FAIL"
    assert empty["drift_count"] == 0
    _populate_payload(files)
    good = inspect_remote(FakeContext(files))  # type: ignore[arg-type]
    assert good["matching_count"] == 46
    assert good["control_manifest_matching_count"] == 2
    assert good["runtime_compatibility_matching"] is True
    files.values[RUNTIME_COMPAT_DESTINATION] = b"drift"
    control_bad = inspect_remote(FakeContext(files))  # type: ignore[arg-type]
    assert control_bad["status"] == "FAIL"
    assert control_bad["control_drift_count"] == 1
    files.values[RUNTIME_COMPAT_DESTINATION] = RUNTIME_COMPAT_SOURCE.read_bytes()
    first = load_manifest()["files"][0]["destinations"][0]
    files.values[first] = b"drift"
    bad = inspect_remote(FakeContext(files))  # type: ignore[arg-type]
    assert bad["status"] == "FAIL"
    assert bad["drift_count"] == 1


def test_apply_is_idempotent_before_seal_and_refuses_after_seal() -> None:
    files = FakeFiles()
    context = FakeContext(files, apply=True)
    first = apply_transfer(context)  # type: ignore[arg-type]
    assert first["created_count"] == 48
    assert first["unchanged_count"] == 0
    assert set(files.overwrite_values) == {False}
    second = apply_transfer(context)  # type: ignore[arg-type]
    assert second["created_count"] == 0
    assert second["unchanged_count"] == 48
    content = _seal_bytes(load_manifest(), _validation())
    files.values[f"{LANDING_ROOT}/{CONTROL_SEAL}"] = content
    with pytest.raises(SafetyError, match="sealed"):
        apply_transfer(context)  # type: ignore[arg-type]


def test_seal_requires_complete_databricks_validation_and_is_deterministic() -> None:
    manifest = load_manifest()
    expected = _validation()
    first = _seal_bytes(manifest, expected)
    second = _seal_bytes(manifest, expected)
    assert first == second
    assert json.loads(first)["storage_worm_guaranteed"] is False
    bad = {**expected, "remote_hashes_verified": 45}
    with pytest.raises(SafetyError, match="every destination"):
        _seal_bytes(manifest, bad)


def test_seal_writes_both_roots_without_overwrite() -> None:
    files = FakeFiles()
    _populate_payload(files)
    result = seal_transfer(FakeContext(files, apply=True), _validation())  # type: ignore[arg-type]
    assert result["status"] == "PASS"
    assert result["sealed_root_count"] == 2
    for root in (LANDING_ROOT, MODEL_ROOT):
        assert f"{root}/{CONTROL_SEAL}" in files.values
    final = inspect_remote(FakeContext(files))  # type: ignore[arg-type]
    assert final["matching_seal_count"] == 2
    assert result["seal_sha256"] == hashlib.sha256(
        files.values[f"{LANDING_ROOT}/{CONTROL_SEAL}"]
    ).hexdigest()


def test_validation_job_is_one_time_cpu_bounded_and_below_ceiling() -> None:
    plan = validation_job_plan()
    assert plan["compute"] == "automated_serverless_cpu"
    assert plan["persistent_job_created"] is False
    assert plan["schedule_created"] is False
    assert plan["gpu"] is False
    assert plan["dependency_count"] == 6
    assert plan["task_timeout_minutes"] == 2.5
    assert plan["controller_deadline_minutes"] == 4
    assert plan["guarded_estimate_inr_pre_tax"] < 250
    assert plan["hard_invoice_cap_guaranteed"] is False


def test_databricks_validator_is_scoped_and_does_not_record_customer_ids() -> None:
    source = NOTEBOOK_SOURCE.read_text(encoding="utf-8")
    compile(source, str(NOTEBOOK_SOURCE), "exec")
    assert str(LANDING_ROOT) in source
    assert str(MODEL_ROOT) in source
    assert '"customer_identifiers_recorded": False' in source
    assert "dbutils.notebook.exit" in source
    assert "requests" not in source
    assert "spark.sql" not in source
