"""Markdown chunking for policy documents: one chunk per h2 section."""

import re
from typing import Any


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def chunk_policy_markdown(text: str, name: str) -> list[dict[str, Any]]:
    # one chunk per ## section so a chunk never straddles two topics,
    # the doc title rides along in every chunk to keep policy context
    title = name
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None

    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
        elif line.startswith("## "):
            if current:
                sections.append(current)
            current = (line[3:].strip(), [])
        elif current:
            current[1].append(line)
        elif line.strip():
            # intro text before the first heading becomes its own chunk
            if not sections and current is None:
                current = ("Overview", [line])
    if current:
        sections.append(current)

    chunks = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        chunks.append(
            {
                "id": f"policy-{name}-{_slugify(heading)}",
                "text": f"{title}, {heading}\n{body}",
                "metadata": {
                    "source": "policy",
                    "policy": name,
                    "section": heading,
                    "title": title,
                },
            }
        )
    return chunks
