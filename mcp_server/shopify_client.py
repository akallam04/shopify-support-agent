"""Thin Shopify Admin GraphQL client. The only module that talks to the store."""

import time
from collections.abc import Iterator
from typing import Any

import httpx


class ShopifyGraphQLError(RuntimeError):
    """Raised when the Admin API returns errors instead of data."""


PRODUCTS_QUERY = """
query Products($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      handle
      status
      productType
      vendor
      tags
      description
      totalInventory
      priceRangeV2 {
        minVariantPrice { amount currencyCode }
        maxVariantPrice { amount currencyCode }
      }
      variants(first: 100) {
        nodes { id title sku price inventoryQuantity availableForSale }
      }
    }
  }
}
"""

ORDERS_QUERY = """
query Orders($cursor: String) {
  orders(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      email
      displayFinancialStatus
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      customer { displayName email }
    }
  }
}
"""

CUSTOMERS_QUERY = """
query Customers($cursor: String) {
  customers(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id displayName email numberOfOrders }
  }
}
"""

SHOP_QUERY = """
{
  shop { name myshopifyDomain currencyCode plan { displayName } }
}
"""


class ShopifyClient:
    def __init__(self, store_domain: str, token: str, api_version: str) -> None:
        self._url = f"https://{store_domain}/admin/api/{api_version}/graphql.json"
        self._http = httpx.Client(
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ShopifyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        # retry only on Shopify's query cost throttle, everything else fails loudly
        for attempt in range(4):
            resp = self._http.post(self._url, json={"query": query, "variables": variables or {}})
            if resp.status_code in (401, 403):
                raise ShopifyGraphQLError(
                    f"auth failed ({resp.status_code}), check SHOPIFY_ADMIN_TOKEN and app scopes"
                )
            resp.raise_for_status()
            payload = resp.json()
            errors = payload.get("errors")
            if not errors:
                return payload["data"]
            throttled = any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors)
            if throttled and attempt < 3:
                time.sleep(1.0 + attempt)
                continue
            raise ShopifyGraphQLError(str(errors))
        raise ShopifyGraphQLError("throttle retries exhausted")

    def _paginate(self, query: str, root: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            data = self.graphql(query, {"cursor": cursor})
            page = data[root]
            yield from page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            cursor = page["pageInfo"]["endCursor"]

    def shop_info(self) -> dict[str, Any]:
        return self.graphql(SHOP_QUERY)["shop"]

    def iterate_products(self) -> Iterator[dict[str, Any]]:
        return self._paginate(PRODUCTS_QUERY, "products")

    def iterate_orders(self) -> Iterator[dict[str, Any]]:
        return self._paginate(ORDERS_QUERY, "orders")

    def iterate_customers(self) -> Iterator[dict[str, Any]]:
        return self._paginate(CUSTOMERS_QUERY, "customers")
