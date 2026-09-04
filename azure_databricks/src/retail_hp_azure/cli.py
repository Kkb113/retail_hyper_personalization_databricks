"""Only local plan/schema and read-only verification are exposed; no deploy command."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from retail_hp_azure.config import schema_text
from retail_hp_azure.live import collect_live
from retail_hp_azure.safety import SafetyError, json_text, plan, preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "schema", "verify-live"])
    parser.add_argument("--root", type=Path, default=Path("azure_databricks"))
    parser.add_argument("--databricks-cli", type=Path)
    parser.add_argument("--record", action="store_true",
                        help="Write sanitized successful live evidence into the Azure subtree")
    args = parser.parse_args()
    try:
        if args.command == "schema":
            print(schema_text(), end="")
            return 0
        root = args.root.resolve()
        config = preflight(root, os.environ)
        result = plan(config, root)
        if args.command == "verify-live":
            if args.databricks_cli is None:
                raise SafetyError("--databricks-cli is required for live validation")
            result["verification"] = collect_live(root, config, args.databricks_cli)
            result["verified_at"] = datetime.now(UTC).isoformat()
            if args.record:
                output = root / "evidence/phase_01/live_validation.json"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json_text(result), encoding="utf-8", newline="\n")
        print(json_text(result), end="")
        return 0
    except (SafetyError, ValidationError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        # ValidationError contains input values. Never echo it into public evidence.
        print(json_text({"status": "FAIL", "deployment_allowed": False,
                         "reason": str(exc) if isinstance(exc, SafetyError) else
                         "Scope/config/authentication check failed; "
                         "review the local configuration and login privately."}),
              file=sys.stderr, end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
