"""One-off store seeder: descriptions, new catalog, customers, orders.

Needs the temporary write scopes on the app. Everything it writes comes from
data/seed/, so the store state is reproducible from the repo.

Run from the repo root: .venv/bin/python -m scripts.seed_store
"""

import json
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from mcp_server.shopify_client import ShopifyClient, ShopifyGraphQLError

SEED_DIR = Path("data/seed")

DESCRIPTION_MUTATION = """
mutation SetDescription($id: ID!, $html: String!) {
  productUpdate(product: {id: $id, descriptionHtml: $html}) {
    product { id }
    userErrors { field message }
  }
}
"""

PRODUCT_SET_MUTATION = """
mutation CreateProduct($input: ProductSetInput!) {
  productSet(input: $input, synchronous: true) {
    product { id handle }
    userErrors { field message }
  }
}
"""

CUSTOMER_CREATE_MUTATION = """
mutation CreateCustomer($input: CustomerInput!) {
  customerCreate(input: $input) {
    customer { id email }
    userErrors { field message }
  }
}
"""

CUSTOMER_LOOKUP_QUERY = """
query FindCustomer($query: String!) {
  customers(first: 1, query: $query) { nodes { id email } }
}
"""

DRAFT_CREATE_MUTATION = """
mutation CreateDraft($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id }
    userErrors { field message }
  }
}
"""

DRAFT_COMPLETE_MUTATION = """
mutation CompleteDraft($id: ID!, $pending: Boolean) {
  draftOrderComplete(id: $id, paymentPending: $pending) {
    draftOrder { order { id name } }
    userErrors { field message }
  }
}
"""

FULFILLMENT_ORDERS_QUERY = """
query FulfillmentOrders($id: ID!) {
  order(id: $id) {
    fulfillmentOrders(first: 10) { nodes { id status } }
  }
}
"""

