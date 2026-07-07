"""Round-trip test for the Chroma-backed vector store."""

from pathlib import Path

from app.rag.vectorstore import ChromaVectorStore


def test_upsert_query_reset(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=str(tmp_path / "chroma"))
    store.upsert(
        "things",
        [
            {"id": "a", "text": "a waterproof jacket for rain", "metadata": {"kind": "jacket"}},
            {"id": "b", "text": "a titanium pot for camp cooking", "metadata": {"kind": "cookware"}},
        ],
    )
    assert store.count("things") == 2

    top = store.query("things", "what should I wear in the rain", k=1)[0]
    assert top["id"] == "a"
    assert top["metadata"]["kind"] == "jacket"
    assert top["distance"] >= 0

    store.reset("things")
    assert store.count("things") == 0
