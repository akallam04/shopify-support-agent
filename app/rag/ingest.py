"""Pulls the live catalog into data/catalog/products.jsonl, the input for indexing.

Run from the repo root: .venv/bin/python -m app.rag.ingest
"""

import json
from pathlib import Path

from app.config import get_settings
from app.rag.documents import build_product_document
from mcp_server.shopify_client import ShopifyClient

OUT_PATH = Path("data/catalog/products.jsonl")


def main() -> None:
    s = get_settings()
    with ShopifyClient(
        s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version
    ) as client:
        # only ACTIVE products, drafts and archived items must never reach customers;
        # gift cards are excluded too, they carry no real inventory and the policy
        # FAQ owns the gift card story
        products = [
            p
            for p in client.iterate_products()
            if p["status"] == "ACTIVE" and not p.get("isGiftCard")
        ]

    docs = [build_product_document(p) for p in products]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print(f"wrote {len(docs)} product documents to {OUT_PATH}")
    if docs:
        lengths = sorted(len(d["text"]) for d in docs)
        print(f"text length: min {lengths[0]}, median {lengths[len(lengths) // 2]}, max {lengths[-1]}")
        print("--- sample document ---")
        print(docs[0]["text"])


if __name__ == "__main__":
    main()
