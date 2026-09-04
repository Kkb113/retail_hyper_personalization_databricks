"""Run with Python -I after wheel install; fail if local legacy modules leak in."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import retail_hp_azure
from retail_hp_azure.config import PocConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=["jobs", "serving", "agent", "app"], required=True)
    args = parser.parse_args()
    origin = Path(retail_hp_azure.__file__).resolve()
    assert "site-packages" in origin.parts, "Must test a wheel install, not an editable source tree"
    assert PocConfig.model_json_schema()["additionalProperties"] is False
    for module in ("custom_app", "streamlit_app", "agent", "src"):
        assert importlib.util.find_spec(module) is None, f"Legacy module leaked: {module}"
    if args.runtime in {"jobs", "agent"}:
        from databricks.sdk import WorkspaceClient

        assert WorkspaceClient is not None  # Import only: no client or network operation.
    if args.runtime == "app":
        from retail_hp_azure.app import create_app

        assert create_app().title == "Retail HP Azure POC"
    assert "mlflow" not in sys.modules
    print(f"PASS: {args.runtime} wheel imports cleanly without legacy runtime or cloud calls")


if __name__ == "__main__":
    main()
