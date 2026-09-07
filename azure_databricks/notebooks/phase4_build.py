# Databricks notebook source
# ruff: noqa: E501, F821, S608
"""Build and validate the governed Phase 4 lakehouse from the sealed snapshot."""

import json
import re

from delta.tables import DeltaTable
from pyspark.sql import Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

CATALOG = "intellify_databricks_demo"
TRANSFER_VERSION = "retail_hp_transfer_v1"
LANDING_ROOT = f"/Volumes/{CATALOG}/bronze/transfer_landing/{TRANSFER_VERSION}"
MANIFEST_PATH = f"{LANDING_ROOT}/_control/transfer_manifest.json"
RUN_ID = "retail_hp_phase4_transfer_v1"
OWNER = "retail_hp_admins"
SOURCE_KEYS = {
    "brands": ["BrandID"],
    "browsing_events": ["EventID"],
    "cart_events": ["CartEventID"],
    "customer_preferences": ["CustomerID"],
    "customer_product_audit": ["CustomerID", "ProductID"],
    "customers": ["CustomerID"],
    "inventory": ["InventoryID"],
    "product_categories": ["CategoryID"],
    "products": ["ProductID"],
    "promotions": ["PromotionID"],
    "recommendation_log": ["RecommendationID"],
    "recommendation_response": ["ResponseID"],
    "recommendation_snapshot": ["CustomerID", "Rank"],
    "regions": ["RegionID"],
    "sales_order_lines": ["OrderLineID"],
    "sales_orders": ["OrderID"],
    "search_events": ["SearchID"],
    "weather": ["WeatherID"],
    "wishlist": ["WishlistID"],
    "customer_profile_history": ["CustomerID", "ProfileVersion"],
}
MONEY_TABLES = {
    "cart_events",
    "products",
    "promotions",
    "recommendation_snapshot",
    "sales_order_lines",
    "sales_orders",
}
RECOMMENDATION_SNAPSHOT_SCHEMA = StructType(
    [
        StructField("CustomerID", StringType()),
        StructField("ProductID", StringType()),
        StructField("RecommendationScore", DoubleType()),
        StructField("ExpertSources", StringType()),
        StructField("Rank", LongType()),
        StructField("FallbackReason", StringType()),
        StructField("ColdWeight", DoubleType()),
        StructField("WarmWeight", DoubleType()),
        StructField("HistoryBand", StringType()),
        StructField("Route", StringType()),
        StructField("RoutingReason", StringType()),
        StructField("BehavioralConfidence", DoubleType()),
        StructField("MetadataConfidence", DoubleType()),
        StructField("ProfileVersion", LongType()),
        StructField("EventCount", LongType()),
        StructField("EventDiversity", LongType()),
        StructField("DaysSinceLastEvent", DoubleType()),
        StructField("PurchaseCount", LongType()),
        StructField("BaseColdWeight", DoubleType()),
        StructField("InactiveAdjustment", DoubleType()),
        # Spark cannot represent Parquet TIMESTAMP_NANOS directly; retain the raw value.
        StructField("DecisionTime", LongType()),
        StructField("PolicyVersion", StringType()),
        StructField("ModelRoute", StringType()),
        StructField("InventoryValid", BooleanType()),
    ]
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def snake_case(name):
    # Preserve common initialisms as one token: CustomerID -> customer_id, not customer_i_d.
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    require(re.fullmatch(r"[a-z][a-z0-9_]*", value) is not None, "Unsafe column name")
    return value


def table_exists(name):
    return spark.catalog.tableExists(name)


def conjunction(left, right, keys):
    expression = None
    for key in keys:
        item = F.col(f"{left}.{key}") == F.col(f"{right}.{key}")
        expression = item if expression is None else expression & item
    return expression


def normalized_source(name, source, source_hash):
    columns = []
    for field in source.schema.fields:
        target = snake_case(field.name)
        value = F.col(f"`{field.name}`")
        if name == "recommendation_snapshot" and field.name == "DecisionTime":
            # Deterministically truncate nanoseconds to Spark's microsecond timestamp precision.
            value = F.timestamp_micros((value / F.lit(1000)).cast("long"))
        elif isinstance(field.dataType, StringType):
            value = F.trim(value)
            if field.name.lower().endswith("id") or field.name.lower() == "sku":
                value = F.upper(value)
        elif isinstance(field.dataType, TimestampType):
            value = F.to_utc_timestamp(value, "UTC")
        columns.append(value.alias(target))
    result = source.select(*columns)
    if name in MONEY_TABLES:
        result = result.withColumn("currency_code", F.lit("UNSPECIFIED"))
    business_columns = result.columns
    result = (
        result.withColumn("source_transfer_version", F.lit(TRANSFER_VERSION))
        .withColumn("source_hash", F.lit(source_hash))
        .withColumn("row_hash", F.sha2(F.to_json(F.struct(*business_columns)), 256))
        .withColumn("conformed_at", F.current_timestamp())
    )
    return result


def merge_bronze(name, source, spec):
    table = f"{CATALOG}.bronze.{name}"
    keys = spec["primary_key"]
    output = (
        source.withColumn("transfer_version", F.lit(TRANSFER_VERSION))
        .withColumn("source_file", F.lit(spec["logical_path"]))
        .withColumn("source_hash", F.lit(spec["sha256"]))
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("run_id", F.lit(RUN_ID))
    )
    if not table_exists(table):
        # Spark Connect rejects the errorifexists alias; the default mode is fail-if-exists.
        output.write.format("delta").saveAsTable(table)
        spark.sql(
            f"ALTER TABLE {table} SET TBLPROPERTIES ("
            "'delta.appendOnly'='true', 'retail_hp.phase'='4', "
            "'retail_hp.classification'='synthetic_data')"
        )
        return int(spec["expected_rows"])
    target = spark.table(table).where(F.col("transfer_version") == TRANSFER_VERSION)
    hashes = [row.source_hash for row in target.select("source_hash").distinct().collect()]
    require(not hashes or hashes == [spec["sha256"]], f"Bronze source drift: {name}")
    match_keys = keys + ["transfer_version"]
    missing = output.alias("s").join(
        target.alias("t"), conjunction("s", "t", match_keys), "left_anti"
    )
    count = missing.count()
    if count:
        missing.write.format("delta").mode("append").saveAsTable(table)
    return count


def merge_current(table, source, keys):
    if not table_exists(table):
        source.write.format("delta").saveAsTable(table)
        spark.sql(
            f"ALTER TABLE {table} SET TBLPROPERTIES ("
            "'retail_hp.phase'='4', 'retail_hp.classification'='synthetic_data')"
        )
        return source.count()
    target = spark.table(table)
    condition = conjunction("s", "t", keys)
    joined = source.alias("s").join(target.alias("t"), condition, "inner")
    changed = joined.where(F.col("s.row_hash") != F.col("t.row_hash")).count()
    new = source.alias("s").join(target.alias("t"), condition, "left_anti").count()
    if changed or new:
        (
            DeltaTable.forName(spark, table)
            .alias("t")
            .merge(source.alias("s"), conjunction("s", "t", keys))
            .whenMatchedUpdateAll(condition="s.row_hash <> t.row_hash")
            .whenNotMatchedInsertAll()
            .execute()
        )
    return changed + new


with open(MANIFEST_PATH, encoding="utf-8") as stream:
    manifest = json.load(stream)
require(manifest["manifest_version"] == "azure_phase0_transfer_v1", "Manifest version drift")
require(manifest["classification"] == "synthetic_data", "Only synthetic data is approved")
require(manifest["production_approved"] is False, "Production data is not approved")
specs = []
for entry in manifest["files"]:
    if "data" not in entry["roles"]:
        continue
    contract = entry["data_contract"]
    name = contract["name"]
    require(name in SOURCE_KEYS, f"Missing key contract: {name}")
    specs.append(
        {
            "name": name,
            "logical_path": entry["logical_path"],
            "volume_path": f"{LANDING_ROOT}/data/{entry['logical_path']}",
            "sha256": entry["sha256"],
            "expected_rows": int(contract["expected_rows"]),
            "required_columns": contract["required_columns"],
            "primary_key": SOURCE_KEYS[name],
        }
    )
require(len(specs) == 20, "Expected 20 source tables")

spark.conf.set("spark.sql.session.timeZone", "UTC")
spark.sql(f"USE CATALOG {CATALOG}")

source_reports = []
first_changes = 0
for spec in sorted(specs, key=lambda item: item["name"]):
    name = spec["name"]
    source = (
        spark.read.schema(RECOMMENDATION_SNAPSHOT_SCHEMA).parquet(spec["volume_path"])
        if name == "recommendation_snapshot"
        else spark.read.parquet(spec["volume_path"])
    )
    rows = source.count()
    require(rows == spec["expected_rows"], f"Source row drift: {name}")
    require(not set(spec["required_columns"]) - set(source.columns), f"Source schema drift: {name}")
    require(
        source.where(F.expr(" OR ".join(f"`{key}` IS NULL" for key in spec["primary_key"])))
        .limit(1)
        .count()
        == 0,
        f"Null source key: {name}",
    )
    require(
        source.groupBy(*spec["primary_key"]).count().where("count > 1").limit(1).count() == 0,
        f"Duplicate source key: {name}",
    )
    bronze_changes = merge_bronze(name, source, spec)
    silver = normalized_source(name, source, spec["sha256"])
    silver_keys = [snake_case(key) for key in spec["primary_key"]]
    silver_changes = merge_current(f"{CATALOG}.silver.{name}", silver, silver_keys)
    first_changes += bronze_changes + silver_changes
    source_reports.append(
        {
            "name": name,
            "rows": rows,
            "bronze_changes": bronze_changes,
            "silver_changes": silver_changes,
        }
    )

# A second pass must resolve to zero candidate changes and performs no write in that case.
second_pass_changes = 0
for spec in sorted(specs, key=lambda item: item["name"]):
    source = (
        spark.read.schema(RECOMMENDATION_SNAPSHOT_SCHEMA).parquet(spec["volume_path"])
        if spec["name"] == "recommendation_snapshot"
        else spark.read.parquet(spec["volume_path"])
    )
    second_pass_changes += merge_bronze(spec["name"], source, spec)
    silver = normalized_source(spec["name"], source, spec["sha256"])
    second_pass_changes += merge_current(
        f"{CATALOG}.silver.{spec['name']}",
        silver,
        [snake_case(key) for key in spec["primary_key"]],
    )
require(second_pass_changes == 0, "Idempotency validation found candidate changes")


def create_feature_table(table, ddl):
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS {table} ({ddl}) USING DELTA "
        "TBLPROPERTIES ('retail_hp.phase'='4', "
        "'retail_hp.classification'='synthetic_data')"
    )


