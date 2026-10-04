"""Read-only snapshot of the development store into the seed file the simulated backend loads.

Run from the repo root: .venv/bin/python -m scripts.export_store_snapshot
"""

import argparse

from app.config import get_settings
from mcp_server.backends.shopify import (
    SNAPSHOT_CUSTOMERS_QUERY,
    SNAPSHOT_ORDERS_QUERY,
    SNAPSHOT_PRODUCTS_QUERY,
    map_customer,
    map_order,
    map_product,
)
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import Customer, Order, Product, SimDB, SnapshotMeta, db_hash, save_db

DEFAULT_OUT = "data/sim/seed.json"
FROZEN_NOW = "2026-10-04T22:54:00Z"
NOTES = [
    "Exported read-only from the development store.",
    "Order returns are not exported until the app holds the read_returns scope, so every order starts with none.",
]


def integrity_problems(
    products: dict[str, Product], customers: dict[str, Customer], orders: dict[str, Order]
) -> list[str]:
    variant_ids = {vid for p in products.values() for vid in p.variants}
    problems = []
    for order in orders.values():
        if order.customer_id and order.customer_id not in customers:
            problems.append(f"{order.name} references a missing customer")
        for li in order.line_items:
            if li.product_id and li.product_id not in products:
                problems.append(f"{order.name} line '{li.title}' references a missing product")
            if li.variant_id and li.variant_id not in variant_ids:
                problems.append(f"{order.name} line '{li.title}' references a missing variant")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--frozen-now", default=FROZEN_NOW)
    args = parser.parse_args()

    s = get_settings()
    with ShopifyClient(s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version) as client:
        shop = client.shop_info()
        if not shop["plan"]["partnerDevelopment"]:
            raise SystemExit("refusing to export: this is not a development store")

        products = {
            p.product_id: p
            for p in map(map_product, client.paginate(SNAPSHOT_PRODUCTS_QUERY, "products"))
        }
        customers = {
            c.customer_id: c
            for c in map(map_customer, client.paginate(SNAPSHOT_CUSTOMERS_QUERY, "customers"))
        }
        orders: dict[str, Order] = {}
        for node in client.paginate(SNAPSHOT_ORDERS_QUERY, "orders"):
            found = map_order(node)
            orders[found.order.name] = found.order
            if found.customer:
                customers.setdefault(found.customer.customer_id, found.customer)
        deprecations = sorted(client.deprecations)

    problems = integrity_problems(products, customers, orders)
    if problems:
        raise SystemExit("snapshot failed integrity checks:\n  " + "\n  ".join(problems))

    db = SimDB(
        meta=SnapshotMeta(
            store_domain=shop["myshopifyDomain"],
            api_version=s.shopify_api_version,
            frozen_now=args.frozen_now,
            notes=NOTES,
        ),
        products=products,
        customers=customers,
        orders=orders,
    )
    save_db(db, args.out)

    print(f"store {shop['myshopifyDomain']} at api {s.shopify_api_version}")
    print(f"products {len(products)}, customers {len(customers)}, orders {len(orders)}")
    print(f"deprecated fields used: {', '.join(deprecations) or 'none'}")
    print(f"wrote {args.out}")
    print(f"state hash {db_hash(db)}")


if __name__ == "__main__":
    main()
