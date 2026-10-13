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
    (re.compile(
        r"\b(wrong item|not what i ordered|(received|got|sent) the wrong|wrong (size|color|colour|one) (arrived|came|was sent)|"
        r"(different|another) (size|color|colour) (arrived|came)|instead of the \w+ (i|we) ordered|"
        r"ordered (a|an|the|size) \w+,? but (a|an|the|size)? ?\w+ (arrived|came)|ordered (a|an|the|size) \w+,? but (got|received) (a|an|the|size)? ?\w+)\b", re.I), ("wrong_item",)),
    (re.compile(r"\btoo (big|large|long|loose|baggy|wide)\b|\bruns? (big|large)\b", re.I), ("size_too_large",)),
    (re.compile(r"\btoo (small|tight|short|snug|narrow)\b|\bruns? small\b", re.I), ("size_too_small",)),
    (re.compile(r"\b(does ?n[o']?t|did ?n[o']?t|does not|did not|won't|will not) fit\b|\bwrong size\b", re.I), ("other", "size_too_large", "size_too_small", "wrong_item")),
    (re.compile(r"\b(not as (described|pictured|shown|advertised)|looks different)\b", re.I), ("not_as_described",)),
    (re.compile(r"\b(defective|faulty|broken|stopped working|does ?n[o']?t work|does not work|ripped|torn)\b", re.I), ("defective",)),
    (re.compile(r"\b(no longer (need|want)|do ?n[o']?t (need|want)|do not (need|want)|changed my mind|not needed)\b", re.I), ("unwanted",)),
    (re.compile(r"\b(do ?n[o']?t|do not) like the (colou?r)\b", re.I), ("color",)),
    (re.compile(r"\b(do ?n[o']?t|do not) like the style\b", re.I), ("style",)),
)


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def stated_reason(customer_texts: list[str]) -> StatedReason | None:
    specific = [(p, c) for p, c in REASON_PHRASES if len(c) == 1]
    ambiguous = [(p, c) for p, c in REASON_PHRASES if len(c) > 1]
    for tier in (specific, ambiguous):
        for text in reversed(customer_texts):
            for pattern, codes in tier:
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
    (re.compile(
        r"\b(cut|removed|took|ripped|pulled|tore|lost) (off )?(the |its |their |all )?(original )?tags?\b|"
        r"\b(no|without|missing) (the |its |their |any )?(original )?tags?\b|"
        r"\btags? (are|is|were|was|have been|has been|came|fell) (off|gone|removed|missing|cut)\b", re.I), "tags_removed"),
    (re.compile(r"\b(opened|used|applied|tried) (the |some of the |my |a bit of the )?(\w+ )?(wax|consumables?)\b|\bwax (is|was|has been) opened\b", re.I), "opened"),
    (re.compile(r"(?<!un-)(?<!pre-)\b(washed|laundered)\b(?! out\b)", re.I), "washed"),
    (re.compile(
        r"(?<!un-)\b(worn|wore|wearing)\b"
        r"(?! (a|an|size|sizes|xs|s|m|l|xl|xxl|small|medium|large|\d+)\b)(?! (this|that|your|the same) (brand|size|style|model|cut)\b)", re.I), "worn"),
    (re.compile(
        r"\bused (it|them|this|these|those)\b|\b(been|was|were|lightly|barely|gently|hardly) used\b|"
        r"\bused (it |them )?(outdoors|outside|once|twice|a few times|several times)\b", re.I), "used"),
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_SPLIT_RE = re.compile(
    r"[,;:()]|\b(but|though|although|however|so|then|until|after|because|since|while|when|once)\b|\band (?=(i|we|it|they|then)\b)",
    re.I,
)
NEGATION_RE = re.compile(
    r"\b(not|never|no|nor|neither|cannot|(ain|aren|can|couldn|didn|doesn|don|hadn|hasn|haven|isn|wasn|weren|won|wouldn)'?t)\b",
    re.I,
)
HYPOTHETICAL_RE = re.compile(r"\b(if|whether|in case|suppose|supposing|hypothetically|what about)\b", re.I)
FIRST_PERSON_RE = re.compile(r"\b(i|i've|i'd|we|we've|my|our)\b", re.I)
TRY_ON_RE = re.compile(r"\b(try|tried|trying) (it |them )?on\b", re.I)


@dataclass(frozen=True)
class ItemCondition:
    condition: str
    stated: str
    quote: str


def _clauses(text: str) -> list[tuple[str, bool]]:
    found = []
    for sentence in SENTENCE_SPLIT_RE.split(text.replace("\u2019", "'")):
        question = sentence.rstrip().endswith("?")
        found += [(c.strip(), question) for c in CLAUSE_SPLIT_RE.split(sentence) if c and c.strip() and not CLAUSE_SPLIT_RE.fullmatch(c)]
    return found


def _stated(clause: str, question: bool, at: int) -> str:
    if NEGATION_RE.search(clause[:at]):
        return "no"
    if HYPOTHETICAL_RE.search(clause) or TRY_ON_RE.search(clause) or (question and not FIRST_PERSON_RE.search(clause)):
        return "unclear"
    return "yes"


def condition_mentions(customer_texts: list[str]) -> list[ItemCondition]:
    latest: dict[str, ItemCondition] = {}
    for text in customer_texts:
        seen: dict[str, ItemCondition] = {}
        for clause, question in _clauses(text):
            for pattern, condition in CONDITION_PHRASES:
                for match in pattern.finditer(clause):
                    mention = ItemCondition(condition, _stated(clause, question, match.start()), clause)
                    if condition not in seen or mention.stated == "yes":
                        seen[condition] = mention
        for condition, mention in seen.items():
            if mention.stated != "unclear" or condition not in latest:
                latest[condition] = mention
    return list(latest.values())


def item_condition(customer_texts: list[str]) -> ItemCondition | None:
    order = [c for _, c in CONDITION_PHRASES]
    stated = sorted((m for m in condition_mentions(customer_texts) if m.stated == "yes"), key=lambda m: order.index(m.condition))
    return stated[0] if stated else None


def stated_condition(customer_texts: list[str]) -> str | None:
    found = item_condition(customer_texts)
    return found.condition if found else None
