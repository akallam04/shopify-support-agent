"""Live backend over the Shopify Admin GraphQL API, mapping responses into the sim storage model."""

from typing import Any

from mcp_server.backends.base import FoundOrder
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import (
    Address,
    Customer,
    FulfilledLine,
    Fulfillment,
    LineItem,
    Order,
    Product,
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


class ShopifyAdminBackend:
    name = "shopify"

    def __init__(self, client: ShopifyClient) -> None:
        self._client = client

    def find_order(self, order_name: str) -> FoundOrder | None:
        data = self._client.graphql(ORDER_BY_NAME_QUERY, {"query": f"name:{order_name}"})
        for node in data["orders"]["nodes"]:
            if node["name"] == order_name:
                return map_order(node)
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