profile_table = f"{CATALOG}.features.customer_profile_features"
create_feature_table(
    profile_table,
    """
  customer_id STRING NOT NULL,
  feature_timestamp TIMESTAMP NOT NULL,
  profile_version BIGINT,
  region_id STRING,
  loyalty_tier STRING,
  customer_segment STRING,
  preferred_channel STRING,
  favorite_category_id STRING,
  favorite_brand_id STRING,
  price_sensitivity STRING,
  color_preference STRING,
  category_affinity_score DOUBLE,
  brand_affinity_score DOUBLE,
  climate_zone STRING,
  freshness_status STRING,
  temporal_quality STRING,
  consent_status STRING,
  model_use_status STRING,
  source_transfer_version STRING,
  row_hash STRING,
  CONSTRAINT customer_profile_features_pk PRIMARY KEY
    (customer_id, feature_timestamp TIMESERIES)
""",
)
profile_source = spark.table(f"{CATALOG}.silver.customer_profile_history").select(
    "customer_id",
    F.col("effective_from").alias("feature_timestamp"),
    "profile_version",
    "region_id",
    "loyalty_tier",
    "customer_segment",
    "preferred_channel",
    "favorite_category_id",
    "favorite_brand_id",
    "price_sensitivity",
    "color_preference",
    "category_affinity_score",
    "brand_affinity_score",
    "climate_zone",
    "freshness_status",
    "temporal_quality",
    "consent_status",
    "model_use_status",
    "source_transfer_version",
)
profile_source = profile_source.withColumn(
    "row_hash", F.sha2(F.to_json(F.struct(*profile_source.columns)), 256)
)
profile_changes = merge_current(profile_table, profile_source, ["customer_id", "feature_timestamp"])

