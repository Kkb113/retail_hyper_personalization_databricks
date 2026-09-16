"""Deploy and evaluate one governed Genie Agent in a bounded, auto-stopped window."""
# ruff: noqa: S608 -- SQL uses only fixed, version-controlled object identifiers.

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from phase10_live import APP, ENDPOINT, arm, automation
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase7_delta import _execute, _rows
from retail_hp_azure.phase10_runtime import RunningBackend
from retail_hp_azure.phase11 import CATALOG, PREFIX, WAREHOUSE
from retail_hp_azure.phase11_genie import (
    CONFIG_VERSION,
    DESCRIPTION,
    PARENT,
    TITLE,
    VIEWER_GROUP,
    business_starter_questions,
    genie_views,
    qualified_view,
    serialized_space,
    space_config,
    validate_config,
)
from retail_hp_azure.safety import require

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "azure_databricks/evidence/phase_11"
STATE = ROOT / "build/phase11-genie.local.json"
LEDGER = ROOT / "build/phase11-genie-cost.local.json"
ALLOWANCE_INR = 100
WINDOW_SECONDS = 1200


def _api(
    client: Any, method: str, path: str, *, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    return client.api_client.do(method, path, body=body)


def _matching_spaces(client: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        suffix = f"&page_token={quote(page_token)}" if page_token else ""
        response = _api(client, "GET", f"/api/2.0/genie/spaces?page_size=100{suffix}")
        matches.extend(space for space in response.get("spaces", []) if space.get("title") == TITLE)
        page_token = response.get("next_page_token")
        if not page_token:
            return matches


def _normalized_instructions(config: dict[str, Any]) -> list[str]:
    return [
        " ".join(" ".join(item["content"]).split())
        for item in config["instructions"]["text_instructions"]
    ]


def plan() -> dict[str, Any]:
    config = space_config()
    validate_config(config)
    return {
        "status": "PASS_GENIE_PLAN",
        "configuration_version": CONFIG_VERSION,
        "tables": len(config["data_sources"]["tables"]),
        "sample_questions": len(config["config"]["sample_questions"]),
        "raw_sql_examples": len(config["instructions"]["example_question_sqls"]),
        "stored_benchmarks": len(config["benchmarks"]["questions"]),
        "live_business_acceptance_questions": 1,
        "aggregate_only": True,
        "new_azure_resources": 0,
        "new_compute": 0,
        "warehouse_auto_stop_minutes": 1,
        "validation_allowance_inr": ALLOWANCE_INR,
        "allowance_is_not_invoice_cap": True,
    }


def inspect(context: CloudContext) -> dict[str, Any]:
    matches = _matching_spaces(context.client)
    require(len(matches) <= 1, "Ambiguous Genie Agent title")
    warehouse = context.client.warehouses.get(WAREHOUSE)
    endpoint = context.client.serving_endpoints.get(ENDPOINT).as_dict()
    app = context.client.apps.get(APP).as_dict()
    served_entities = endpoint.get("config", {}).get("served_entities", [])
    result: dict[str, Any] = {
        "space_count": len(matches),
        "space_id": matches[0].get("space_id") if matches else None,
        "warehouse_state": warehouse.state.value if warehouse.state else None,
        "warehouse_auto_stop_minutes": warehouse.auto_stop_mins,
        "endpoint_state": endpoint.get("state", {}).get("suspend"),
        "endpoint_state_detail": endpoint.get("state", {}),
        "endpoint_scale_to_zero": bool(served_entities)
        and all(item.get("scale_to_zero_enabled") for item in served_entities),
        "app_compute_state": app.get("compute_status", {}).get("state"),
        "cloud_mutations": 0,
    }
    if matches:
        conversations = _api(
            context.client,
            "GET",
            f"/api/2.0/genie/spaces/{matches[0]['space_id']}/conversations?page_size=10",
        ).get("conversations", [])
        if conversations:
            latest = max(conversations, key=lambda item: item.get("created_timestamp", 0))
            messages = _api(
                context.client,
                "GET",
                f"/api/2.0/genie/spaces/{matches[0]['space_id']}/conversations/{latest['conversation_id']}/messages?page_size=10",
            ).get("messages", [])
            if messages:
                message = max(messages, key=lambda item: item.get("created_timestamp", 0))
                result["latest_business_conversation"] = {
                    "conversation_id": latest["conversation_id"],
                    "status": message.get("status"),
                    "has_error": bool(message.get("error")),
                    "text_attachments": sum(
                        bool(item.get("text")) for item in message.get("attachments") or []
                    ),
                    "answer_characters": sum(
                        len(item.get("text", {}).get("content", ""))
                        for item in message.get("attachments") or []
                        if item.get("text", {}).get("purpose") != "FOLLOW_UP_QUESTION"
                    ),
                    "query_attachments": sum(
                        bool(item.get("query")) for item in message.get("attachments") or []
                    ),
                    "visualizations": sum(
                        bool(item.get("viz")) for item in message.get("attachments") or []
                    ),
                }
    return result


def finalize(context: CloudContext) -> dict[str, Any]:
    require(context.apply, "Apply required")
    observed = inspect(context)
    require(observed["space_count"] == 1, "Exactly one governed Genie Agent is required")
    conversation = observed.get("latest_business_conversation", {})
    require(conversation.get("status") == "COMPLETED", "Business acceptance is incomplete")
    require(not conversation.get("has_error"), "Business acceptance contains an error")
    require(conversation.get("answer_characters", 0) >= 80, "Business answer is too short")
    require(conversation.get("query_attachments", 0) >= 1, "Governed data evidence is missing")
    require(observed["app_compute_state"] == "STOPPED", "Databricks App must remain stopped")
    require(observed["endpoint_scale_to_zero"], "Recommendation endpoint is not scale-to-zero")
    warehouse_state = observed["warehouse_state"]
    if warehouse_state not in {"STOPPED", "STOPPING"}:
        context.client.warehouses.stop(WAREHOUSE)
    stopped = context.client.warehouses.wait_get_warehouse_stopped(WAREHOUSE).state.value
    require(stopped == "STOPPED", "Warehouse shutdown failed")
    result = {
        "status": "PASS_GENIE_DEPLOYMENT",
        "space_id": observed["space_id"],
        "title": TITLE,
        "configuration_sha256": hashlib.sha256(serialized_space().encode()).hexdigest(),
        "business_acceptance": conversation,
        "business_starter_questions": 6,
        "stored_benchmarks": 0,
        "raw_sql_examples": 0,
        "viewer_group": VIEWER_GROUP,
        "warehouse_state": stopped,
        "warehouse_auto_stop_minutes": 1,
        "genie_user_promotion_checked_at": "2026-09-09",
        "genie_user_usage_free_through": "2027-01-31",
        "service_principal_usage_validated": False,
        "new_azure_resources": 0,
        "allowance_inr": ALLOWANCE_INR,
        "allowance_is_not_invoice_cap": True,
    }
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "genie_deployment.json").write_text(json.dumps(result, indent=2))
    return result


def _create_views(context: CloudContext, backend: RunningBackend) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, select in genie_views().items():
        backend.check_running()
        _execute(
            context.client,
            WAREHOUSE,
            f"CREATE OR REPLACE VIEW {qualified_view(name)} AS {select}",
            deadline_seconds=45,
        )
        _execute(
            context.client,
            WAREHOUSE,
            f"ALTER VIEW {qualified_view(name)} OWNER TO `retail_hp_admins`",
            deadline_seconds=30,
        )
        rows = _rows(
            _execute(
                context.client,
                WAREHOUSE,
                f"SELECT count(*) AS n FROM {qualified_view(name)}",
                deadline_seconds=30,
            )
        )
        counts[name] = int(rows[0]["n"])
        require(counts[name] > 0, f"Empty governed view: {name}")
    for source in space_config()["data_sources"]["tables"]:
        backend.check_running()
        _execute(
            context.client,
            WAREHOUSE,
            f"GRANT SELECT ON TABLE {source['identifier']} TO `{VIEWER_GROUP}`",
            deadline_seconds=30,
        )
    source_sql = {
        "genie_customer_portfolio": f"SELECT count(*) AS n FROM {CATALOG}.serving.tool_customers",
        "genie_product_portfolio": f"SELECT count(*) AS n FROM {CATALOG}.serving.tool_products",
        "genie_recommendation_insights": (
            f"SELECT count(*) AS n FROM {CATALOG}.serving.customer_recommendations r "
            f"JOIN {CATALOG}.serving.tool_customers c ON r.customer_id=c.customer_id "
            f"JOIN {CATALOG}.serving.tool_products p ON r.product_id=p.product_id "
            "WHERE r.registered_model_version='3'"
        ),
        "genie_opportunity_insights": (
            f"SELECT count(*) AS n FROM {PREFIX}.phase11_opportunities o "
            f"JOIN {CATALOG}.serving.tool_customers c ON o.customer_id=c.customer_id "
            f"JOIN {CATALOG}.serving.tool_products p ON o.product_id=p.product_id"
        ),
    }
    aggregate_sql = {
        "genie_customer_portfolio": "sum(customers)",
        "genie_product_portfolio": "sum(eligible_products)",
        "genie_recommendation_insights": "sum(recommendations)",
        "genie_opportunity_insights": "sum(opportunities)",
    }
    for name, source_query in source_sql.items():
        source_rows = _rows(_execute(context.client, WAREHOUSE, source_query, deadline_seconds=30))
        aggregate_rows = _rows(
            _execute(
                context.client,
                WAREHOUSE,
                f"SELECT {aggregate_sql[name]} AS n FROM {qualified_view(name)}",
                deadline_seconds=30,
            )
        )
        require(
            int(aggregate_rows[0]["n"]) == int(source_rows[0]["n"]),
            f"Genie aggregate reconciliation failed: {name}",
        )
    return counts


def _upsert_space(context: CloudContext) -> dict[str, Any]:
    client = context.client
    matches = _matching_spaces(client)
    require(len(matches) <= 1, "Ambiguous Genie Agent title")
    payload = {
        "title": TITLE,
        "description": DESCRIPTION,
        "warehouse_id": WAREHOUSE,
        "parent_path": PARENT,
        "serialized_space": serialized_space(),
    }
    if matches:
        space_id = matches[0]["space_id"]
        existing = _api(
            client,
            "GET",
            f"/api/2.0/genie/spaces/{space_id}?include_serialized_space=true",
        )
        require(existing.get("parent_path") == PARENT, "Genie Agent ownership scope mismatch")
        payload["etag"] = existing["etag"]
        space = _api(client, "PATCH", f"/api/2.0/genie/spaces/{space_id}", body=payload)
    else:
        space = _api(client, "POST", "/api/2.0/genie/spaces", body=payload)
    space_id = space["space_id"]
    _api(
        client,
        "PATCH",
        f"/api/2.0/permissions/genie/{space_id}",
        body={"access_control_list": [{"group_name": VIEWER_GROUP, "permission_level": "CAN_RUN"}]},
    )
    readback = _api(
        client,
        "GET",
        f"/api/2.0/genie/spaces/{space_id}?include_serialized_space=true",
    )
    require(readback["title"] == TITLE and readback["warehouse_id"] == WAREHOUSE, "Space drift")
    require(readback["parent_path"] == PARENT, "Space path drift")
    expected_config = json.loads(serialized_space())
    actual_config = json.loads(readback["serialized_space"])
    require(actual_config.get("version") == 2, "Genie configuration version drift")
    require(
        {item["identifier"] for item in actual_config["data_sources"]["tables"]}
        == {item["identifier"] for item in expected_config["data_sources"]["tables"]},
        "Genie data-source drift",
    )
    require(
        [item["question"] for item in actual_config["config"]["sample_questions"]]
        == [item["question"] for item in expected_config["config"]["sample_questions"]],
        "Genie business-question drift",
    )
    require(
        _normalized_instructions(actual_config) == _normalized_instructions(expected_config),
        "Genie instruction drift",
    )
    require(
        not actual_config.get("instructions", {}).get("example_question_sqls", []),
        "Unexpected raw SQL examples in Genie readback",
    )
    require(
        not actual_config.get("benchmarks", {}).get("questions", []),
        "Unexpected benchmark questions in Genie readback",
    )
    permissions = _api(client, "GET", f"/api/2.0/permissions/genie/{space_id}")
    viewer = [
        item
        for item in permissions.get("access_control_list", [])
        if item.get("group_name") == VIEWER_GROUP
    ]
    require(viewer, "Viewer group permission missing")
    permission_levels = {
        permission.get("permission_level")
        for item in viewer
        for permission in item.get("all_permissions", [])
    }
    require(
        bool(permission_levels.intersection({"CAN_RUN", "CAN_EDIT", "CAN_MANAGE"})),
        "Viewer group cannot run the Genie Agent",
    )
    return readback


def _run_business_acceptance(
    context: CloudContext, space_id: str, deadline: float
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for question in business_starter_questions()[:1]:
        started = _api(
            context.client,
            "POST",
            f"/api/2.0/genie/spaces/{space_id}/start-conversation",
            body={"content": question, "enable_visualization": True},
        )
        message = started.get("message", started)
        conversation = started.get("conversation", {})
        conversation_id = (
            message.get("conversation_id")
            or conversation.get("conversation_id")
            or conversation.get("id")
        )
        message_id = started.get("message_id") or message.get("message_id") or message.get("id")
        require(bool(conversation_id and message_id), "Genie conversation identifiers missing")
        delay = 2.0
        current = message
        while time.time() < deadline - 120:
            current = _api(
                context.client,
                "GET",
                f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
            )
            if current.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(delay)
            delay = min(delay * 1.6, 15.0)
        require(current.get("status") == "COMPLETED", "Business question did not complete")
        require(not current.get("error"), "Business question returned an error")
        attachments = current.get("attachments") or []
        answers = [
            item["text"]["content"]
            for item in attachments
            if item.get("text", {}).get("content")
            and item.get("text", {}).get("purpose") != "FOLLOW_UP_QUESTION"
        ]
        queries = [item["query"] for item in attachments if item.get("query")]
        require(sum(len(answer) for answer in answers) >= 80, "Business answer is too short")
        require(queries, "Business question did not produce governed data evidence")
        require(
            all(query.get("statement_id") for query in queries),
            "Business question SQL evidence did not execute",
        )
        results.append(
            {
                "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
                "status": current["status"],
                "answer_characters": sum(len(answer) for answer in answers),
                "query_attachments": len(queries),
                "visualizations": sum(bool(item.get("viz")) for item in attachments),
                "conversation_id": conversation_id,
            }
        )
    return results


def deploy(context: CloudContext) -> dict[str, Any]:
    require(context.apply, "Apply required")
    validate_config(space_config())
    preflight = inspect(context)
    require(preflight["space_count"] <= 1, "Ambiguous existing Genie Agent")
    require(
        preflight["warehouse_state"] in {"STOPPED", "STARTING", "RUNNING", "STOPPING"},
        "Warehouse state is outside the controlled lifecycle",
    )
    require(preflight["warehouse_auto_stop_minutes"] == 1, "Warehouse auto-stop drift")
    endpoint_stopped = preflight["endpoint_state"] == "STOPPED" or (
        preflight["endpoint_state_detail"].get("ready") == "NOT_READY"
        and preflight["endpoint_scale_to_zero"]
    )
    require(endpoint_stopped, "Recommendation endpoint must be stopped or scaled to zero")
    require(preflight["app_compute_state"] == "STOPPED", "Databricks App must be stopped")
    require(_verify_budget(context)["current_spend_inr"] < 9000, "Monthly spend safety margin")
    if LEDGER.exists():
        require(STATE.exists(), "Active allowance has no shutdown state")
        active_window = json.loads(STATE.read_text())
        deadline = float(active_window["deadline"])
        stop_job = str(active_window["stop_job"])
        stop_status = automation(context, "GET", "/jobs/" + stop_job)["properties"]["status"]
        if deadline <= time.time() + 180:
            require(stop_status == "Completed", "Existing shutdown controller is still active")
            require(
                int(active_window.get("renewal_count", 0)) == 0,
                "Genie allowance cannot be renewed again",
            )
            deadline = time.time() + 600
            stop_job = arm(context, int(deadline))
            STATE.write_text(
                json.dumps({"deadline": deadline, "stop_job": stop_job, "renewal_count": 1})
            )
        else:
            require(stop_status == "Running", "Independent shutdown controller is not running")
    else:
        deadline = time.time() + WINDOW_SECONDS
        stop_job = arm(context, int(deadline))
        LEDGER.write_text(
            json.dumps(
                {
                    "reserved_inr": ALLOWANCE_INR,
                    "reservation_is_not_measured_spend": True,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        )
        STATE.write_text(json.dumps({"deadline": deadline, "stop_job": stop_job}))
    if preflight["warehouse_state"] == "STOPPING":
        context.client.warehouses.wait_get_warehouse_stopped(WAREHOUSE)
        context.client.warehouses.start(WAREHOUSE)
    elif preflight["warehouse_state"] == "STOPPED":
        context.client.warehouses.start(WAREHOUSE)
    backend = RunningBackend(context.client, WAREHOUSE)
    backend.lease_expires = deadline
    backend.check_running()
    counts = (
        _create_views(context, backend)
        if preflight["space_count"] == 0
        else {"reused_existing_governed_views": 1}
    )
    space = _upsert_space(context)
    business_acceptance = _run_business_acceptance(context, space["space_id"], deadline)
    context.client.warehouses.stop(WAREHOUSE).result(timeout=timedelta(minutes=3))
    final = context.client.warehouses.wait_get_warehouse_stopped(WAREHOUSE).state.value
    require(final == "STOPPED", "Warehouse shutdown failed")
    require(
        automation(context, "GET", "/jobs/" + stop_job)["properties"]["status"] == "Running",
        "Independent shutdown controller missing",
    )
    result = {
        "status": "PASS_GENIE_DEPLOYMENT",
        "space_id": space["space_id"],
        "title": TITLE,
        "configuration_sha256": hashlib.sha256(serialized_space().encode()).hexdigest(),
        "view_row_groups": counts,
        "business_acceptance": business_acceptance,
        "stored_benchmarks": 0,
        "raw_sql_examples": 0,
        "viewer_group": VIEWER_GROUP,
        "warehouse_state": final,
        "warehouse_auto_stop_minutes": 1,
        "genie_user_promotion_checked_at": "2026-09-09",
        "genie_user_usage_free_through": "2027-01-31",
        "service_principal_usage_validated": False,
        "new_azure_resources": 0,
        "allowance_inr": ALLOWANCE_INR,
        "allowance_is_not_invoice_cap": True,
    }
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "genie_deployment.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "inspect", "deploy", "finalize"])
    args = parser.parse_args()
    if args.command == "plan":
        output = plan()
    else:
        ctx = CloudContext(apply=args.command in {"deploy", "finalize"}, direct_operator_token=True)
        if args.command == "inspect":
            output = inspect(ctx)
        elif args.command == "deploy":
            output = deploy(ctx)
        else:
            output = finalize(ctx)
    print(json.dumps(output, indent=2))
