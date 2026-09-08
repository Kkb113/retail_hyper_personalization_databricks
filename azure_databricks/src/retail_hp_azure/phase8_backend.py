"""Identity-authenticated Databricks adapter; fixed views and parameterized values only."""
# ruff: noqa: S608 -- interpolated identifiers are closed module constants, values are parameters.

from __future__ import annotations

import json
import threading
from typing import Any

from retail_hp_azure.config import HOST
from retail_hp_azure.phase6 import ENDPOINT_NAME
from retail_hp_azure.phase7 import OperationalEvent, WriteResult
from retail_hp_azure.phase7_delta import DeltaOperationalStore, _execute, _rows
from retail_hp_azure.phase8 import Provenance
from retail_hp_azure.phase8_semantic import (
    EMBEDDING_ENDPOINT,
    EMBEDDING_MODEL,
    SemanticIndex,
    normalize,
)
from retail_hp_azure.safety import require
from retail_hp_azure.serving_client import (
    RecommendationClient,
    ServingRequest,
    authenticated_transport,
)

PRODUCT_VIEW = "intellify_databricks_demo.serving.tool_products"
CUSTOMER_VIEW = "intellify_databricks_demo.serving.tool_customers"
RECOMMENDATIONS = "intellify_databricks_demo.serving.customer_recommendations"
OPPORTUNITIES = "intellify_databricks_demo.serving.tool_opportunities"
QUALITY_VIEW = "intellify_databricks_demo.serving.tool_quality"
PRODUCT_COLUMNS = (
    "product_id, product_name, category_id, category_name, brand_name, base_price, "
    "currency_code, available_qty, active_discount_pct, CAST(inventory_snapshot_at AS STRING) "
    "AS inventory_snapshot_at"
)
RECOMMENDATION_COLUMNS = "r.product_id, r.rank, r.score, r.route, r.reason_codes"


class EmbeddingClient:
    """Existing shared pay-per-token endpoint; zero retries, finite session request quota."""

    def __init__(self, client: Any, *, max_calls: int = 32) -> None:
        require(0 < max_calls <= 64, "Invalid embedding session quota")
        self.client, self.remaining = client, max_calls
        self.tokens = 0
        self._quota_lock = threading.Lock()

    def embed(self, text: str) -> tuple[float, ...]:
        import requests

        require(0 < len(text) <= 300, "Embedding query length exceeds bound")
        # Reserve before network work. Failed calls still consume the session quota.
        with self._quota_lock:
            require(self.remaining > 0, "Embedding session quota exhausted")
            self.remaining -= 1
        endpoint = self.client.serving_endpoints.get(EMBEDDING_ENDPOINT)
        entities = endpoint.config.served_entities if endpoint.config else []
        require(
            len(entities or []) == 1
            and entities[0].foundation_model is not None
            and entities[0].foundation_model.name == EMBEDDING_MODEL,
            "Embedding endpoint identity drift",
        )
        response = requests.post(
            f"{HOST}/serving-endpoints/{EMBEDDING_ENDPOINT}/invocations",
            headers=self.client.config.authenticate(),
            json={"input": [text]},
            timeout=(5, 25),
            allow_redirects=False,
        )
        require(
            response.status_code == 200 and len(response.content) <= 100_000,
            "Embedding request failed",
        )
        body = response.json()
        require(len(body.get("data", [])) == 1, "Embedding response count mismatch")
        require(body["data"][0].get("index") == 0, "Embedding response index mismatch")
        with self._quota_lock:
            self.tokens += int(body.get("usage", {}).get("total_tokens", 0))
        return normalize(body["data"][0]["embedding"])


