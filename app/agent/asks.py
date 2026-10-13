"""What the customer's latest message asks for, read from their words: a question about a change, or a claim for a person."""

import re

ELIGIBILITY_RE = re.compile(
    r"\b(can i still|could i still|am i still able to|is it still possible to|can (it|this|the order) still be|"
    r"is it too late to|is it too late for|too late to|how long do i have|how much time do i have|how many days do i have|"
    r"until (when|what time)|by when|what('s| is) the (deadline|cutoff|last day)|do i still have time|when is the last day)\b",
    re.IGNORECASE,
)
REQUEST_RE = re.compile(
    r"\b(please|i want to|i'd like to|i would like to|go ahead|i need to|can you|could you|would you|will you)\b[^.?!\n]{0,40}"
    r"\b(cancel|return|change|update|process|start|submit)\b|^\s*(cancel|return|change|update)\b",
    re.IGNORECASE,
)
CLAIM_RE = re.compile(
    r"\b(file|make|start|open|submit|put in|lodge) (a |an )?(warranty )?(claim|warranty request)\b|"
    r"\bwarranty (claim|repair|replacement)\b|\bclaim (it |this |them )?(under|on) (the |my )?warranty\b",
    re.IGNORECASE,
)
DEFECT_RE = re.compile(
    r"\b(defective|falling apart|coming apart|came apart|fell apart|broke|broken|stopped working|ripped|torn|"
    r"cracked|leaking|leaks|peeling|arrived damaged|was damaged|is damaged|damaged in shipping)\b",
    re.IGNORECASE,
)
REMEDY_RE = re.compile(r"\b(replace|replacement|repair|fix it|fixed|warranty|exchange it|refund for)\b", re.IGNORECASE)
QUESTION_ONLY_RE = re.compile(r"^\s*(what|does|do|is|are|how|which|when)\b[^.!\n]*\?\s*$", re.IGNORECASE)


def asks_eligibility(text: str) -> bool:
    return bool(ELIGIBILITY_RE.search(text) and not REQUEST_RE.search(text))


def claims_warranty(text: str) -> bool:
    if CLAIM_RE.search(text):
        return True
    if QUESTION_ONLY_RE.match(text):
        return False
    return bool(DEFECT_RE.search(text) and REMEDY_RE.search(text))


def latest_customer_text(messages: list[dict]) -> str:
    return next((str(m["content"]) for m in reversed(messages) if m["role"] == "user"), "")
