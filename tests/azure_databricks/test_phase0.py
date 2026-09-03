from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pyarrow.parquet as parquet

ROOT = Path(__file__).resolve().parents[2]
AZURE_ROOT = ROOT / "azure_databricks"
sys.path.insert(0, str(AZURE_ROOT / "scripts"))

import build_phase0  # noqa: E402


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_scope_is_pinned_to_the_verified_azure_workspace():
    scope = read_json("azure_databricks/config/phase0_scope.json")
    assert scope["azure"]["resource_group"] == "Databricks"
    assert scope["azure"]["default_new_resource_region"] == "westus"
    assert scope["databricks"]["workspace_name"] == "intellify-databricks-demo"
    assert scope["databricks"]["workspace_region"] == "westus"
    assert scope["databricks"]["catalog"] == "intellify_databricks_demo"
    assert scope["data"] == {
        "classification": "synthetic_data",
        "production_data_allowed": False,
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in scope["fingerprints"].values()
    )


def test_paid_resource_creation_fails_closed():
    scope = read_json("azure_databricks/config/phase0_scope.json")
    costs = read_json("azure_databricks/config/cost_guardrails.json")
    assert scope["cost_policy"]["allow_paid_resource_creation"] is False
    assert scope["cost_policy"]["free_sku_must_not_fallback_to_paid"] is True
    assert costs["phase_0"] == {
        "cloud_mutations_allowed": False,
        "incremental_cost_usd": 0,
        "paid_resource_creation_allowed": False,
    }
    assert costs["budget"]["amount_usd"] is None
    assert costs["budget"]["notification_recipient_committed"] is False
    assert costs["budget"]["state"] == "BLOCKED_PENDING_OWNER_THRESHOLD_AND_RECIPIENT"
    assert not any(costs["default_optional_features"].values())
    assert costs["hard_limits"]["all_purpose_clusters"] == 0
    assert costs["hard_limits"]["gpu_endpoints"] == 0
    assert costs["hard_limits"]["vector_search_endpoints"] == 0


def test_every_proposed_resource_stays_in_the_approved_boundary():
    plan = read_json("azure_databricks/config/proposed_resources.json")
    assert plan["scope"] == {
        "azure_resource_group": "Databricks",
        "default_region": "westus",
        "workspace": "intellify-databricks-demo",
    }
    assert plan["resources"]
    for resource in plan["resources"]:
        assert resource["azure_resource_group"] == "Databricks"
        assert resource["workspace"] in {None, "intellify-databricks-demo"}
        assert resource["lifecycle_controls"]
        if resource["state"] != "existing_retain":
            assert resource["enabled_by_default"] is False
    search = next(item for item in plan["resources"] if item["kind"] == "azure_ai_search")
    assert "free_sku_only" in search["lifecycle_controls"]
    assert "never_fallback_to_basic" in search["lifecycle_controls"]
    vector = next(
        item for item in plan["resources"] if item["kind"] == "databricks_ai_search"
    )
    assert vector["state"] == "rejected_by_default"


def test_transfer_manifest_is_complete_and_reconciled():
    manifest = read_json("azure_databricks/contracts/transfer_manifest.json")
    assert manifest["classification"] == "synthetic_data"
    assert manifest["production_approved"] is False
    assert manifest["destination"]["workspace"] == "intellify-databricks-demo"
    assert manifest["destination"]["catalog"] == "intellify_databricks_demo"
    expected_summary = {
        "cross_role_overlap_count": 1,
        "data_file_count": 20,
        "declared_data_rows": 275630,
        "hash_mismatch_count": 0,
        "missing_file_count": 0,
        "model_file_count": 26,
        "observed_data_rows": 275630,
        "row_mismatch_count": 0,
        "unique_file_count": 45,
    }
    for key, value in expected_summary.items():
        assert manifest["summary"][key] == value
    assert manifest["summary"]["total_unique_bytes"] > 0
    paths = [item["path"] for item in manifest["files"]]
    assert len(paths) == len(set(paths)) == 45
    assert all(path.startswith("migration_assets/") for path in paths)
    logical_paths = [item["logical_path"] for item in manifest["files"]]
    assert len(logical_paths) == len(set(logical_paths)) == 45
    assert not any(path.startswith("migration_assets/") for path in logical_paths)
    assert sum(
        set(item["roles"]) == {"data", "model"} for item in manifest["files"]
    ) == 1


