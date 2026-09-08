"""Checkpointed operator-driven Databricks embeddings, with no Spark/warehouse compute."""

import hashlib
import json
import os
import time
from datetime import UTC, datetime

import pandas as pd
import requests
from databricks.sdk.errors import NotFound
from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase3 import REPO_ROOT, load_manifest
from retail_hp_azure.phase8_semantic import (
    DOCUMENT_VERSION,
    EMBEDDING_ENDPOINT,
    EMBEDDING_MODEL,
    VOLUME,
    normalize,
    product_document,
)
from retail_hp_azure.safety import require

ROOT = REPO_ROOT / "build/phase8-embedding-checkpoints"
ROOT.mkdir(parents=True, exist_ok=True)
LEDGER = ROOT / "ledger.json"


def documents():
    manifest = load_manifest()
    frames = {}
    for entry in manifest["files"]:
        name = entry.get("data_contract", {}).get("name")
        if name not in {"products", "product_categories", "brands", "inventory"}:
            continue
        path = REPO_ROOT / entry["path"]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], "Source drift")
        frames[name] = pd.read_parquet(path)
    stock = (
        frames["inventory"]
        .sort_values("SnapshotDate")
        .drop_duplicates(["StoreID", "ProductID"], keep="last")
        .groupby("ProductID")
        .AvailableQty.sum()
    )
    products = (
        frames["products"]
        .merge(frames["product_categories"], on="CategoryID")
        .merge(frames["brands"][["BrandID", "BrandName"]], on="BrandID")
    )
    products = products[products.ActiveFlag & products.ProductID.isin(stock[stock > 0].index)]
    names = {
        "ProductName": "product_name",
        "CategoryName": "category_name",
        "DepartmentName": "department_name",
        "BrandName": "brand_name",
        "Season": "season",
        "Color": "color",
        "Size": "size",
    }
    result = []
    for row in products.sort_values("ProductID").to_dict(orient="records"):
        text, digest = product_document({value: row[key] for key, value in names.items()})
        result.append((row["ProductID"], text, digest))
    require(0 < len(result) <= 3000, "Catalog bound exceeded")
    return result


def main():
    from phase8_control import plan, stopped

    require(os.environ.get("RETAIL_HP_PHASE8_CEILING_INR") == "250", "Execution gate required")
    context = CloudContext()
    stopped(context)
    client = context.client
    endpoint = client.serving_endpoints.get(EMBEDDING_ENDPOINT)
    require(
        endpoint.config.served_entities[0].foundation_model.name == EMBEDDING_MODEL,
        "Embedding identity drift",
    )
    docs = documents()
    version = hashlib.sha256(
        json.dumps(
            {
                "model": EMBEDDING_MODEL,
                "document_version": DOCUMENT_VERSION,
                "documents": [(pid, digest) for pid, _, digest in docs],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    ledger = (
        json.loads(LEDGER.read_text())
        if LEDGER.exists()
        else {"attempted_input_bytes": 0, "successful_tokens": 0, "requests": 0, "throttles": 0}
    )
    rows = []
    pending = []
    for pid, text, digest in docs:
        key = hashlib.sha256(
            f"{EMBEDDING_MODEL}:{DOCUMENT_VERSION}:{pid}:{digest}".encode()
        ).hexdigest()
        path = ROOT / f"{key}.json"
        if path.exists():
            row = json.loads(path.read_text())
            require(
                row["product_id"] == pid and row["document_sha256"] == digest,
                "Checkpoint identity mismatch",
            )
            normalize(row["embedding"])
            rows.append(row)
        else:
            pending.append((pid, text, digest, path))
    if pending:
        require(
            plan()["guarded_estimate_inr_pre_tax"] < 250,
            "Cumulative phase cost plan does not admit further embedding calls",
        )
    started = time.monotonic()
    for offset in range(0, len(pending), 32):
        require(time.monotonic() - started < 600, "Operator embedding deadline reached")
        batch = pending[offset : offset + 32]
        for attempt in range(3):
            reserved = sum(len(row[1].encode()) for row in batch)
            require(
                ledger["attempted_input_bytes"] + reserved <= 500_000,
                "Embedding cumulative input allowance exhausted",
            )
            ledger["attempted_input_bytes"] += reserved
            ledger["requests"] += 1
            LEDGER.write_text(json.dumps(ledger))
            response = requests.post(
                f"{client.config.host}/serving-endpoints/{EMBEDDING_ENDPOINT}/invocations",
                headers=client.config.authenticate(),
                json={"input": [row[1] for row in batch]},
                timeout=(5, 25),
                allow_redirects=False,
            )
            if response.status_code != 429 or attempt == 2:
                break
            ledger["throttles"] += 1
            LEDGER.write_text(json.dumps(ledger))
            time.sleep(10)
        require(
            response.status_code == 200 and len(response.content) < 2_000_000,
            f"Embedding HTTP {response.status_code}; checkpoints preserved",
        )
        body = response.json()
        data = sorted(body["data"], key=lambda row: row["index"])
        require(
            [row["index"] for row in data] == list(range(len(batch))), "Batch response mismatch"
        )
        ledger["successful_tokens"] += int(body["usage"]["total_tokens"])
        LEDGER.write_text(json.dumps(ledger))
        for (pid, text, digest, path), item in zip(batch, data, strict=True):
            row = {
                "product_id": pid,
                "document": text,
                "document_sha256": digest,
                "model_version": EMBEDDING_MODEL,
                "document_version": DOCUMENT_VERSION,
                "embedding": [round(x, 8) for x in normalize(item["embedding"])],
                "generated_at": datetime.now(UTC).isoformat(),
            }
            path.write_text(json.dumps(row, separators=(",", ":")))
            rows.append(row)
        print(
            json.dumps({"checkpointed_products": len(rows), "total_products": len(docs)}),
            flush=True,
        )
        time.sleep(1)
    rows.sort(key=lambda row: row["product_id"])
    require(len(rows) == len(docs), "Embedding checkpoint incomplete")
    payload = json.dumps(
        {"model": EMBEDDING_MODEL, "snapshot_version": version, "rows": rows}, separators=(",", ":")
    ).encode()
    path = f"{VOLUME}/precomputed_{version}.json"
    try:
        remote = client.files.download(path)
        with remote.contents as stream:
            require(
                hashlib.sha256(stream.read()).digest() == hashlib.sha256(payload).digest(),
                "Immutable precomputed artifact drift",
            )
    except NotFound:
        import io

        client.files.upload(path, io.BytesIO(payload), overwrite=False)
    report = {
        "status": "PASS",
        "model": EMBEDDING_MODEL,
        "products": len(rows),
        "snapshot_version": version,
        "ledger": ledger,
        "warehouse_started": False,
        "spark_started": False,
        "estimated_embedding_cost_inr_pre_tax": round(
            ledger["successful_tokens"] * 1.857 * 44.91 / 1_000_000, 4
        ),
    }
    evidence = REPO_ROOT / "azure_databricks/evidence/phase_08/operator_embeddings.json"
    evidence.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
