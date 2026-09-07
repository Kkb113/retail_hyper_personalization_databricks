# Phase 4 lakehouse contract

- Version: `retail_hp_lakehouse_v1`
- Input: sealed `retail_hp_transfer_v1` snapshot
- Classification: synthetic POC data
- Catalog: `intellify_databricks_demo`

All Phase 4 objects are owned by the approved `retail_hp_admins` group.

## Layer contract

| Layer | Objects | Contract |
|---|---:|---|
| Bronze | 20 Delta tables | Source columns plus transfer version, source file, source SHA-256, ingestion timestamp and run ID. Append-only and deduplicated by transfer version/source hash. |
| Silver | 20 Delta tables | Snake-case columns, deterministic types, conditional key-based merge and fail-closed source-hash drift handling. |
| Features | 3 Delta tables | Informational Unity Catalog primary keys; customer feature timestamps are `TIMESERIES` keys. |
| Gold | 6 governed views | Current Customer 360, Product 360, inventory, promotions, eligible catalog and recommendation snapshot. |
| Dictionary | 1 Delta table | Column-level type, nullability, layer, object and description metadata. |

## Source and key contract

| Entity | Business key |
|---|---|
| brands | `brand_id` |
| browsing_events | `event_id` |
| cart_events | `cart_event_id` |
| customer_preferences | `customer_id` |
| customer_product_audit | `customer_id`, `product_id` |
| customers | `customer_id` |
| inventory | `inventory_id` |
| product_categories | `category_id` |
| products | `product_id` |
| promotions | `promotion_id` |
| recommendation_log | `recommendation_id` |
| recommendation_response | `response_id` |
| recommendation_snapshot | `customer_id`, `rank` |
| regions | `region_id` |
| sales_order_lines | `order_line_id` |
| sales_orders | `order_id` |
| search_events | `search_id` |
| weather | `weather_id` |
| wishlist | `wishlist_id` |
| customer_profile_history | `customer_id`, `profile_version` |

## Feature contract

- `features.customer_profile_features`: customer profile versions keyed by
  (`customer_id`, `profile_timestamp TIMESERIES`).
- `features.customer_behavior_features`: cumulative browsing, cart, purchase,
  spend, wishlist and search signals keyed by
  (`customer_id`, `feature_timestamp TIMESERIES`). Events are assigned to the
  next UTC-day boundary, so every contributing event is strictly earlier than
  its feature timestamp.
- `features.product_features`: product-level sales, revenue and browse
  aggregates keyed by `product_id`.

This phase establishes offline feature tables. Phase 5 must use point-in-time
joins when assembling historical training sets and must bind the exact feature,
transfer, policy and model versions.

## Semantic decisions

- The source has no declared monetary currency. Monetary columns retain their
  source numeric values and carry `currency_code = 'UNSPECIFIED'`; production
  use is blocked until the owner supplies a governed currency contract.
- The Parquet recommendation snapshot stores nanosecond timestamps. Bronze
  retains the raw 64-bit value and Silver deterministically truncates it to
  Spark microsecond precision.
- Gold objects are deterministic current-snapshot views, not slowly changing
  history and not real-time feeds.
- Primary and foreign keys are informational/data-quality contracts. Unity
  Catalog does not enforce them as transactional constraints.

## Quality and operating contract

- Bronze and Silver must each reconcile to 275,630 rows.
- All 20 Silver business keys must be unique and non-null.
- Tested customer, product, category, brand, region and order relationships
  must have zero orphans.
- Prices, inventory, promotions, orders and weather percentages must satisfy
  their encoded range checks.
- `max_source_event_time < feature_timestamp` for every behavior feature row.
- Reapplying the same sealed snapshot must produce zero business-row changes.
- There is no schedule, continuous pipeline, all-purpose cluster, GPU, online
  feature store or new Azure resource in Phase 4.
- All 50 objects must have `retail_hp_admins` ownership; name-only inventory is
  insufficient for the governance gate.

## Production hold

This contract covers a synthetic, immutable POC snapshot. It does not establish
real-data privacy classification, deletion/retention obligations, source SLAs,
streaming freshness, production ownership, enforced WORM storage, or business
approval. Those remain release gates.
