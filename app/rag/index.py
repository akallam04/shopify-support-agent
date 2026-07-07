"""Builds the vector index from the catalog snapshot and policy documents.

Run from the repo root: .venv/bin/python -m app.rag.index
"""

import json
from pathlib import Path

from app.rag.chunking import chunk_policy_markdown
from app.rag.vectorstore import ChromaVectorStore

CATALOG_PATH = Path("data/catalog/products.jsonl")
POLICIES_DIR = Path("data/policies")


def main() -> None:
    store = ChromaVectorStore()

    catalog = [json.loads(line) for line in CATALOG_PATH.read_text().splitlines() if line]
    store.reset("catalog")
    store.upsert("catalog", catalog)

    policy_files = sorted(POLICIES_DIR.glob("*.md"))
    policy_docs = []
    for md in policy_files:
        policy_docs.extend(chunk_policy_markdown(md.read_text(), md.stem))
    store.reset("policies")
    store.upsert("policies", policy_docs)

    print(f"catalog: {store.count('catalog')} documents")
    print(f"policies: {store.count('policies')} chunks from {len(policy_files)} files")


if __name__ == "__main__":
    main()
