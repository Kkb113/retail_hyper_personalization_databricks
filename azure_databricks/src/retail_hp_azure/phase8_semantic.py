"""Bounded cosine retrieval with explicit embedding identity and deterministic ties."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from retail_hp_azure.safety import require

EMBEDDING_ENDPOINT = "databricks-gte-large-en"
EMBEDDING_MODEL = "system.ai.gte_large_en_v1_5"
DIMENSION = 1024
MAX_PRODUCTS = 3000
DOCUMENT_VERSION = "retail_product_document_v1"
TABLE = "intellify_databricks_demo.features.product_embeddings"
VOLUME = "/Volumes/intellify_databricks_demo/serving/semantic_assets"


def product_document(product: dict[str, Any]) -> tuple[str, str]:
    """Only descriptive public attributes invalidate an embedding, not price or stock."""
    text = " | ".join(
        str(product.get(column) or "")
        for column in (
            "product_name",
            "category_name",
            "department_name",
            "brand_name",
            "season",
            "color",
            "size",
        )
    )
    require(0 < len(text) <= 512, "Product document exceeds bound")
    return text, hashlib.sha256(text.encode()).hexdigest()


def normalize(vector: Sequence[float]) -> tuple[float, ...]:
    require(len(vector) == DIMENSION, "Embedding dimension mismatch")
    require(
        all(type(x) in (int, float) and math.isfinite(x) for x in vector),
        "Embedding contains nonfinite values",
    )
    norm = math.sqrt(math.fsum(x * x for x in vector))
    require(norm > 0 and math.isfinite(norm), "Embedding norm is invalid")
    return tuple(x / norm for x in vector)


class SemanticIndex:
    """Public product vectors only. Never cache customer records or tool responses."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        require(snapshot.get("model") == EMBEDDING_MODEL, "Embedding model mismatch")
        require(snapshot.get("document_version") == DOCUMENT_VERSION, "Document version mismatch")
        rows = snapshot["rows"]
        require(0 < len(rows) <= MAX_PRODUCTS, "Product index exceeds approved catalog bound")
        self.version = snapshot["snapshot_version"]
        require(
            isinstance(self.version, str)
            and re.fullmatch(r"[0-9a-f]{64}", self.version) is not None,
            "Invalid snapshot version",
        )
        self.generated_at = snapshot["generated_at"]
        require(isinstance(self.generated_at, str), "Invalid snapshot timestamp")
        timestamp = datetime.fromisoformat(self.generated_at)
        require(timestamp.tzinfo is not None, "Snapshot timestamp must include timezone")
        require(
            all(
                isinstance(row["product_id"], str)
                and re.fullmatch(r"PRO[0-9]{6}", row["product_id"])
                for row in rows
            ),
            "Invalid snapshot product identifier",
        )
        self.vectors = {row["product_id"]: normalize(row["embedding"]) for row in rows}
        require(len(self.vectors) == len(rows), "Duplicate product embeddings")

    def rank(self, query: Sequence[float], eligible_ids: set[str]) -> list[str]:
        require(eligible_ids <= self.vectors.keys(), "SEMANTIC_INDEX_REBUILD_REQUIRED")
        normalized = normalize(query)
        scores = [
            (round(math.fsum(a * b for a, b in zip(normalized, vector, strict=True)), 10), pid)
            for pid, vector in self.vectors.items()
            if pid in eligible_ids
        ]
        return [pid for _, pid in sorted(scores, key=lambda item: (-item[0], item[1]))]
