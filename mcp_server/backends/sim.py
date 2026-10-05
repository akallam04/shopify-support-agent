"""Simulated backend over a SimDB held in memory, one fresh copy per conversation."""

import re
from typing import Any

from mcp_server.backends.base import FoundOrder
from mcp_server.clock import Clock, FrozenClock, format_instant, parse_instant
from mcp_server.search import WORD_RE, prefix_hit, words
from mcp_server.simdb import Address, Order, Product, ReturnLine, ReturnRequest, SimDB

TRAILING_DIGITS = re.compile(r"(\d+)$")


def _store_order(product: Product) -> tuple[int, str]:
    match = TRAILING_DIGITS.search(product.product_id)
    return (int(match.group(1)) if match else 0, product.product_id)


class SimStoreBackend:
    name = "sim"
    supports_writes = True
    reads_returns = True

    def __init__(self, db: SimDB, clock: Clock | None = None) -> None:
        self.db = db
        self.clock = clock or FrozenClock(parse_instant(db.meta.frozen_now))
        self.executed: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.handoffs: list[dict[str, Any]] = []

    def find_order(self, order_name: str) -> FoundOrder | None:
        order = self.db.orders.get(order_name)
        if order is None:
            return None
        customer = self.db.customers.get(order.customer_id) if order.customer_id else None
        return FoundOrder(order=order, customer=customer)

    def orders_for_email(self, email: str, limit: int) -> list[Order]:
        wanted = email.lower()
        matches = [o for o in self.db.orders.values() if (o.email or "").lower() == wanted]
        matches.sort(key=lambda o: (o.processed_at, o.name), reverse=True)
        return matches[:limit]

    def search_products(self, text: str, limit: int) -> list[Product]:
        tokens = WORD_RE.findall(text.lower())
        if not tokens:
            return []
        matches = []
        for product in self.db.products.values():
            if product.status != "ACTIVE":
                continue
            haystack = words(
                product.title,
                product.handle,
                product.product_type,
                product.vendor,
                product.description,
                *product.tags,
                *(v.sku for v in product.variants.values()),
            )
            if all(prefix_hit(t, haystack) for t in tokens):
                matches.append(product)
        matches.sort(key=_store_order)
        return matches[:limit]

    def products_by_id(self, product_ids: set[str]) -> dict[str, Product]:
        return {pid: self.db.products[pid] for pid in product_ids if pid in self.db.products}

    def cancel_order(self, order_name: str) -> None:
        order = self.db.orders[order_name]
        order.cancelled_at = format_instant(self.clock.now())
        order.cancel_reason = "CUSTOMER"
        order.fulfillment_status = "FULFILLMENT_NOT_REQUIRED"
        if order.financial_status == "PAID":
            order.financial_status = "REFUNDED"

    def update_shipping_address(self, order_name: str, address: Address) -> None:
        self.db.orders[order_name].shipping_address = address

    def request_return(self, order_name: str, lines: list[tuple[str, int]], reason: str) -> None:
        self.db.orders[order_name].returns.append(
            ReturnRequest(
                status="REQUESTED",
                reason=reason,
                line_items=[ReturnLine(line_item_id=lid, quantity=qty) for lid, qty in lines],
            )
        )

    def record_handoff(self, record: dict[str, Any]) -> None:
        self.handoffs.append(record)
