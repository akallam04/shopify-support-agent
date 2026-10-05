"""Live backend over the Shopify Admin GraphQL API, mapping responses into the sim storage model."""

import time
from typing import Any

from mcp_server.backends.base import FoundOrder, StoreWriteError
from mcp_server.clock import SystemClock
from mcp_server.shopify_client import ShopifyClient, ShopifyGraphQLError
from mcp_server.simdb import (
    Address,
    Customer,
    FulfilledLine,
    Fulfillment,
    LineItem,
    Order,
    Product,
    ReturnLine,
    ReturnRequest,
    Tracking,
    Variant,
)

VARIANT_PAGE = 100
LINE_ITEM_PAGE = 50

ADDRESS_FIELDS = "firstName lastName address1 address2 city provinceCode zip countryCodeV2"

PRODUCT_FRAGMENT = f"""
fragment ProductFields on Product {{
  id handle title status productType vendor description tags isGiftCard
  variants(first: {VARIANT_PAGE}) {{
    nodes {{ id title sku price inventoryQuantity availableForSale }}
  }}
}}
"""

CUSTOMER_FRAGMENT = f"""
fragment CustomerFields on Customer {{
  id firstName lastName
  defaultEmailAddress {{ emailAddress }}
  defaultAddress {{ {ADDRESS_FIELDS} }}
}}
"""

ORDER_FRAGMENT = (
    f"""
fragment OrderFields on Order {{
  id name email createdAt processedAt cancelledAt cancelReason tags
  displayFinancialStatus displayFulfillmentStatus
  totalPriceSet {{ shopMoney {{ amount currencyCode }} }}
  customer {{ ...CustomerFields }}
  shippingAddress {{ {ADDRESS_FIELDS} }}
  lineItems(first: {LINE_ITEM_PAGE}) {{
    nodes {{
      id title variantTitle quantity sku
      variant {{ id }}
      product {{ id }}
      originalUnitPriceSet {{ shopMoney {{ amount currencyCode }} }}
    }}
  }}
  fulfillments {{
    id status createdAt deliveredAt displayStatus
    trackingInfo {{ number company url }}
    fulfillmentLineItems(first: {LINE_ITEM_PAGE}) {{ nodes {{ id lineItem {{ id }} quantity }} }}
  }}
}}
"""
    + CUSTOMER_FRAGMENT
)

ORDER_BY_NAME_QUERY = (
    """
query OrderByName($query: String!) {
  orders(first: 5, query: $query) { nodes { ...OrderFields } }
}
"""
    + ORDER_FRAGMENT
)

ORDERS_BY_EMAIL_QUERY = (
    """
query OrdersByEmail($query: String!, $limit: Int!) {
  orders(first: $limit, query: $query, sortKey: PROCESSED_AT, reverse: true) {
    nodes { ...OrderFields }
  }
}
"""
    + ORDER_FRAGMENT
)

PRODUCT_SEARCH_QUERY = (
    """
query ProductSearch($query: String!, $limit: Int!) {
  products(first: $limit, query: $query, sortKey: ID) { nodes { ...ProductFields } }
}
"""
    + PRODUCT_FRAGMENT
)

RETURNS_SELECTION = """
returns(first: 10) {
  nodes {
    status
    returnLineItems(first: 20) {
      nodes {
        quantity
        ... on ReturnLineItem { fulfillmentLineItem { lineItem { id } } returnReasonDefinition { handle } }
      }
    }
  }
}
"""

ORDER_WITH_RETURNS_QUERY = (
    """
query OrderWithReturns($query: String!) {
  orders(first: 5, query: $query) { nodes { ...OrderFields """
    + RETURNS_SELECTION
    + """ } }
}
"""
    + ORDER_FRAGMENT
)

SNAPSHOT_ORDERS_WITH_RETURNS_QUERY = (
    """
query SnapshotOrdersWithReturns($cursor: String) {
  orders(first: 10, after: $cursor, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { ...OrderFields """
    + RETURNS_SELECTION
    + """ }
  }
}
"""
    + ORDER_FRAGMENT
)

ORDER_CANCEL_MUTATION = """
mutation Cancel($orderId: ID!, $note: String) {
  orderCancel(
    orderId: $orderId
    reason: CUSTOMER
    restock: false
    notifyCustomer: false
    refundMethod: {originalPaymentMethodsRefund: true}
    staffNote: $note
  ) {
    job { id }
    orderCancelUserErrors { field message code }
  }
}
"""

