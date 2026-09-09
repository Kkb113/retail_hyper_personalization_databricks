"""Regression coverage for the Databricks 10 MB source-export limit workaround."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def bootstrap():
    path = Path(__file__).resolve().parents[2] / "azure_databricks/app/bootstrap.py"
    spec = importlib.util.spec_from_file_location("retail_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reassembles_verified_parts(tmp_path):
    payload = b'{"products": []}'
    parts = {"semantic-part-000": payload[:5], "semantic-part-001": payload[5:]}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in parts.items()}
    manifest["semantic.json"] = hashlib.sha256(payload).hexdigest()
    for name, data in parts.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    bootstrap().assemble(tmp_path)
    assert (tmp_path / "semantic.json").read_bytes() == payload
    (tmp_path / "semantic-part-001").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        bootstrap().assemble(tmp_path)


def test_rejects_missing_parts(tmp_path):
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(RuntimeError, match="Invalid semantic parts"):
        bootstrap().assemble(tmp_path)