def test_every_manifest_file_exists_and_matches_its_hash_and_rows():
    manifest = read_json("azure_databricks/contracts/transfer_manifest.json")
    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file(), item["path"]
        assert path.stat().st_size == item["size_bytes"]
        assert sha256_file(path) == item["sha256"]
        if "data" in item["roles"]:
            rows = parquet.ParquetFile(path).metadata.num_rows
            contract = item["data_contract"]
            assert rows == contract["expected_rows"] == contract["observed_rows"]


def test_manifest_contains_the_full_approved_model_runtime():
    manifest = read_json("azure_databricks/contracts/transfer_manifest.json")
    actual = {
        item["logical_path"]: item["sha256"]
        for item in manifest["files"]
        if "model" in item["roles"]
    }
    expected = build_phase0._approved_model_hashes()
    assert actual == expected
    required_previous_omissions = {
        "artifacts/retrieval_v2/als_factors.npz",
        "artifacts/retrieval_v2/content_matrix.npz",
        "artifacts/cold_start_v1/inference_reference.joblib",
        "artifacts/ranker_v2/known_ranker.json",
        "artifacts/ranker_v2/lowhistory_ranker.json",
    }
    assert required_previous_omissions.issubset(actual)


def test_bundle_digests_are_reproducible():
    manifest = read_json("azure_databricks/contracts/transfer_manifest.json")
    assert build_phase0._bundle_digest(manifest["files"]) == manifest["bundle_sha256"]
    assert (
        build_phase0._bundle_digest(manifest["files"], "data")
        == manifest["data_bundle_sha256"]
    )
    assert (
        build_phase0._bundle_digest(manifest["files"], "model")
        == manifest["model_bundle_sha256"]
    )


def test_local_baseline_preserves_metrics_and_release_hold():
    baseline = read_json("azure_databricks/evidence/phase_00/local_baseline.json")
    metrics = baseline["selected_validation_metrics"]
    assert baseline["selected_candidate"] == "adaptive_router_v1"
    assert metrics["NDCGAt10"] == 0.1768528204702373
    assert metrics["RecallAt10"] == 0.2471537897023363
    assert metrics["HitRateAt10"] == 0.4438166980539862
    assert metrics["CustomerCoverage"] == 1.0
    assert baseline["release"]["decision"] == "HOLD_PENDING_FUTURE_HOLDOUT"
    assert baseline["release"]["release_approved"] is False
    assert all(baseline["review_findings"].values())


def test_live_inventory_matches_scope_and_contains_no_mutation():
    inventory = read_json("azure_databricks/evidence/phase_00/live_inventory.json")
    assert inventory["collection_mode"] == "read_only"
    assert inventory["raw_identifiers_committed"] is False
    assert inventory["cloud_mutations_performed"] is False
    assert all(inventory["scope_validation"]["fingerprint_checks"].values())
    assert inventory["scope_validation"]["all_fingerprints_match"] is True
    assert inventory["azure"]["subscription_state"] == "Enabled"
    assert inventory["azure"]["resource_group"]["name"] == "Databricks"
    assert inventory["azure"]["workspace"]["name"] == "intellify-databricks-demo"
    assert inventory["azure"]["workspace"]["location"] == "westus"
    assert inventory["azure"]["workspace"]["sku"].lower() == "premium"
    assert inventory["azure"]["workspace"]["unity_catalog_enabled"] is True
    assert "intellify_databricks_demo" in inventory["databricks"]["catalogs"]["names"]
    assert inventory["databricks"]["identity"]["principal_name_redacted"] is True
    endpoints = inventory["databricks"]["serving_endpoints"]
    assert endpoints["foundation_count"] > 0
    assert endpoints["custom_count"] == 0


