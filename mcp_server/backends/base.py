"""The contract every store backend implements. Authorization and formatting live above it."""

from dataclasses import dataclass
from typing import Protocol

from mcp_server.simdb import Customer, Order, Product


@dataclass(frozen=True)
class FoundOrder:
    order: Order
    customer: Customer | None


class StoreBackend(Protocol):
    name: str

    def find_order(self, order_name: str) -> FoundOrder | None: ...

    def orders_for_email(self, email: str, limit: int) -> list[Order]: ...

    def search_products(self, text: str, limit: int) -> list[Product]: ...
