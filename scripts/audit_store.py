"""Audit of what the dev store's generated test data actually contains.

Run from the repo root: .venv/bin/python -m scripts.audit_store
"""

from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

from app.config import get_settings
from mcp_server.shopify_client import ShopifyClient, ShopifyGraphQLError


def fetch(label: str, fn: Callable[[], Iterator[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    # None means the call failed, an empty list means the store really has zero
    try:
        items = list(fn())
    except ShopifyGraphQLError as e:
        print(f"\n{label}: FAILED {e}")
        return None
    print(f"\n{label}: {len(items)} total")
    return items


def main() -> None:
    s = get_settings()
    with ShopifyClient(
        s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version
    ) as client:
        shop = client.shop_info()
        print(
            f"shop: {shop['name']} ({shop['myshopifyDomain']}), "
            f"currency {shop['currencyCode']}, plan {shop['plan']['displayName']}"
        )

        products = fetch("products", client.iterate_products)
        if products:
            by_status = Counter(p["status"] for p in products)
            variant_total = sum(len(p["variants"]["nodes"]) for p in products)
            no_desc = sum(1 for p in products if not p["description"])
            print(f"by status {dict(by_status)}")
            print(f"variants: {variant_total}, products without description: {no_desc}")
            print("sample:")
            for p in products[:8]:
                lo = p["priceRangeV2"]["minVariantPrice"]
                print(
                    f"  - {p['title']} [{p['status']}] from {lo['amount']} "
                    f"{lo['currencyCode']}, stock {p['totalInventory']}"
                )

        orders = fetch("orders", client.iterate_orders)
        if orders:
            fulfill = Counter(o["displayFulfillmentStatus"] for o in orders)
            financial = Counter(o["displayFinancialStatus"] for o in orders)
            with_email = sum(
                1 for o in orders if o["email"] or (o["customer"] or {}).get("email")
            )
            dates = sorted(o["createdAt"] for o in orders)
            print(f"{with_email} with a customer email")
            print(f"fulfillment: {dict(fulfill)}")
            print(f"financial: {dict(financial)}")
            print(f"created between {dates[0]} and {dates[-1]}")
            print("sample:")
            for o in orders[:5]:
                cust = (o["customer"] or {}).get("displayName") or "no customer"
                total = o["totalPriceSet"]["shopMoney"]
                print(
                    f"  - {o['name']} {o['displayFulfillmentStatus']}/"
                    f"{o['displayFinancialStatus']} {total['amount']} "
                    f"{total['currencyCode']} for {cust}"
                )

        customers = fetch("customers", client.iterate_customers)
        if customers:
            with_orders = sum(1 for c in customers if int(c.get("numberOfOrders") or 0) > 0)
            print(f"{with_orders} with at least one order")


if __name__ == "__main__":
    main()
