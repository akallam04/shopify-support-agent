"""Simulated backend over a SimDB held in memory, one fresh copy per conversation."""

import re

from mcp_server.backends.base import FoundOrder
from mcp_server.clock import Clock, FrozenClock, parse_instant
from mcp_server.search import WORD_RE, prefix_hit, words
from mcp_server.simdb import Order, Product, SimDB

TRAILING_DIGITS = re.compile(r"(\d+)$")


def _store_order(product: Product) -> tuple[int, str]:
    match = TRAILING_DIGITS.search(product.product_id)
    return (int(match.group(1)) if match else 0, product.product_id)


class SimStoreBackend:
    name = "sim"

    def __init__(self, db: SimDB, clock: Clock | None = None) -> None:
        self.db = db
        self.clock = clock or FrozenClock(parse_instant(db.meta.frozen_now))

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
