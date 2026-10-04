"""Word matching shared by the simulated search and the tool-level ranking."""

import re

WORD_RE = re.compile(r"[a-z0-9]+")


def words(*fields: str | None) -> list[str]:
    return [w for f in fields if f for w in WORD_RE.findall(f.lower())]


def prefix_hit(token: str, haystack: list[str]) -> bool:
    return any(w.startswith(token) for w in haystack)


def title_score(tokens: list[str], title: str) -> int:
    title_words = words(title)
    return sum(prefix_hit(t, title_words) for t in tokens)
