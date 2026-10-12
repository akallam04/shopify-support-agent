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


def customer_texts(state: dict) -> list[str]:
    texts = [str(m["content"]) for m in state["messages"] if m["role"] == "user"]
    return texts + ([state["context_digest"]] if state.get("context_digest") else [])


def grounded(quote: str, customer_texts: list[str]) -> bool:
    wanted = [w for w in _words(quote) if w not in FILLER]
    if not wanted or len(" ".join(wanted)) < 3:
        return False
    return any(set(wanted) <= set(_words(text)) for text in customer_texts)


CONDITION_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(cut|removed|took|ripped|pulled) (off )?(the |its |their )?(original )?tags?\b|\b(no|without|missing) (the |its |their )?(original )?tags?\b|\btags? (are|is|were|was) (off|gone|removed|missing)\b", re.I), "tags_removed"),
    (re.compile(r"\b(opened|used|applied) (the |some of the |my )?(\w+ )?(wax|consumables?)\b", re.I), "opened"),
    (re.compile(r"\b(washed|laundered)\b", re.I), "washed"),
    (re.compile(r"\b(worn|wore)\b", re.I), "worn"),
    (re.compile(r"\b(used) (it|them|this|these|the \w+)\b|\bused (it )?(outdoors|outside)\b", re.I), "used"),
)
NEGATION_RE = re.compile(r"\b(not|never|haven't|hasn't|hadn't|didn't|don't|without|no)\W+(\w+\W+)?$", re.I)


def stated_condition(customer_texts: list[str]) -> str | None:
    for text in reversed(customer_texts):
        for pattern, condition in CONDITION_PHRASES:
            for match in pattern.finditer(text):
                if condition == "tags_removed" or not NEGATION_RE.search(text[: match.start()]):
                    return condition
    return None
