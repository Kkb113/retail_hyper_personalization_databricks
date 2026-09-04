"""Generate deterministic local contracts only. No Azure or Databricks calls."""

from __future__ import annotations

import os
from pathlib import Path

from retail_hp_azure.config import schema_text
from retail_hp_azure.safety import json_text, plan, preflight

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = preflight(ROOT, os.environ)
    (ROOT / "config" / "poc.schema.json").write_text(schema_text(), encoding="utf-8", newline="\n")
    output = ROOT / "evidence" / "phase_01"
    output.mkdir(exist_ok=True, parents=True)
    (output / "local_plan.json").write_text(
        json_text(plan(config, ROOT)), encoding="utf-8", newline="\n",
    )
    print("Phase 1 schema and resource-free plan generated; no cloud operations")


if __name__ == "__main__":
    main()