orders = spark.table(f"{CATALOG}.silver.sales_orders").where(F.lower("order_status") != "cancelled")
lines = spark.table(f"{CATALOG}.silver.sales_order_lines")
purchases = lines.join(orders.select("order_id", "customer_id", "order_date"), "order_id")


def event_frame(frame, time_column, metrics):
    result = frame.select(
        "customer_id",
        F.col(time_column).alias("event_time"),
        *[
            F.col(value).alias(key) if isinstance(value, str) else value.alias(key)
            for key, value in metrics.items()
        ],
    )
    return result


zeros = {
    key: F.lit(0).cast("long")
    for key in [
        "browse_count",
        "cart_add_count",
        "purchase_count",
        "wishlist_count",
        "search_count",
        "search_click_count",
    ]
}
browsing = event_frame(
    spark.table(f"{CATALOG}.silver.browsing_events"),
    "event_time",
    {**zeros, "browse_count": F.lit(1).cast("long"), "purchase_spend": F.lit(0.0)},
)
cart = event_frame(
    spark.table(f"{CATALOG}.silver.cart_events"),
    "event_time",
    {
        **zeros,
        "cart_add_count": F.when(F.lower("action") == "add", 1).otherwise(0).cast("long"),
        "purchase_spend": F.lit(0.0),
    },
)
purchase_events = event_frame(
    purchases.withColumnRenamed("order_date", "purchase_time"),
    "purchase_time",
    {
        **zeros,
        "purchase_count": F.lit(1).cast("long"),
        "purchase_spend": F.col("line_amount").cast("double"),
    },
)
wishlist_events = event_frame(
    spark.table(f"{CATALOG}.silver.wishlist"),
    "added_date",
    {**zeros, "wishlist_count": F.lit(1).cast("long"), "purchase_spend": F.lit(0.0)},
)
search_events = event_frame(
    spark.table(f"{CATALOG}.silver.search_events"),
    "event_time",
    {
        **zeros,
        "search_count": F.lit(1).cast("long"),
        "search_click_count": F.when(F.col("clicked_product_id").isNotNull(), 1)
        .otherwise(0)
        .cast("long"),
        "purchase_spend": F.lit(0.0),
    },
)
events = (
    browsing.unionByName(cart)
    .unionByName(purchase_events)
    .unionByName(wishlist_events)
    .unionByName(search_events)
)
daily = (
    events.withColumn(
        "feature_timestamp", F.date_trunc("DAY", "event_time") + F.expr("INTERVAL 1 DAY")
    )
    .groupBy("customer_id", "feature_timestamp")
    .agg(
        F.sum("browse_count").alias("browse_count"),
        F.sum("cart_add_count").alias("cart_add_count"),
        F.sum("purchase_count").alias("purchase_count"),
        F.sum("purchase_spend").alias("purchase_spend"),
        F.sum("wishlist_count").alias("wishlist_count"),
        F.sum("search_count").alias("search_count"),
        F.sum("search_click_count").alias("search_click_count"),
        F.max("event_time").alias("daily_max_event_time"),
    )
)
window = (
    Window.partitionBy("customer_id")
    .orderBy(F.col("feature_timestamp").cast("long"))
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)
behavior = daily.select(
    "customer_id",
    "feature_timestamp",
    F.sum("browse_count").over(window).alias("browse_count_prior"),
    F.sum("cart_add_count").over(window).alias("cart_add_count_prior"),
    F.sum("purchase_count").over(window).alias("purchase_count_prior"),
    F.sum("purchase_spend").over(window).alias("purchase_spend_prior"),
    F.sum("wishlist_count").over(window).alias("wishlist_count_prior"),
    F.sum("search_count").over(window).alias("search_count_prior"),
    F.sum("search_click_count").over(window).alias("search_click_count_prior"),
    F.max("daily_max_event_time").over(window).alias("max_source_event_time"),
).withColumn("source_transfer_version", F.lit(TRANSFER_VERSION))
behavior = behavior.withColumn("row_hash", F.sha2(F.to_json(F.struct(*behavior.columns)), 256))

