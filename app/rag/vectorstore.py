"""Vector store behind a small interface so the backend can swap without touching callers."""

from typing import Any, Protocol, TypedDict

import chromadb


class SearchResult(TypedDict):
    id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class VectorStore(Protocol):
    def reset(self, collection: str) -> None: ...

    def upsert(self, collection: str, docs: list[dict[str, Any]]) -> None: ...

    def query(self, collection: str, text: str, k: int = 4) -> list[SearchResult]: ...

    def count(self, collection: str) -> int: ...


class ChromaVectorStore:
    """Chroma with its default local embedding model (all-MiniLM-L6-v2 over ONNX)."""

    def __init__(self, path: str = "chroma_db") -> None:
        self._client = chromadb.PersistentClient(path=path)

    def _collection(self, name: str) -> Any:
        return self._client.get_or_create_collection(name)

    def reset(self, collection: str) -> None:
        # index rebuilds start clean so deleted source docs cannot linger
        if any(c.name == collection for c in self._client.list_collections()):
            self._client.delete_collection(collection)

    def upsert(self, collection: str, docs: list[dict[str, Any]]) -> None:
        self._collection(collection).upsert(
            ids=[d["id"] for d in docs],
            documents=[d["text"] for d in docs],
            metadatas=[d["metadata"] for d in docs],
        )

    def query(self, collection: str, text: str, k: int = 4) -> list[SearchResult]:
        res = self._collection(collection).query(query_texts=[text], n_results=k)
        return [
            {
                "id": res["ids"][0][i],
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i],
            }
            for i in range(len(res["ids"][0]))
        ]

    def count(self, collection: str) -> int:
        return self._collection(collection).count()
