"""Prepare a private, immutable App package while compute stays stopped."""

import argparse
import hashlib
import io
import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from databricks.sdk.errors import ResourceAlreadyExists
from databricks.sdk.service.catalog import PermissionsChange, Privilege, VolumeType
from databricks.sdk.service.workspace import ExportFormat, ImportFormat
from retail_hp_azure.phase2 import CATALOG, CloudContext
from retail_hp_azure.phase8_semantic import VOLUME, SemanticIndex
from retail_hp_azure.phase10_release import STATE_VOLUME
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "retail-hp-app-private"
REMOTE = "/Workspace/Shared/retail-hp-poc-app-releases"


def prepare(context, *, active_window=False):
    require(context.apply, "Explicit apply required")
    client = context.client
    app = client.apps.get("retail-hp-poc-app")
    if active_window:
        import time

        from phase10_live import STATE, automation

        state = json.loads(STATE.read_text())
        require(state["deadline"] > time.time() + 240, "Release window too short")
        require(
            automation(context, "GET", "/jobs/" + state["stop_job"])["properties"]["status"]
            == "Running",
            "Independent controller must remain armed",
        )
    else:
        require(app.compute_status.state.value == "STOPPED", "App must be stopped")
    warehouse = [w for w in client.warehouses.list() if w.name == "retail-hp-poc-sql"]
    require(len(warehouse) == 1, "Warehouse drift")
    require(active_window or warehouse[0].state.value == "STOPPED", "Warehouse running")
    sources = [
        json.loads(p.read_text())
        for p in (ROOT / "azure_databricks/evidence/phase_08").glob("generation_*.json")
    ]
    accepted = [entry for entry in sources if entry["status"] == "PASS"]
    require(len(accepted) == 1, "Semantic release ambiguity")
    version = accepted[0]["generation"]["snapshot_version"]
    # Reuse only the previous immutable package's verified, unchanged public vectors.
    previous_control = ROOT / "build/phase10-release.local.json"
    snapshot = None
    if previous_control.exists():
        old = json.loads(previous_control.read_text())
        cached = Path(old["local_package"]).resolve()
        require(cached.is_relative_to((ROOT / "build").resolve()), "Cache path outside build")
        manifest = json.loads((cached / "manifest.json").read_text())
        require(
            hashlib.sha256((cached / "manifest.json").read_bytes()).hexdigest() == old["release"],
            "Cached release hash drift",
        )
        candidate = (cached / "semantic.json").read_bytes()
        require(
            hashlib.sha256(candidate).hexdigest() == manifest["semantic.json"],
            "Cached semantic hash drift",
        )
        if SemanticIndex(json.loads(candidate)).version == version:
            snapshot = candidate
    if snapshot is None:
        with client.files.download(f"{VOLUME}/{version}.json").contents as stream:
            snapshot = stream.read(80_000_001)
    require(len(snapshot) <= 80_000_000, "Semantic snapshot too large")
    SemanticIndex(json.loads(snapshot))
    wheels = list((ROOT / "build/phase10-wheels").glob("retail_hp_azure_databricks-*.whl"))
    require(len(wheels) == 1, "Build exactly one release wheel first")
    # Build directory is a new temporary path inside the ignored build root.
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="phase10-release-", dir=ROOT / "build"))
    shutil.copytree(ROOT / "azure_databricks/app/frontend/dist", directory / "static")
    shutil.copyfile(ROOT / "azure_databricks/app/app.yaml", directory / "app.yaml")
    shutil.copyfile(ROOT / "azure_databricks/app/bootstrap.py", directory / "bootstrap.py")
    shutil.copyfile(wheels[0], directory / wheels[0].name)
    (directory / "semantic.json").write_bytes(snapshot)
    for offset in range(0, len(snapshot), 8_000_000):
        (directory / f"semantic-part-{offset // 8_000_000:03d}").write_bytes(
            snapshot[offset : offset + 8_000_000]
        )
    lock = (ROOT / "azure_databricks/environments/app.lock").read_text()
    # Databricks' pip installer is Python 3.11. Use its supported uv strategy
    # to retain the project's tested Python 3.12 runtime without changing SKU.
    pins = re.findall(r"^([A-Za-z0-9_.-]+==[^\s;\\]+)", lock, re.MULTILINE)
    require(pins, "Missing pinned App dependencies")
    project = (
        '[project]\nname = "retail-hp-app-release"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12,<3.13"\n'
        'dependencies = ["retail-hp-azure-databricks[app]==0.1.0"]\n\n'
        "[tool.uv]\nconstraint-dependencies = " + json.dumps(pins) + "\n\n"
        "[tool.uv.sources]\nretail-hp-azure-databricks = { path = "
        + json.dumps(wheels[0].name)
        + " }\n"
    )
    (directory / "pyproject.toml").write_text(project)
    uv = shutil.which("uv")
    require(uv is not None, "Install uv before packaging")
    subprocess.run([uv, "lock", "--directory", str(directory)], check=True)  # noqa: S603
    require((directory / "uv.lock").is_file(), "Missing deployment lock")
    manifest = {
        str(path.relative_to(directory)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    release = hashlib.sha256(manifest_bytes).hexdigest()
    (directory / "manifest.json").write_bytes(manifest_bytes)
    principal = app.service_principal_client_id
    require(principal, "Missing App identity")
    volumes = list(client.volumes.list(CATALOG, "agent"))
    if not any(v.name == "app_launches" for v in volumes):
        client.volumes.create(
            CATALOG,
            "agent",
            "app_launches",
            VolumeType.MANAGED,
            comment="Retail App single-use launch claims; no customer data",
        )
    for kind, name, privileges in [
        ("catalog", CATALOG, [Privilege.USE_CATALOG]),
        ("schema", CATALOG + ".agent", [Privilege.USE_SCHEMA]),
        (
            "volume",
            CATALOG + ".agent.app_launches",
            [Privilege.READ_VOLUME, Privilege.WRITE_VOLUME],
        ),
    ]:
        client.grants.update(
            kind, name, changes=[PermissionsChange(principal=principal, add=privileges)]
        )
    if not any(s.name == SCOPE for s in client.secrets.list_scopes()):
        client.secrets.create_scope(SCOPE)
    keys = {s.key for s in client.secrets.list_secrets(SCOPE)}
    if "actor" not in keys:
        client.secrets.put_secret(SCOPE, "actor", string_value=secrets.token_hex(32))
    if "launch" not in keys:
        client.secrets.put_secret(SCOPE, "launch", string_value="{}")  # Locked until armed.
    resources = [
        {"name": "actor-secret", "secret": {"scope": SCOPE, "key": "actor", "permission": "READ"}},
        {
            "name": "launch-config",
            "secret": {"scope": SCOPE, "key": "launch", "permission": "READ"},
        },
        {"name": "sql", "sql_warehouse": {"id": warehouse[0].id, "permission": "CAN_USE"}},
        {
            "name": "recommender",
            "serving_endpoint": {"name": "retail-hp-poc-recommender", "permission": "CAN_VIEW"},
        },
    ]
    require(
        all(r.name in {x["name"] for x in resources} for r in app.resources or []),
        "Unexpected existing App resource binding",
    )
    client.api_client.do(
        "PATCH",
        "/api/2.0/apps/retail-hp-poc-app",
        body={
            "resources": resources,
            "user_api_scopes": ["sql", "model-serving"],
        },
    )
    remote = REMOTE + "/" + release
    client.workspace.mkdirs(remote)
    for path in directory.rglob("*"):
        if path.is_file() and path.name != "semantic.json":
            require(path.stat().st_size < 10_485_760, "App export file too large")
            relative = str(path.relative_to(directory)).replace("\\", "/")
            target = remote + "/" + relative
            client.workspace.mkdirs(target.rsplit("/", 1)[0])
            try:
                client.workspace.upload(
                    target, io.BytesIO(path.read_bytes()), format=ImportFormat.AUTO, overwrite=False
                )
            except ResourceAlreadyExists:
                with client.workspace.download(target, format=ExportFormat.AUTO) as stream:
                    require(stream.read() == path.read_bytes(), "Immutable remote file differs")
    me = client.current_user.me()
    require(me.id and me.active, "Operator identity unavailable")
    control = {
        "customer_context_version": "customer_context_v2",
        "release": release,
        "source_path": remote,
        "warehouse_id": warehouse[0].id,
        "operator_id": me.id,
        "state_volume": STATE_VOLUME,
        "local_package": str(directory),
    }
    (ROOT / "build/phase10-release.local.json").write_text(json.dumps(control))
    return {
        "status": "PREPARED_IN_BOUNDED_WINDOW" if active_window else "PREPARED_COMPUTE_STOPPED",
        "release": release,
        "files": len(manifest) + 1,
        "new_billable_compute": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--active-window", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                CloudContext(apply=True, direct_operator_token=True),
                active_window=args.active_window,
            ),
            indent=2,
        )
    )
