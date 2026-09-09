"""One deterministic cohort policy for batch publication and offline demo review."""

from retail_hp_azure.safety import require


class CustomerDirectoryUnavailable(RuntimeError):
    """Dependency availability, not failed user authentication."""


def select_demo_customers(customer_ids: list[str], *, limit: int = 100) -> list[str]:
    require(1 <= limit <= 5000, "Invalid demo cohort limit")
    ids = sorted(customer_ids)
    require(len(ids) == len(set(ids)) and bool(ids), "Invalid customer population")
    size = min(limit, len(ids))
    return [ids[i * len(ids) // size] for i in range(size)]
