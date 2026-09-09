"""On-demand, aggregate-only POC analytics. Missing measurements are never zero."""
# ruff: noqa: S608 -- SQL interpolates only fixed internal identifiers, never user inputs.

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

CATALOG = "intellify_databricks_demo"
PREFIX = f"{CATALOG}.monitoring"
VERSION = "retail_operations_v1"
WAREHOUSE = "77cfc492a3539c60"
DASHBOARD_NAME = "Retail POC — Business and operations"
TRACE_REQUEST: ContextVar[str] = ContextVar("retail_request_hash", default="")


def monitoring_queries() -> dict[str, str]:
    """Fixed aggregate queries; no unrestricted customer-level dashboard datasets."""
    r = f"{CATALOG}.serving.customer_recommendations"
    p = f"{CATALOG}.serving.tool_products"
    c = f"{CATALOG}.serving.tool_customers"
    return {
        "coverage": f"""WITH batch AS (
          SELECT r.*, p.product_id AS eligible_product, c.customer_id AS active_customer
          FROM {r} r LEFT JOIN {p} p ON r.product_id=p.product_id
          LEFT JOIN {c} c ON r.customer_id=c.customer_id
          WHERE r.registered_model_version='3')
        SELECT count(*) AS published_rows, count(DISTINCT customer_id) AS published_customers,
          count(DISTINCT active_customer) AS active_published_customers,
          count(DISTINCT eligible_product) AS recommended_eligible_products,
          (SELECT count(*) FROM {c}) AS active_customer_population,
          (SELECT count(*) FROM {p}) AS eligible_product_population,
          try_divide(count(DISTINCT active_customer), (SELECT count(*) FROM {c}))
            AS customer_coverage,
          try_divide(count(DISTINCT eligible_product), (SELECT count(*) FROM {p}))
            AS product_coverage,
          try_divide(count(eligible_product), count(*)) AS inventory_validity,
          min(try_cast(inventory_snapshot_at AS TIMESTAMP)) AS oldest_inventory_snapshot,
          current_timestamp() AS checked_at FROM batch""",
        "routes": f"""SELECT route, count(*) AS recommendation_rows,
          count(DISTINCT customer_id) AS customers FROM {r}
          WHERE registered_model_version='3' GROUP BY route""",
        "candidate_sources": f"""SELECT coalesce(candidate_sources,'Not recorded')
          AS source_combination,
          count(*) AS recommendation_rows FROM {r} WHERE registered_model_version='3'
          GROUP BY candidate_sources""",
        "quality": f"""WITH batches AS (SELECT customer_id, count(*) AS n,
          count(DISTINCT product_id) AS products, count(DISTINCT rank) AS ranks,
          min(rank) AS lo, max(rank) AS hi, count(DISTINCT batch_id) AS batches
          FROM {r} WHERE registered_model_version='3' GROUP BY customer_id)
          SELECT count(*) AS checked_customers,
          count_if(n<>10 OR products<>10 OR ranks<>10 OR lo<>1 OR hi<>10 OR batches<>1)
          AS invalid_customer_batches, current_timestamp() AS checked_at FROM batches""",
        "feedback": f"""WITH f AS (SELECT DISTINCT event_id,
          get_json_object(payload_json,'$.sentiment') AS sentiment
          FROM {CATALOG}.agent.feedback WHERE expires_at>current_timestamp()),
          i AS (SELECT count(DISTINCT event_id) AS impressions
          FROM {CATALOG}.agent.recommendation_impression WHERE expires_at>current_timestamp())
          SELECT count(*) AS feedback_events, count_if(sentiment='positive') AS positive_events,
          try_divide(count_if(sentiment='positive'),count(*)) AS positive_feedback_rate,
          (SELECT impressions FROM i) AS recorded_impressions,
          CAST(NULL AS DOUBLE) AS feedback_rate,
          'Feedback attribution is not verified; rate unavailable' AS limitation
          FROM f""",
        "opportunities": f"""SELECT opportunity_type, count(*) AS opportunities,
          max(evaluated_at) AS evaluated_at FROM {PREFIX}.phase11_opportunities
          GROUP BY opportunity_type""",
        "evaluation": "SELECT metric, value, scope, source, observed_at "
        f"FROM {PREFIX}.phase11_metrics",
        "requests": f"""SELECT model_version, count(*) AS requests,
          count_if(status<>'ok') AS non_success_responses,
          percentile_approx(elapsed_ms,0.95) AS p95_latency_ms,
          sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
          sum(cost_estimate_inr) AS estimated_llm_inr, max(observed_at) AS last_observed
          FROM {PREFIX}.phase11_requests WHERE observed_at>current_timestamp()-INTERVAL 30 DAYS
          GROUP BY model_version""",
    }


