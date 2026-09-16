"""Shutdown deployment safety checks without cloud access."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
from retail_hp_azure.safety import SafetyError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "azure_databricks/scripts"
preserved_acl = runpy.run_path(str(SCRIPT / "phase10_shutdown.py"))["preserved_acl"]
bootstrap = runpy.run_path(str(SCRIPT / "phase10_automation.py"))["bootstrap"]


def test_preserve_owner_admin_and_other_direct_grants():
    acl = [
        {"user_name": "owner", "all_permissions": [{"permission_level": "IS_OWNER"}]},
        {"group_name": "admins", "all_permissions": [{"permission_level": "CAN_MANAGE"}]},
        {"group_name": "readers", "all_permissions": [{"permission_level": "CAN_USE"}]},
        {"group_name": "inherited", "all_permissions": [
            {"permission_level": "CAN_MANAGE", "inherited": True}]},
    ]
    result = preserved_acl(acl, "controller")
    assert result[:3] == [
        {"user_name": "owner", "permission_level": "IS_OWNER"},
        {"group_name": "admins", "permission_level": "CAN_MANAGE"},
        {"group_name": "readers", "permission_level": "CAN_USE"},
    ]
    assert len(result) == 4
    assert result[-1]["service_principal_name"] == "controller"


def test_controller_update_is_idempotent():
    acl = [{"service_principal_name": "controller", "all_permissions": [
        {"permission_level": "CAN_MANAGE"}]}]
    assert preserved_acl(acl, "controller") == [
        {"service_principal_name": "controller", "permission_level": "CAN_MANAGE"}]


def test_ambiguous_acl_identity_is_rejected():
    with pytest.raises(SafetyError):
        preserved_acl([{"user_name": "a", "group_name": "b"}], "controller")


def test_account_requires_explicit_apply():
    with pytest.raises(SafetyError):
        bootstrap(Mock(apply=False))


def test_stop_controller_has_fixed_targets_and_bounded_retries():
    source = (ROOT / "azure_databricks/app/stop_demo.ps1").read_text()
    assert "[long]$DeadlineUnix" in source
    assert "($now + 1800)" in source
    assert ".AddMinutes(5)" in source
    assert "ALL_TARGETS_STOPPED" in source
    assert "STOP_NOT_VERIFIED" in source
    assert "/config:stop" in source
    assert "/start" not in source and "/config:start" not in source
    assert "Invoke-Workspace 'POST'" in source
    assert "-MaximumRedirection 0" in source
    assert "Write-Output $token" not in source
