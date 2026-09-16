"""Version-controlled configuration for the governed retail business Genie Agent."""
# ruff: noqa: E501, S608 -- fixed reviewed SQL is intentionally readable.

from __future__ import annotations

import json
import re
from typing import Any

from retail_hp_azure.phase11 import CATALOG, PREFIX
from retail_hp_azure.safety import require

TITLE = "Retail Hyper-Personalization Business Analyst"
DESCRIPTION = (
    "Governed business analysis of the synthetic retail recommendation portfolio, customer "
    "segments, product availability, campaign opportunities, quality controls and agent operations."
)
PARENT = "/Shared/retail_hp_phase11"
VIEWER_GROUP = "retail_hp_viewers"
CONFIG_VERSION = "retail_genie_v1"


def _id(section: int, position: int) -> str:
    return f"{section:02x}{position:030x}"


def genie_views() -> dict[str, str]:
    """Aggregate-only views: no customer identifiers or unrestricted event rows."""
    return {
        "genie_customer_portfolio": f"""SELECT customer_segment, loyalty_tier,
          preferred_channel, region_id, count(*) AS customers,
          avg(purchase_count) AS avg_historical_purchases,
          avg(browse_count) AS avg_historical_browses,
          min(behavior_as_of) AS oldest_behavior_snapshot,
          max(behavior_as_of) AS newest_behavior_snapshot,
          'Synthetic historical customer aggregates' AS data_scope
          FROM {CATALOG}.serving.tool_customers
          GROUP BY customer_segment, loyalty_tier, preferred_channel, region_id""",
        "genie_product_portfolio": f"""SELECT category_name, brand_name, count(*) AS eligible_products,
          avg(base_price) AS avg_source_price, sum(available_qty) AS available_quantity,
          count_if(active_discount_pct>0) AS promoted_products,
          avg(active_discount_pct) AS avg_discount_pct,
          min(inventory_snapshot_at) AS oldest_inventory_snapshot,
          max(inventory_snapshot_at) AS newest_inventory_snapshot,
          'Currency unspecified; global synthetic inventory snapshot' AS data_scope
          FROM {CATALOG}.serving.tool_products GROUP BY category_name, brand_name""",
        "genie_recommendation_insights": f"""SELECT c.customer_segment, c.loyalty_tier,
          c.preferred_channel, r.route AS recommendation_strategy,
          p.category_name, p.brand_name, count(*) AS recommendations,
          count(DISTINCT r.customer_id) AS customers,
          count(DISTINCT r.product_id) AS products,
          avg(r.rank) AS avg_rank, avg(r.score) AS avg_model_score,
          avg(p.base_price) AS avg_source_price, sum(p.available_qty) AS available_quantity,
          avg(p.active_discount_pct) AS avg_discount_pct,
          min(p.inventory_snapshot_at) AS oldest_inventory_snapshot,
          'Model version 3; scores are relative ranks, not purchase probabilities' AS data_scope
          FROM {CATALOG}.serving.customer_recommendations r
          JOIN {CATALOG}.serving.tool_customers c ON r.customer_id=c.customer_id
          JOIN {CATALOG}.serving.tool_products p ON r.product_id=p.product_id
          WHERE r.registered_model_version='3'
          GROUP BY c.customer_segment, c.loyalty_tier, c.preferred_channel, r.route,
            p.category_name, p.brand_name""",
        "genie_opportunity_insights": f"""SELECT o.opportunity_type, c.customer_segment,
          c.loyalty_tier, p.category_name, p.brand_name,
          count(*) AS opportunities, count(DISTINCT o.customer_id) AS customers,
          count(DISTINCT o.product_id) AS products, avg(o.evidence_value) AS avg_evidence_value,
          min(o.evidence_as_of) AS oldest_evidence_snapshot,
          max(o.evaluated_at) AS last_evaluated_at, o.rule_version,
          'Deterministic candidate rules; opportunities are not conversions' AS data_scope
          FROM {PREFIX}.phase11_opportunities o
          JOIN {CATALOG}.serving.tool_customers c ON o.customer_id=c.customer_id
          JOIN {CATALOG}.serving.tool_products p ON o.product_id=p.product_id
          GROUP BY o.opportunity_type, c.customer_segment, c.loyalty_tier,
            p.category_name, p.brand_name, o.rule_version""",
    }


