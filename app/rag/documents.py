"""Turns raw Shopify product payloads into flat documents ready for embedding."""

from typing import Any


def _variant_line(v: dict[str, Any]) -> str:
    qty = v.get("inventoryQuantity")
    if v.get("availableForSale"):
        stock = f"in stock ({qty} available)" if isinstance(qty, int) and qty > 0 else "in stock"
    else:
        stock = "out of stock"
    # single-variant products come back as "Default Title", customers never see that name
    label = v["title"] if v["title"] != "Default Title" else "Standard"
    return f"- {label}: {v['price']}, {stock}"


def build_product_document(product: dict[str, Any]) -> dict[str, Any]:
    # one document per product, variants inlined so price and stock questions
    # retrieve the same chunk as the description
    rng = product["priceRangeV2"]
    lo, hi = rng["minVariantPrice"], rng["maxVariantPrice"]
    price_line = (
        f"Price: {lo['amount']} {lo['currencyCode']}"
        if lo["amount"] == hi["amount"]
        else f"Price range: {lo['amount']} to {hi['amount']} {lo['currencyCode']}"
    )

    lines = [f"Product: {product['title']}"]
    header_bits = [b for b in (product.get("productType"), product.get("vendor")) if b]
    if header_bits:
        lines.append(" / ".join(header_bits))
    tags = ", ".join(product.get("tags") or [])
    if tags:
        lines.append(f"Tags: {tags}")
    lines.append(price_line)
    if product.get("description"):
        lines.append(product["description"])
    variants = product["variants"]["nodes"]
    if variants:
        lines.append("Variants:")
        lines.extend(_variant_line(v) for v in variants)

    numeric_id = product["id"].rsplit("/", 1)[-1]
    return {
        "id": f"product-{numeric_id}",
        "text": "\n".join(lines),
        "metadata": {
            "product_id": numeric_id,
            "handle": product["handle"],
            "title": product["title"],
            "source": "catalog",
        },
    }
