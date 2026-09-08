"""Portable, deterministic inference for the frozen retail recommender experts.

The runtime deliberately has no Spark, database, UI, Azure, or LLM dependency.  It
loads only the sealed synthetic snapshot and frozen model artifacts supplied by
MLflow, which makes the same implementation usable by a triggered validation job
and (in a later phase) model serving.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import xgboost as xgb
from scipy.sparse import load_npz  # type: ignore[import-untyped]

from retail_hp_azure.pickle_compat import load_approved_joblib

MODEL_VERSION = "retail_hyper_personalization_demo_v1"
POLICY_VERSION = "adaptive_router_v1"
RUNTIME_VERSION = "azure_functional_recommender_v2_deterministic_ties"
DEFAULT_AS_OF = pd.Timestamp("2026-01-01T00:00:00Z")
REQUEST_TYPES = {"existing_customer", "new_customer", "scenario"}
PROFILE_FIELDS = (
    "RegionID",
    "State",
    "ClimateZone",
    "CustomerSegment",
    "LoyaltyTier",
    "PreferredChannel",
    "FavoriteCategoryID",
    "FavoriteBrandID",
    "PriceSensitivity",
    "ColorPreference",
    "CategoryAffinityScore",
    "BrandAffinityScore",
)


class RecommendationInputError(ValueError):
    """Typed, safe validation error for an invalid inference record."""


@dataclass(frozen=True)
class Request:
    request_id: str
    request_type: str
    customer_id: str | None
    scenario_id: str | None
    top_n: int
    as_of: pd.Timestamp
    profile: dict[str, Any]
    session_events: list[dict[str, Any]]
    excluded_products: set[str]

    @property
    def entity_id(self) -> str:
        return self.customer_id or self.scenario_id or self.request_id


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    result = str(value).strip()
    return result or None


def _json_list(value: Any, field: str) -> list[Any]:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return []
    try:
        result = json.loads(str(value)) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise RecommendationInputError(f"{field} must be valid JSON") from exc
    if not isinstance(result, list):
        raise RecommendationInputError(f"{field} must be a JSON list")
    return result


def parse_request(row: pd.Series) -> Request:
    request_id = _text(row.get("request_id"))
    request_type = _text(row.get("request_type"))
    if not request_id:
        raise RecommendationInputError("request_id is required")
    if request_type not in REQUEST_TYPES:
        raise RecommendationInputError(
            "request_type must be existing_customer, new_customer, or scenario"
        )
    customer_id = _text(row.get("customer_id"))
    scenario_id = _text(row.get("scenario_id"))
    if request_type == "existing_customer" and not customer_id:
        raise RecommendationInputError("customer_id is required for existing_customer")
    if request_type == "scenario" and not scenario_id:
        raise RecommendationInputError("scenario_id is required for scenario")
    try:
        top_n = int(row.get("top_n", 10))
    except (TypeError, ValueError) as exc:
        raise RecommendationInputError("top_n must be an integer") from exc
    if not 1 <= top_n <= 50:
        raise RecommendationInputError("top_n must be between 1 and 50")
    raw_as_of = _text(row.get("as_of"))
    try:
        as_of = pd.Timestamp(raw_as_of) if raw_as_of else DEFAULT_AS_OF
    except ValueError as exc:
        raise RecommendationInputError("as_of must be an ISO-8601 timestamp") from exc
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")
    else:
        as_of = as_of.tz_convert("UTC")
    profile = {}
    for field in PROFILE_FIELDS:
        input_name = _snake(field)
        value = row.get(input_name)
        if value is not None and not pd.isna(value):
            profile[field] = value
    raw_events = _json_list(row.get("session_events_json"), "session_events_json")
    if not all(isinstance(item, dict) for item in raw_events):
        raise RecommendationInputError("session_events_json entries must be objects")
    allowed_event_keys = {"event_type", "product_id", "event_time"}
    events: list[dict[str, Any]] = []
    for item in raw_events:
        unknown = set(item) - allowed_event_keys
        if unknown:
            raise RecommendationInputError("session event contains unsupported fields")
        event_type = _text(item.get("event_type"))
        product_id = _text(item.get("product_id"))
        if event_type not in {"browse", "search_click", "wishlist", "cart_add"} or not product_id:
            raise RecommendationInputError("session event type or product_id is invalid")
        events.append({"event_type": event_type, "product_id": product_id})
    excluded = {
        _text(item)
        for item in _json_list(row.get("excluded_product_ids_json"), "excluded_product_ids_json")
    }
    return Request(
        request_id=request_id,
        request_type=request_type,
        customer_id=customer_id,
        scenario_id=scenario_id,
        top_n=top_n,
        as_of=as_of,
        profile=profile,
        session_events=events,
        excluded_products={item for item in excluded if item},
    )


def _snake(name: str) -> str:
    import re

    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def _percentile(values: pd.Series) -> pd.Series:
    return values.round(12).rank(method="average", pct=True).fillna(0.0)


class AdaptiveRetailRecommender:
    """Execute frozen retrieval, ranking, routing, and business-rule artifacts."""

    def __init__(self, data_root: str | Path, model_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self.model_root = Path(model_root)
        self._load()

    def _read(self, name: str) -> pd.DataFrame:
        base = self.data_root / "data_cache"
        path = (
            base
            / ("profiles" if name == "customer_profile_history" else "base")
            / f"{name}.parquet"
        )
        return pd.read_parquet(path)

    def _load(self) -> None:
        names = (
            "brands",
            "browsing_events",
            "cart_events",
            "customer_preferences",
            "customers",
            "inventory",
            "product_categories",
            "products",
            "promotions",
            "regions",
            "sales_order_lines",
            "sales_orders",
            "search_events",
            "weather",
            "wishlist",
            "customer_profile_history",
        )
        self.tables = {name: self._read(name) for name in names}
        products = self.tables["products"].copy()
        products["ProductID"] = products.ProductID.astype(str)
        products = products.merge(
            self.tables["product_categories"][
                ["CategoryID", "ParentCategoryID", "CategoryName", "DepartmentName"]
            ],
            on="CategoryID",
            how="left",
            validate="many_to_one",
        ).merge(
            self.tables["brands"][["BrandID", "BrandTier", "ActiveFlag"]].rename(
                columns={"ActiveFlag": "BrandActiveFlag"}
            ),
            on="BrandID",
            how="left",
            validate="many_to_one",
        )
        price_rank = products.BasePrice.astype(float).rank(method="first", pct=True)
        products["PriceBand"] = pd.cut(
            price_rank,
            [0, 0.25, 0.5, 0.75, 1],
            labels=["value", "standard", "premium", "luxury"],
            include_lowest=True,
        ).astype("string")
        latest_day = pd.to_datetime(self.tables["inventory"].SnapshotDate).max()
        available = (
            self.tables["inventory"][
                pd.to_datetime(self.tables["inventory"].SnapshotDate).eq(latest_day)
            ]
            .groupby("ProductID", as_index=False)
            .AvailableQty.sum()
        )
        products = products.merge(available, on="ProductID", how="left")
        active = products.ActiveFlag.astype(bool) & products.BrandActiveFlag.astype(bool)
        self.products = products[active & products.AvailableQty.fillna(0).gt(0)].copy()
        self.eligible_ids = set(self.products.ProductID)
        self.product_by_id = self.products.set_index("ProductID", drop=False)

        profiles = (
            self.tables["customer_profile_history"]
            .sort_values(["CustomerID", "EffectiveFrom", "ProfileVersion"], kind="stable")
            .drop_duplicates("CustomerID", keep="last")
        )
        self.profiles = profiles.set_index(profiles.CustomerID.astype(str), drop=False)
        self.profiles.index.name = None
        self.events = self._events()
        self._build_popularity()
        self._build_copurchase()

        retrieval = self.model_root / "retrieval_v2"
        factors = np.load(retrieval / "als_factors.npz")
        self.user_factors = factors["user_factors"]
        self.item_factors = factors["item_factors"]
        mappings = json.loads((retrieval / "factor_mappings.json").read_text(encoding="utf-8"))
        self.customer_map = {
            str(key): int(value) for key, value in mappings["customer_to_index"].items()
        }
        self.product_map = {
            str(key): int(value) for key, value in mappings["product_to_index"].items()
        }
        self.index_to_product = {value: key for key, value in self.product_map.items()}
        self.metadata_factor_model = load_approved_joblib(
            retrieval / "metadata_factor_model.joblib"
        )
        self.content_matrix = load_npz(retrieval / "content_matrix.npz")
        self.content_neighbors = load_approved_joblib(retrieval / "content_neighbor_model.joblib")
        self.content_product_map = {
            str(product): index for index, product in enumerate(self.tables["products"].ProductID)
        }
        self.retrieval_policy = json.loads(
            (retrieval / "retrieval_metadata.json").read_text(encoding="utf-8")
        )

        ranker = self.model_root / "ranker_v2"
        self.warm_preprocessor = load_approved_joblib(ranker / "ranker_preprocessor.joblib")
        self.warm_ranker = xgb.Booster(model_file=str(ranker / "shared_ranker.json"))
        self.warm_contract = json.loads((ranker / "inference_contract.json").read_text())
        cold = self.model_root / "cold_start_v1"
        self.cold_preprocessor = load_approved_joblib(cold / "cold_start_preprocessor.joblib")
        self.cold_ranker = xgb.Booster(model_file=str(cold / "cold_start_ranker.json"))
        self.cold_metadata = json.loads((cold / "cold_start_metadata.json").read_text())
        self.routing_policy = json.loads(
            (self.model_root / "adaptive_router_v1" / "routing_policy.json").read_text()
        )

    def _events(self) -> pd.DataFrame:
        rows = []
        specs = (
            ("browsing_events", "ProductID", "EventTime", "browse", 1.0),
            ("cart_events", "ProductID", "EventTime", "cart_add", 3.0),
            ("wishlist", "ProductID", "AddedDate", "wishlist", 2.0),
        )
        for table, product, timestamp, event_type, weight in specs:
            frame = self.tables[table]
            if table == "cart_events":
                frame = frame[frame.Action.astype(str).str.lower().eq("add")]
            rows.append(
                pd.DataFrame(
                    {
                        "CustomerID": frame.CustomerID.astype(str),
                        "ProductID": frame[product].astype(str),
                        "EventTime": pd.to_datetime(frame[timestamp], utc=True),
                        "EventType": event_type,
                        "Weight": weight,
                    }
                )
            )
        search = self.tables["search_events"].dropna(subset=["ClickedProductID"])
        rows.append(
            pd.DataFrame(
                {
                    "CustomerID": search.CustomerID.astype(str),
                    "ProductID": search.ClickedProductID.astype(str),
                    "EventTime": pd.to_datetime(search.EventTime, utc=True),
                    "EventType": "search_click",
                    "Weight": 1.5,
                }
            )
        )
        orders = self.tables["sales_orders"]
        orders = orders[~orders.OrderStatus.astype(str).str.lower().isin({"cancelled", "returned"})]
        purchase = orders[["OrderID", "CustomerID", "OrderDate"]].merge(
            self.tables["sales_order_lines"][["OrderID", "ProductID", "LineAmount", "UnitPrice"]],
            on="OrderID",
            how="inner",
        )
        rows.append(
            pd.DataFrame(
                {
                    "CustomerID": purchase.CustomerID.astype(str),
                    "ProductID": purchase.ProductID.astype(str),
                    "EventTime": pd.to_datetime(purchase.OrderDate, utc=True),
                    "EventType": "purchase",
                    "Weight": 5.0,
                    "Amount": purchase.LineAmount.astype(float),
                    "UnitPrice": purchase.UnitPrice.astype(float),
                }
            )
        )
        result = pd.concat(rows, ignore_index=True)
        result["Amount"] = result.get("Amount", 0.0).fillna(0.0)
        result["UnitPrice"] = result.get("UnitPrice", 0.0).fillna(0.0)
        return result.sort_values(["EventTime", "CustomerID", "ProductID"], kind="stable")

    def _build_popularity(self) -> None:
        scored = self.events.groupby("ProductID").Weight.sum().sort_values(ascending=False)
        self.global_scores = scored[scored.index.isin(self.eligible_ids)]
        joined = self.events.merge(
            self.profiles[["CustomerID", "RegionID", "CustomerSegment"]],
            on="CustomerID",
            how="left",
        )
        self.regional_scores = {
            str(key): group.groupby("ProductID").Weight.sum().sort_values(ascending=False)
            for key, group in joined.groupby("RegionID", dropna=True)
        }
        self.segment_scores = {
            str(key): group.groupby("ProductID").Weight.sum().sort_values(ascending=False)
            for key, group in joined.groupby("CustomerSegment", dropna=True)
        }

    def _build_copurchase(self) -> None:
        valid = self.tables["sales_orders"]
        valid = valid[~valid.OrderStatus.astype(str).str.lower().isin({"cancelled", "returned"})]
        lines = (
            valid[["OrderID"]]
            .merge(self.tables["sales_order_lines"][["OrderID", "ProductID"]], on="OrderID")
            .drop_duplicates()
        )
        counts: dict[str, dict[str, int]] = {}
        for products in lines.groupby("OrderID").ProductID:
            values = sorted(set(map(str, products[1])))
            for left in values:
                target = counts.setdefault(left, {})
                for right in values:
                    if right != left:
                        target[right] = target.get(right, 0) + 1
        self.copurchase = {
            key: sorted(value.items(), key=lambda item: (-item[1], item[0]))
            for key, value in counts.items()
        }

    def _profile(self, request: Request) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        if request.customer_id and request.customer_id in self.profiles.index:
            row = self.profiles.loc[request.customer_id]
            profile = {field: row.get(field) for field in PROFILE_FIELDS}
            profile["ProfileVersion"] = int(row.get("ProfileVersion", 0))
            profile["ConsentStatus"] = row.get("ConsentStatus", "Unknown")
            profile["FreshnessStatus"] = row.get("FreshnessStatus", "Unknown")
        profile.update(request.profile)
        return profile

    def _history(self, request: Request) -> pd.DataFrame:
        if not request.customer_id:
            return self.events.iloc[0:0]
        return self.events[
            self.events.CustomerID.eq(request.customer_id) & self.events.EventTime.lt(request.as_of)
        ]

    @staticmethod
    def _source_frame(values: list[tuple[str, float]], source: str, maximum: int) -> pd.DataFrame:
        # Never let platform-specific equal-score ordering decide candidate quotas.
        values = sorted(
            ((p, round(float(s), 12)) for p, s in values), key=lambda item: (-item[1], item[0])
        )
        rows = [
            {"ProductID": product, "Source": source, "SourceRank": rank, "SourceScore": score}
            for rank, (product, score) in enumerate(values[:maximum], 1)
        ]
        return pd.DataFrame(rows, columns=["ProductID", "Source", "SourceRank", "SourceScore"])

    def _ordered_scores(
        self, scores: pd.Series | None, maximum: int = 160
    ) -> list[tuple[str, float]]:
        if scores is None:
            return []
        return sorted(
            [
                (str(product), float(score))
                for product, score in scores.items()
                if str(product) in self.eligible_ids
            ],
            key=lambda item: (-round(item[1], 12), item[0]),
        )[:maximum]

    def _retrieval_sources(
        self, request: Request, profile: dict[str, Any], history: pd.DataFrame
    ) -> dict[str, pd.DataFrame]:
        sources: dict[str, pd.DataFrame] = {}
        maximum = 160
        if request.customer_id in self.customer_map:
            user = self.user_factors[self.customer_map[request.customer_id]]
            score = np.round(self.item_factors.astype(np.float64) @ user.astype(np.float64), 12)
            order = np.argsort(-score, kind="stable")
            values = [
                (self.index_to_product[int(index)], float(score[int(index)]))
                for index in order
                if self.index_to_product.get(int(index)) in self.eligible_ids
            ]
            sources["als"] = self._source_frame(values, "als", maximum)
        if profile:
            input_frame = pd.DataFrame(
                [
                    {
                        field: profile.get(field)
                        for field in (
                            "RegionID",
                            "CustomerSegment",
                            "LoyaltyTier",
                            "PreferredChannel",
                            "FavoriteCategoryID",
                            "FavoriteBrandID",
                            "PriceSensitivity",
                            "ClimateZone",
                            "CategoryAffinityScore",
                            "BrandAffinityScore",
                        )
                    }
                ]
            )
            numeric_metadata = {"CategoryAffinityScore", "BrandAffinityScore"}
            for column in input_frame:
                if column in numeric_metadata:
                    input_frame[column] = pd.to_numeric(
                        input_frame[column], errors="coerce"
                    ).fillna(0.0)
                else:
                    input_frame[column] = input_frame[column].astype("string").fillna("Unknown")
            predicted = self.metadata_factor_model.predict(input_frame)[0]
            score = np.round(
                self.item_factors.astype(np.float64) @ predicted.astype(np.float64), 12
            )
            order = np.argsort(-score, kind="stable")
            values = [
                (self.index_to_product[int(index)], float(score[int(index)]))
                for index in order
                if self.index_to_product.get(int(index)) in self.eligible_ids
            ]
            sources["metadata"] = self._source_frame(values, "metadata", maximum)
        global_values = self._ordered_scores(self.global_scores)
        sources["global_popularity"] = self._source_frame(
            global_values, "global_popularity", maximum
        )
        region = _text(profile.get("RegionID"))
        segment = _text(profile.get("CustomerSegment"))
        sources["regional_popularity"] = self._source_frame(
            self._ordered_scores(self.regional_scores.get(region or "")),
            "regional_popularity",
            maximum,
        )
        sources["segment_popularity"] = self._source_frame(
            self._ordered_scores(self.segment_scores.get(segment or "")),
            "segment_popularity",
            maximum,
        )
        favorite = _text(profile.get("FavoriteCategoryID"))
        preferred = [
            (product, score)
            for product, score in global_values
            if _text(self.product_by_id.loc[product].CategoryID) == favorite
        ]
        sources["preferred_category"] = self._source_frame(preferred, "preferred_category", maximum)
        anchors = list(
            history.sort_values("EventTime", ascending=False).ProductID.astype(str).head(5)
        )
        anchors = [event["product_id"] for event in request.session_events] + anchors
        content_scores: dict[str, float] = {}
        session_scores: dict[str, float] = {}
        content_product_ids = self.tables["products"].ProductID.to_numpy()
        for position, anchor in enumerate(dict.fromkeys(anchors)):
            index = self.content_product_map.get(anchor)
            if index is None:
                continue
            distances, indices = self.content_neighbors.kneighbors(
                self.content_matrix[index], n_neighbors=self.content_matrix.shape[0]
            )
            target = session_scores if position < len(request.session_events) else content_scores
            neighbors = sorted(
                zip(distances[0], indices[0], strict=True),
                key=lambda pair: (
                    round(float(pair[0]), 12),
                    str(content_product_ids[int(pair[1])]),
                ),
            )[:31]
            for distance, neighbor in neighbors:
                product = str(content_product_ids[int(neighbor)])
                if product != anchor and product in self.eligible_ids:
                    target[product] = max(target.get(product, 0.0), 1.0 - float(distance))
        for source, source_scores in (
            ("content", content_scores),
            ("session_intent", session_scores),
        ):
            ordered = sorted(source_scores.items(), key=lambda item: (-item[1], item[0]))
            sources[source] = self._source_frame(ordered, source, maximum)
        copurchase_scores: dict[str, float] = {}
        for anchor in history[history.EventType.eq("purchase")].ProductID.astype(str).tail(5):
            for product, count in self.copurchase.get(anchor, [])[:30]:
                if product in self.eligible_ids:
                    copurchase_scores[product] = copurchase_scores.get(product, 0.0) + count
        sources["copurchase"] = self._source_frame(
            sorted(copurchase_scores.items(), key=lambda item: (-item[1], item[0])),
            "copurchase",
            maximum,
        )
        promotions = self.tables["promotions"].copy()
        start = pd.to_datetime(promotions.StartDate, utc=True)
        end = pd.to_datetime(promotions.EndDate, utc=True) + pd.Timedelta(days=1)
        promotions = promotions[
            promotions.ActiveFlag.astype(bool) & start.le(request.as_of) & end.gt(request.as_of)
        ]
        promo = (
            self.products.merge(
                promotions[["CategoryID", "DiscountPct"]], on="CategoryID", how="inner"
            )
            .groupby("ProductID")
            .DiscountPct.max()
            .sort_values(ascending=False)
        )
        sources["promotion"] = self._source_frame(self._ordered_scores(promo), "promotion", maximum)
        return sources

    def _cohort(self, event_count: int) -> str:
        if event_count == 0:
            return "ColdStartNatural"
        return "LowHistory" if event_count <= 10 else "Known"

    def _combine(self, sources: dict[str, pd.DataFrame], cohort: str) -> pd.DataFrame:
        quotas = self.retrieval_policy["source_quotas"][cohort]
        weights = {**self.retrieval_policy["source_weights"], "copurchase": 0.60, "promotion": 0.35}
        product_rows: dict[str, dict[str, Any]] = {}
        effective_quotas = {**quotas, "copurchase": 40, "promotion": 30}
        for source, quota in effective_quotas.items():
            frame = sources.get(source)
            if frame is None or frame.empty or int(quota) <= 0:
                continue
            for item in frame.head(int(quota)).itertuples(index=False):
                entry = product_rows.setdefault(
                    str(item.ProductID), {"score": 0.0, "sources": [], "ranks": {}}
                )
                entry["score"] += float(weights[source]) / float(item.SourceRank)
                entry["sources"].append(source)
                entry["ranks"][source] = int(item.SourceRank)
        rows = []
        for product, entry in sorted(
            product_rows.items(),
            key=lambda item: (-item[1]["score"], -len(item[1]["sources"]), item[0]),
        )[:200]:
            row = {
                "ProductID": product,
                "PreRankScore": entry["score"],
                "CandidateSources": ";".join(sorted(entry["sources"])),
                "CandidateSourceCount": len(entry["sources"]),
                "RetrievalCohort": cohort,
            }
            for source in weights:
                row[f"source_{source}"] = int(source in entry["sources"])
                row[f"rank_{source}"] = entry["ranks"].get(source, np.nan)
            rows.append(row)
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["CandidateRank"] = np.arange(1, len(frame) + 1)
        return frame

    def _history_stats(self, history: pd.DataFrame, request: Request) -> dict[str, Any]:
        if history.empty:
            return {
                "event_count": 0,
                "event_diversity": 0,
                "days_since": 3650.0,
                "purchase_count": 0,
            }
        days = max(0.0, (request.as_of - history.EventTime.max()).total_seconds() / 86400)
        return {
            "event_count": len(history),
            "event_diversity": history.EventType.nunique(),
            "days_since": days,
            "purchase_count": int(history.EventType.eq("purchase").sum()),
        }

    def _behavioral_confidence(self, stats: dict[str, Any]) -> float:
        settings = self.routing_policy["behavioral_confidence"]
        weights = settings["behavioral_weights"]
        count = min(
            1.0, math.log1p(stats["event_count"]) / math.log1p(settings["event_count_reference"])
        )
        diversity = min(1.0, stats["event_diversity"] / settings["event_diversity_reference"])
        recency = math.exp(-stats["days_since"] * math.log(2) / settings["recency_half_life_days"])
        stability = min(1.0, stats["event_count"] / 20.0)
        return float(
            np.clip(
                weights["event_count"] * count
                + weights["event_diversity"] * diversity
                + weights["recency"] * recency
                + weights["stability"] * stability,
                0,
                1,
            )
        )

    def _metadata_confidence(self, profile: dict[str, Any]) -> float:
        settings = self.routing_policy["metadata_confidence"]
        known = sum(_text(profile.get(field)) is not None for field in PROFILE_FIELDS[:10]) / 10
        consent = (
            1.0
            if str(profile.get("ConsentStatus", "")).lower() in {"granted", "active"}
            else settings["unknown_consent_score"]
        )
        weights = settings["metadata_weights"]
        return float(
            np.clip(
                weights["completeness"] * known
                + weights["freshness"] * settings["assumed_static_freshness_score"]
                + weights["consent"] * consent
                + weights["calibration"] * settings["calibrated_model_score"],
                0,
                1,
            )
        )

    def _warm_features(
        self,
        candidates: pd.DataFrame,
        request: Request,
        profile: dict[str, Any],
        history: pd.DataFrame,
        stats: dict[str, Any],
        behavioral_confidence: float,
    ) -> pd.DataFrame:
        frame = pd.DataFrame(index=candidates.index)
        frame["signal_collaborative"] = candidates.get("source_als", 0)
        frame["signal_content"] = candidates.get("source_content", 0).astype(int) | candidates.get(
            "source_session_intent", 0
        ).astype(int)
        frame["signal_preference"] = candidates.get("source_metadata", 0).astype(
            int
        ) | candidates.get("source_preferred_category", 0).astype(int)
        frame["signal_regional"] = candidates.get("source_regional_popularity", 0)
        frame["signal_popularity"] = candidates.get("source_segment_popularity", 0).astype(
            int
        ) | candidates.get("source_global_popularity", 0).astype(int)
        for field in ("CustomerSegment", "LoyaltyTier", "PreferredChannel", "PriceSensitivity"):
            frame[field] = profile.get(field, "Unknown")
        product = self.product_by_id.loc[candidates.ProductID].reset_index(drop=True)
        for field in (
            "CategoryID",
            "DepartmentName",
            "BrandTier",
            "BasePrice",
            "CostPrice",
            "MarginPct",
            "Season",
            "Color",
            "Size",
        ):
            frame[field] = product[field].to_numpy()
        favorite_category = _text(profile.get("FavoriteCategoryID"))
        favorite_brand = _text(profile.get("FavoriteBrandID"))
        frame["favorite_category_match"] = (
            product.CategoryID.astype(str).eq(favorite_category).astype(int).to_numpy()
        )
        frame["favorite_brand_match"] = (
            product.BrandID.astype(str).eq(favorite_brand).astype(int).to_numpy()
        )
        favorite_department = set(
            self.products[
                self.products.CategoryID.astype(str).eq(favorite_category)
            ].DepartmentName.astype(str)
        )
        frame["same_department_as_favorite"] = (
            product.DepartmentName.astype(str).isin(favorite_department).astype(int).to_numpy()
        )
        event_names = {
            "browse": "browse",
            "search_click": "search_click",
            "cart_add": "cart_add",
            "purchase": "purchase",
        }
        for event, label in event_names.items():
            frame[f"customer_total_{label}_count"] = int(history.EventType.eq(event).sum())
        frame["customer_total_positive_rating_count"] = 0
        for days in (30, 90):
            recent = history[history.EventTime.ge(request.as_of - pd.Timedelta(days=days))]
            for event in ("browse", "cart_add", "purchase"):
                frame[f"customer_recent_{days}d_{event}_count"] = int(
                    recent.EventType.eq(event).sum()
                )
        product_totals = (
            self.events.groupby(["ProductID", "EventType"]).size().unstack(fill_value=0)
        )
        for event, label in (("browse", "browse"), ("cart_add", "cart"), ("purchase", "purchase")):
            frame[f"product_global_{label}_count"] = [
                int(product_totals.get(event, {}).get(pid, 0))
                if hasattr(product_totals.get(event, {}), "get")
                else 0
                for pid in candidates.ProductID
            ]
        for days in (30, 90):
            recent = self.events[self.events.EventTime.ge(request.as_of - pd.Timedelta(days=days))]
            totals = recent.groupby(["ProductID", "EventType"]).size().unstack(fill_value=0)
            for event, label in (
                ("browse", "browse"),
                ("cart_add", "cart"),
                ("purchase", "purchase"),
            ):
                name = f"product_recent_{days}d_{label}_count"
                if name in self.warm_preprocessor.feature_names_in_:
                    frame[name] = [
                        int(totals.get(event, {}).get(pid, 0))
                        if hasattr(totals.get(event, {}), "get")
                        else 0
                        for pid in candidates.ProductID
                    ]
        pair = history.groupby(["ProductID", "EventType"]).size().unstack(fill_value=0)
        for event, label in (
            ("browse", "browse"),
            ("search_click", "search_click"),
            ("cart_add", "cart_add"),
            ("purchase", "purchase"),
        ):
            frame[f"prior_{label}_count"] = [
                int(pair.get(event, {}).get(pid, 0)) if hasattr(pair.get(event, {}), "get") else 0
                for pid in candidates.ProductID
            ]
        frame["prior_positive_rating_count"] = 0
        for days in (30, 90):
            recent = history[history.EventTime.ge(request.as_of - pd.Timedelta(days=days))]
            counts = recent.groupby("ProductID").size()
            frame[f"recent_{days}d_pair_interaction_count"] = (
                candidates.ProductID.map(counts).fillna(0).astype(int)
            )
        last_pair = history.groupby("ProductID").EventTime.max()
        frame["days_since_pair_interaction"] = [
            max(0.0, (request.as_of - last_pair[pid]).total_seconds() / 86400)
            if pid in last_pair
            else 3650.0
            for pid in candidates.ProductID
        ]
        frame["customer_days_since_last_interaction"] = stats["days_since"]
        purchases = history[history.EventType.eq("purchase")]
        frame["customer_days_since_last_purchase"] = (
            max(0.0, (request.as_of - purchases.EventTime.max()).total_seconds() / 86400)
            if not purchases.empty
            else 3650.0
        )
        frame["customer_average_order_value"] = (
            purchases.Amount.mean() if not purchases.empty else 0.0
        )
        frame["customer_average_item_price"] = (
            purchases.UnitPrice.mean() if not purchases.empty else self.products.BasePrice.median()
        )
        frame["customer_discount_affinity"] = 0.0
        frame["customer_purchase_rate"] = stats["purchase_count"] / max(1, stats["event_count"])
        event_product = self.events.groupby("ProductID").size()
        purchase_product = (
            self.events[self.events.EventType.eq("purchase")].groupby("ProductID").size()
        )
        frame["product_purchase_rate"] = [
            purchase_product.get(pid, 0) / max(1, event_product.get(pid, 0))
            for pid in candidates.ProductID
        ]
        frame["product_popularity_score"] = _percentile(
            candidates.ProductID.map(self.global_scores).fillna(0)
        )
        frame["product_average_rating"] = 0.0
        frame["product_rating_count"] = 0
        purchased_ids = set(purchases.ProductID)
        frame["previous_purchase_indicator"] = candidates.ProductID.isin(purchased_ids).astype(int)
        durable = product.DepartmentName.fillna("").str.contains(
            "Electronics|Furniture|Appliance|Equipment", case=False, regex=True
        )
        frame["repeatable_category_indicator"] = (~durable).astype(int).to_numpy()
        average_price = float(frame["customer_average_item_price"].iloc[0])
        frame["price_to_customer_average_ratio"] = product.BasePrice.astype(float).to_numpy() / max(
            average_price, 0.01
        )
        frame["price_distance_from_customer_average"] = abs(
            product.BasePrice.astype(float).to_numpy() - average_price
        )
        frame["global_available_qty"] = product.AvailableQty.fillna(0).to_numpy()
        frame["is_globally_available"] = 1
        frame["regional_available_qty"] = product.AvailableQty.fillna(0).to_numpy()
        frame["is_regionally_available"] = 1
        frame["served_month"] = request.as_of.month
        frame["served_day_of_week"] = request.as_of.dayofweek
        frame["served_is_weekend"] = int(request.as_of.dayofweek >= 5)
        frame["served_hour"] = request.as_of.hour
        frame["region_climate_zone"] = profile.get("ClimateZone", "Unknown")
        weather = self.tables["weather"]
        weather = weather[weather.RegionID.astype(str).eq(_text(profile.get("RegionID")))]
        weather = weather[pd.to_datetime(weather.Date, utc=True).le(request.as_of)]
        weather_row = weather.sort_values("Date").iloc[-1] if not weather.empty else {}
        frame["weather_condition"] = weather_row.get("Condition", "Unknown")
        frame["temperature_f"] = weather_row.get("TemperatureF", 0.0)
        frame["precipitation_in"] = weather_row.get("PrecipitationIn", 0.0)
        frame["snowfall_in"] = weather_row.get("SnowfallIn", 0.0)
        frame["humidity_pct"] = weather_row.get("HumidityPct", 0.0)
        frame["holiday_sales_impact"] = 0.0
        frame["holiday_indicator"] = 0
        promotion = self.tables["promotions"]
        active = (
            promotion.ActiveFlag.astype(bool)
            & pd.to_datetime(promotion.StartDate, utc=True).le(request.as_of)
            & (pd.to_datetime(promotion.EndDate, utc=True) + pd.Timedelta(days=1)).gt(request.as_of)
        )
        discount = promotion[active].groupby("CategoryID").DiscountPct.max()
        frame["promotion_discount_pct"] = product.CategoryID.map(discount).fillna(0).to_numpy()
        frame["promotion_active"] = frame.promotion_discount_pct.gt(0).astype(int)
        frame["season_match"] = (
            product.Season.astype(str)
            .str.lower()
            .isin({"all", "all season", "winter"})
            .astype(int)
            .to_numpy()
        )
        frame["weather_relevance_score"] = 0.0
        frame["channel"] = profile.get("PreferredChannel", "Unknown")
        frame["profile_version"] = float(profile.get("ProfileVersion", 0))
        frame["days_since_profile_change"] = 0.0
        frame["metadata_completeness"] = self._metadata_confidence(profile)
        frame["history_event_count"] = stats["event_count"]
        frame["event_type_diversity"] = stats["event_diversity"]
        frame["HistoryClass"] = "Known" if stats["event_count"] > 10 else "LowHistory"
        frame["behavioral_confidence"] = behavioral_confidence
        for column in self.warm_preprocessor.feature_names_in_:
            if column not in frame:
                frame[column] = 0.0
        return frame[list(self.warm_preprocessor.feature_names_in_)]

    def _warm_rank(
        self,
        candidates: pd.DataFrame,
        request: Request,
        profile: dict[str, Any],
        history: pd.DataFrame,
        stats: dict[str, Any],
        behavioral: float,
    ) -> pd.DataFrame:
        if candidates.empty or stats["event_count"] == 0:
            return pd.DataFrame()
        transformed = self.warm_preprocessor.transform(
            self._warm_features(candidates, request, profile, history, stats, behavioral)
        )
        scored = candidates.copy()
        scored["ExpertScore"] = self.warm_ranker.predict(xgb.DMatrix(transformed))
        scored["FinalScore"] = float(self.warm_contract["blend_weight"]) * _percentile(
            scored.ExpertScore
        ) + (1 - float(self.warm_contract["blend_weight"])) * _percentile(scored.PreRankScore)
        return scored.sort_values(
            ["FinalScore", "ProductID"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)

    def _cold_rank(
        self, candidates: pd.DataFrame, request: Request, profile: dict[str, Any]
    ) -> pd.DataFrame:
        if candidates.empty:
            return candidates
        product = self.product_by_id.loc[candidates.ProductID].reset_index(drop=True)
        frame = pd.DataFrame(index=candidates.index)
        for field in (
            "RegionID",
            "State",
            "ClimateZone",
            "CustomerSegment",
            "LoyaltyTier",
            "PreferredChannel",
            "FavoriteCategoryID",
            "FavoriteBrandID",
            "PriceSensitivity",
            "ColorPreference",
            "CategoryAffinityScore",
            "BrandAffinityScore",
        ):
            frame[field] = profile.get(
                field,
                "Unknown" if field not in {"CategoryAffinityScore", "BrandAffinityScore"} else 0.0,
            )
        mapping = {
            "ProductCategoryID": "CategoryID",
            "ProductBrandID": "BrandID",
            "ProductSeason": "Season",
            "ProductColor": "Color",
            "ProductSize": "Size",
            "PriceBand": "PriceBand",
            "BasePrice": "BasePrice",
            "MarginPct": "MarginPct",
        }
        for output, source in mapping.items():
            frame[output] = product[source].to_numpy()
        session_products = [item["product_id"] for item in request.session_events]
        session_product_rows = self.products[self.products.ProductID.isin(session_products)]
        session_category = (
            _text(session_product_rows.CategoryID.mode().iloc[0])
            if not session_product_rows.empty
            else None
        )
        session_brand = (
            _text(session_product_rows.BrandID.mode().iloc[0])
            if not session_product_rows.empty
            else None
        )
        frame["SessionCategoryID"] = session_category or "Unknown"
        frame["SessionBrandID"] = session_brand or "Unknown"
        frame["favorite_category_match"] = (
            product.CategoryID.astype(str)
            .eq(_text(profile.get("FavoriteCategoryID")))
            .astype(int)
            .to_numpy()
        )
        frame["favorite_brand_match"] = (
            product.BrandID.astype(str)
            .eq(_text(profile.get("FavoriteBrandID")))
            .astype(int)
            .to_numpy()
        )
        frame["color_match"] = (
            product.Color.astype(str)
            .eq(_text(profile.get("ColorPreference")))
            .astype(int)
            .to_numpy()
        )
        sensitivity = str(profile.get("PriceSensitivity", "")).lower()
        compatible = {
            "high": {"value"},
            "medium": {"value", "standard"},
            "low": {"premium", "luxury"},
        }
        frame["price_compatibility"] = (
            product.PriceBand.astype(str)
            .isin(compatible.get(sensitivity, {"value", "standard", "premium", "luxury"}))
            .astype(int)
            .to_numpy()
        )
        frame["global_product_score"] = (
            candidates.ProductID.map(self.global_scores).fillna(0).to_numpy()
        )
        frame["regional_product_score"] = (
            candidates.ProductID.map(
                self.regional_scores.get(
                    _text(profile.get("RegionID")) or "", pd.Series(dtype=float)
                )
            )
            .fillna(0)
            .to_numpy()
        )
        frame["segment_product_score"] = (
            candidates.ProductID.map(
                self.segment_scores.get(
                    _text(profile.get("CustomerSegment")) or "", pd.Series(dtype=float)
                )
            )
            .fillna(0)
            .to_numpy()
        )
        frame["session_category_match"] = (
            product.CategoryID.astype(str).eq(session_category).astype(int).to_numpy()
        )
        frame["session_brand_match"] = (
            product.BrandID.astype(str).eq(session_brand).astype(int).to_numpy()
        )
        frame["session_signal_count"] = len(request.session_events)
        frame["session_recency_days"] = 0.0 if request.session_events else 3650.0
        known = sum(_text(profile.get(field)) is not None for field in PROFILE_FIELDS[:10]) / 10
        frame["profile_coverage"] = known
        transformed = self.cold_preprocessor.transform(
            frame[list(self.cold_preprocessor.feature_names_in_)]
        )
        scored = candidates.copy()
        scored["ExpertScore"] = self.cold_ranker.predict(xgb.DMatrix(transformed))
        blend = float(self.cold_metadata["trained_ranker_blend_weight"])
        scored["FinalScore"] = blend * _percentile(scored.ExpertScore) + (1 - blend) * _percentile(
            scored.PreRankScore
        )
        return scored.sort_values(
            ["FinalScore", "ProductID"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)

    def _cold_weight(self, stats: dict[str, Any]) -> tuple[str, float]:
        count = stats["event_count"]
        band = (
            "NoEvents"
            if count == 0
            else "FirstSignals"
            if count <= 2
            else "EarlyBehavior"
            if count <= 5
            else "LowHistory"
            if count <= 10
            else "Established"
        )
        value = float(self.routing_policy["selected_base_cold_weights"][band])
        if (
            stats["days_since"]
            > self.routing_policy["behavioral_confidence"]["inactive_after_days"]
        ):
            value = min(
                1.0, value + float(self.routing_policy["adaptation"]["inactive_cold_weight_boost"])
            )
        return band, value

    def _blocked(self, request: Request, history: pd.DataFrame) -> set[str]:
        blocked = set(request.excluded_products)
        keywords = "|".join(self.routing_policy["business_rules"]["durable_category_keywords"])
        durable_ids = set(
            self.products[
                self.products.CategoryName.fillna("").str.contains(keywords, case=False, regex=True)
                | self.products.DepartmentName.fillna("").str.contains(
                    keywords, case=False, regex=True
                )
            ].ProductID
        )
        recent = history[
            history.EventType.eq("purchase")
            & history.EventTime.ge(
                request.as_of
                - pd.Timedelta(
                    days=int(self.routing_policy["business_rules"]["recent_durable_purchase_days"])
                )
            )
        ]
        blocked.update(set(recent.ProductID) & durable_ids)
        return blocked

    def recommend_one(self, request: Request) -> pd.DataFrame:
        if request.customer_id and request.customer_id not in self.profiles.index:
            raise RecommendationInputError("existing customer_id is unknown")
        profile = self._profile(request)
        history = self._history(request)
        stats = self._history_stats(history, request)
        behavioral = self._behavioral_confidence(stats)
        metadata = self._metadata_confidence(profile)
        cohort = self._cohort(stats["event_count"])
        sources = self._retrieval_sources(request, profile, history)
        candidates = self._combine(sources, cohort)
        blocked = self._blocked(request, history)
        candidates = candidates[~candidates.ProductID.isin(blocked)].copy()
        if candidates.empty:
            raise RecommendationInputError("no eligible products remain after exclusions")
        warm = self._warm_rank(candidates, request, profile, history, stats, behavioral)
        cold = self._cold_rank(candidates, request, profile)
        band, cold_weight = self._cold_weight(stats)
        if warm.empty:
            cold_weight = 1.0
            fallback = "Warm expert unavailable; cold expert used"
        else:
            fallback = "Not required"
        parts = []
        if not warm.empty:
            item = warm.head(100).copy()
            item["BlendScore"] = (1 - cold_weight) / (np.arange(len(item)) + 1)
            item["Expert"] = "warm_ranker"
            parts.append(item)
        if not cold.empty:
            item = cold.head(100).copy()
            item["BlendScore"] = cold_weight / (np.arange(len(item)) + 1)
            item["Expert"] = "cold_start_ranker"
            parts.append(item)
        blended = (
            pd.concat(parts, ignore_index=True)
            .groupby("ProductID", as_index=False)
            .agg(
                score=("BlendScore", "sum"),
                candidate_sources=(
                    "CandidateSources",
                    lambda values: ";".join(sorted(set(";".join(values).split(";")))),
                ),
                experts=("Expert", lambda values: ";".join(sorted(set(values)))),
            )
            .sort_values(["score", "ProductID"], ascending=[False, True], kind="stable")
        )
        # Lightweight diversity: at most three products per category before deterministic backfill.
        selected: list[str] = []
        category_counts: dict[str, int] = {}
        for product in blended.ProductID:
            category = str(self.product_by_id.loc[product].CategoryID)
            if category_counts.get(category, 0) < 3:
                selected.append(product)
                category_counts[category] = category_counts.get(category, 0) + 1
            if len(selected) == request.top_n:
                break
        for product in blended.ProductID:
            if product not in selected:
                selected.append(product)
            if len(selected) == request.top_n:
                break
        output = blended.set_index("ProductID").loc[selected].reset_index()
        output["rank"] = np.arange(1, len(output) + 1)
        output["request_id"] = request.request_id
        output["customer_id"] = request.customer_id
        output["scenario_id"] = request.scenario_id
        output["route"] = "cold_only" if warm.empty else "adaptive_blend"
        output["cold_weight"] = cold_weight
        output["warm_weight"] = 1 - cold_weight
        output["behavioral_confidence"] = behavioral
        output["metadata_confidence"] = metadata
        output["reason_codes"] = output.apply(
            lambda row: ";".join(
                [f"route:{row.route}", f"history:{band}", f"experts:{row.experts}"]
            ),
            axis=1,
        )
        output["fallback_reason"] = fallback
        output["inventory_snapshot_at"] = str(
            pd.to_datetime(self.tables["inventory"].SnapshotDate).max().date()
        )
        output["model_version"] = MODEL_VERSION
        output["policy_version"] = POLICY_VERSION
        output["runtime_version"] = RUNTIME_VERSION
        columns = [
            "request_id",
            "customer_id",
            "scenario_id",
            "rank",
            "ProductID",
            "score",
            "route",
            "cold_weight",
            "warm_weight",
            "behavioral_confidence",
            "metadata_confidence",
            "candidate_sources",
            "reason_codes",
            "fallback_reason",
            "inventory_snapshot_at",
            "model_version",
            "policy_version",
            "runtime_version",
        ]
        return output[columns].rename(columns={"ProductID": "product_id"})

    def predict(self, model_input: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(model_input, pd.DataFrame) or model_input.empty:
            raise RecommendationInputError("model_input must contain at least one record")
        results = [self.recommend_one(parse_request(row)) for _, row in model_input.iterrows()]
        output = pd.concat(results, ignore_index=True)
        string_columns = [
            "request_id",
            "customer_id",
            "scenario_id",
            "product_id",
            "route",
            "candidate_sources",
            "reason_codes",
            "fallback_reason",
            "inventory_snapshot_at",
            "model_version",
            "policy_version",
            "runtime_version",
        ]
        for column in string_columns:
            output[column] = output[column].astype(object)
        return output