ORDER_CANCELLED_QUERY = """
query Cancelled($id: ID!) { order(id: $id) { cancelledAt } }
"""

ORDER_UPDATE_MUTATION = """
mutation UpdateAddress($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id }
    userErrors { field message }
  }
}
"""

RETURN_REQUEST_MUTATION = """
mutation RequestReturn($input: ReturnRequestInput!) {
  returnRequest(input: $input) {
    return { id status }
    userErrors { field message code }
  }
}
"""

RETURN_REASON_DEFINITIONS_QUERY = """
{ returnReasonDefinitions(first: 50) { nodes { id handle } } }
"""

RETURN_REASON_HANDLES = {
    "size_too_small": "too-small",
    "size_too_large": "too-big",
    "unwanted": "changed-my-mind",
    "not_as_described": "item-not-as-described",
    "wrong_item": "received-the-wrong-item",
    "defective": "damaged-or-defective",
    "style": "style",
    "color": "color",
    "other": "other-reason",
}

RETURN_REASON_FROM_HANDLE = {handle: reason for reason, handle in RETURN_REASON_HANDLES.items()}

CANCEL_POLL_ATTEMPTS = 15
INACTIVE_RETURN_STATUSES = frozenset({"DECLINED", "CANCELED"})

PRODUCTS_BY_ID_QUERY = (
    """
query ProductsById($ids: [ID!]!) {
  nodes(ids: $ids) { ... on Product { ...ProductFields } }
}
"""
    + PRODUCT_FRAGMENT
)

SNAPSHOT_PRODUCTS_QUERY = (
    """
query SnapshotProducts($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { ...ProductFields }
  }
}
"""
    + PRODUCT_FRAGMENT
)

SNAPSHOT_CUSTOMERS_QUERY = (
    """
query SnapshotCustomers($cursor: String) {
  customers(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { ...CustomerFields }
  }
}
"""
    + CUSTOMER_FRAGMENT
)

SNAPSHOT_ORDERS_QUERY = (
    """
query SnapshotOrders($cursor: String) {
  orders(first: 50, after: $cursor, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { ...OrderFields }
  }
}
"""
    + ORDER_FRAGMENT
)


class TruncatedConnectionError(RuntimeError):
    pass


def _nodes(connection: dict[str, Any], page: int, what: str) -> list[dict[str, Any]]:
    nodes = connection["nodes"]
    if len(nodes) >= page:
        raise TruncatedConnectionError(f"{what} hit the page size of {page}, raise it")
    return nodes


def map_address(node: dict[str, Any] | None) -> Address | None:
    if not node:
        return None
    return Address(
        first_name=node.get("firstName"),
        last_name=node.get("lastName"),
        address1=node.get("address1"),
        address2=node.get("address2"),
        city=node.get("city"),
        province_code=node.get("provinceCode"),
        zip=node.get("zip"),
        country_code=node.get("countryCodeV2"),
    )


def map_product(node: dict[str, Any]) -> Product:
    variants = [
        Variant(
            variant_id=v["id"],
            title=v["title"],
            sku=v.get("sku") or None,
            price=v["price"],
            inventory_quantity=v.get("inventoryQuantity"),
            available_for_sale=bool(v["availableForSale"]),
        )
        for v in _nodes(node["variants"], VARIANT_PAGE, f"variants of {node['handle']}")
    ]
    return Product(
        product_id=node["id"],
        handle=node["handle"],
        title=node["title"],
        status=node["status"],
        product_type=node.get("productType") or "",
        vendor=node.get("vendor") or "",
        description=node.get("description") or "",
        tags=list(node.get("tags") or []),
        is_gift_card=bool(node.get("isGiftCard")),
        variants={v.variant_id: v for v in variants},
    )


def map_customer(node: dict[str, Any]) -> Customer:
    return Customer(
        customer_id=node["id"],
        first_name=node.get("firstName"),
        last_name=node.get("lastName"),
        email=(node.get("defaultEmailAddress") or {}).get("emailAddress"),
        default_address=map_address(node.get("defaultAddress")),
    )