def opportunity_select() -> str:
    """Rules only for supplied facts, evaluated at the frozen data cutoff, not today."""
    return f"""WITH eligible AS (
      SELECT r.customer_id, r.product_id, r.batch_id, p.active_discount_pct,
        p.inventory_snapshot_at, c.browse_count, c.cart_add_count, c.purchase_count,
        c.behavior_as_of FROM {CATALOG}.serving.customer_recommendations r
      JOIN {CATALOG}.serving.tool_products p ON r.product_id=p.product_id
      JOIN {CATALOG}.gold.customer_360 c ON r.customer_id=c.customer_id
      WHERE r.registered_model_version='3' AND c.customer_status='Active' AND p.available_qty>0),
      rules AS (
        SELECT customer_id, product_id, batch_id, 'ACTIVE_PROMOTION' AS opportunity_type,
          active_discount_pct AS evidence_value, inventory_snapshot_at AS evidence_as_of
        FROM eligible WHERE active_discount_pct>0
        UNION ALL
        SELECT customer_id, product_id, batch_id, 'BROWSE_OR_CART_WITHOUT_PURCHASE',
          CAST(browse_count+cart_add_count AS DOUBLE), behavior_as_of
        FROM eligible WHERE purchase_count=0 AND browse_count+cart_add_count>0)
      SELECT sha2(concat_ws('|',customer_id,product_id,batch_id,opportunity_type,'{VERSION}'),256)
        AS opportunity_id, customer_id, product_id, batch_id, opportunity_type,
        evidence_value, evidence_as_of, '{VERSION}' AS rule_version,
        current_timestamp() AS evaluated_at FROM rules"""


def opportunity_ddl() -> str:
    return f"""CREATE TABLE IF NOT EXISTS {PREFIX}.phase11_opportunities (
      opportunity_id STRING, customer_id STRING, product_id STRING, batch_id STRING,
      opportunity_type STRING, evidence_value DOUBLE, evidence_as_of TIMESTAMP,
      rule_version STRING, evaluated_at TIMESTAMP) USING DELTA"""


def opportunity_refresh() -> str:
    # Snapshot replacement deliberately removes obsolete opportunities, not append-only duplicates.
    return f"INSERT OVERWRITE {PREFIX}.phase11_opportunities {opportunity_select()}"