def test_resource_inventory_contains_only_existing_phase0_resources():
    inventory = read_json("azure_databricks/resource_inventory.json")
    assert inventory["new_resources_created_in_phase_0"] == 0
    assert inventory["cloud_mutations_performed"] is False
    assert {item["name"] for item in inventory["items"]} == {
        "Databricks",
        "intellify-databricks-demo",
    }
    assert all(item["lifecycle"] == "existing_retain" for item in inventory["items"])


def test_phase0_outputs_do_not_commit_raw_ids_credentials_or_personal_email():
    prohibited = [
        re.compile(r"\bdapi[a-zA-Z0-9]{20,}\b"),
        re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ]
    output_paths = [*AZURE_ROOT.rglob("*.json"), *AZURE_ROOT.rglob("*.md")]
    assert output_paths
    for path in output_paths:
        text = path.read_text(encoding="utf-8")
        for pattern in prohibited:
            assert pattern.search(text) is None, f"Prohibited value in {path}"


def test_research_uses_only_primary_documentation_domains():
    text = (
        AZURE_ROOT / "evidence" / "phase_00" / "research_notes.md"
    ).read_text(encoding="utf-8")
    links = re.findall(r"https://[^)\s]+", text)
    assert len(links) >= 10
    assert {urlparse(link).hostname for link in links}.issubset(
        {"learn.microsoft.com", "mlflow.org"}
    )


def test_code_review_records_all_release_blockers():
    text = (
        AZURE_ROOT / "evidence" / "phase_00" / "codebase_review.md"
    ).read_text(encoding="utf-8")
    for finding in range(1, 10):
        assert f"P0-00{finding}" in text
    assert "HOLD_PENDING_FUTURE_HOLDOUT" in text
    assert "snapshot" in text.lower()
    assert "intellify_databricks_demo" in text


def test_phase0_markdown_is_not_ignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!/azure_databricks/" in gitignore


def test_repository_index_contains_only_the_azure_databricks_solution():
    tracked = build_phase0._git_output(["ls-files"]).splitlines()
    assert tracked
    allowed_roots = {
        ".editorconfig",
        ".gitattributes",
        ".github",
        ".gitignore",
        "CONTRIBUTING.md",
        "README.md",
        "SECURITY.md",
        "azure_databricks",
        "azure_databricks_implementation.md",
        "migration_assets",
        "pyproject.toml",
        "requirements-dev.lock",
        "tests",
    }
    observed_roots = {path.split("/", 1)[0] for path in tracked}
    assert observed_roots <= allowed_roots
    assert "databricks.yml" not in tracked
    assert not any(
        path.startswith("tests/")
        for path in tracked
        if not path.startswith("tests/azure_databricks/")
    )


def test_migration_contracts_are_self_contained():
    data_contract = read_json("azure_databricks/contracts/source_asset_inventory.json")
    model_contract = read_json("azure_databricks/contracts/approved_model_hashes.json")
    assert data_contract["contract_version"] == "azure_source_asset_inventory_v1"
    assert data_contract["integrity_contract"]["expected_data_asset_count"] == 20
    assert len(data_contract["data_assets"]) == 20
    assert model_contract["contract_version"] == "azure_model_payload_hashes_v1"
    assert len(model_contract["assets"]) == 26


def test_phase0_acceptance_is_complete_but_paid_deployment_remains_blocked():
    acceptance = read_json(
        "azure_databricks/evidence/phase_00/phase0_acceptance.json"
    )
    assert acceptance["engineering_status"] == "PASS"
    assert acceptance["phase_1_code_work_allowed"] is True
    assert acceptance["paid_resource_creation_allowed"] is False
    assert acceptance["cloud_mutations_performed"] is False
    assert acceptance["release_status"] == "POC_ONLY"
    assert all(value.startswith("PASS") for value in acceptance["tests"].values())
