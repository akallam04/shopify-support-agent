"""Retrieval smoke checks against the built index. Fast signal, not the eval harness.

Run from the repo root: .venv/bin/python -m scripts.check_retrieval
"""

from app.rag.vectorstore import ChromaVectorStore

# (collection, query, expected handle or doc id, must appear in top 3)
CASES = [
    ("catalog", "waterproof rain jacket for hiking", "stormline-rain-jacket"),
    ("catalog", "is the complete snowboard in stock", "the-complete-snowboard"),
    ("catalog", "warm winter down jacket", "glacier-point-down-parka"),
    ("catalog", "bottle that keeps drinks cold all day", "wander-insulated-bottle-32oz"),
    ("policies", "how long do I have to return an item", "policy-returns-return-window"),
    ("policies", "how much does expedited shipping cost", "policy-shipping-domestic-shipping-options"),
    ("policies", "do gift cards expire", "policy-faq-gift-cards"),
    ("policies", "do you ship to canada", "policy-shipping-international-shipping"),
]


def main() -> None:
    store = ChromaVectorStore()
    passed = 0
    for collection, query, expected in CASES:
        results = store.query(collection, query, k=3)
        keys = [r["metadata"].get("handle") or r["id"] for r in results]
        rank = next((i for i, key in enumerate(keys) if expected in key), None)
        if rank is None:
            print(f"FAIL          [{collection}] {query!r}")
            print(f"              expected {expected}, got {keys}")
        else:
            passed += 1
            print(f"PASS (rank {rank + 1}) [{collection}] {query!r} -> {keys[rank]}")
    print(f"\n{passed}/{len(CASES)} checks passed")


if __name__ == "__main__":
    main()
