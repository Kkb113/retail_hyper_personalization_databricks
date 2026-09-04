from __future__ import annotations

import ast
import copy
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from retail_hp_azure.app import create_app
from retail_hp_azure.config import HOST, ROOT_PATH, PocConfig, load_config, schema_text
from retail_hp_azure.live import validate_plan_output, verify_azure
from retail_hp_azure.safety import (
    FINGERPRINTS,
    REQUIRED_TAGS,
    SafetyError,
    bundle_variables,
    check_environment,
    check_fingerprint,
    expected_bundle,
    plan,
    preflight,
    validate_proposed_mutation,
    validate_resolved,
)

ROOT = Path(__file__).resolve().parents[2]
AZURE = ROOT / "azure_databricks"


@pytest.fixture
def config():
    return load_config(AZURE / "config/poc.json")


@pytest.fixture
def sandbox(tmp_path, config):
    root = tmp_path / "azure_databricks"
    (root / "config").mkdir(parents=True)
    (root / "config/poc.json").write_text(config.model_dump_json(), encoding="utf-8")
    (root / "databricks.yml").write_text(yaml.safe_dump(expected_bundle(config)), encoding="utf-8")
    (root / "bundle_files").mkdir()
    (root / "bundle_files/README.md").write_text(
        (AZURE / "bundle_files/README.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    return root


def test_authoritative_contract_matches_schema_and_source(config):
    assert preflight(AZURE, {}) == config
    schema = json.loads(schema_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(config.model_dump(), schema)
    assert (AZURE / "config/poc.schema.json").read_text(encoding="utf-8") == schema_text()
    assert config.cost.monthly_target == config.cost.internal_stop_target + config.cost.reserve
    old = json.loads((AZURE / "config/phase0_scope.json").read_text())
    assert old["fingerprints"] == FINGERPRINTS
    assert old["required_tags"] == REQUIRED_TAGS


@pytest.mark.parametrize(("section", "key", "value"), [
    ("scope", "resource_group", "another-group"),
    ("scope", "host", "https://example.invalid"),
    ("scope", "catalog", "retail_hyper_personalization"),
    ("scope", "region", "eastus"),
    ("scope", "workspace", "legacy"),
    ("cost", "paid_resource_creation_allowed", True),
    ("cost", "cloud_mutations_allowed", True),
    ("cost", "free_to_paid_fallback", True),
    ("cost", "monthly_target", 12001),
    ("cost", "monthly_target", "12000"),
    ("cost", "human_idle_limit_minutes", 30),
    ("cost", "budget_deployed", True),
    ("features", "app", "false"),
    ("features", "app", 0),
    ("features", "extra_feature", False),
    ("names", "app", "legacy-app"),
    ("names", "ml_schema", "../outside"),
])
def test_invalid_policy_fails_closed(config, section, key, value):
    changed = config.model_dump()
    changed[section][key] = value
    with pytest.raises(ValidationError):
        PocConfig.model_validate(changed)


@pytest.mark.parametrize("feature", [
    "jobs", "sql_warehouse", "model_serving", "llm", "app", "lakebase",
    "vector_search", "azure_ai_search",
])
def test_all_features_must_remain_disabled(config, feature):
    changed = config.model_dump()
    changed["features"][feature] = True
    with pytest.raises(ValidationError):
        PocConfig.model_validate(changed)


@pytest.mark.parametrize("override", [
    "DATABRICKS_BUNDLE_VAR_catalog", "DATABRICKS_CONFIG_PROFILE", "DATABRICKS_CONFIG_FILE",
    "DATABRICKS_BUNDLE_ENGINE", "DATABRICKS_BUNDLE_ROOT", "DATABRICKS_TOKEN",
    "DATABRICKS_HOST", "databricks_host",
])
def test_environment_overrides_fail(override):
    with pytest.raises(SafetyError):
        check_environment({override: "unapproved"})


def test_only_explicit_azure_cli_environment_is_accepted():
    check_environment({"DATABRICKS_HOST": HOST, "DATABRICKS_AUTH_TYPE": "azure-cli"})


@pytest.mark.parametrize(("key", "value"), [
    ("resources", {"jobs": {"unsafe": {}}}),
    ("include", ["../databricks.yml"]),
    ("artifacts", {"wheel": {"build": "echo unsafe"}}),
    ("scripts", {"deploy": {"content": "echo unsafe"}}),
    ("sync", {"paths": ["../"]}),
    ("targets", {"dev": {"workspace": {"host": HOST}}}),
    ("run_as", {"service_principal_name": "unknown"}),
])
def test_bundle_side_effects_and_alternate_targets_rejected(sandbox, config, key, value):
    bundle = expected_bundle(config)
    bundle[key] = value
    (sandbox / "databricks.yml").write_text(yaml.safe_dump(bundle), encoding="utf-8")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})


def test_duplicate_yaml_keys_rejected(sandbox):
    with (sandbox / "databricks.yml").open("a", encoding="utf-8") as handle:
        handle.write("\nresources: {}\n")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})