behavior_table = f"{CATALOG}.features.customer_behavior_features"
create_feature_table(
    behavior_table,
    """
  customer_id STRING NOT NULL,
  feature_timestamp TIMESTAMP NOT NULL,
  browse_count_prior BIGINT,
  cart_add_count_prior BIGINT,
  purchase_count_prior BIGINT,
  purchase_spend_prior DOUBLE,
  wishlist_count_prior BIGINT,
  search_count_prior BIGINT,
  search_click_count_prior BIGINT,
  max_source_event_time TIMESTAMP,
  source_transfer_version STRING,
  row_hash STRING,
  CONSTRAINT customer_behavior_features_pk PRIMARY KEY
    (customer_id, feature_timestamp TIMESERIES)
""",
)
behavior_changes = merge_current(behavior_table, behavior, ["customer_id", "feature_timestamp"])

products = spark.table(f"{CATALOG}.silver.products").alias("p")
categories = spark.table(f"{CATALOG}.silver.product_categories").alias("c")
brands = spark.table(f"{CATALOG}.silver.brands").alias("b")
product_sales = purchases.groupBy("product_id").agg(
    F.sum("qty").alias("units_sold"),
    F.sum("line_amount").cast("double").alias("revenue"),
    F.countDistinct("order_id").alias("order_count"),
)
product_browse = (
    spark.table(f"{CATALOG}.silver.browsing_events")
    .groupBy("product_id")
    .agg(
        F.count("*").alias("browse_events"),
        F.countDistinct("customer_id").alias("browsing_customers"),
    )
)
product_features = (
    products.join(categories, F.col("p.category_id") == F.col("c.category_id"), "left")
    .join(brands, F.col("p.brand_id") == F.col("b.brand_id"), "left")
    .join(product_sales, F.col("p.product_id") == product_sales.product_id, "left")
    .join(product_browse, F.col("p.product_id") == product_browse.product_id, "left")
    .select(
        F.col("p.product_id").alias("product_id"),
        F.col("p.sku").alias("sku"),
        F.col("p.product_name").alias("product_name"),
        F.col("p.category_id").alias("category_id"),
        F.col("c.category_name").alias("category_name"),
        F.col("c.department_name").alias("department_name"),
        F.col("p.brand_id").alias("brand_id"),
        F.col("b.brand_name").alias("brand_name"),
        F.col("p.base_price").cast("double").alias("base_price"),
        F.col("p.margin_pct").cast("double").alias("margin_pct"),
        F.col("p.season").alias("season"),
        F.col("p.color").alias("color"),
        F.col("p.size").alias("size"),
        F.col("p.active_flag").alias("active_flag"),
        F.coalesce(F.col("units_sold"), F.lit(0)).cast("long").alias("units_sold"),
        F.coalesce(F.col("revenue"), F.lit(0.0)).cast("double").alias("revenue"),
        F.coalesce(F.col("order_count"), F.lit(0)).cast("long").alias("order_count"),
        F.coalesce(F.col("browse_events"), F.lit(0)).cast("long").alias("browse_events"),
        F.coalesce(F.col("browsing_customers"), F.lit(0)).cast("long").alias("browsing_customers"),
        F.lit("UNSPECIFIED").alias("currency_code"),
        F.lit(TRANSFER_VERSION).alias("source_transfer_version"),
    )
)
product_features = product_features.withColumn(
    "row_hash", F.sha2(F.to_json(F.struct(*product_features.columns)), 256)
)
product_table = f"{CATALOG}.features.product_features"
create_feature_table(
    product_table,
    """
  product_id STRING NOT NULL,
  sku STRING,
  product_name STRING,
  category_id STRING,
  category_name STRING,
  department_name STRING,
  brand_id STRING,
  brand_name STRING,
  base_price DOUBLE,
  margin_pct DOUBLE,
  season STRING,
  color STRING,
  size STRING,
  active_flag BOOLEAN,
  units_sold BIGINT,
  revenue DOUBLE,
  order_count BIGINT,
  browse_events BIGINT,
  browsing_customers BIGINT,
  currency_code STRING,
  source_transfer_version STRING,
  row_hash STRING,
  CONSTRAINT product_features_pk PRIMARY KEY (product_id)
""",
)
product_changes = merge_current(product_table, product_features, ["product_id"])

