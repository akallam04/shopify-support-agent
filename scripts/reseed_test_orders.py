"""Tops up the development store's live-write test orders, since cancellation cannot be undone.

Orders are created with orderCreate so their order and delivery dates can sit in the past,
which lets one store hold returns both inside and outside the 30 day window. Every fixture time
is an offset from one anchor instant (default: now); export the simulated store with that same
anchor as its frozen time. Dry run by default. Writing needs --apply and SHOPIFY_WRITE_TOKEN (see docs/live-write-testing.md).
Fixture orders carry the v2-live-test tag and are never deleted.

Run from the repo root: .venv/bin/python -m scripts.reseed_test_orders [--apply]
"""

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import get_settings
from mcp_server.clock import SystemClock, parse_ago, parse_instant
from mcp_server.shopify_client import ShopifyClient
from scripts.seed_store import check, pick_location

FIXTURES_PATH = Path("data/seed/live_test_orders.json")
SUITE_TAG = "v2-live-test"
RETURN_WINDOW_DAYS = 30
CHANGE_WINDOW = timedelta(hours=2)
FRESHNESS_MARGIN = timedelta(minutes=30)
ORDER_CREATE_PACING_S = 13
DELIVERY_TOLERANCE = timedelta(minutes=5)
REQUIRED_WRITE_SCOPES = frozenset(
    {"write_orders", "write_fulfillments", "read_products", "read_customers", "read_locations"}
)
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
    nodes { id name tags processedAt cancelledAt displayFulfillmentStatus returnStatus fulfillments { deliveredAt } }
  }
}
"""

SCOPES_QUERY = "{ currentAppInstallation { accessScopes { handle } } }"

CUSTOMER_QUERY = """
query Customer($query: String!) {
  customers(first: 1, query: $query) { nodes { id firstName lastName } }
}
"""

ORDER_CREATE_MUTATION = """
mutation CreateOrder($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) {
    order { id name fulfillments { id } }
    userErrors { field message }
  }
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

DELIVERED_AT_QUERY = """
query DeliveredAt($id: ID!) {
  order(id: $id) { fulfillments { deliveredAt } }
}
"""


class BackdateNotHonoredError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExistingOrder:
    name: str
    cancelled: bool
    fulfillment_status: str
    delivered_at: datetime | None
    returned: bool = False
    placed_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    key: str
    action: str
    reason: str


@dataclass(frozen=True)
class Line:
    variant_id: str
    price: Decimal
    quantity: int


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fixture_tag(key: str) -> str:
    return f"fixture-{key}"


def is_usable(spec: dict[str, Any], order: ExistingOrder, now: datetime) -> bool:
    if order.cancelled or order.returned:
        return False
    state = spec["state"]
    if state == "unfulfilled":
        fresh = order.placed_at is not None and now - order.placed_at <= CHANGE_WINDOW - FRESHNESS_MARGIN
        return order.fulfillment_status == "UNFULFILLED" and fresh
    if state == "fulfilled":
        return order.fulfillment_status == "FULFILLED" and order.delivered_at is None
    if state == "delivered":
        if order.delivered_at is None:
            return False
        inside = now - order.delivered_at <= timedelta(days=RETURN_WINDOW_DAYS)
        return inside == (parse_ago(spec["delivered_ago"]) <= timedelta(days=RETURN_WINDOW_DAYS))
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


def creation_order(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(specs, key=lambda s: -parse_ago(s["delivered_ago"]) if "delivered_ago" in s else timedelta(0))


def order_input(
    spec: dict[str, Any], lines: list[Line], customer: dict[str, Any], location_id: str, now: datetime
) -> dict[str, Any]:
    placed = iso(now - parse_ago(spec["placed_ago"]))
    total = sum((line.price * line.quantity for line in lines), Decimal("0"))
    order: dict[str, Any] = {
        "lineItems": [{"variantId": line.variant_id, "quantity": line.quantity} for line in lines],
        "customer": {"toAssociate": {"id": customer["id"]}},
        "email": spec["customer_email"],
        "shippingAddress": {
            "firstName": customer.get("firstName") or "",
            "lastName": customer.get("lastName") or "",
            **TEST_ADDRESS,
        },
        "processedAt": placed,
        "financialStatus": "PAID",
        "transactions": [
            {
                "kind": "SALE",
                "status": "SUCCESS",
                "gateway": "manual",
                "processedAt": placed,
                "amountSet": {"shopMoney": {"amount": f"{total:.2f}", "currencyCode": "USD"}},
            }
        ],
        "tags": [SUITE_TAG, fixture_tag(spec["key"])],
    }
    if spec["state"] in ("fulfilled", "delivered"):
        order["fulfillmentStatus"] = "FULFILLED"
        order["fulfillment"] = {
            "locationId": location_id,
            "trackingNumber": spec["tracking"]["number"],
            "trackingCompany": spec["tracking"]["company"],
            "shipmentStatus": "IN_TRANSIT",
            "notifyCustomer": False,
        }
    return order


def load_catalog(client: ShopifyClient) -> dict[str, dict[str, Line]]:
    catalog: dict[str, dict[str, Line]] = {}
    for p in client.iterate_products():
        catalog[p["handle"]] = {
            v["title"]: Line(variant_id=v["id"], price=Decimal(v["price"]), quantity=1)
            for v in p["variants"]["nodes"]
        }
    return catalog


def resolve_lines(spec: dict[str, Any], catalog: dict[str, dict[str, Line]]) -> list[Line]:
    lines = []
    for item in spec["items"]:
        variant = catalog.get(item["handle"], {}).get(item["variant"])
        if variant is None:
            raise SystemExit(f"fixture {spec['key']}: {item['handle']} / {item['variant']} is not in the store")
        lines.append(Line(variant_id=variant.variant_id, price=variant.price, quantity=item["quantity"]))
    return lines


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
            returned=node["returnStatus"] != "NO_RETURN",
            placed_at=parse_instant(node["processedAt"]),
        )
        for tag in node["tags"]:
            if tag.startswith("fixture-"):
                by_key.setdefault(tag.removeprefix("fixture-"), []).append(order)
    return by_key


