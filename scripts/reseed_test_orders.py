"""Tops up the development store's live-write test orders, since cancellation cannot be undone.

Dry run by default: prints which fixtures are still usable and which would be recreated.
Writing needs --apply plus the write_draft_orders, write_merchant_managed_fulfillment_orders
and write_fulfillments scopes. Fixture orders carry the v2-live-test tag and are never deleted.

Run from the repo root: .venv/bin/python -m scripts.reseed_test_orders [--apply]
"""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import get_settings
from mcp_server.clock import Clock, SystemClock, parse_instant
from mcp_server.shopify_client import ShopifyClient, ShopifyGraphQLError
from scripts.seed_store import (
    DRAFT_COMPLETE_MUTATION,
    DRAFT_CREATE_MUTATION,
    FULFILLMENT_CREATE_MUTATION,
    check,
    variant_map,
    wait_for_fulfillment_orders,
)

FIXTURES_PATH = Path("data/seed/live_test_orders.json")
SUITE_TAG = "v2-live-test"
RETURN_WINDOW_DAYS = 30
TEST_ADDRESS = {
    "address1": "123 Trailhead Way",
    "city": "Boulder",
    "provinceCode": "CO",
    "countryCode": "US",
    "zip": "80302",
}

FIXTURE_ORDERS_QUERY = """
query FixtureOrders($query: String!) {
  orders(first: 100, query: $query) {
    nodes {
      id name tags cancelledAt displayFulfillmentStatus
      fulfillments { deliveredAt }
    }
  }
}
"""

CUSTOMER_ID_QUERY = """
query CustomerId($query: String!) {
  customers(first: 1, query: $query) { nodes { id } }
}
"""

DELIVERED_EVENT_MUTATION = """
mutation Delivered($event: FulfillmentEventInput!) {
  fulfillmentEventCreate(fulfillmentEvent: $event) {
    fulfillmentEvent { id status }
    userErrors { field message }
  }
}
"""

FULFILLMENT_ID_QUERY = """
query FulfillmentIds($id: ID!) {
  order(id: $id) { fulfillments { id } }
}
"""


@dataclass(frozen=True)
class ExistingOrder:
    name: str
    cancelled: bool
    fulfillment_status: str
    delivered_at: datetime | None


@dataclass(frozen=True)
class Decision:
    key: str
    action: str
    reason: str


def fixture_tag(key: str) -> str:
    return f"fixture-{key}"


def is_usable(spec: dict[str, Any], order: ExistingOrder, now: datetime) -> bool:
    if order.cancelled:
        return False
    state = spec["state"]
    if state == "unfulfilled":
        return order.fulfillment_status == "UNFULFILLED"
    if state == "fulfilled":
        return order.fulfillment_status == "FULFILLED" and order.delivered_at is None
    if state == "delivered":
        if order.delivered_at is None:
            return False
        inside = now - order.delivered_at <= timedelta(days=RETURN_WINDOW_DAYS)
        return inside == (spec["delivered_days_ago"] <= RETURN_WINDOW_DAYS)
    raise ValueError(f"unknown fixture state {state}")


def plan(
    specs: list[dict[str, Any]], existing: dict[str, list[ExistingOrder]], now: datetime
) -> list[Decision]:
    decisions = []
    for spec in specs:
        usable = [o for o in existing.get(spec["key"], []) if is_usable(spec, o, now)]
        if usable:
            decisions.append(Decision(spec["key"], "keep", f"{usable[0].name} still usable"))
        else:
            spent = len(existing.get(spec["key"], []))
            decisions.append(Decision(spec["key"], "create", f"{spent} earlier order(s), none usable"))
    return decisions


def load_existing(client: ShopifyClient) -> dict[str, list[ExistingOrder]]:
    nodes = client.graphql(FIXTURE_ORDERS_QUERY, {"query": f"tag:{SUITE_TAG}"})["orders"]["nodes"]
    by_key: dict[str, list[ExistingOrder]] = {}
    for node in nodes:
        delivered = [f["deliveredAt"] for f in node["fulfillments"] if f.get("deliveredAt")]
        order = ExistingOrder(
            name=node["name"],
            cancelled=node["cancelledAt"] is not None,
            fulfillment_status=node["displayFulfillmentStatus"],
            delivered_at=parse_instant(max(delivered)) if delivered else None,
        )
        for tag in node["tags"]:
            if tag.startswith("fixture-"):
                by_key.setdefault(tag.removeprefix("fixture-"), []).append(order)
    return by_key