# Gold views are deterministic over the conformed and feature layers.
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.current_inventory AS
WITH ranked AS (
  SELECT *, row_number() OVER (PARTITION BY store_id, product_id ORDER BY snapshot_date DESC) AS rn
  FROM {CATALOG}.silver.inventory
)
SELECT product_id, max(snapshot_date) AS inventory_snapshot_at,
       sum(on_hand_qty) AS on_hand_qty, sum(reserved_qty) AS reserved_qty,
       sum(available_qty) AS available_qty,
       CASE WHEN sum(available_qty) > 0 THEN true ELSE false END AS inventory_available
FROM ranked WHERE rn = 1 GROUP BY product_id
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.active_promotions AS
WITH as_of AS (SELECT max(inventory_snapshot_at) AS snapshot_at FROM {CATALOG}.gold.current_inventory)
SELECT p.*, a.snapshot_at AS evaluated_at
FROM {CATALOG}.silver.promotions p CROSS JOIN as_of a
WHERE p.active_flag = true AND p.start_date <= a.snapshot_at AND p.end_date >= a.snapshot_at
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.product_360 AS
SELECT pf.*, i.inventory_snapshot_at, coalesce(i.available_qty, 0) AS available_qty,
       coalesce(i.inventory_available, false) AS inventory_available