def qualified_view(name: str) -> str:
    if name not in genie_views():
        raise ValueError("Unknown Genie view")
    return f"{PREFIX}.{name}"


def business_starter_questions() -> list[str]:
    return [
        "Give me an executive overview of recommendation reach and inventory validity.",
        "Which customer segments have the strongest recommendation coverage?",
        "Which campaign opportunity types should the business prioritize?",
        "Where is recommended assortment diversity strongest by customer segment?",
        "Summarize eligible product availability and promotions by category.",
        "Compare historical browsing and purchase engagement across customer segments.",
    ]


def _instructions() -> str:
    return f"""You are the Retail Hyper-Personalization Business Analyst for Intellify.

Audience and answer style:
- Write for business leaders, merchandisers, campaign managers and retail analysts. Lead with a concise executive answer, then evidence, business implication, recommended action and material limitations.
- Use clear retail language. Do not expose SQL implementation details unless the user asks for them.
- Prefer a small result table or visualization when it improves comparison. State the data snapshot or evaluation timestamp when available.

Data contract and routing:
- Use `{PREFIX}.genie_recommendation_insights` for segment, loyalty, channel, category, brand and recommendation-strategy analysis.
- Use `{PREFIX}.genie_customer_portfolio` for aggregate historical browsing and purchase behavior. It contains no customer-level rows.
- Use `{PREFIX}.genie_product_portfolio` for eligible assortment, availability, source price and promotion analysis.
- Use `{PREFIX}.genie_opportunity_insights` for campaign candidates by deterministic rule and segment.
- Use the `phase11_*_summary` views for coverage, route mix, quality, feedback, evaluation and observed request operations.

Business definitions and mandatory qualifications:
- All data is synthetic and historical. Never describe it as current production sales, revenue, profit, demand or live store inventory.
- Recommendation coverage is reach, not recommendation accuracy. Customer coverage is active published customers divided by all active profiles. Product coverage is distinct eligible recommended products divided by the eligible product catalog.
- Model scores are relative ranking signals, not probabilities or predicted conversion rates. Do not sum model scores as business value.
- `adaptive_blend` and `cold_only` are recommendation strategies. Translate them as blended personalization and cold-start personalization, while retaining the source value in parentheses.
- Opportunities are eligible campaign candidates, not conversions, sales or guaranteed uplift. `ACTIVE_PROMOTION` means a recommended eligible product has a positive snapshot discount. `BROWSE_OR_CART_WITHOUT_PURCHASE` is based on aggregate customer intent and does not prove that the specific recommended product was browsed.
- Currency is unspecified for source prices. Never add INR, USD or another symbol. Never total prices as revenue.
- Inventory is a global synthetic snapshot, not store-specific availability. Always mention the snapshot date for inventory-sensitive answers.
- Positive-feedback rate is based only on recorded feedback. Feedback rate is unavailable until impressions are attributed; NULL means unknown, never zero.
- NDCG, recall, hit rate and purchase hit rate are unavailable unless a qualified current-release holdout is present. Never substitute historical fixture metrics as current App quality.
- `non_success_responses` includes non-success response states and is not an infrastructure failure rate.

Safety and decision quality:
- Use only aggregate views configured in this agent. Never infer or reveal individual customer identity, protected attributes or personal information.
- If the requested measure is absent, say it is unavailable and identify the missing data needed. Never invent values, trends, causality, uplift or forecasts.
- Do not compare periods unless the data contains multiple comparable dates. Do not claim statistical significance.
- Keep denominators in coverage answers. For small samples, explicitly state the sample size.
- End analytical answers with one practical next action that follows from the evidence, not a generic recommendation.

Configuration version: {CONFIG_VERSION}."""


