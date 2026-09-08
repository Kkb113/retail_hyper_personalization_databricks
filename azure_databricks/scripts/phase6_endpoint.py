"""Operate only the existing scoped POC endpoint; never create implicit compute."""

import argparse
import json
from pathlib import Path

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase5 import REGISTERED_MODEL, inspect_registered_model
from retail_hp_azure.phase6 import ENDPOINT_NAME, admit_session, resolve_release
from retail_hp_azure.serving_client import authenticated_transport

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["inspect", "query", "stop", "start"])
parser.add_argument("--prior-cost-inr", type=float)
parser.add_argument("--ceiling-inr", type=float, default=250)
args = parser.parse_args()
context = CloudContext(apply=args.command != "inspect")
client = context.client
path = f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}"
endpoint = client.api_client.do("GET", path)
config = endpoint.get("config") or endpoint.get("pending_config")
entity = config["served_entities"][0]
assert entity["entity_name"] == REGISTERED_MODEL
assert entity["workload_type"] == "CPU" and entity["workload_size"] == "Small"
assert entity["scale_to_zero_enabled"] is True
if args.command == "start":
    assert args.prior_cost_inr is not None, "Reconcile prior spend before starting"
    _verify_budget(context)
    admit_session(
        hourly_cost_inr=31.3392,
        active_minutes=5,
        prior_cost_inr=args.prior_cost_inr,
        ceiling_inr=args.ceiling_inr,
        launch_cost_inr=2 * 7.8348,
    )
    version, _ = resolve_release(inspect_registered_model(context))
    assert int(entity["entity_version"]) == version, "Endpoint does not match Champion"
    root = Path(__file__).resolve().parents[2]
    accepted = json.loads(
        (root / "azure_databricks/evidence/phase_06/live_inference.json").read_text()
    )
    assert accepted["status"] == "PASS" and int(accepted["registered_version"]) == version
    assert endpoint["state"].get("suspend") == "STOPPED"
    client.api_client.do("POST", path + "/config:start")
    report = {"status": "START_SUBMITTED", "version": version}
elif args.command == "stop":
    if endpoint["state"].get("suspend") != "STOPPED":
        client.api_client.do("POST", path + "/config:stop")
    report = {"status": "STOP_REQUESTED"}
elif args.command == "query":
    assert endpoint["state"]["ready"] == "READY", "Explicitly warm the demo first"
    status, body = authenticated_transport(client.config.authenticate)(
        {
            "dataframe_records": [
                {"request_id": "manual-smoke", "request_type": "new_customer", "top_n": 10}
            ]
        },
        120,
    )
    assert status == 200 and len(body["predictions"]) == 10
    report = {"status": "PASS", "rows": 10}
else:
    report = {
        "state": endpoint["state"],
        "entity": {
            key: entity.get(key)
            for key in (
                "entity_name",
                "entity_version",
                "workload_type",
                "workload_size",
                "scale_to_zero_enabled",
                "state",
            )
        },
    }
print(json.dumps(report))