FROM {CATALOG}.features.product_features pf
LEFT JOIN {CATALOG}.gold.current_inventory i USING (product_id)
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.eligible_product_catalog AS
WITH promo AS (
  SELECT category_id, max(discount_pct) AS active_discount_pct,
         concat_ws(',', sort_array(collect_set(promotion_id))) AS active_promotion_ids
  FROM {CATALOG}.gold.active_promotions GROUP BY category_id
)
SELECT p.*, promo.active_discount_pct, promo.active_promotion_ids
FROM {CATALOG}.gold.product_360 p LEFT JOIN promo USING (category_id)
WHERE p.active_flag = true AND p.inventory_available = true
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.customer_360 AS
WITH latest_profile AS (
  SELECT *, row_number() OVER (PARTITION BY customer_id ORDER BY feature_timestamp DESC) AS rn
  FROM {CATALOG}.features.customer_profile_features
), latest_behavior AS (
  SELECT *, row_number() OVER (PARTITION BY customer_id ORDER BY feature_timestamp DESC) AS rn
  FROM {CATALOG}.features.customer_behavior_features
)
SELECT c.customer_id, c.region_id, c.loyalty_tier, c.created_date, c.customer_segment,
       c.preferred_channel, c.customer_status,
       p.favorite_category_id, p.favorite_brand_id, p.price_sensitivity, p.color_preference,
       p.category_affinity_score, p.brand_affinity_score, p.consent_status, p.model_use_status,
       b.feature_timestamp AS behavior_as_of, coalesce(b.browse_count_prior, 0) AS browse_count,
       coalesce(b.cart_add_count_prior, 0) AS cart_add_count,
       coalesce(b.purchase_count_prior, 0) AS purchase_count,
       coalesce(b.purchase_spend_prior, 0.0) AS purchase_spend,
       coalesce(b.wishlist_count_prior, 0) AS wishlist_count,
       coalesce(b.search_count_prior, 0) AS search_count,
       'UNSPECIFIED' AS currency_code
FROM {CATALOG}.silver.customers c
LEFT JOIN latest_profile p ON c.customer_id = p.customer_id AND p.rn = 1
LEFT JOIN latest_behavior b ON c.customer_id = b.customer_id AND b.rn = 1
""")
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.current_recommendations AS
SELECT r.*, p.product_name, p.category_name, p.brand_name, p.base_price,
       p.available_qty, p.inventory_available
FROM {CATALOG}.silver.recommendation_snapshot r
LEFT JOIN {CATALOG}.gold.product_360 p USING (product_id)
""")

dictionary_rows = []
dictionary_objects = (
    [f"{CATALOG}.silver.{spec['name']}" for spec in specs]
    + [profile_table, behavior_table, product_table]
    + [
        f"{CATALOG}.gold.customer_360",
        f"{CATALOG}.gold.product_360",
        f"{CATALOG}.gold.current_inventory",
        f"{CATALOG}.gold.active_promotions",
        f"{CATALOG}.gold.eligible_product_catalog",
        f"{CATALOG}.gold.current_recommendations",
    ]
)
for full_name in dictionary_objects:
    layer = full_name.split(".")[1]
    table_name = full_name.split(".")[2]
    for field in spark.table(full_name).schema.fields:
        dictionary_rows.append(
            (
                layer,
                table_name,
                field.name,
                field.dataType.simpleString(),
                f"Governed {layer} field from {TRANSFER_VERSION}",
                TRANSFER_VERSION,
            )
        )
dictionary = spark.createDataFrame(
    dictionary_rows,
    "layer STRING, table_name STRING, column_name STRING, data_type STRING, "
    "description STRING, source_transfer_version STRING",
).withColumn(
    "row_hash",
    F.sha2(F.to_json(F.struct("data_type", "description", "source_transfer_version")), 256),
)
dictionary_changes = merge_current(
    f"{CATALOG}.gold.data_dictionary", dictionary, ["layer", "table_name", "column_name"]
)