class DatabricksToolBackend:
    def __init__(
        self,
        client: Any,
        warehouse_id: str,
        *,
        index: SemanticIndex | None = None,
        embeddings: EmbeddingClient | None = None,
    ) -> None:
        self.client, self.warehouse_id = client, warehouse_id
        self.index, self.embeddings = index, embeddings
        self.store = DeltaOperationalStore(client, warehouse_id)

    def _query(self, statement: str, values: dict[str, Any]) -> list[dict[str, Any]]:
        from databricks.sdk.service.sql import StatementParameterListItem

        parameters = [
            StatementParameterListItem(
                name=key,
                value=None if value is None else str(value),
                type="BOOLEAN" if type(value) is bool else "STRING",
            )
            for key, value in values.items()
        ]
        return _rows(
            _execute(
                self.client,
                self.warehouse_id,
                statement,
                parameters=parameters,
                deadline_seconds=30,
            )
        )

    def _products(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        require(len(ids) <= 20, "Product lookup exceeds bound")
        return self._query(
            f"SELECT {PRODUCT_COLUMNS} FROM {PRODUCT_VIEW} "
            "WHERE array_contains(from_json(:ids, 'array<string>'), product_id) "
            "ORDER BY product_id LIMIT 20",  # noqa: S608
            {"ids": json.dumps(ids)},
        )

    def read(
        self,
        tool: str,
        arguments: dict[str, Any],
        request_id: str,
    ) -> tuple[list[dict[str, Any]], Provenance]:
        source, model = PRODUCT_VIEW, "not_applicable"
        rows: list[dict[str, Any]]
        if tool in {"get_product_details", "compare_products"}:
            ids = arguments.get("product_ids", [arguments.get("product_id")])
            rows = self._products(ids)
        elif tool == "search_products":
            require(
                arguments.get("region_id") is None,
                "REGIONAL_INVENTORY_UNAVAILABLE_NO_STORE_REGION_MAPPING",
            )
            require(
                self.index is not None and self.embeddings is not None,
                "SEMANTIC_SESSION_NOT_ENABLED",
            )
            eligible = self._query(
                f"SELECT to_json(sort_array(collect_list(product_id))) AS ids "
                f"FROM {PRODUCT_VIEW} WHERE (:category IS NULL OR category_id = :category) "
                "AND (:price IS NULL OR base_price <= CAST(:price AS DOUBLE)) "
                "AND (:promotion = false OR active_discount_pct > 0)",  # noqa: S608
                {
                    "category": arguments.get("category_id"),
                    "price": arguments.get("max_price"),
                    "promotion": arguments.get("promotion_only", False),
                },
            )
            ids = set(json.loads(eligible[0]["ids"]))
            assert self.index is not None
            require(ids <= self.index.vectors.keys(), "SEMANTIC_INDEX_REBUILD_REQUIRED")
            if not ids:
                rows = []
            else:
                assert self.index is not None and self.embeddings is not None
                ranked = self.index.rank(self.embeddings.embed(arguments["query"]), ids)
                selected = ranked[: arguments["top_n"]]
                facts = {row["product_id"]: row for row in self._products(selected)}
                # Reapply filters after the second read to handle concurrent source updates.
                rows = [
                    facts[pid]
                    for pid in selected
                    if pid in facts
                    and (
                        arguments.get("max_price") is None
                        or float(facts[pid]["base_price"]) <= arguments["max_price"]
                    )
                    and (
                        not arguments.get("category_id")
                        or facts[pid]["category_id"] == arguments["category_id"]
                    )
                    and (
                        not arguments.get("promotion_only")
                        or float(facts[pid]["active_discount_pct"]) > 0
                    )
                ]
            model = EMBEDDING_MODEL
        elif tool == "get_customer_360":
            source = CUSTOMER_VIEW
            rows = self._query(
                f"SELECT customer_id, customer_segment, loyalty_tier, preferred_channel, "
                f"region_id, CAST(behavior_as_of AS STRING) AS behavior_as_of, "
                f"purchase_count, browse_count FROM {CUSTOMER_VIEW} "
                "WHERE customer_id = :customer LIMIT 1",  # noqa: S608
                {"customer": arguments["customer_id"]},
            )
        elif tool in {"get_recommendations", "explain_recommendation", "simulate_scenario"}:
            source, model = RECOMMENDATIONS, "3"
            if tool == "simulate_scenario" or arguments.get("mode") == "realtime":
                endpoint = self.client.api_client.do(
                    "GET", f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}"
                )
                require(
                    endpoint.get("state", {}).get("suspend") == "NOT_SUSPENDED"
                    and endpoint.get("state", {}).get("ready") == "READY",
                    "RECOMMENDER_NOT_EXPLICITLY_RUNNING",
                )
                entities = endpoint.get("config", {}).get("served_entities", [])
                require(
                    len(entities) == 1 and entities[0].get("entity_version") == "3",
                    "Recommender version drift",
                )
                allowed = self._query(
                    f"SELECT to_json(collect_list(product_id)) AS ids FROM {PRODUCT_VIEW}", {}
                )  # noqa: S608
                scenario = tool == "simulate_scenario"
                request = ServingRequest(
                    request_id=request_id,
                    request_type="scenario" if scenario else "existing_customer",
                    customer_id=arguments["customer_id"],
                    scenario_id=arguments.get("scenario_id"),
                    top_n=arguments["top_n"],
                    favorite_category_id=arguments.get("favorite_category_id"),
                    price_sensitivity=arguments.get("price_sensitivity"),
                    excluded_product_ids_json=json.dumps(arguments.get("excluded_product_ids", [])),
                )
                predictions = RecommendationClient(
                    authenticated_transport(self.client.config.authenticate), timeout_seconds=30
                ).predict(request, eligible_ids=set(json.loads(allowed[0]["ids"])))
                rows = [
                    {
                        key: row[key]
                        for key in ("product_id", "rank", "score", "route", "reason_codes")
                    }
                    for row in predictions
                ]
                source = ENDPOINT_NAME
            else:
                rows = self._query(
                    f"SELECT {RECOMMENDATION_COLUMNS} FROM {RECOMMENDATIONS} r "
                    f"JOIN {PRODUCT_VIEW} p ON r.product_id = p.product_id "
                    "WHERE r.customer_id = :customer AND r.rank <= CAST(:top_n AS INT) "
                    "AND (:product IS NULL OR r.product_id = :product) "
                    "AND r.registered_model_version = '3' ORDER BY r.rank LIMIT 20",  # noqa: S608
                    {
                        "customer": arguments["customer_id"],
                        "top_n": arguments.get("top_n", 20),
                        "product": arguments.get("product_id"),
                    },
                )
        elif tool == "get_opportunities":
            source, model = OPPORTUNITIES, "3"
            rows = self._query(
                f"SELECT product_id, opportunity_type, discount_pct FROM {OPPORTUNITIES} "
                "WHERE customer_id = :customer ORDER BY discount_pct DESC, product_id LIMIT 20",
                {"customer": arguments["customer_id"]},  # noqa: S608
            )
        elif tool == "get_quality_summary":
            source, model = QUALITY_VIEW, "3"
            rows = self._query(f"SELECT * FROM {QUALITY_VIEW} LIMIT 1", {})  # noqa: S608
        else:
            raise ValueError("Unsupported backend tool")
        snapshot = self._query(
            f"SELECT CAST(max(inventory_snapshot_at) AS STRING) AS source_at FROM {PRODUCT_VIEW}",
            {},
        )
        require(
            len(snapshot) == 1 and bool(snapshot[0].get("source_at")),
            "Source timestamp unavailable",
        )
        source_at = str(snapshot[0]["source_at"])
        if tool == "get_customer_360" and rows:
            source_at = str(rows[0]["behavior_as_of"])
        provenance = Provenance(
            source=source,
            source_timestamp=source_at,
            data_version=(
                self.index.version
                if tool == "search_products" and self.index
                else "retail_hp_transfer_v1"
            ),
            model_version=model,
        )
        return rows, provenance

    def write_feedback(self, event: OperationalEvent) -> WriteResult:
        return self.store.write(event)
