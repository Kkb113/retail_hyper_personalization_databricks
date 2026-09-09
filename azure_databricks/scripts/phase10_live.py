"""One bounded release window, armed independent stop job, no recurring resources."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import requests
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget, _verify_warehouse_contract
from retail_hp_azure.phase2_identity import IDENTITY_NAME
from retail_hp_azure.phase10_release import Launch, verify_files
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "build/phase10-live.local.json"
LEDGER = ROOT / "build/phase10-cost.local.json"
DEMO_LEDGER = ROOT / "build/phase10-owner-demo.local.json"
CHAT_LEDGER = ROOT / "build/phase10-chat-validation.local.json"
APP = "retail-hp-poc-app"
ENDPOINT = "retail-hp-poc-recommender"


def automation(context, method, suffix, body=None):
    token = context.az_json(
        [
            "account",
            "get-access-token",
            "--subscription",
            context.subscription,
            "--resource",
            "https://management.azure.com/",
        ]
    )["accessToken"]
    url = (
        "https://management.azure.com"
        + context.group["id"]
        + "/providers/Microsoft.Automation/automationAccounts/retail-hp-poc-shutdown"
        + suffix
        + "?api-version=2024-10-23"
    )
    result = requests.request(
        method,
        url,
        headers={"Authorization": "Bearer " + token},
        json=body,
        timeout=30,
        allow_redirects=False,
    )
    require(200 <= result.status_code < 300, f"Automation HTTP {result.status_code}")
    return result.json() if result.content else {}


def arm(context, deadline):
    job = str(uuid4())
    automation(
        context,
        "PUT",
        "/jobs/" + job,
        {
            "properties": {
                "runbook": {"name": "retail-hp-stop-demo"},
                "parameters": {"DeadlineUnix": str(deadline)},
            }
        },
    )
    for _ in range(24):
        state = automation(context, "GET", "/jobs/" + job)["properties"]["status"]
        require(state not in {"Failed", "Stopped", "Suspended"}, "Controller failed to arm")
        streams = automation(context, "GET", "/jobs/" + job + "/streams")
        if any(
            x["properties"].get("summary") == "CONTROLLER_ARMED" for x in streams.get("value", [])
        ):
            return job
        time.sleep(5)
    raise RuntimeError("Controller readiness not verified; no compute may start")


def stopped(context, warehouse):
    client = context.client
    require(client.apps.get(APP).compute_status.state.value == "STOPPED", "App running")
    require(client.warehouses.get(warehouse).state.value == "STOPPED", "Warehouse running")
    endpoint = client.api_client.do("GET", f"/api/2.0/serving-endpoints/{ENDPOINT}")
    require(endpoint["state"].get("suspend") == "STOPPED", "Endpoint running")


def start(context, *, owner_demo=False, chat_upgrade=False):
    require(not (owner_demo and chat_upgrade), "Choose one authorized launch purpose")
    preflight = json.loads(
        (ROOT / "azure_databricks/evidence/phase_10/release_preflight.json").read_text()
    )
    require(preflight["status"] == "PASS_METADATA_AND_CLAIM", "Identity preflight required")
    control = json.loads((ROOT / "build/phase10-release.local.json").read_text())
    require(preflight.get("release") == control["release"], "Release preflight is stale")
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        require(
            automation(context, "GET", "/jobs/" + previous["stop_job"])["properties"]["status"]
            == "Completed",
            "Previous deadline controller must finish before a new launch",
        )
    client = context.client
    stopped(context, control["warehouse_id"])
    existing_deployment = client.apps.get(APP).active_deployment
    if existing_deployment is None:
        succeeded = [
            deployment
            for deployment in client.apps.list_deployments(APP)
            if deployment.status and deployment.status.state.value == "SUCCEEDED"
        ]
        if succeeded:
            existing_deployment = max(succeeded, key=lambda item: item.create_time or "")
    require(
        chat_upgrade
        or existing_deployment is None
        or (
            existing_deployment.source_code_path == control["source_path"]
            and existing_deployment.status.state.value == "SUCCEEDED"
        ),
        "A different active release requires a separately reviewed upgrade",
    )
    _verify_warehouse_contract(client.warehouses.get(control["warehouse_id"]))
    require(_verify_budget(context)["current_spend_inr"] < 9000, "Monthly spend safety margin")
    # Explicit owner-requested demo is separate from completed validation history.
    # Neither ledger is reset; this option permits one bounded demo only.
    ledger_path = CHAT_LEDGER if chat_upgrade else DEMO_LEDGER if owner_demo else LEDGER
    ledger = (
        json.loads(ledger_path.read_text())
        if ledger_path.exists()
        else {"reserved_inr": 0, "runs": []}
    )
    # Chat: 20-minute deadline + 5-minute stop headroom, INR 25 LLM gate,
    # XXSmall warehouse with 1-minute idle stop, no real-time endpoint start.
    # Other launches retain 12 minutes. Tax/rate/storage margin is a planning
    # assumption, not a guaranteed Azure invoice ceiling.
    reserve = 220
    # Owner approved a cumulative INR 440 allowance on 2026-09-09 after the
    # first startup timeout. Reservations persist even when actual billing lags.
    # Separate additional INR 220 chat-fix validation explicitly approved by owner.
    # Owner approved one further INR 220 chat test after the transition failure.
    # Owner approved one additional INR 220 business-response deployment window
    # on 2026-09-09. Preserve both earlier chat reservations; no fourth is authorized.
    ceiling = 660 if chat_upgrade else 220 if owner_demo else 440
    require(ledger["reserved_inr"] + reserve <= ceiling, "Phase 10 launch allowance exhausted")
    ticket = uuid4().hex
    ledger["reserved_inr"] += reserve
    ledger["runs"].append({"ticket": ticket, "reserved_inr": reserve})
    ledger_path.write_text(json.dumps(ledger))  # Retain reservation on any uncertain failure.
    principals = [p for p in client.service_principals.list() if p.display_name == IDENTITY_NAME]
    require(len(principals) == 1 and principals[0].active, "Existing test identity missing")
    tester = principals[0]
    # Additive App use only, for the already provisioned non-admin test identity.
    client.api_client.do(
        "PATCH",
        f"/api/2.0/permissions/apps/{APP}",
        body={
            "access_control_list": [
                {"service_principal_name": tester.application_id, "permission_level": "CAN_USE"}
            ]
        },
    )
    # Owner asked to test too: one shared 20-minute window, not immediate teardown.
    # Chat uses batch recommendations; the real-time endpoint stays stopped.
    deadline = int(time.time()) + (1200 if chat_upgrade else 720)
    launch = {
        "ticket": ticket,
        "expires": deadline,
        "warehouse_id": control["warehouse_id"],
        "entitlements": [
            {
                "subject": control["operator_id"],
                "allowed_customers": [],
                "can_view_quality": True,
            },
            {"subject": str(tester.id), "allowed_customers": ["CUS000001"]},
        ],
        "cohort_subjects": [control["operator_id"]],
    }
    Launch.model_validate_json(json.dumps(launch))
    job = arm(context, deadline)
    require(time.time() < deadline - 480, "Insufficient safe launch window")
    # An installed old release can restart automatically with App compute. Give
    # that process an expired lease so it cannot claim this ticket or serve calls.
    # Only the reviewed new snapshot receives the live lease at deployment time.
    initial_launch = {**launch, "expires": 1} if chat_upgrade else launch
    client.secrets.put_secret(
        "retail-hp-app-private", "launch", string_value=json.dumps(initial_launch)
    )
    state = {
        **control,
        "ticket": ticket,
        "deadline": deadline,
        "stop_job": job,
        "tester_id": str(tester.id),
        "start_time": time.time(),
        "status": "STARTING",
    }
    STATE.write_text(json.dumps(state))
    try:
        if control.get("customer_context_version") == "customer_context_v2":
            # Upgrade the existing view before any new App process can read it.
            # Reuse the same approved warehouse and independent deadline.
            from phase10_customer_context_migrate import migrate

            client.warehouses.start(control["warehouse_id"])
            while client.warehouses.get(control["warehouse_id"]).state.value != "RUNNING":
                require(time.time() < deadline - 480, "Customer view startup exceeded window")
                time.sleep(3)
            migration = migrate(context)
            migration_report = (
                ROOT / "azure_databricks/evidence/phase_10/customer_context_migration.json"
            )
            migration_report.write_text(json.dumps(migration, indent=2))
        client.apps.start(APP)
        while True:
            require(time.time() < deadline - 240, "App startup exceeded safe window")
            compute = client.apps.get(APP).compute_status.state.value
            if compute == "ACTIVE":
                break
            require(compute == "STARTING", "App compute failed to start")
            time.sleep(5)
        if chat_upgrade:
            # ACTIVE compute is not proof that its automatic deployment finished.
            # Keep the expired secret until that transition is terminal; otherwise
            # the restarting old process can consume the new release's ticket.
            while existing_deployment is not None:
                require(time.time() < deadline - 240, "Old deployment exceeded safe window")
                history = list(client.apps.list_deployments(APP))
                pending = [
                    item
                    for item in history
                    if item.status
                    and item.status.state.value not in {"SUCCEEDED", "FAILED", "CANCELLED"}
                ]
                restarted = any(
                    item.create_time
                    and datetime.fromisoformat(item.create_time.replace("Z", "+00:00")).timestamp()
                    >= state["start_time"]
                    for item in history
                )
                if restarted and not pending:
                    break
                time.sleep(5)
            client.secrets.put_secret(
                "retail-hp-app-private", "launch", string_value=json.dumps(launch)
            )
        if existing_deployment is None or chat_upgrade:
            deployment = client.api_client.do(
                "POST",
                f"/api/2.0/apps/{APP}/deployments",
                body={"source_code_path": control["source_path"], "mode": "SNAPSHOT"},
            )
            state["deployment_id"] = deployment["deployment_id"]
        else:
            # start() already runs the installed release with refreshed secrets.
            # Deploying again would consume the same one-use claim twice.
            state["deployment_id"] = existing_deployment.deployment_id
        state["url"] = client.apps.get(APP).url
        state["status"] = "DEPLOYMENT_SUBMITTED"
        STATE.write_text(json.dumps(state))
        # Do not pay for dependencies while waiting for App compute allocation.
        client.warehouses.start(control["warehouse_id"])
        if not chat_upgrade:
            client.api_client.do("POST", f"/api/2.0/serving-endpoints/{ENDPOINT}/config:start")
    except BaseException:
        # Independent controller remains armed if operator cleanup itself fails.
        for operation in [
            lambda: client.apps.stop(APP),
            lambda: client.warehouses.stop(control["warehouse_id"]),
            lambda: client.api_client.do(
                "POST", f"/api/2.0/serving-endpoints/{ENDPOINT}/config:stop"
            ),
        ]:
            try:
                operation()
            except Exception:
                print("Operator stop not confirmed; independent controller remains armed")
        raise
    return {
        "status": state["status"],
        "url": state["url"],
        "deadline": deadline,
        "reserved_estimate_inr": reserve,
        "invoice_cap": False,
    }


def inspect(context):
    state = json.loads(STATE.read_text())
    app = context.client.apps.get(APP)
    return {
        "app_compute": app.compute_status.state.value,
        "app_status": app.app_status.as_dict() if app.app_status else None,
        "deployment": (
            context.client.apps.get_deployment(APP, state["deployment_id"]).as_dict()
            if state.get("deployment_id")
            else None
        ),
        "warehouse": context.client.warehouses.get(state["warehouse_id"]).state.value,
        "endpoint": context.client.api_client.do("GET", f"/api/2.0/serving-endpoints/{ENDPOINT}")[
            "state"
        ],
        "seconds_left": int(state["deadline"] - time.time()),
    }


def redeploy(context):
    """Repair a failed build inside the existing lease; never start any compute."""
    state = json.loads(STATE.read_text())
    require(time.time() < state["deadline"] - 180, "Insufficient existing release window")
    require(
        automation(context, "GET", "/jobs/" + state["stop_job"])["properties"]["status"]
        == "Running",
        "Controller must remain armed",
    )
    require(context.client.apps.get(APP).compute_status.state.value == "ACTIVE", "App not active")
    previous = context.client.apps.get_deployment(APP, state["deployment_id"])
    require(previous.status.state.value == "FAILED", "Only failed builds can be repaired")
    control = json.loads((ROOT / "build/phase10-release.local.json").read_text())
    verify_files(Path(control["local_package"]))
    result = context.client.api_client.do(
        "POST",
        f"/api/2.0/apps/{APP}/deployments",
        body={"source_code_path": control["source_path"], "mode": "SNAPSHOT"},
    )
    state.update(control)
    state["deployment_id"] = result["deployment_id"]
    STATE.write_text(json.dumps(state))
    return {"status": "REDEPLOYED_WITHIN_EXISTING_LEASE", "deadline": state["deadline"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "inspect", "stop", "redeploy"])
    parser.add_argument(
        "--chat-upgrade",
        action="store_true",
        help="One owner-approved INR 220 chat revision validation with expired old-release lease",
    )
    parser.add_argument(
        "--owner-demo",
        action="store_true",
        help="One explicitly requested demo; preserve validation reservations",
    )
    args = parser.parse_args()
    require(not args.owner_demo or args.command == "start", "Demo flag requires start")
    require(not args.chat_upgrade or args.command == "start", "Upgrade flag requires start")
    context = CloudContext(apply=args.command != "inspect", direct_operator_token=True)
    if args.command == "start":
        result = start(context, owner_demo=args.owner_demo, chat_upgrade=args.chat_upgrade)
    elif args.command == "stop":
        result = {"stop_job": arm(context, int(time.time()))}
    elif args.command == "redeploy":
        result = redeploy(context)
    else:
        result = inspect(context)
    print(json.dumps(result, indent=2))
