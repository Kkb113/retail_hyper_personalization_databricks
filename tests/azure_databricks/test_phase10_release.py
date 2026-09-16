"""Release integrity and restart controls, without paid compute."""

import hashlib
import json
from unittest.mock import Mock

import pytest
from retail_hp_azure.phase10_release import Launch, claim_launch, verify_files
from retail_hp_azure.safety import SafetyError


def launch(expires=150):
    return Launch.model_validate_json(
        json.dumps(
            {
                "ticket": "a" * 32,
                "expires": expires,
                "warehouse_id": "b" * 16,
                "entitlements": [{"subject": "demo"}],
            }
        )
    )


def test_claim_is_create_only_and_duplicate_fails():
    client = Mock()
    claim_launch(client, launch(), now=100)
    assert client.files.upload.call_args.kwargs == {"overwrite": False}
    client.files.upload.side_effect = RuntimeError("Already exists")
    with pytest.raises(RuntimeError):
        claim_launch(client, launch(), now=100)


@pytest.mark.parametrize("expires", [99, 100, 1901])
def test_expired_or_overlong_launch_is_rejected(expires):
    client = Mock()
    with pytest.raises(SafetyError):
        claim_launch(client, launch(expires), now=100)
    client.files.upload.assert_not_called()


def test_manifest_integrity(tmp_path):
    (tmp_path / "static").mkdir()
    (tmp_path / "static/index.html").write_text("chat")
    (tmp_path / "semantic.json").write_text("{}")
    manifest = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in ["static/index.html", "semantic.json"]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    verify_files(tmp_path)
    (tmp_path / "static/index.html").write_text("changed")
    with pytest.raises(SafetyError):
        verify_files(tmp_path)


def test_manifest_rejects_parent_paths(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"../outside": "a" * 64}))
    with pytest.raises(SafetyError):
        verify_files(tmp_path)