def map_order(node: dict[str, Any]) -> FoundOrder:
    money = node["totalPriceSet"]["shopMoney"]
    customer = map_customer(node["customer"]) if node.get("customer") else None
    line_items = [
        LineItem(
            line_item_id=li["id"],
            product_id=(li.get("product") or {}).get("id"),
            variant_id=(li.get("variant") or {}).get("id"),
            title=li["title"],
            variant_title=li.get("variantTitle"),
            sku=li.get("sku") or None,
            quantity=li["quantity"],
            unit_price=li["originalUnitPriceSet"]["shopMoney"]["amount"],
        )
        for li in _nodes(node["lineItems"], LINE_ITEM_PAGE, f"line items of {node['name']}")
    ]
    fulfillments = [
        Fulfillment(
            fulfillment_id=f["id"],
            status=f["status"],
            display_status=f.get("displayStatus"),
            created_at=f["createdAt"],
            delivered_at=f.get("deliveredAt"),
            tracking=[
                Tracking(number=t.get("number"), company=t.get("company"), url=t.get("url"))
                for t in f.get("trackingInfo") or []
            ],
            line_items=[
                FulfilledLine(
                    fulfillment_line_item_id=fl["id"],
                    line_item_id=fl["lineItem"]["id"],
                    quantity=fl["quantity"],
                )
                for fl in _nodes(
                    f["fulfillmentLineItems"], LINE_ITEM_PAGE, f"fulfilled lines of {node['name']}"
                )
            ],
        )
        for f in node.get("fulfillments") or []
    ]
    order = Order(
        order_id=node["id"],
        name=node["name"],
        email=node.get("email"),
        customer_id=customer.customer_id if customer else None,
        created_at=node["createdAt"],
        processed_at=node["processedAt"],
        cancelled_at=node.get("cancelledAt"),
        cancel_reason=node.get("cancelReason"),
        financial_status=node["displayFinancialStatus"],
        fulfillment_status=node["displayFulfillmentStatus"],
        currency=money["currencyCode"],
        total=money["amount"],
        tags=list(node.get("tags") or []),
        shipping_address=map_address(node.get("shippingAddress")),
        line_items=line_items,
        fulfillments=fulfillments,
    )
    return FoundOrder(order=order, customer=customer)


def map_returns(node: dict[str, Any]) -> list[ReturnRequest]:
    requests = []
    for r in node.get("returns", {}).get("nodes", []):
        if r["status"] in INACTIVE_RETURN_STATUSES:
            continue
        items = [li for li in r["returnLineItems"]["nodes"] if li.get("fulfillmentLineItem")]
        lines = [ReturnLine(line_item_id=li["fulfillmentLineItem"]["lineItem"]["id"], quantity=li["quantity"]) for li in items]
        handles = [(li.get("returnReasonDefinition") or {}).get("handle") for li in items]
        reason = RETURN_REASON_FROM_HANDLE.get(handles[0] if handles else "", "other")
        requests.append(ReturnRequest(status=r["status"], reason=reason, line_items=lines))
    return requests


def _check_errors(payload: dict[str, Any], key: str, errors_field: str = "userErrors") -> dict[str, Any]:
    errors = payload[key].get(errors_field) or []
    if errors:
        raise StoreWriteError("; ".join(e.get("message", "") for e in errors))
    return payload[key]