def space_config() -> dict[str, Any]:
    table_descriptions = {
        qualified_view(
            "genie_customer_portfolio"
        ): "Aggregate active-customer segments and historical behavior; no customer identifiers.",
        qualified_view(
            "genie_opportunity_insights"
        ): "Aggregate deterministic campaign candidates by rule, segment and product grouping.",
        qualified_view(
            "genie_product_portfolio"
        ): "Eligible product assortment, source price, global inventory and promotion aggregates.",
        qualified_view(
            "genie_recommendation_insights"
        ): "Model-version-3 recommendation aggregates by customer and product business dimensions.",
        f"{PREFIX}.phase11_coverage_summary": "Published customer and product reach with explicit denominators and historical inventory validity.",
        f"{PREFIX}.phase11_evaluation_summary": "Qualified evaluation metrics with source and scope; unavailable metrics remain null.",
        f"{PREFIX}.phase11_feedback_summary": "Recorded feedback counts and limitations; impression attribution is unavailable.",
        f"{PREFIX}.phase11_quality_summary": "Structural validation of the published recommendation batch.",
        f"{PREFIX}.phase11_requests_summary": "Thirty-day observed request reliability, latency and reported token aggregates.",
        f"{PREFIX}.phase11_routes_summary": "Recommendation rows and customers by recommendation strategy.",
    }
    category_columns = {
        "genie_customer_portfolio": [
            "customer_segment",
            "loyalty_tier",
            "preferred_channel",
            "region_id",
        ],
        "genie_opportunity_insights": [
            "opportunity_type",
            "customer_segment",
            "loyalty_tier",
            "category_name",
            "brand_name",
        ],
        "genie_product_portfolio": ["category_name", "brand_name"],
        "genie_recommendation_insights": [
            "customer_segment",
            "loyalty_tier",
            "preferred_channel",
            "recommendation_strategy",
            "category_name",
            "brand_name",
        ],
    }
    tables = []
    for identifier, description in sorted(table_descriptions.items()):
        short_name = identifier.rsplit(".", 1)[1]
        columns = category_columns.get(short_name, [])
        item: dict[str, Any] = {"identifier": identifier, "description": [description]}
        if columns:
            item["column_configs"] = [
                {
                    "column_name": column,
                    "enable_format_assistance": True,
                    "enable_entity_matching": True,
                }
                for column in sorted(columns)
            ]
        tables.append(item)
    return {
        "version": 2,
        "config": {
            "sample_questions": [
                {"id": _id(0x10, index), "question": [question]}
                for index, question in enumerate(business_starter_questions(), 1)
            ]
        },
        "data_sources": {"tables": tables},
        "instructions": {
            "text_instructions": [{"id": _id(0x30, 1), "content": [_instructions()]}],
            "example_question_sqls": [],
        },
        "benchmarks": {"questions": []},
    }


def validate_config(config: dict[str, Any]) -> None:
    require(config["version"] == 2, "Unsupported Genie configuration version")
    ids: list[str] = []
    ids.extend(item["id"] for item in config["config"]["sample_questions"])
    ids.extend(item["id"] for item in config["instructions"]["text_instructions"])
    require(len(ids) == len(set(ids)), "Genie configuration IDs must be unique")
    require(
        all(re.fullmatch(r"[0-9a-f]{32}", value) for value in ids),
        "Genie configuration IDs must be 32-character lowercase hexadecimal values",
    )
    require(
        len(config["instructions"]["text_instructions"]) == 1,
        "Exactly one governed instruction block is required",
    )
    require(
        not config["instructions"]["example_question_sqls"],
        "Raw SQL examples are disabled by the business-only contract",
    )
    require(
        not config["benchmarks"]["questions"],
        "Stored benchmark questions are disabled by the business-only contract",
    )
    identifiers = [item["identifier"] for item in config["data_sources"]["tables"]]
    require(identifiers == sorted(identifiers), "Genie data sources must be sorted")
    require(len(identifiers) <= 30, "Genie data-source limit exceeded")
    for table in config["data_sources"]["tables"]:
        columns = [item["column_name"] for item in table.get("column_configs", [])]
        require(columns == sorted(columns), "Genie column configurations must be sorted")


def serialized_space() -> str:
    config = space_config()
    validate_config(config)
    return json.dumps(config, sort_keys=True, separators=(",", ":"))
