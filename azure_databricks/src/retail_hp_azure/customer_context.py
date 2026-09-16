"""Versioned business evidence view; no new table, compute or raw-data grants."""
# ruff: noqa: S608 -- identifiers are fixed constants, not caller inputs.

CATALOG = "intellify_databricks_demo"
REQUIRED_SOURCE_COLUMNS = {
    "gold.customer_360": {
        "customer_id",
        "customer_status",
        "customer_segment",
        "loyalty_tier",
        "preferred_channel",
        "region_id",
        "behavior_as_of",
        "purchase_count",
        "browse_count",
        "favorite_category_id",
        "favorite_brand_id",
        "price_sensitivity",
        "color_preference",
    },
    "silver.sales_orders": {"order_id", "customer_id", "order_date", "order_status"},
    "silver.sales_order_lines": {"order_id", "product_id"},
    "silver.products": {"product_id", "product_name", "category_id", "brand_id"},
    "silver.product_categories": {"category_id", "category_name"},
    "silver.brands": {"brand_id", "brand_name"},
    "serving.customer_recommendations": {
        "customer_id",
        "product_id",
        "rank",
        "batch_id",
        "registered_model_version",
    },
}


def customer_view_sql() -> str:
    """Bound purchase evidence to the same historical cutoff as customer features."""
    return f"""CREATE OR REPLACE VIEW {CATALOG}.serving.tool_customers AS
WITH recent AS (
  SELECT c.customer_id, l.product_id, p.product_name, cat.category_name, b.brand_name,
         max(o.order_date) AS last_purchased_at, count(*) AS purchase_count,
         row_number() OVER (PARTITION BY c.customer_id
           ORDER BY max(o.order_date) DESC, l.product_id) AS rn
  FROM {CATALOG}.gold.customer_360 c
  JOIN {CATALOG}.silver.sales_orders o ON c.customer_id = o.customer_id
  JOIN {CATALOG}.silver.sales_order_lines l ON o.order_id = l.order_id
  JOIN {CATALOG}.silver.products p ON l.product_id = p.product_id
  JOIN {CATALOG}.silver.product_categories cat ON p.category_id = cat.category_id
  JOIN {CATALOG}.silver.brands b ON p.brand_id = b.brand_id
  WHERE lower(o.order_status) != 'cancelled' AND o.order_date < c.behavior_as_of
  GROUP BY c.customer_id, l.product_id, p.product_name, cat.category_name, b.brand_name
), evidence AS (
  SELECT customer_id, to_json(sort_array(collect_list(named_struct(
    'position', rn, 'product_id', product_id, 'product_name', product_name,
    'category_name', category_name, 'brand_name', brand_name,
    'last_purchased_at', CAST(last_purchased_at AS STRING),
    'purchase_count', purchase_count)))) AS recent_purchases_json
  FROM recent WHERE rn <= 10 GROUP BY customer_id
)
SELECT c.customer_id, c.customer_segment, c.loyalty_tier, c.preferred_channel,
       c.region_id, c.behavior_as_of, c.purchase_count, c.browse_count,
       cat.category_name AS favorite_category_name, b.brand_name AS favorite_brand_name,
       c.price_sensitivity, c.color_preference,
       'synthetic_profile_not_verified_preference' AS preference_source,
       coalesce(e.recent_purchases_json, '[]') AS recent_purchases_json,
       'customer_context_v2' AS evidence_version
FROM {CATALOG}.gold.customer_360 c
LEFT JOIN {CATALOG}.silver.product_categories cat ON c.favorite_category_id = cat.category_id
LEFT JOIN {CATALOG}.silver.brands b ON c.favorite_brand_id = b.brand_id
LEFT JOIN evidence e ON c.customer_id = e.customer_id
WHERE c.customer_status = 'Active'
"""