def resolve_line_items(spec: dict[str, Any], variants: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    items = []
    for item in spec["items"]:
        titles = variants.get(item["handle"])
        if titles is None or item["variant"] not in titles:
            raise SystemExit(f"fixture {spec['key']}: {item['handle']} / {item['variant']} is not in the store")
        items.append({"variantId": titles[item["variant"]], "quantity": item["quantity"]})
    return items


def create_fixture(
    client: ShopifyClient, spec: dict[str, Any], line_items: list[dict[str, Any]], clock: Clock
) -> str:
    customers = client.graphql(CUSTOMER_ID_QUERY, {"query": f"email:{spec['customer_email']}"})
    customer_nodes = customers["customers"]["nodes"]
    if not customer_nodes:
        raise SystemExit(f"fixture {spec['key']}: no customer {spec['customer_email']}")
    draft_input = {
        "email": spec["customer_email"],
        "purchasingEntity": {"customerId": customer_nodes[0]["id"]},
        "tags": [SUITE_TAG, fixture_tag(spec["key"])],
        "shippingAddress": TEST_ADDRESS,
        "lineItems": line_items,
    }
    draft = check(client.graphql(DRAFT_CREATE_MUTATION, {"input": draft_input}), "draftOrderCreate")
    completed = check(
        client.graphql(DRAFT_COMPLETE_MUTATION, {"id": draft["draftOrder"]["id"], "pending": False}),
        "draftOrderComplete",
    )
    order = completed["draftOrder"]["order"]
    if spec["state"] in ("fulfilled", "delivered"):
        fulfillment_orders = wait_for_fulfillment_orders(client, order["id"])
        if not fulfillment_orders:
            raise ShopifyGraphQLError(f"no open fulfillment orders for {order['name']}")
        for fo in fulfillment_orders:
            fulfillment = {
                "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo["id"]}],
                "trackingInfo": spec["tracking"],
                "notifyCustomer": False,
            }
            check(
                client.graphql(FULFILLMENT_CREATE_MUTATION, {"fulfillment": fulfillment}),
                "fulfillmentCreate",
            )
    if spec["state"] == "delivered":
        happened = clock.now() - timedelta(days=spec["delivered_days_ago"])
        fulfillments = client.graphql(FULFILLMENT_ID_QUERY, {"id": order["id"]})["order"]["fulfillments"]
        for f in fulfillments:
            event = {
                "fulfillmentId": f["id"],
                "status": "DELIVERED",
                "happenedAt": happened.isoformat().replace("+00:00", "Z"),
            }
            check(client.graphql(DELIVERED_EVENT_MUTATION, {"event": event}), "fulfillmentEventCreate")
    return order["name"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    specs = json.loads(FIXTURES_PATH.read_text())
    clock = SystemClock()
    s = get_settings()
    with ShopifyClient(s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version) as client:
        shop = client.shop_info()
        if not shop["plan"]["partnerDevelopment"]:
            raise SystemExit("refusing to run: this is not a development store")
        variants = variant_map(client)
        line_items = {spec["key"]: resolve_line_items(spec, variants) for spec in specs}
        decisions = plan(specs, load_existing(client), clock.now())

        for d in decisions:
            print(f"{d.key:<24} {d.action:<7} {d.reason}")
        to_create = [spec for spec in specs if any(d.key == spec["key"] and d.action == "create" for d in decisions)]
        if not args.apply:
            print(f"\ndry run: {len(to_create)} order(s) would be created. Re-run with --apply to write.")
            return
        for spec in to_create:
            name = create_fixture(client, spec, line_items[spec["key"]], clock)
            print(f"created {name} for {spec['key']}")


if __name__ == "__main__":
    main()
