"""Unit tests for the product document builder."""

from app.rag.documents import build_product_document

SAMPLE = {
    "id": "gid://shopify/Product/1234567890",
    "title": "Trail Runner Pack",
    "handle": "trail-runner-pack",
    "status": "ACTIVE",
    "productType": "Backpack",
    "vendor": "Aurora Outfitters",
    "tags": ["outdoor", "hiking"],
    "description": "A light pack for day hikes.",
    "totalInventory": 7,
    "priceRangeV2": {
        "minVariantPrice": {"amount": "49.99", "currencyCode": "USD"},
        "maxVariantPrice": {"amount": "59.99", "currencyCode": "USD"},
    },
    "variants": {
        "nodes": [
            {
                "id": "gid://shopify/ProductVariant/1",
                "title": "Blue",
                "sku": "TRP-BLUE",
                "price": "49.99",
                "inventoryQuantity": 7,
                "availableForSale": True,
            },
            {
                "id": "gid://shopify/ProductVariant/2",
                "title": "Red",
                "sku": "TRP-RED",
                "price": "59.99",
                "inventoryQuantity": 0,
                "availableForSale": False,
            },
        ]
    },
}


def test_build_product_document() -> None:
    doc = build_product_document(SAMPLE)
    assert doc["id"] == "product-1234567890"
    assert doc["metadata"]["product_id"] == "1234567890"
    assert doc["metadata"]["handle"] == "trail-runner-pack"
    text = doc["text"]
    assert "Product: Trail Runner Pack" in text
    assert "Price range: 49.99 to 59.99 USD" in text
    assert "- Blue: 49.99, in stock (7 available)" in text
    assert "- Red: 59.99, out of stock" in text
    assert "outdoor, hiking" in text


def test_single_variant_gets_standard_label() -> None:
    product = dict(SAMPLE)
    product["variants"] = {
        "nodes": [
            {
                "id": "gid://shopify/ProductVariant/3",
                "title": "Default Title",
                "sku": "TRP",
                "price": "49.99",
                "inventoryQuantity": None,
                "availableForSale": True,
            }
        ]
    }
    text = build_product_document(product)["text"]
    assert "- Standard: 49.99, in stock" in text
    assert "Default Title" not in text
