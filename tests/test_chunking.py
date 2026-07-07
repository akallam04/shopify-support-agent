"""Unit tests for the policy markdown chunker."""

from app.rag.chunking import chunk_policy_markdown

SAMPLE = """# Test Policy

An intro line about this policy.

## First Section

Body line one.
Body line two.

## Second Section

More text here.

## Empty Section
"""


def test_chunks_by_heading() -> None:
    chunks = chunk_policy_markdown(SAMPLE, "test")
    ids = [c["id"] for c in chunks]
    assert ids == [
        "policy-test-overview",
        "policy-test-first-section",
        "policy-test-second-section",
    ]


def test_chunk_carries_title_and_metadata() -> None:
    chunks = chunk_policy_markdown(SAMPLE, "test")
    first = chunks[1]
    assert first["text"].startswith("Test Policy, First Section")
    assert "Body line two." in first["text"]
    assert first["metadata"] == {
        "source": "policy",
        "policy": "test",
        "section": "First Section",
        "title": "Test Policy",
    }


def test_empty_sections_are_dropped() -> None:
    chunks = chunk_policy_markdown(SAMPLE, "test")
    assert all("empty" not in c["id"] for c in chunks)
