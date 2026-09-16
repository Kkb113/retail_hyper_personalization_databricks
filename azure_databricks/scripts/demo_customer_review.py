"""Free offline review of the same synthetic cohort selected for batch publishing."""

import json
from pathlib import Path

import pandas as pd
from retail_hp_azure.demo_cohort import select_demo_customers
from retail_hp_azure.recommender import AdaptiveRetailRecommender
from threadpoolctl import threadpool_limits

root = Path(__file__).resolve().parents[2]
model = AdaptiveRetailRecommender(root / "migration_assets", root / "migration_assets/artifacts")
ids = select_demo_customers(list(model.profiles.index.astype(str)))
requests = pd.DataFrame(
    [dict(request_id=c, request_type="existing_customer", customer_id=c, top_n=10) for c in ids]
)
with threadpool_limits(limits=1):
    scored = model.predict(requests)
joined = scored.merge(
    model.products, left_on="product_id", right_on="ProductID", validate="many_to_one"
)
reviews = []
for customer, rows in joined.groupby("customer_id"):
    profile = model.profiles.loc[customer]
    events = model.events[model.events.CustomerID.eq(customer)]
    reviews.append(
        {
            "customer_id": customer,
            "segment": str(profile.CustomerSegment),
            "purchase_entries": int(events.EventType.eq("purchase").sum()),
            "unique_products": int(rows.product_id.nunique()),
            "categories": int(rows.CategoryName.nunique()),
            "products": rows.sort_values("rank").ProductName.tolist(),
        }
    )
assert all(r["unique_products"] == 10 for r in reviews)
repeat = sorted(
    (r for r in reviews if r["purchase_entries"] >= 3),
    key=lambda r: (-r["categories"], -r["purchase_entries"], r["customer_id"]),
)
new = sorted(
    (r for r in reviews if r["purchase_entries"] == 0),
    key=lambda r: (-r["categories"], r["customer_id"]),
)
report = {
    "status": "PASS_LOCAL_MODEL_REVIEW",
    "live_validated": False,
    "scope": "synthetic_demo_cohort",
    "reviewed_customers": len(reviews),
    "selection_basis": "product category diversity and recorded purchase entries; not accuracy",
    "repeat_customer_examples": repeat[:3],
    "new_customer_example": new[:1],
}
target = root / "azure_databricks/evidence/phase_10/demo_customer_review.json"
target.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
