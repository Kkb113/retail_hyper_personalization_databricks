"""Deterministic business facts, separate from unverified model attribution."""

from typing import Any


def product_reason(product: dict[str, Any], customer: dict[str, Any]) -> str:
    history = customer.get("recent_purchases", [])
    if any(p["product_id"] == product["product_id"] for p in history):
        return "Previously purchased by this customer. Check whether a repeat purchase is useful."
    category = product.get("category_name")
    brand = product.get("brand_name")
    if category and any(p["category_name"] == category for p in history):
        return f"Past purchases include {category}, making this a related option to explore."
    if brand and any(p["brand_name"] == brand for p in history):
        return f"The customer has previously bought from {brand}; this offers another option."
    if category and customer.get("favorite_category_name") == category:
        return f"Matches {category}, the category listed in the demo customer profile."
    if brand and customer.get("favorite_brand_name") == brand:
        return f"Matches {brand}, the brand listed in the demo customer profile."
    return "An option to explore; the available history does not establish a specific preference."


def recommendation_summary(customer: dict[str, Any], count: int) -> str:
    if not customer:
        return f"Here are {count} product suggestions. Customer details are currently unavailable."
    return (
        f"Here are {count} suggestions for {customer['customer_id']}, "
        f"a {customer['customer_segment']} with {customer['loyalty_tier']} loyalty status. "
        f"The available record contains {customer['purchase_count']} purchase entries and "
        f"{customer['browse_count']} browsing visits. Use the matches below to guide "
        "the next conversation, rather than treating them as confirmed purchase intent."
    )


def recommendation_items(cards: list[dict[str, Any]]) -> list[str]:
    return [
        f"{i}. {card.get('product_name', card['product_id'])} — {card['business_reason']}"
        for i, card in enumerate(cards, 1)
    ]


def customer_items(customer: dict[str, Any]) -> list[str]:
    if not customer:
        return ["Customer details are temporarily unavailable."]
    items = [
        f"Preferred shopping channel: {customer['preferred_channel']}.",
        f"Loyalty status: {customer['loyalty_tier']}.",
        f"Customer history cutoff: {customer['behavior_as_of']}.",
    ]
    for field, label in (
        ("favorite_category_name", "Category"),
        ("favorite_brand_name", "Brand"),
        ("price_sensitivity", "Price sensitivity"),
    ):
        if customer.get(field):
            items.append(f"{label} listed in the synthetic profile: {customer[field]}.")
    purchases = customer.get("recent_purchases", [])
    if purchases:
        items.append(
            "Recent recorded purchases include: "
            + "; ".join(p["product_name"] for p in purchases[:3])
            + "."
        )
    return items
