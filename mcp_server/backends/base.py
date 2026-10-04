"""The contract every store backend implements. Authorization and formatting live above it."""

from dataclasses import dataclass
from typing import Any, Protocol

from mcp_server.clock import Clock
from mcp_server.simdb import Address, Customer, Order, Product


class StoreWriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class FoundOrder:
    order: Order
    customer: Customer | None


class StoreBackend(Protocol):
    name: str
    clock: Clock
    supports_writes: bool

    def find_order(self, order_name: str) -> FoundOrder | None: ...

    def orders_for_email(self, email: str, limit: int) -> list[Order]: ...

    def search_products(self, text: str, limit: int) -> list[Product]: ...

    def products_by_id(self, product_ids: set[str]) -> dict[str, Product]: ...


class WritableStore(StoreBackend, Protocol):
    executed: dict[str, dict[str, Any]]
    audit: list[dict[str, Any]]
    handoffs: list[dict[str, Any]]

    def cancel_order(self, order_name: str) -> None: ...

    def update_shipping_address(self, order_name: str, address: Address) -> None: ...

    def request_return(self, order_name: str, lines: list[tuple[str, int]], reason: str) -> None: ...

    def record_handoff(self, record: dict[str, Any]) -> None: ...
