"""Phase 10 metadata-only bootstrap. Intentionally has NO start/deploy command."""

import argparse
import json

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.safety import require

APP_NAME = "retail-hp-poc-app"


def inspect_app(context):
    apps = [app for app in context.client.apps.list() if app.name == APP_NAME]
    require(len(apps) <= 1, "Ambiguous project App")
    if not apps:
        return {"name": APP_NAME, "exists": False, "compute_started": False}
    app = apps[0]
    state = (
        app.compute_status.state.value
        if app.compute_status and app.compute_status.state
        else "UNKNOWN"
    )
    return {
        "name": APP_NAME,
        "exists": True,
        "compute_state": state,
        "size": app.compute_size.value if app.compute_size else "UNKNOWN",
        "dedicated_identity": bool(app.service_principal_client_id),
        "active_deployment": bool(app.active_deployment),
    }


def bootstrap(context):
    from databricks.sdk.service.apps import App, ComputeSize

    require(context.apply, "Explicit apply context required")
    existing = inspect_app(context)
    if not existing["exists"]:
        context.client.apps.create(
            App(
                name=APP_NAME,
                compute_size=ComputeSize.MEDIUM,
                description="Synthetic retail POC. Requires a verified bounded stop controller.",
                user_api_scopes=["sql", "model-serving"],
            ),
            no_compute=True,
        )
    result = inspect_app(context)
    require(result.get("compute_state") in {"STOPPED", "STOPPING"}, "App not stopped")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "bootstrap-stopped"])
    args = parser.parse_args()
    context = CloudContext(apply=args.command == "bootstrap-stopped")
    print(json.dumps(bootstrap(context) if context.apply else inspect_app(context), indent=2))
