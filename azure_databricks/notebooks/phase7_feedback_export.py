# Databricks notebook source
# ruff: noqa: F821, E501, S608
"""Manual-only, idempotent export of synthetic POC feedback to Monitoring."""

import json

CATALOG = "intellify_databricks_demo"
SOURCE = f"{CATALOG}.agent.feedback"
TARGET = f"{CATALOG}.monitoring.feedback_event_export"
STATEMENT = f"""MERGE INTO {TARGET} AS target
USING (
  SELECT event_id, actor_hash, correlation_id,
         get_json_object(payload_json, '$.sentiment') AS sentiment,
         get_json_object(payload_json, '$.reason_code') AS reason_code,
         get_json_object(payload_json, '$.product_id') AS product_id,
         created_at, current_timestamp() AS exported_at
  FROM {SOURCE}
  WHERE expires_at > current_timestamp()
) AS source
ON target.event_id = source.event_id
WHEN NOT MATCHED THEN INSERT *"""

before = spark.table(TARGET).count()
spark.sql(STATEMENT)
after_first = spark.table(TARGET).count()
spark.sql(STATEMENT)
after_second = spark.table(TARGET).count()
assert after_first == after_second
assert after_first >= before

dbutils.notebook.exit(json.dumps({
    "status": "PASS",
    "source": "agent.feedback",
    "target": "monitoring.feedback_event_export",
    "rows_before": before,
    "rows_after": after_second,
    "second_pass_changes": after_second - after_first,
    "idempotent": True,
    "schedule_created": False,
}))
