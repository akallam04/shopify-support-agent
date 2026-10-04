import pytest

from mcp_server.simdb import (
    Customer,
    Fulfillment,
    FulfilledLine,
    LineItem,
    Order,
    Product,
    SimDB,
    SnapshotMeta,
    Tracking,
    Variant,
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: hits the real development store, run with SHOPIFY_LIVE_TESTS=1"
    )


def make_db() -> SimDB:
    jacket = Product(
        product_id="p1",
        handle="stormline-rain-jacket",
        title="Stormline Rain Jacket",
        status="ACTIVE",
        product_type="Outerwear",
        tags=["rain", "jacket"],
        variants={
            "v1": Variant(variant_id="v1", title="S", price="179.99", inventory_quantity=5, available_for_sale=True),
            "v2": Variant(variant_id="v2", title="M", price="179.99", inventory_quantity=14, available_for_sale=True),
        },
    )
    bottle = Product(
        product_id="p2",
        handle="wander-insulated-bottle",
        title="Wander Insulated Bottle",
        status="ACTIVE",
        variants={
            "v3": Variant(variant_id="v3", title="Default Title", price="39.95", inventory_quantity=None, available_for_sale=True),
        },
    )
    draft = Product(
        product_id="p3",
        handle="prototype-rain-shell",
        title="Prototype Rain Shell",
        status="DRAFT",
        variants={
            "v4": Variant(variant_id="v4", title="M", price="99.00", inventory_quantity=3, available_for_sale=False),
        },
    )
    maya = Customer(customer_id="c1", first_name="Maya", last_name="Thompson", email="maya.thompson@example.com")
    jordan = Customer(customer_id="c2", first_name="Jordan", last_name="Lee", email="jordan.lee@example.com")
    o1001 = Order(
        order_id="o1",
        name="#1001",
        email="maya.thompson@example.com",
        customer_id="c1",
        created_at="2026-07-07T00:26:32Z",
        processed_at="2026-07-07T00:26:32Z",
        financial_status="PAID",
        fulfillment_status="FULFILLED",
        currency="USD",
        total="219.94",
        line_items=[
            LineItem(line_item_id="l1", product_id="p1", variant_id="v2", title="Stormline Rain Jacket", variant_title="M", quantity=1, unit_price="179.99"),
            LineItem(line_item_id="l2", product_id="p2", variant_id="v3", title="Wander Insulated Bottle", quantity=1, unit_price="39.95"),
        ],
        fulfillments=[
            Fulfillment(
                fulfillment_id="f1",
                status="SUCCESS",
                created_at="2026-07-07T00:30:00Z",
                tracking=[Tracking(number="1Z999AA10123456784", company="UPS")],
                line_items=[
                    FulfilledLine(fulfillment_line_item_id="fl1", line_item_id="l1", quantity=1),
                    FulfilledLine(fulfillment_line_item_id="fl2", line_item_id="l2", quantity=1),
                ],
            )
        ],
    )
    o1002 = Order(
        order_id="o2",
        name="#1002",
        email="maya.thompson@example.com",
        customer_id="c1",
        created_at="2026-07-07T00:26:36Z",
        processed_at="2026-07-07T00:26:36Z",
        financial_status="PAID",
        fulfillment_status="UNFULFILLED",
        currency="USD",
        total="169.98",
        line_items=[LineItem(line_item_id="l3", product_id="p1", variant_id="v1", title="Stormline Rain Jacket", variant_title="S", quantity=1, unit_price="169.98")],
    )
    o1004 = Order(
        order_id="o4",
        name="#1004",
        email=None,
        customer_id="c2",
        created_at="2026-07-07T00:26:41Z",
        processed_at="2026-07-07T00:26:41Z",
        financial_status="PAID",
        fulfillment_status="UNFULFILLED",
        currency="USD",
        total="39.95",
        line_items=[LineItem(line_item_id="l4", product_id="p2", variant_id="v3", title="Wander Insulated Bottle", quantity=1, unit_price="39.95")],
    )
    return SimDB(
        meta=SnapshotMeta(store_domain="test.myshopify.com", api_version="2026-10", frozen_now="2026-08-12T16:00:00Z"),
        products={p.product_id: p for p in (jacket, bottle, draft)},
        customers={c.customer_id: c for c in (maya, jordan)},
        orders={o.name: o for o in (o1001, o1002, o1004)},
    )


@pytest.fixture
def db() -> SimDB:
    return make_db()
