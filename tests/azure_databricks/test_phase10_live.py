"""No-cloud regressions for bounded launch and single-use restart behavior."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from retail_hp_azure.safety import SafetyError


def load_script():
    path = Path(__file__).resolve().parents[2] / "azure_databricks/scripts/phase10_live.py"
    spec = importlib.util.spec_from_file_location("phase10_launch_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("installed", [False, True])
def test_single_claim_deploy_or_restart(tmp_path, monkeypatch, installed):
    module = load_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(module, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(module, "stopped", lambda *args: None)
    monkeypatch.setattr(module, "arm", lambda *args: "job")
    monkeypatch.setattr(module, "_verify_budget", lambda *args: {"current_spend_inr": 0})
    monkeypatch.setattr(module, "_verify_warehouse_contract", lambda *args: None)
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    (tmp_path / "build").mkdir()
    evidence = tmp_path / "azure_databricks/evidence/phase_10"
    evidence.mkdir(parents=True)
    (evidence / "release_preflight.json").write_text(
        json.dumps(
            {
                "status": "PASS_METADATA_AND_CLAIM",
                "release": "release",
            }
        )
    )
    (tmp_path / "build/phase10-release.local.json").write_text(
        json.dumps(
            {
                "release": "release",
                "source_path": "/Workspace/release",
                "warehouse_id": "b" * 16,
                "operator_id": "operator",
            }
        )
    )
    client = Mock()
    client.service_principals.list.return_value = [
        SimpleNamespace(
            display_name=module.IDENTITY_NAME,
            active=True,
            id="tester",
            application_id="app",
        )
    ]
    deployment = SimpleNamespace(
        source_code_path="/Workspace/release",
        create_time="2026-09-09",
        deployment_id="installed",
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
    )
    client.apps.get.return_value = SimpleNamespace(
        active_deployment=None,
        compute_status=SimpleNamespace(state=SimpleNamespace(value="ACTIVE")),
        url="https://app",
    )
    client.apps.list_deployments.return_value = [deployment] if installed else []
    client.api_client.do.return_value = {"deployment_id": "new"}
    context = SimpleNamespace(client=client)
    module.start(context)
    deploys = [
        call
        for call in client.api_client.do.call_args_list
        if call.args[:2] == ("POST", "/api/2.0/apps/retail-hp-poc-app/deployments")
    ]
    assert len(deploys) == (0 if installed else 1)
    assert json.loads(module.LEDGER.read_text())["reserved_inr"] == 220
    # An uncertain old reservation cannot silently disappear on a later run.
    module.LEDGER.write_text(json.dumps({"reserved_inr": 440, "runs": []}))
    monkeypatch.setattr(module, "automation", lambda *args: {"properties": {"status": "Completed"}})
    with pytest.raises(SafetyError, match="allowance exhausted"):
        module.start(context)