class ShopifyAdminBackend:
    name = "shopify"

    def __init__(self, client: ShopifyClient, write_client: ShopifyClient | None = None) -> None:
        self._client = client
        self._write = write_client
        self.supports_writes = write_client is not None
        self.reads_returns = write_client is not None
        self.clock = SystemClock()
        self.executed: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.handoffs: list[dict[str, Any]] = []
        self._reason_ids: dict[str, str] = {}

    def find_order(self, order_name: str) -> FoundOrder | None:
        if self._write is not None:
            data = self._write.graphql(ORDER_WITH_RETURNS_QUERY, {"query": f"name:{order_name}"})
        else:
            data = self._client.graphql(ORDER_BY_NAME_QUERY, {"query": f"name:{order_name}"})
        for node in data["orders"]["nodes"]:
            if node["name"] == order_name:
                found = map_order(node)
                if "returns" in node:
                    found.order.returns = map_returns(node)
                return found
        return None

    def orders_for_email(self, email: str, limit: int) -> list[Order]:
        data = self._client.graphql(
            ORDERS_BY_EMAIL_QUERY, {"query": f"email:{email}", "limit": limit}
        )
        return [map_order(n).order for n in data["orders"]["nodes"]]

    def search_products(self, text: str, limit: int) -> list[Product]:
        data = self._client.graphql(
            PRODUCT_SEARCH_QUERY, {"query": f"status:active {text}", "limit": limit}
        )
        return [map_product(n) for n in data["products"]["nodes"]]

    def products_by_id(self, product_ids: set[str]) -> dict[str, Product]:
        if not product_ids:
            return {}
        data = self._client.graphql(PRODUCTS_BY_ID_QUERY, {"ids": sorted(product_ids)})
        return {p.product_id: p for p in (map_product(n) for n in data["nodes"] if n)}

    def _writer(self) -> ShopifyClient:
        if self._write is None:
            raise StoreWriteError("this backend was built without the write-test token")
        return self._write

    def _order_id(self, order_name: str) -> FoundOrder:
        found = self.find_order(order_name)
        if found is None:
            raise StoreWriteError(f"{order_name} disappeared before the change could be made")
        return found

    def cancel_order(self, order_name: str) -> None:
        writer = self._writer()
        order_id = self._order_id(order_name).order.order_id
        try:
            payload = writer.graphql(
                ORDER_CANCEL_MUTATION,
                {"orderId": order_id, "note": "Cancelled by the support agent at the customer's request."},
            )
            _check_errors(payload, "orderCancel", "orderCancelUserErrors")
            for _ in range(CANCEL_POLL_ATTEMPTS):
                if writer.graphql(ORDER_CANCELLED_QUERY, {"id": order_id})["order"]["cancelledAt"]:
                    return
                time.sleep(1.0)
        except ShopifyGraphQLError as e:
            raise StoreWriteError(str(e)) from e
        raise StoreWriteError(f"{order_name} cancellation is still processing")

    def update_shipping_address(self, order_name: str, address: Address) -> None:
        writer = self._writer()
        order_id = self._order_id(order_name).order.order_id
        shipping = {
            "firstName": address.first_name,
            "lastName": address.last_name,
            "address1": address.address1,
            "address2": address.address2,
            "city": address.city,
            "provinceCode": address.province_code,
            "zip": address.zip,
            "countryCode": address.country_code,
        }
        try:
            payload = writer.graphql(
                ORDER_UPDATE_MUTATION,
                {"input": {"id": order_id, "shippingAddress": {k: v for k, v in shipping.items() if v}}},
            )
        except ShopifyGraphQLError as e:
            raise StoreWriteError(str(e)) from e
        _check_errors(payload, "orderUpdate")

    def request_return(self, order_name: str, lines: list[tuple[str, int]], reason: str) -> None:
        writer = self._writer()
        order = self._order_id(order_name).order
        by_line = {fl.line_item_id: fl.fulfillment_line_item_id for f in order.fulfillments for fl in f.line_items}
        missing = [lid for lid, _ in lines if lid not in by_line]
        if missing:
            raise StoreWriteError(f"{order_name} has no fulfilled line for {missing}")
        reason_id = self._reason_definition_id(reason)
        items = [
            {
                "fulfillmentLineItemId": by_line[lid],
                "quantity": qty,
                "returnReasonDefinitionId": reason_id,
                "customerNote": f"Reason: {reason}",
            }
            for lid, qty in lines
        ]
        try:
            payload = writer.graphql(RETURN_REQUEST_MUTATION, {"input": {"orderId": order.order_id, "returnLineItems": items}})
        except ShopifyGraphQLError as e:
            raise StoreWriteError(str(e)) from e
        _check_errors(payload, "returnRequest")

    def _reason_definition_id(self, reason: str) -> str:
        if not self._reason_ids:
            try:
                nodes = self._writer().graphql(RETURN_REASON_DEFINITIONS_QUERY)["returnReasonDefinitions"]["nodes"]
            except ShopifyGraphQLError as e:
                raise StoreWriteError(str(e)) from e
            self._reason_ids = {n["handle"]: n["id"] for n in nodes}
        handle = RETURN_REASON_HANDLES.get(reason, "other-reason")
        if handle not in self._reason_ids:
            raise StoreWriteError(f"the store has no return reason definition {handle}")
        return self._reason_ids[handle]

    def record_handoff(self, record: dict[str, Any]) -> None:
        self.handoffs.append(record)
