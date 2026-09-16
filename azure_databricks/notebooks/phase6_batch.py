# Databricks notebook source
# ruff: noqa: F821, E501, S608
"""Triggered, checkpointed demo-cohort scoring of the pinned POC model."""
import json
import time
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient
from pyspark.sql import functions as F
from retail_hp_azure.demo_cohort import select_demo_customers
from threadpoolctl import threadpool_limits

CATALOG = "intellify_databricks_demo"
MODEL = f"{CATALOG}.ml.adaptive_recommender"
BATCH = "poc_candidate_3_demo100_20260101"
HISTORY = f"{CATALOG}.gold.customer_recommendation_history"
CURRENT = f"{CATALOG}.gold.customer_recommendation_current"
VIEW = f"{CATALOG}.serving.customer_recommendations"
CHECKPOINT = Path(f"/Volumes/{CATALOG}/ml/model_assets/_phase6/{BATCH}")
mlflow.set_registry_uri("databricks-uc")
assert str(MlflowClient().get_model_version_by_alias(MODEL, "Candidate").version) == "3"
model = mlflow.pyfunc.load_model(f"models:/{MODEL}/3")
runtime = model.unwrap_python_model().model
customers = sorted(runtime.profiles.index.astype(str))
assert len(customers) == 5000
# An explicit representative demo cohort, never mislabeled as full-population coverage.
customers = select_demo_customers(customers)
assert len(customers) == 100
CHECKPOINT.mkdir(parents=True, exist_ok=True)
started = time.monotonic()


def shard(index):
    selected = customers[index:index + 100]
    path = CHECKPOINT / f"shard_{index:05d}.parquet"
    if path.exists():
        result = pd.read_parquet(path)
    else:
        records = pd.DataFrame([{
            "request_id": "batch-" + customer,
            "request_type": "existing_customer", "customer_id": customer,
            "top_n": 10, "as_of": "2026-01-01T00:00:00Z",
        } for customer in selected])
        for column in ("request_id", "request_type", "customer_id", "as_of"):
            records[column] = records[column].astype(object)
        result = model.predict(records)
        for column in result:
            if column not in {"rank", "score", "cold_weight", "warm_weight", "behavioral_confidence", "metadata_confidence"}:
                result[column] = result[column].astype("string")
    assert len(result) == len(selected) * 10
    assert set(result.customer_id) == set(selected)
    assert result.product_id.isin(runtime.eligible_ids).all()
    assert result.groupby("customer_id").product_id.nunique().eq(10).all()
    if not path.exists():
        result.to_parquet(path, index=False)
    return len(result)


with threadpool_limits(limits=1):
    counts = [shard(0)]
assert sum(counts) == 1000
scored = (spark.read.parquet(str(CHECKPOINT / "shard_*.parquet"))
          .withColumn("batch_id", F.lit(BATCH))
          .withColumn("registered_model_version", F.lit("3"))
          .withColumn("release_scope", F.lit("SYNTHETIC_POC_ONLY")))
assert scored.count() == 1000
assert scored.select("customer_id", "rank").distinct().count() == 1000
if not spark.catalog.tableExists(HISTORY):
    scored.write.format("delta").mode("error").saveAsTable(HISTORY)
else:
    present = spark.table(HISTORY).where(F.col("batch_id") == BATCH).count()
    if present == 0:
        scored.write.format("delta").mode("append").saveAsTable(HISTORY)
    else:
        assert present == 1000
history = spark.table(HISTORY).where(F.col("batch_id") == BATCH)
assert history.count() == 1000
# Delta overwrite is one transaction: readers see the old or complete new snapshot.
history.write.format("delta").mode("overwrite").saveAsTable(CURRENT)
spark.sql(f"""CREATE OR REPLACE VIEW {VIEW} AS
 SELECT r.* FROM {CURRENT} r
 INNER JOIN {CATALOG}.gold.eligible_product_catalog p ON r.product_id = p.product_id
""")
for table in (HISTORY, CURRENT):
    spark.sql(f"ALTER TABLE {table} OWNER TO `retail_hp_admins`")
spark.sql(f"ALTER VIEW {VIEW} OWNER TO `retail_hp_admins`")
assert spark.table(CURRENT).count() == 1000
visible = spark.table(VIEW).count()
assert 0 < visible <= 1000
dbutils.notebook.exit(json.dumps({
    "status": "PASS", "customers": 100, "population_customers": 5000,
    "coverage": "DEMO_COHORT_ONLY", "history_batch_rows": 1000,
    "current_rows": 1000, "eligible_view_rows": visible,
    "batch_id": BATCH, "registered_model_version": "3",
    "release_scope": "SYNTHETIC_POC_ONLY", "atomic_current_publish": True,
    "elapsed_seconds": round(time.monotonic() - started, 2),
    "schedule_created": False,
}))