# Executable quality checks.
orphan_checks = [
    ("products_category", "products", "category_id", "product_categories", "category_id", False),
    ("products_brand", "products", "brand_id", "brands", "brand_id", False),
    ("customers_region", "customers", "region_id", "regions", "region_id", False),
    (
        "preferences_customer",
        "customer_preferences",
        "customer_id",
        "customers",
        "customer_id",
        False,
    ),
    ("inventory_product", "inventory", "product_id", "products", "product_id", False),
    ("orders_customer", "sales_orders", "customer_id", "customers", "customer_id", False),
    ("lines_order", "sales_order_lines", "order_id", "sales_orders", "order_id", False),
    ("lines_product", "sales_order_lines", "product_id", "products", "product_id", False),
    ("browsing_customer", "browsing_events", "customer_id", "customers", "customer_id", False),
    ("browsing_product", "browsing_events", "product_id", "products", "product_id", False),
    ("cart_customer", "cart_events", "customer_id", "customers", "customer_id", False),
    ("cart_product", "cart_events", "product_id", "products", "product_id", False),
    (
        "recommendation_customer",
        "recommendation_log",
        "customer_id",
        "customers",
        "customer_id",
        False,
    ),
    ("recommendation_product", "recommendation_log", "product_id", "products", "product_id", False),
    (
        "response_recommendation",
        "recommendation_response",
        "recommendation_id",
        "recommendation_log",
        "recommendation_id",
        False,
    ),
    ("search_customer", "search_events", "customer_id", "customers", "customer_id", False),
    (
        "search_clicked_product",
        "search_events",
        "clicked_product_id",
        "products",
        "product_id",
        True,
    ),
    ("weather_region", "weather", "region_id", "regions", "region_id", False),
    ("wishlist_customer", "wishlist", "customer_id", "customers", "customer_id", False),
    ("wishlist_product", "wishlist", "product_id", "products", "product_id", False),
    (
        "profile_customer",
        "customer_profile_history",
        "customer_id",
        "customers",
        "customer_id",
        False,
    ),
    (
        "snapshot_customer",
        "recommendation_snapshot",
        "customer_id",
        "customers",
        "customer_id",
        False,
    ),
    ("snapshot_product", "recommendation_snapshot", "product_id", "products", "product_id", False),
]
orphan_results = {}
for label, child_table, child_key, parent_table, parent_key, optional in orphan_checks:
    child = spark.table(f"{CATALOG}.silver.{child_table}").select(child_key)
    if optional:
        child = child.where(F.col(child_key).isNotNull())
    parent = spark.table(f"{CATALOG}.silver.{parent_table}").select(parent_key)
    count = child.join(parent, child[child_key] == parent[parent_key], "left_anti").count()
    orphan_results[label] = count
require(sum(orphan_results.values()) == 0, "Referential-integrity checks failed")

invalid_ranges = {
    "negative_product_prices": spark.table(f"{CATALOG}.silver.products")
    .where((F.col("base_price") < 0) | (F.col("cost_price") < 0))
    .count(),
    "invalid_margin_pct": spark.table(f"{CATALOG}.silver.products")
    .where(~F.col("margin_pct").between(0, 100))
    .count(),
    "negative_inventory": spark.table(f"{CATALOG}.silver.inventory")
    .where((F.col("on_hand_qty") < 0) | (F.col("available_qty") < 0) | (F.col("reserved_qty") < 0))
    .count(),
    "invalid_promotion_pct": spark.table(f"{CATALOG}.silver.promotions")
    .where(~F.col("discount_pct").between(0, 100))
    .count(),
    "invalid_promotion_dates": spark.table(f"{CATALOG}.silver.promotions")
    .where(F.col("end_date") < F.col("start_date"))
    .count(),
    "invalid_order_amounts": spark.table(f"{CATALOG}.silver.sales_orders")
    .where((F.col("gross_amount") < 0) | (F.col("discount_amount") < 0) | (F.col("net_amount") < 0))
    .count(),
    "invalid_humidity": spark.table(f"{CATALOG}.silver.weather")
    .where(~F.col("humidity_pct").between(0, 100))
    .count(),
}
require(sum(invalid_ranges.values()) == 0, "Range and date checks failed")

future_leakage_rows = (
    spark.table(behavior_table)
    .where(F.col("max_source_event_time") >= F.col("feature_timestamp"))
    .count()
)
require(future_leakage_rows == 0, "Point-in-time feature leakage detected")

