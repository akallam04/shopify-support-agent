"""Return reasons from the customer's own words, so a reason is never guessed and a short one is never refused."""

import re
from dataclasses import dataclass

WORD_RE = re.compile(r"[a-z0-9']+")
FILLER = frozenset({"a", "an", "the", "it", "its", "it's", "is", "was", "are", "were", "be", "i", "my", "me", "this", "that", "they", "them", "just", "really", "so", "for", "on", "of"})


@dataclass(frozen=True)
class StatedReason:
    phrase: str
    codes: tuple[str, ...]


REASON_PHRASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\btoo (big|large|long|loose|baggy|wide)\b|\bruns? (big|large)\b", re.I), ("size_too_large",)),
    (re.compile(r"\btoo (small|tight|short|snug|narrow)\b|\bruns? small\b", re.I), ("size_too_small",)),
    (re.compile(r"\b(does ?n[o']?t|did ?n[o']?t|does not|did not|won't|will not) fit\b|\bwrong size\b", re.I), ("other", "size_too_large", "size_too_small")),
    (re.compile(r"\b(not as (described|pictured|shown|advertised)|looks different)\b", re.I), ("not_as_described",)),
    (re.compile(r"\b(wrong item|not what i ordered|received the wrong|sent the wrong|different (size|color|colour) (arrived|came))\b", re.I), ("wrong_item",)),
    (re.compile(r"\b(defective|faulty|broken|stopped working|does ?n[o']?t work|does not work|ripped|torn)\b", re.I), ("defective",)),
    (re.compile(r"\b(no longer (need|want)|do ?n[o']?t (need|want)|do not (need|want)|changed my mind|not needed)\b", re.I), ("unwanted",)),
    (re.compile(r"\b(do ?n[o']?t|do not) like the (colou?r)\b", re.I), ("color",)),
    (re.compile(r"\b(do ?n[o']?t|do not) like the style\b", re.I), ("style",)),
)


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def stated_reason(customer_texts: list[str]) -> StatedReason | None:
    for text in reversed(customer_texts):
        for pattern, codes in REASON_PHRASES:
            match = pattern.search(text)
            if match:
                return StatedReason(match.group(0).lower(), codes)
    return None


def grounded(quote: str, customer_texts: list[str]) -> bool:
    wanted = [w for w in _words(quote) if w not in FILLER]
    if not wanted or len(" ".join(wanted)) < 3:
        return False
    return any(set(wanted) <= set(_words(text)) for text in customer_texts)
