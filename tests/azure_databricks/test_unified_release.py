"""Feature-flag rollback and graceful pricing failure do not disable retail."""

import json
import sys
import time
from types import ModuleType
from unittest.mock import Mock

import pytest
from retail_hp_azure.config import HOST
from retail_hp_azure.phase10_release import build_app
from retail_hp_azure.safety import SafetyError


@pytest.mark.parametrize("enabled", [False, True])
def test_pricing_flag_and_failure_preserve_retail(tmp_path, monkeypatch, enabled):
    import retail_hp_azure.phase10_release as release

    (tmp_path / "semantic.json").write_text("{}")
    monkeypatch.setattr(release, "verify_files", Mock())
    monkeypatch.setattr(release, "SemanticIndex", Mock())
    monkeypatch.setattr(release, "claim_launch", Mock())
    runtime = Mock()
    app = Mock()
    monkeypatch.setattr(release, "WorkbenchRuntime", runtime)
    monkeypatch.setattr(release, "create_app", app)
    module = ModuleType("dynamic_pricing_app.runtime")
    loader = Mock()
    loader.load.side_effect = RuntimeError("Invalid model must be withheld")
    module.PricingRuntime = loader
    monkeypatch.setitem(sys.modules, "dynamic_pricing_app.runtime", module)
    client = Mock()
    client.config.host, client.config.auth_type = HOST, "oauth-m2m"
    config = {
        "ticket": "a" * 32,
        "expires": int(time.time()) + 300,
        "warehouse_id": "b" * 16,
        "entitlements": [{"subject": "demo"}],
        "pricing_enabled": enabled,
        "pricing_subjects": ["demo"],
    }
    build_app(tmp_path, client, json.dumps(config), "s" * 64)
    assert runtime.call_args.kwargs["pricing"] is None
    assert runtime.call_args.kwargs["pricing_subjects"] == (
        frozenset({"demo"}) if enabled else frozenset()
    )
    assert loader.load.call_count == int(enabled)
    assert app.call_args.kwargs["max_concurrent"] == (5 if enabled else 1)
    config["pricing_subjects"] = ["unknown"]
    with pytest.raises(SafetyError):
        build_app(tmp_path, client, json.dumps(config), "s" * 64)