FULFILLMENT_CREATE_MUTATION = """
mutation Fulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

ORDER_CANCEL_MUTATION = """
mutation Cancel($orderId: ID!) {
  orderCancel(orderId: $orderId, reason: CUSTOMER, refund: true, restock: true, notifyCustomer: false) {
    job { id }
    orderCancelUserErrors { field message }
  }
}
"""


def load(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text())


def check(data: dict[str, Any], key: str, error_field: str = "userErrors") -> dict[str, Any]:
    errors = data[key].get(error_field) or []
    if errors:
        raise ShopifyGraphQLError(f"{key}: {errors}")
    return data[key]


def pick_location(client: ShopifyClient) -> str:
    # "Shop location" is the store default, the custom one exists for multi-location fixtures
    nodes = client.graphql("{ locations(first: 10) { nodes { id name } } }")["locations"]["nodes"]
    for loc in nodes:
        if loc["name"] == "Shop location":
            return loc["id"]
    return nodes[0]["id"]


def seed_descriptions(client: ShopifyClient) -> None:
    wanted: dict[str, str] = load("descriptions.json")
    updated = skipped = 0
    for p in client.iterate_products():
        html = wanted.get(p["handle"])
        if not html:
            continue
        if p["description"]:
            skipped += 1
            continue
        check(client.graphql(DESCRIPTION_MUTATION, {"id": p["id"], "html": html}), "productUpdate")
        updated += 1
    print(f"descriptions: {updated} updated, {skipped} already had one")


def seed_products(client: ShopifyClient, location_id: str) -> None:
    existing = {p["handle"] for p in client.iterate_products()}
    created = skipped = 0
    for spec in load("products.json"):
        if spec["handle"] in existing:
            skipped += 1
            continue
        option = spec["option_name"] or "Title"
        names = [v["name"] or "Default Title" for v in spec["variants"]]
        variants = [
            {
                "optionValues": [{"optionName": option, "name": name}],
                "price": v["price"],
                "inventoryItem": {"tracked": True},
                "inventoryQuantities": [
                    {"locationId": location_id, "name": "available", "quantity": v["stock"]}
                ],
            }
            for v, name in zip(spec["variants"], names)
        ]
        payload = {
            "title": spec["title"],
            "handle": spec["handle"],
            "descriptionHtml": spec["description"],
            "productType": spec["product_type"],
            "vendor": "Aurora Outfitters",
            "tags": spec["tags"],
            "status": "ACTIVE",
            "productOptions": [{"name": option, "position": 1, "values": [{"name": n} for n in names]}],
            "variants": variants,
        }
        check(client.graphql(PRODUCT_SET_MUTATION, {"input": payload}), "productSet")
        created += 1
    print(f"products: {created} created, {skipped} already existed")


def seed_customers(client: ShopifyClient) -> dict[str, str]:
    ids: dict[str, str] = {}
    created = skipped = 0
    for spec in load("customers.json"):
        found = client.graphql(CUSTOMER_LOOKUP_QUERY, {"query": f"email:{spec['email']}"})
        nodes = found["customers"]["nodes"]
        if nodes:
            ids[spec["email"]] = nodes[0]["id"]
            skipped += 1
            continue
        result = check(
            client.graphql(
                CUSTOMER_CREATE_MUTATION,
                {
                    "input": {
                        "firstName": spec["first_name"],
                        "lastName": spec["last_name"],
                        "email": spec["email"],
                    }
                },
            ),
            "customerCreate",
        )
        ids[spec["email"]] = result["customer"]["id"]
        created += 1
    print(f"customers: {created} created, {skipped} already existed")
    return ids


def variant_map(client: ShopifyClient) -> dict[str, dict[str, str]]:
    # handle -> variant title -> variant gid, resolved live so seeds reference names not ids
    result: dict[str, dict[str, str]] = {}
    for p in client.iterate_products():
        result[p["handle"]] = {v["title"]: v["id"] for v in p["variants"]["nodes"]}
    return result


def wait_for_fulfillment_orders(client: ShopifyClient, order_id: str) -> list[dict[str, Any]]:
    # fulfillment orders appear a beat after the order does
    for _ in range(6):
        nodes = client.graphql(FULFILLMENT_ORDERS_QUERY, {"id": order_id})["order"][
            "fulfillmentOrders"
        ]["nodes"]
        open_fos = [fo for fo in nodes if fo["status"] in ("OPEN", "IN_PROGRESS")]
        if open_fos:
            return open_fos
        time.sleep(1.0)
    return []


def seed_orders(client: ShopifyClient, customer_ids: dict[str, str]) -> None:
    if list(client.iterate_orders()):
        print("orders: store already has orders, skipping order seeding entirely")
        return
    variants = variant_map(client)
    specs = load("orders.json")
    for i, spec in enumerate(specs, start=1):
        line_items = []
        for item in spec["items"]:
            title = item["variant"] or "Default Title"
            line_items.append(
                {"variantId": variants[item["handle"]][title], "quantity": item["quantity"]}
            )
        draft_input = {
            "email": spec["customer_email"],
            "purchasingEntity": {"customerId": customer_ids[spec["customer_email"]]},
            "tags": ["seeded"],
            "lineItems": line_items,
        }
        draft = check(client.graphql(DRAFT_CREATE_MUTATION, {"input": draft_input}), "draftOrderCreate")
        pending = spec["state"] == "pending_payment"
        completed = check(
            client.graphql(
                DRAFT_COMPLETE_MUTATION, {"id": draft["draftOrder"]["id"], "pending": pending}
            ),
            "draftOrderComplete",
        )
        order = completed["draftOrder"]["order"]
        state = spec["state"]

        if state == "fulfilled":
            fos = wait_for_fulfillment_orders(client, order["id"])
            if not fos:
                raise ShopifyGraphQLError(f"no open fulfillment orders for {order['name']}")
            for fo in fos:
                fulfillment = {
                    "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo["id"]}],
                    "trackingInfo": {
                        "number": spec["tracking"]["number"],
                        "company": spec["tracking"]["company"],
                    },
                    "notifyCustomer": False,
                }
                check(
                    client.graphql(FULFILLMENT_CREATE_MUTATION, {"fulfillment": fulfillment}),
                    "fulfillmentCreate",
                )
        elif state == "cancelled":
            check(
                client.graphql(ORDER_CANCEL_MUTATION, {"orderId": order["id"]}),
                "orderCancel",
                error_field="orderCancelUserErrors",
            )

        print(f"orders: {i}/{len(specs)} {order['name']} -> {state}")


def main() -> None:
    s = get_settings()
    with ShopifyClient(
        s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version
    ) as client:
        location_id = pick_location(client)
        seed_descriptions(client)
        seed_products(client, location_id)
        customer_ids = seed_customers(client)
        seed_orders(client, customer_ids)
    print("seeding done")


if __name__ == "__main__":
    main()