def test_local_variable_overrides_rejected(sandbox):
    overrides = sandbox / ".databricks/bundle/poc/variable-overrides.json"
    overrides.parent.mkdir(parents=True)
    overrides.write_text("{}", encoding="utf-8")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})


def test_missing_tags_rejected(sandbox, config):
    changed = config.model_dump()
    changed["tags"].pop("cost-profile")
    (sandbox / "config/poc.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})


def test_neither_free_nor_paid_mutation_is_authorized(config):
    for sku in ("Free", "Premium"):
        with pytest.raises(SafetyError, match="All cloud mutations"):
            validate_proposed_mutation({"resource_group": "Databricks", "workspace_host": HOST,
                                        "tags": REQUIRED_TAGS, "sku": sku}, config)


@pytest.mark.parametrize("key", ["resource_group", "workspace_host", "tags"])
def test_wrong_mutation_boundary_is_rejected(config, key):
    resource = {"resource_group": "Databricks", "workspace_host": HOST, "tags": REQUIRED_TAGS}
    resource.pop(key)
    with pytest.raises(SafetyError):
        validate_proposed_mutation(resource, config)


def test_resolved_cli_config_is_verified(config):
    resolved_root = ROOT_PATH.replace("${workspace.current_user.userName}", "test-user")
    document = {"workspace": {"host": HOST, "root_path": resolved_root,
                              "current_user": {"userName": "test-user"}},
                "bundle": {"target": "poc"}, "resources": {},
                "variables": {key: {"value": value}
                              for key, value in bundle_variables(config).items()}}
    validate_resolved(document, config)
    for section, key, value in [("workspace", "host", "https://example.invalid"),
                                ("workspace", "root_path", "/Workspace/other"),
                                ("bundle", "target", "dev"),
                                ("variables", "enable_app", {"value": "true"})]:
        changed = copy.deepcopy(document)
        changed[section][key] = value
        with pytest.raises(SafetyError):
            validate_resolved(changed, config)


def test_plan_is_reproducible_and_has_no_deployment(config):
    first = plan(config, AZURE)
    assert first == plan(config, AZURE)
    assert first == json.loads((AZURE / "evidence/phase_01/local_plan.json").read_text())
    assert first["actions"] == []
    assert first["deployment_allowed"] is False
    assert first["new_billable_resources"] == 0


def test_plan_output_is_fail_closed():
    valid = {"plan_version": 2, "cli_version": "1.15.0", "plan": {}}
    validate_plan_output(valid)
    for invalid in ({}, {**valid, "plan": None}, {**valid, "extra": "unknown"},
                    {**valid, "plan_version": 3}, {**valid, "cli_version": "1.0.0"},
                    {**valid, "plan": {"resources.jobs.test": {"action": "create"}}}):
        with pytest.raises(SafetyError):
            validate_plan_output(invalid)


def test_wrong_account_stops_before_any_resource_reads(config):
    calls = []

    def runner(arguments):
        calls.append(arguments)
        return {"id": "unapproved", "tenantId": "unapproved", "state": "Enabled"}

    with pytest.raises(SafetyError, match="subscription"):
        verify_azure(config, runner, "az")
    assert len(calls) == 1
    assert calls[0][1:3] == ["account", "show"]


def test_all_fingerprint_mismatches_fail():
    for key in FINGERPRINTS:
        with pytest.raises(SafetyError):
            check_fingerprint(key, "not-the-approved-resource")


def test_azure_reads_are_explicitly_scoped_and_tags_are_observed(config, monkeypatch):
    import retail_hp_azure.live as live_module

    checks = []
    monkeypatch.setattr(live_module, "check_fingerprint", lambda key, value: checks.append(key))
    calls = []
    responses = iter([
        {"id": "synthetic-subscription", "tenantId": "synthetic-tenant", "state": "Enabled"},
        {"id": "synthetic-group", "name": "Databricks"},
        {"id": "synthetic-workspace", "name": config.scope.workspace, "location": "westus",
         "sku": {"name": "premium"}, "tags": {},
         "properties": {"workspaceId": "synthetic-workspace-id",
                        "workspaceUrl": HOST.removeprefix("https://"),
                        "provisioningState": "Succeeded"}},
        [{"type": "Microsoft.Databricks/workspaces"}],
        [],
    ])

    def runner(arguments):
        calls.append(arguments)
        return next(responses)

    result = verify_azure(config, runner, "az")
    assert set(checks) == set(FINGERPRINTS)
    assert result["budget_count"] == 0
    assert result["existing_workspace_tags_match_policy"] is False
    assert result["existing_tags_modified"] is False
    for call in calls[1:]:
        assert call[call.index("--subscription") + 1] == "synthetic-subscription"
        group_flag = "--name" if call[1] == "group" else "--resource-group"
        assert call[call.index(group_flag) + 1] == "Databricks"


def test_sync_boundary_rejects_unexpected_files_and_changed_marker(sandbox):
    injected = sandbox / "bundle_files/private.txt"
    injected.write_text("not eligible for sync", encoding="utf-8")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})
    injected.unlink()
    (sandbox / "bundle_files/README.md").write_text("changed marker", encoding="utf-8")
    with pytest.raises(SafetyError):
        preflight(sandbox, {})


