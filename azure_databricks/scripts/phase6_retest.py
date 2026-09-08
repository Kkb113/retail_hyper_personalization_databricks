"""Single approved INR400 retest: update existing endpoint, cold-resume, promote."""

import argparse
import json
import time
from pathlib import Path

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase5 import REGISTERED_MODEL
from retail_hp_azure.phase6 import ENDPOINT_NAME, admit_session, endpoint_configuration
from retail_hp_azure.serving_client import authenticated_transport

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["deploy", "start-test", "wake", "promote"])
args = parser.parse_args()
context = CloudContext(apply=True)
client = context.client
root = Path(__file__).resolve().parents[2]
evidence = root / "azure_databricks/evidence/phase_06"
path = f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}"
endpoint = client.api_client.do("GET", path)
assert endpoint["config"]["served_entities"][0]["entity_name"] == REGISTERED_MODEL
if args.command == "deploy":
    assert endpoint["state"]["suspend"] == "STOPPED"
    assert str(endpoint["config"]["served_entities"][0]["entity_version"]) == "2"
    aliases = client.registered_models.get(REGISTERED_MODEL, include_aliases=True).aliases
    assert any(a.alias_name.casefold() == "candidate" and int(a.version_num) == 3 for a in aliases)
    _verify_budget(context)
    reserve = admit_session(
        hourly_cost_inr=31.3392,
        active_minutes=5,
        prior_cost_inr=300,
        launch_cost_inr=4 * 7.8348,
        ceiling_inr=400,
    )
    config = endpoint_configuration(3)["config"]
    config["served_entities"][0]["environment_vars"] = {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    client.api_client.do("PUT", path + "/config", body=config)
    report = {
        "status": "SUBMITTED",
        "version": 3,
        "approved_phase_allowance_inr": 400,
        "prior_and_batch_reserve_inr": 300,
        "endpoint_reserve_inr": reserve,
        "launches_reserved": 2,
        "hard_invoice_cap_guaranteed": False,
        "started_epoch": time.time(),
    }
elif args.command == "start-test":
    assert endpoint["state"]["suspend"] == "STOPPED"
    assert endpoint["state"]["config_update"] == "NOT_UPDATING"
    assert str(endpoint["config"]["served_entities"][0]["entity_version"]) == "3"
    assert (
        json.loads((evidence / "retest_deploy.json").read_text())["approved_phase_allowance_inr"]
        == 400
    )
    assert not (evidence / "retest_start-test.json").exists(), "Only one admitted initial launch"
    client.api_client.do("POST", path + "/config:start")
    report = {"status": "START_SUBMITTED", "version": 3, "launch_number": 1}
elif args.command == "wake":
    assert json.loads((evidence / "live_inference.json").read_text())["status"] == "PASS"
    assert endpoint["state"]["suspend"] == "STOPPED"
    assert not (evidence / "cold_resume.json").exists(), "Only one admitted cold-resume test"
    assert str(endpoint["config"]["served_entities"][0]["entity_version"]) == "3"
    started = time.monotonic()
    client.api_client.do("POST", path + "/config:start")
    report = {"status": "FAIL", "method": "EXPLICIT_STOP_START", "native_idle_timer_tested": False}
    try:
        while time.monotonic() - started < 300:
            current = client.api_client.do("GET", path)
            if current["state"]["ready"] == "READY":
                break
            print(
                json.dumps({"waiting_for_resume_seconds": round(time.monotonic() - started)}),
                flush=True,
            )
            time.sleep(15)
        else:
            raise TimeoutError("Explicit resume did not finish within five minutes")
        status, body = authenticated_transport(client.config.authenticate)(
            {
                "dataframe_records": [
                    {"request_id": "wake-smoke", "request_type": "new_customer", "top_n": 10}
                ]
            },
            120,
        )
        assert status == 200 and len(body["predictions"]) == 10
        report.update(status="PASS", elapsed_seconds=round(time.monotonic() - started, 3), rows=10)
    finally:
        client.api_client.do("POST", path + "/config:stop")
        (evidence / "cold_resume.json").write_text(json.dumps(report, indent=2) + "\n")
elif args.command == "promote":
    for file in ("live_inference.json", "cold_resume.json"):
        assert json.loads((evidence / file).read_text())["status"] == "PASS"
    batch = json.loads((evidence / "batch_run.json").read_text())["validation"]
    assert batch["status"] == "PASS" and batch["registered_model_version"] == "3"
    assert endpoint["state"]["suspend"] == "STOPPED"
    import mlflow
    from mlflow.tracking import MlflowClient
    from retail_hp_azure.phase6 import configure_mlflow_identity

    configure_mlflow_identity()
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    tracking = MlflowClient()
    tracking.set_model_version_tag(
        REGISTERED_MODEL, "3", "retail_hp_poc_status", "APPROVED_SYNTHETIC_ONLY"
    )
    client.registered_models.set_alias(REGISTERED_MODEL, "Champion", 3)
    report = {
        "status": "PASS",
        "champion_version": 3,
        "production_approved": False,
        "release_scope": "SYNTHETIC_POC_DEMO100",
        "endpoint_stopped": True,
    }
if args.command != "wake":
    (evidence / ("retest_" + args.command + ".json")).write_text(
        json.dumps(report, indent=2) + "\n"
    )
print(json.dumps(report))