def missing_scopes(client: ShopifyClient) -> set[str]:
    granted = {s["handle"] for s in client.graphql(SCOPES_QUERY)["currentAppInstallation"]["accessScopes"]}
    return set(REQUIRED_WRITE_SCOPES) - granted


def create_fixture(
    client: ShopifyClient, spec: dict[str, Any], lines: list[Line], location_id: str, now: datetime
) -> str:
    customers = client.graphql(CUSTOMER_QUERY, {"query": f"email:{spec['customer_email']}"})["customers"]["nodes"]
    if not customers:
        raise SystemExit(f"fixture {spec['key']}: no customer {spec['customer_email']}")
    created = check(
        client.graphql(
            ORDER_CREATE_MUTATION,
            {
                "order": order_input(spec, lines, customers[0], location_id, now),
                "options": {"sendReceipt": False, "sendFulfillmentReceipt": False},
            },
        ),
        "orderCreate",
    )
    order = created["order"]
    if spec["state"] == "delivered":
        target = now - parse_ago(spec["delivered_ago"])
        for fulfillment in order["fulfillments"]:
            event = {"fulfillmentId": fulfillment["id"], "status": "DELIVERED", "happenedAt": iso(target)}
            check(client.graphql(DELIVERED_EVENT_MUTATION, {"event": event}), "fulfillmentEventCreate")
        recorded = client.graphql(DELIVERED_AT_QUERY, {"id": order["id"]})["order"]["fulfillments"]
        delivered = [parse_instant(f["deliveredAt"]) for f in recorded if f.get("deliveredAt")]
        if not delivered or abs(max(delivered) - target) > DELIVERY_TOLERANCE:
            got = iso(max(delivered)) if delivered else "nothing"
            raise BackdateNotHonoredError(
                f"{order['name']}: asked for delivery at {iso(target)}, Shopify recorded {got}"
            )
    return order["name"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--anchor", help="ISO time every fixture offset counts back from (default: now)")
    args = parser.parse_args()

    s = get_settings()
    if args.apply and not s.shopify_write_token:
        raise SystemExit("SHOPIFY_WRITE_TOKEN is not set in .env, see docs/live-write-testing.md")
    token = s.shopify_write_token or s.shopify_admin_token
    specs = json.loads(FIXTURES_PATH.read_text())
    real_now = SystemClock().now()
    now = parse_instant(args.anchor) if args.anchor else real_now.replace(second=0, microsecond=0)
    if now > real_now:
        raise SystemExit("the anchor cannot be in the future, Shopify clamps future order dates")
    print(f"anchor {iso(now)}: export the simulated store with --frozen-now {iso(now)}")

    with ShopifyClient(s.shopify_store_domain, token, s.shopify_api_version) as client:
        if not client.shop_info()["plan"]["partnerDevelopment"]:
            raise SystemExit("refusing to run: this is not a development store")
        if args.apply:
            missing = missing_scopes(client)
            if missing:
                raise SystemExit(f"the write-test app is missing scopes: {', '.join(sorted(missing))}")
        catalog = load_catalog(client)
        lines = {spec["key"]: resolve_lines(spec, catalog) for spec in specs}
        decisions = plan(specs, load_existing(client), real_now)

        for d in decisions:
            print(f"{d.key:<24} {d.action:<7} {d.reason}")
        wanted = {d.key for d in decisions if d.action == "create"}
        to_create = [spec for spec in creation_order(specs) if spec["key"] in wanted]
        for spec in to_create:
            placed = iso(now - parse_ago(spec["placed_ago"]))
            delivered = f", delivered {iso(now - parse_ago(spec['delivered_ago']))}" if "delivered_ago" in spec else ""
            print(f"  would create {spec['key']}: placed {placed}{delivered}")
        if not args.apply:
            print(f"\ndry run: {len(to_create)} order(s) would be created. Re-run with --apply to write.")
            return

        location_id = pick_location(client)
        for i, spec in enumerate(to_create):
            if i:
                time.sleep(ORDER_CREATE_PACING_S)
            try:
                name = create_fixture(client, spec, lines[spec["key"]], location_id, now)
            except BackdateNotHonoredError as e:
                raise SystemExit(
                    f"stopped: Shopify did not keep the backdated delivery time ({e}). "
                    "No further fixtures were created. See 'If delivery dates cannot be backdated' "
                    "in docs/live-write-testing.md."
                ) from e
            print(f"created {name} for {spec['key']}")


if __name__ == "__main__":
    main()