def test_live_evidence_matches_current_bundle_and_does_not_claim_deployment(config):
    evidence = json.loads((AZURE / "evidence/phase_01/live_validation.json").read_text())
    assert evidence["source_hashes"] == plan(config, AZURE)["source_hashes"]
    assert evidence["verification"]["bundle_validation"] == "PASS_STRICT"
    assert evidence["verification"]["bundle_plan"] == "PASS_NO_RESOURCE_ACTIONS"
    assert evidence["verification"]["azure"]["scope_fingerprints_match"] is True
    assert evidence["verification"]["cloud_mutations_performed"] is False
    assert evidence["deployment_allowed"] is False


def test_cli_rejects_deploy_and_redacts_invalid_config(sandbox, monkeypatch, capsys):
    from retail_hp_azure.cli import main

    monkeypatch.setattr("sys.argv", ["retail-hp-preflight", "deploy"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    capsys.readouterr()
    (sandbox / "config/poc.json").write_text('{"private_input":"do-not-echo"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["retail-hp-preflight", "plan", "--root", str(sandbox)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "do-not-echo" not in captured.err
    assert json.loads(captured.err)["deployment_allowed"] is False


def test_ci_cannot_skip_runtime_gate_and_still_report_success():
    workflow = (ROOT / ".github/workflows/azure-phase0-ci.yml").read_text()
    assert "needs: [runtime-smoke]" in workflow
    assert "if: ${{ always() }}" in workflow
    assert 'test "$RUNTIME_RESULT" = success' in workflow


def test_no_private_runtime_cache_is_tracked():
    git = shutil.which("git")
    assert git
    tracked = subprocess.run(  # noqa: S603 - fixed read-only Git command
        [git, "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    for name in tracked.splitlines():
        assert not {".databricks", ".venv", "__pycache__", "build", "dist"}.intersection(
            Path(name).parts,
        )
        assert not name.endswith(".local.json")


def test_app_is_honest_about_readiness_and_has_no_fake_recommendations():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "alive", "phase": "1"}
        assert client.get("/health/ready").status_code == 503
        assert client.get("/version").json()["release"] == "POC_ONLY"
        assert client.post("/recommendations", json={"user_id": "synthetic"}).status_code == 404


def test_package_has_no_legacy_or_cloud_side_effect_imports():
    forbidden = {"src", "agent", "app", "custom_app", "streamlit", "streamlit_app",
                 "pyodbc", "boto3", "pyspark", "mlflow"}
    for source in (AZURE / "src/retail_hp_azure").glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(item.name.split(".")[0] not in forbidden for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden


def test_build_tool_pins_include_verified_security_fixes():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["build-system"]["requires"] == ["setuptools==83.0.0", "wheel==0.46.2"]