latest_window = Window.partitionBy("customer_id").orderBy(F.col("feature_timestamp").desc())
latest_behavior = (
    spark.table(behavior_table).withColumn("rn", F.row_number().over(latest_window)).where("rn = 1")
)
latest_totals = latest_behavior.agg(
    F.sum("browse_count_prior").alias("browse"),
    F.sum("cart_add_count_prior").alias("cart"),
    F.sum("purchase_count_prior").alias("purchase"),
    F.sum("purchase_spend_prior").alias("spend"),
    F.sum("wishlist_count_prior").alias("wishlist"),
    F.sum("search_count_prior").alias("search"),
    F.sum("search_click_count_prior").alias("search_click"),
).first()
product_totals = (
    spark.table(product_table)
    .agg(
        F.sum("units_sold").alias("units"),
        F.sum("revenue").alias("revenue"),
        F.sum("browse_events").alias("browse"),
    )
    .first()
)
feature_parity = {
    "customer_behavior_feature_rows": spark.table(behavior_table).count(),
    "customer_behavior_customer_count": spark.table(behavior_table)
    .select("customer_id")
    .distinct()
    .count(),
    "behavior_input_event_count": events.count(),
    "latest_browse_count": int(latest_totals.browse),
    "latest_cart_add_count": int(latest_totals.cart),
    "latest_purchase_count": int(latest_totals.purchase),
    "latest_purchase_spend": round(float(latest_totals.spend), 6),
    "latest_wishlist_count": int(latest_totals.wishlist),
    "latest_search_count": int(latest_totals.search),
    "latest_search_click_count": int(latest_totals.search_click),
    "customer_profile_feature_rows": spark.table(profile_table).count(),
    "product_feature_rows": spark.table(product_table).count(),
    "product_total_units_sold": int(product_totals.units),
    "product_total_revenue": round(float(product_totals.revenue), 6),
    "product_total_browse_events": int(product_totals.browse),
}

for schema in ["bronze", "silver", "features"]:
    for table in spark.sql(f"SHOW TABLES IN {CATALOG}.{schema}").collect():
        if table.tableName in (
            {spec["name"] for spec in specs}
            if schema != "features"
            else {"customer_profile_features", "customer_behavior_features", "product_features"}
        ):
            spark.sql(f"ALTER TABLE {CATALOG}.{schema}.{table.tableName} OWNER TO `{OWNER}`")
spark.sql(f"ALTER TABLE {CATALOG}.gold.data_dictionary OWNER TO `{OWNER}`")
for view in [
    "customer_360",
    "product_360",
    "current_inventory",
    "active_promotions",
    "eligible_product_catalog",
    "current_recommendations",
]:
    spark.sql(f"ALTER VIEW {CATALOG}.gold.{view} OWNER TO `{OWNER}`")

bronze_rows = sum(
    spark.table(f"{CATALOG}.bronze.{spec['name']}")
    .where(F.col("transfer_version") == TRANSFER_VERSION)
    .count()
    for spec in specs
)
silver_rows = sum(spark.table(f"{CATALOG}.silver.{spec['name']}").count() for spec in specs)
require(bronze_rows == 275630 and silver_rows == 275630, "Layer reconciliation failed")

result = {
    "version": "azure_phase4_databricks_build_v1",
    "status": "PASS",
    "classification": "synthetic_data",
    "production_approved": False,
    "source_table_count": 20,
    "source_rows": 275630,
    "bronze_table_count": 20,
    "bronze_rows": bronze_rows,
    "silver_table_count": 20,
    "silver_rows": silver_rows,
    "feature_table_count": 3,
    "gold_view_count": 6,
    "data_dictionary_row_count": len(dictionary_rows),
    "first_pass_candidate_changes": first_changes,
    "second_pass_candidate_changes": second_pass_changes,
    "idempotency": "PASS_ZERO_SECOND_PASS_CHANGES",
    "orphan_check_count": len(orphan_results),
    "orphan_rows": sum(orphan_results.values()),
    "range_check_count": len(invalid_ranges),
    "invalid_range_rows": sum(invalid_ranges.values()),
    "future_leakage_rows": future_leakage_rows,
    "feature_parity": feature_parity,
    "currency_code": "UNSPECIFIED",
    "currency_gate": "REAL_SOURCE_CURRENCY_REQUIRED_BEFORE_PRODUCTION",
    "freshness_reference": "LATEST_FROZEN_SOURCE_SNAPSHOT",
    "customer_identifiers_recorded": False,
    "persistent_job_created": False,
    "schedule_created": False,
    "continuous_pipeline_created": False,
}
dbutils.notebook.exit(json.dumps(result, sort_keys=True))