def safe_trace(record: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed value/field allowlist: never log text, arguments, IDs or exceptions."""
    safe: dict[str, Any] = {"schema_version": VERSION, "observed_at": datetime.now(UTC).isoformat()}
    for key in ("request_hash", "prompt_hash", "release_hash"):
        value = record.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            safe[key] = value
    for key in (
        "event",
        "tool",
        "action",
        "status",
        "agent_version",
        "prompt_version",
        "version",
        "error_type",
        "response_mode",
        "model_version",
    ):
        value = record.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value):
            safe[key] = value
    for key in ("elapsed_ms", "input_tokens", "output_tokens", "cost_estimate_inr", "tool_calls"):
        value = record.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1e9
        ):
            safe[key] = value
    return safe


def trace_json(record: dict[str, Any]) -> str:
    return json.dumps(safe_trace(record), sort_keys=True, allow_nan=False)


def sample_request(request_id: str, percentage: int = 100) -> bool:
    if not 0 <= percentage <= 100:
        raise ValueError("Sampling percentage must be between 0 and 100")
    return int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100 < percentage


class TraceArchive:
    """One bounded redacted file per completed request, using existing launch-volume access.

    No SQL, no compute wake-up. Failures cannot break the business response.
    Evaluation captures 100%; records are retained for manual export/30-day cleanup.
    """

    def __init__(self, client: Any, ticket: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", ticket):
            raise ValueError("Invalid launch ticket")
        self.client = client
        self.root = f"/Volumes/{CATALOG}/agent/app_launches/observability/{ticket}"
        self.pending: dict[str, list[dict[str, Any]]] = {}
        self.lock = threading.Lock()
        self.written = 0

    def __call__(self, record: dict[str, Any]) -> None:
        safe = safe_trace({"request_hash": TRACE_REQUEST.get(), **record})
        print(json.dumps(safe), flush=True)
        key = safe.get("request_hash")
        if not key:
            return
        with self.lock:
            if self.written >= 200 or (key not in self.pending and len(self.pending) >= 20):
                return
            events = self.pending.setdefault(key, [])
            if len(events) < 40:
                events.append(safe)
            if safe.get("event") != "agent_request":
                return
            self.pending.pop(key)
            self.written += 1
        try:
            self.client.files.create_directory(self.root)
            self.client.files.upload(
                self.root + "/" + key + ".json",
                io.BytesIO(json.dumps({"events": events}).encode()),
                overwrite=False,
            )
        except Exception:
            print(json.dumps({"event": "trace_archive_failed", "request_hash": key}), flush=True)


def incident_findings(quality: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
    findings = []
    if int(quality["invalid_customer_batches"]) > 0:
        findings.append("BLOCK_DEMO_INVALID_BATCH")
    validity = coverage.get("inventory_validity")
    if validity is None or float(validity) < 1:
        findings.append("REVIEW_INVENTORY_ELIGIBILITY")
    if int(coverage["active_published_customers"]) == 0:
        findings.append("BLOCK_DEMO_NO_CUSTOMERS")
    return findings


def dashboard_spec() -> dict[str, Any]:
    # Native AI/BI table widgets keep exact denominators and timestamps visible.
    columns = {
        "coverage": "published_rows published_customers active_published_customers "
        "recommended_eligible_products active_customer_population eligible_product_population "
        "customer_coverage product_coverage inventory_validity "
        "oldest_inventory_snapshot checked_at",
        "routes": "route recommendation_rows customers",
        "candidate_sources": "source_combination recommendation_rows",
        "quality": "checked_customers invalid_customer_batches checked_at",
        "feedback": "feedback_events positive_events positive_feedback_rate recorded_impressions "
        "feedback_rate limitation",
        "opportunities": "opportunity_type opportunities evaluated_at",
        "evaluation": "metric value scope source observed_at",
        "requests": "model_version requests non_success_responses p95_latency_ms "
        "input_tokens output_tokens estimated_llm_inr last_observed",
    }
    datasets, layout = [], []
    for index, (name, query) in enumerate(monitoring_queries().items()):
        datasets.append(
            {"name": name, "displayName": name.replace("_", " ").title(), "queryLines": [query]}
        )
        layout.append(
            {
                "widget": {
                    "name": f"widget_{name}",
                    "queries": [
                        {
                            "name": "main_query",
                            "query": {
                                "datasetName": name,
                                "fields": [
                                    {"name": field, "expression": f"`{field}`"}
                                    for field in columns[name].split()
                                ],
                                "disaggregated": True,
                            },
                        }
                    ],
                    "spec": {
                        "version": 1,
                        "widgetType": "table",
                        "encodings": {
                            "columns": [
                                {
                                    "fieldName": field,
                                    "title": field.replace("_", " ").title(),
                                    "type": "string",
                                    "displayAs": "string",
                                    "visible": True,
                                    "order": i,
                                    "allowHTML": False,
                                }
                                for i, field in enumerate(columns[name].split())
                            ]
                        },
                        "frame": {"showTitle": True, "title": name.replace("_", " ").title()},
                    },
                },
                "position": {"x": 0, "y": index * 5 + 2, "width": 6, "height": 5},
            }
        )
    layout.insert(
        0,
        {
            "widget": {
                "name": "scope_note",
                "textbox_spec": "## Retail POC operations\n"
                "Synthetic, historical data—not live revenue or inventory. "
                "Refresh on demand only. Coverage is not recommendation accuracy. "
                "Candidate-source combinations are not causal contribution. "
                "Missing feedback attribution is not zero engagement. "
                "Billing is delayed and is not a hard spending cap.",
            },
            "position": {"x": 0, "y": 0, "width": 6, "height": 2},
        },
    )
    return {
        "datasets": datasets,
        "pages": [{"name": "operations", "displayName": "Operations", "layout": layout}],
    }
