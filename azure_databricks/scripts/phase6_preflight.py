"""Read-only Phase 6 price, registry and compute inspection."""

import json

import requests
from retail_hp_azure.phase2 import CloudContext, inspect_compute
from retail_hp_azure.phase2_compute import _verify_budget
from retail_hp_azure.phase5 import inspect_registered_model

response = requests.get(
    "https://prices.azure.com/api/retail/prices",
    params={
        "currencyCode": "INR",
        "$filter": "serviceName eq 'Azure Databricks' and armRegionName eq 'westus'",
    },
    timeout=45,
)
response.raise_for_status()
prices = response.json()
print(
    json.dumps(
        {
            "prices": [
                {k: x[k] for k in ("meterName", "retailPrice", "unitOfMeasure", "skuName")}
                for x in prices["Items"]
            ],
            "more_prices": bool(prices.get("NextPageLink")),
        }
    ),
    flush=True,
)
context = CloudContext()
print(
    json.dumps(
        {
            "registry": inspect_registered_model(context),
            "compute": inspect_compute(context),
            "budget": _verify_budget(context),
            "active_runs": len(list(context.client.jobs.list_runs(active_only=True, limit=25))),
        }
    )
)
