"""Fail-closed App startup: pinned files, private entitlements and one-use launch claim."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from retail_hp_azure.config import HOST
from retail_hp_azure.phase8 import ToolContext
from retail_hp_azure.phase8_semantic import SemanticIndex
from retail_hp_azure.phase10_app import create_app
from retail_hp_azure.phase10_runtime import WorkbenchRuntime
from retail_hp_azure.safety import require

STATE_VOLUME = "/Volumes/intellify_databricks_demo/agent/app_launches"


class Launch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket: str = Field(pattern=r"^[0-9a-f]{32}$")
    expires: int
    warehouse_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    entitlements: list[ToolContext] = Field(min_length=1, max_length=20)


def verify_files(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    require(0 < len(manifest) < 100, "Invalid release manifest")
    for name, digest in manifest.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root.resolve()) and path.is_file(), "Release path drift")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Release hash mismatch")
    require(
        "static/index.html" in manifest and "semantic.json" in manifest,
        "Incomplete release manifest",
    )


def claim_launch(client: Any, launch: Launch, *, now: float) -> None:
    require(now < launch.expires <= now + 1800, "Launch lease invalid or expired")
    # Files API overwrite=False is create-only. A duplicate or uncertain outcome
    # fails startup; it never resets the per-launch allowance after an App restart.
    client.files.upload(
        f"{STATE_VOLUME}/{launch.ticket}.json",
        io.BytesIO(json.dumps({"claimed_at": now, "expires": launch.expires}).encode()),
        overwrite=False,
    )


def build_app(root: Path, client: Any, config: str, actor_secret: str) -> Any:
    verify_files(root)
    launch = Launch.model_validate_json(config)
    require(
        client.config.host.rstrip("/") == HOST and client.config.auth_type == "oauth-m2m",
        "Native App identity required",
    )
    require(len(actor_secret) >= 64, "Actor secret unavailable")
    entitlements = {entry.subject: entry for entry in launch.entitlements}
    require(len(entitlements) == len(launch.entitlements), "Duplicate actor mapping")
    index = SemanticIndex(json.loads((root / "semantic.json").read_text()))
    claim_launch(client, launch, now=time.time())
    runtime = WorkbenchRuntime(
        app_client=client,
        warehouse_id=launch.warehouse_id,
        entitlements=entitlements,
        actor_secret=actor_secret.encode(),
        ledger=root / ".runtime" / f"{launch.ticket}.json",
        index=index,
        lease_expires=launch.expires,
        trace_sink=lambda record: print(
            json.dumps(
                {
                    "event": "retail_tool",
                    "status": record.get("status"),
                    "action": record.get("action"),
                }
            )
        ),
    )
    return create_app(runtime, static_dir=root / "static", lease_expires=launch.expires)


def main() -> None:
    import uvicorn
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    # Deliberately no Azure CLI/developer credential fallback.
    client = WorkspaceClient(
        config=Config(host=HOST, auth_type="oauth-m2m", http_timeout_seconds=20)
    )
    app = build_app(
        Path.cwd(), client, os.environ["RETAIL_HP_LAUNCH"], os.environ["RETAIL_HP_ACTOR_SECRET"]
    )
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 -- only behind authenticated Databricks App proxy
        port=int(os.environ["DATABRICKS_APP_PORT"]),
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
