"""No-cloud regressions for bounded launch and single-use restart behavior."""

import importlib.util
import json
import sys
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
@pytest.mark.parametrize("owner_demo", [False, True])
@pytest.mark.parametrize("chat_upgrade", [False, True])
@pytest.mark.parametrize("context_upgrade", [False, True])
def test_single_claim_deploy_or_restart(
    tmp_path, monkeypatch, installed, owner_demo, chat_upgrade, context_upgrade
):
    if owner_demo and chat_upgrade:
        pytest.skip("mutually exclusive launch purposes")
    module = load_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(module, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(module, "DEMO_LEDGER", tmp_path / "demo-ledger.json")
    monkeypatch.setattr(module, "CHAT_LEDGER", tmp_path / "chat-ledger.json")
    if owner_demo:
        module.LEDGER.write_text(json.dumps({"reserved_inr": 440, "runs": []}))
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
                **({"customer_context_version": "customer_context_v2"} if context_upgrade else {}),
            }
        )
    )
    client = Mock()
    client.warehouses.get.return_value.state.value = "RUNNING"
    migrated = []

    def migrate(context):
        client.apps.start.assert_not_called()
        client.warehouses.start.assert_called()
        migrated.append(True)
        return {"status": "PASS_VIEW_UPGRADE"}

    monkeypatch.setitem(
        sys.modules, "phase10_customer_context_migrate", SimpleNamespace(migrate=migrate)
    )
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
    if installed and chat_upgrade:
        pending = SimpleNamespace(
            create_time="2026-09-09",
            status=SimpleNamespace(state=SimpleNamespace(value="IN_PROGRESS")),
        )
        client.apps.list_deployments.side_effect = [[deployment], [pending], [deployment]]

        def wait_for_terminal(_):
            # Only an expired lease may be visible while the automatic deployment runs.
            current = client.secrets.put_secret.call_args.kwargs["string_value"]
            assert json.loads(current)["expires"] == 1

        monkeypatch.setattr(module.time, "sleep", wait_for_terminal)
    client.api_client.do.return_value = {"deployment_id": "new"}
    context = SimpleNamespace(client=client)
    module.start(context, owner_demo=owner_demo, chat_upgrade=chat_upgrade)
    assert bool(migrated) == context_upgrade
    deploys = [
        call
        for call in client.api_client.do.call_args_list
        if call.args[:2] == ("POST", "/api/2.0/apps/retail-hp-poc-app/deployments")
    ]
    assert len(deploys) == (0 if installed and not chat_upgrade else 1)
    if chat_upgrade:
        launches = client.secrets.put_secret.call_args_list
        assert json.loads(launches[0].kwargs["string_value"])["expires"] == 1
        assert json.loads(launches[1].kwargs["string_value"])["expires"] == 2200
        assert not any(
            call.args[:2]
            == ("POST", "/api/2.0/serving-endpoints/retail-hp-poc-recommender/config:start")
            for call in client.api_client.do.call_args_list
        )
    ledger = (
        module.CHAT_LEDGER
        if chat_upgrade
        else (module.DEMO_LEDGER if owner_demo else module.LEDGER)
    )
    assert json.loads(ledger.read_text())["reserved_inr"] == 220
    if owner_demo:
        assert json.loads(module.LEDGER.read_text())["reserved_inr"] == 440
    # An uncertain old reservation cannot silently disappear on a later run.
    ledger.write_text(json.dumps({"reserved_inr": 880 if chat_upgrade else 440, "runs": []}))
    client.apps.list_deployments.side_effect = None
    monkeypatch.setattr(module, "automation", lambda *args: {"properties": {"status": "Completed"}})
    with pytest.raises(SafetyError, match="allowance exhausted"):
        module.start(context, owner_demo=owner_demo, chat_upgrade=chat_upgrade)
