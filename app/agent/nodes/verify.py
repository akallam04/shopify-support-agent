"""Deterministic checks on every model-written reply: citations, numbers, identifiers, and the lookup rule."""

import re
from dataclasses import dataclass
from typing import Any

from app.agent.claims import Claim, attributed, claims_in, facts_in, sentences, supported
from app.agent.evidence import Evidence, build_evidence
from app.agent.prompts import (
    CITATION_FEEDBACK,
    IDENTIFIER_FEEDBACK,
    LOOKUP_FEEDBACK,
    NUMBERS_FEEDBACK,
    ORDER_NUMBER_HELP,
    SAFE_FALLBACK_RESPONSE,
    UNCITED_FEEDBACK,
)
from app.agent.state import AgentState

BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,80})\]")
ORDER_NUMBER_RE = re.compile(r"#\d{3,}")
# at least one digit, so status words like FULFILLED never match
TRACKING_LIKE_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")

UNCHECKED_INTENTS = {"injection", "out_of_scope", "handoff"}
RETRIEVE_INTENTS = {"product", "policy"}

RETRACTION_RE = re.compile(
    r"\b(correct(ing)? my (last|earlier|previous) (message|reply|answer)|"
    r"(never|did not|didn't) (actually )?(look|looked) (that |it |this )?up|"
    r"(should not|shouldn't) have (described|stated|said|mentioned|given)|"
    r"(do not|don't) (actually )?have (order\b|its details|any record of (order|that order|this order|it)\b)|"
    r"(do not|don't) have (a|any) source|(can't|cannot|could not|couldn't) (verify|confirm) (those|these|that|the) (figures|details|numbers|amounts|terms)|"
    r"(check|verify|confirm) (those|these) (details|figures|numbers|amounts|terms) with)",
    re.IGNORECASE,
)
RETRACTION_FEEDBACK = "do not retract or correct the confirmation and receipt messages: the store system built them from store records"
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
SYSTEM_START_RE = re.compile(
    r"^(Just to confirm, I will|Sorry, I want to be sure before I change anything|"
    r"I cannot make this change depend on a condition|That is already done\.|Order #\d+ is cancelled\.|"
    r"The shipping address on order #\d+ is now|A return is requested on order #\d+)"
)
RECONFIRM_MARK = "Your change is still waiting: I will"
DISTINCT_NUMBER_RE = re.compile(r"(?<![#\d.])(\d[\d,]*\.\d+|\d{3,})")
NEGATED_BEFORE_RE = re.compile(r"\b(not|rather than|instead of|isn't|wasn't|is not|was not)\s+(about\s+)?$", re.IGNORECASE)

LOOKUP_VERB = r"(look(ing)? (it |them |this |that |your orders? |the orders? |orders )?up|find|locate|pull up|search for|track down|retrieve|list|show you)"
OTHER_ID = r"((full |first and last )?name|phone( number)?|(shipping |home |street |billing )?address|account)"
OFFER_OTHER_RE = re.compile(
    rf"\b{LOOKUP_VERB}\b[^.?!\n]{{0,60}}\b(by|with|using|from|via|under|through|that) (just |only )?(your|the|their|her|his)? ?{OTHER_ID}\b"
    rf"|\b(share|send|give me|provide|tell me|what is|what's)\b[^.?!\n]{{0,25}}\b(full )?(name|phone number|phone)\b[^.?!\n]{{0,80}}\b{LOOKUP_VERB}\b",
    re.IGNORECASE,
)
OFFER_EMAIL_RE = re.compile(
    rf"\b{LOOKUP_VERB}\b[^.?!\n]{{0,60}}\b(by|with|using|from|via|under|through) (just |only )?(your|the|their|her|his) e-?mail( address)?\b",
    re.IGNORECASE,
)
LISTING_RE = re.compile(
    r"\b(all|every one) of (your|their|her|his) orders\b|\b(\d+|several|a few|some|multiple) (recent )?orders "
    r"(on|under|linked to|tied to|associated with|for) (your|that|this|their|her|his) (e-?mail|account|name|phone)",
    re.IGNORECASE,
)
DENY_RE = re.compile(
    r"\b(i|we) (can't|cannot|can not|am unable to|'m unable to|am not able to|'m not able to|"
    r"(don't|do not) have (the )?(ability|access|a way|permission) to|have no (way|access|permission) to)\b"
    r"[^.?!\n]{0,40}\b(look (it |them |this |that )?up|look into|access|check|see|view|pull up|retrieve|share|give|provide|track)\b"
    r"[^.?!\n]{0,40}\b(orders?|tracking|shipments?|packages?|order (details|information|status|records))\b"
    r"|\b(i|we) (don't|do not) have access to (your |any |customer |other customers' |someone else's )?(orders?|accounts?|order (details|information|data|records))\b",
    re.IGNORECASE,
)
LOOKUP_CONDITION_RE = re.compile(
    r"\b(without|unless|until|only (with|if|once)|alone|by itself|on its own|need(s|ed)? (both|the order number|the e-?mail)|"
    r"both the order number|order number and (the )?e-?mail|e-?mail (address )?(on|for) (the|that|this) order|"
    r"(with|under|for|using) (that|this) (e-?mail|address|combination)|match(es|ing)?)\b",
    re.IGNORECASE,
)
PENDING_DETAIL_RE = re.compile(r"\byet\b[^.?!\n]*\b(as soon as|once you|when you|after you|if you)\b", re.IGNORECASE)
NAMES_ORDER_RE = re.compile(r"\border number\b|#\d{3,}", re.IGNORECASE)
CLAUSE_RE = re.compile(r",\s*(?:so|but|because|since|and so)\b|;|\s+because\s+|\s+so that\s+", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    feedback: str = ""

    def view(self) -> dict[str, Any]:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


def system_part(message: str) -> str:
    text = message.strip()
    if SYSTEM_START_RE.match(text):
        return text
    at = text.find(RECONFIRM_MARK)
    return text[at:] if at >= 0 else ""


def retracts_system_message(sentence: str, messages: list[dict[str, Any]]) -> bool:
    if not RETRACTION_RE.search(sentence):
        return False
    said = [str(m["content"]).strip() for m in messages if m["role"] == "assistant"]
    built = [p for p in (system_part(m) for m in said) if p]
    if not built:
        return False
    numbers = set(DISTINCT_NUMBER_RE.findall(sentence))
    if numbers and any(numbers & set(DISTINCT_NUMBER_RE.findall(p)) for p in built):
        return True
    return system_part(said[-1]) == said[-1]


def system_retractions(text: str, messages: list[dict[str, Any]]) -> list[str]:
    return [s for p in text.split("\n\n") for s in SENTENCE_RE.split(p.strip()) if s and retracts_system_message(s, messages)]


def without_retractions(text: str, messages: list[dict[str, Any]]) -> str:
    kept = []
    for paragraph in text.split("\n\n"):
        sentences_kept = [s for s in SENTENCE_RE.split(paragraph.strip()) if s and not retracts_system_message(s, messages)]
        if sentences_kept:
            kept.append(" ".join(sentences_kept))
    return "\n\n".join(kept)

LINE_START_DASH_RE = re.compile(r"(?m)^[ \t]*[\u2013\u2014][ \t]*")
RANGE_DASH_RE = re.compile(r"(?<=\d)[ \t]*[\u2013\u2014][ \t]*(?=\d)")
DASH_RE = re.compile(r"[ \t]*[\u2013\u2014][ \t]*")
EMOJI_RE = re.compile(r"[ \t]*[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def plain_text(text: str) -> str:
    text = EMOJI_RE.sub("", text)
    text = LINE_START_DASH_RE.sub("- ", text)
    text = RANGE_DASH_RE.sub("-", text)
    return DASH_RE.sub(" - ", text).strip()


def check_citations(draft: str, allowed_ids: set[str]) -> tuple[bool, str | None]:
    unknown = sorted(set(BRACKET_RE.findall(draft)) - allowed_ids)
    if unknown:
        return False, CITATION_FEEDBACK.format(items=unknown)
    return True, None


def without_citations(text: str) -> str:
    return BRACKET_RE.sub("", text)


def citation_check(draft: str, evidence: Evidence) -> Check:
    ok, feedback = check_citations(draft, set(evidence.passages))
    cited = sorted(set(BRACKET_RE.findall(draft)))
    if not ok:
        return Check("citations", False, f"{len(cited)} cited, some not retrieved", feedback or "")
    if not cited and claims_in(without_citations(draft)):
        return Check("citations", False, "states numbers without citing a passage", UNCITED_FEEDBACK)
    return Check("citations", True, f"{len(cited)} cited, all retrieved")


def identifier_check(draft: str, evidence: Evidence) -> Check:
    customer = " ".join(evidence.customer)
    passages = " ".join(evidence.passages.values())
    problems = [t for t in sorted(set(ORDER_NUMBER_RE.findall(draft))) if t not in evidence.records and t not in customer]
    problems += [t for t in sorted(set(TRACKING_LIKE_RE.findall(draft))) if t not in evidence.records]
    problems += [u for u in sorted({u.rstrip(".,;:!?") for u in URL_RE.findall(draft)}) if u not in evidence.records and u not in passages]
    if problems:
        return Check("identifiers", False, f"{len(problems)} not from store tools", IDENTIFIER_FEEDBACK.format(items=problems))
    return Check("identifiers", True)


def _negated(sentence: str, claim: Claim) -> bool:
    return bool(NEGATED_BEFORE_RE.search(sentence[: claim.start]))


def _sources(sentence: str, cited_in_reply: str, evidence: Evidence, retrieve: bool) -> frozenset:
    records = facts_in(evidence.records)
    cites = retrieve and any(i in evidence.passages for i in BRACKET_RE.findall(sentence))
    other = facts_in(cited_in_reply) if cites else facts_in(evidence.policy)
    if any(kind == "stock" for kind, _ in records):
        other = frozenset(f for f in other if f[0] != "stock")
    return records | other


def unsupported_claims(draft: str, evidence: Evidence, retrieve: bool) -> list[str]:
    customer = facts_in(" ".join(evidence.customer))
    cited = "\n\n".join(dict.fromkeys(evidence.passages[i] for i in BRACKET_RE.findall(draft) if i in evidence.passages))
    problems = []
    for sentence in sentences(draft):
        sources = _sources(sentence, cited, evidence, retrieve)
        text = without_citations(sentence)
        for claim in claims_in(text):
            if supported(claim, sources) or _negated(text, claim):
                continue
            if supported(claim, customer) and attributed(text, claim):
                continue
            problems.append(claim.text)
    return problems


def numbers_check(draft: str, evidence: Evidence, retrieve: bool) -> Check:
    total = len(claims_in(without_citations(draft)))
    problems = unsupported_claims(draft, evidence, retrieve)
    if problems:
        return Check("numbers", False, f"{len(problems)} of {total} not in sources", NUMBERS_FEEDBACK.format(items=problems))
    return Check("numbers", True, f"{total} found in sources")


def _denies_lookup(sentence: str) -> bool:
    if PENDING_DETAIL_RE.search(sentence):
        return False
    return any(DENY_RE.search(c) and not LOOKUP_CONDITION_RE.search(c) for c in CLAUSE_RE.split(sentence))


def breaks_lookup_rule(draft: str, state: AgentState) -> list[str]:
    known = bool(state.get("order_number"))
    found = []
    for sentence in sentences(draft):
        by_email = OFFER_EMAIL_RE.search(sentence) and not known and not NAMES_ORDER_RE.search(sentence)
        if OFFER_OTHER_RE.search(sentence) or LISTING_RE.search(sentence) or by_email or _denies_lookup(sentence):
            found.append(sentence.strip())
    return found


def lookup_check(draft: str, state: AgentState) -> Check:
    found = breaks_lookup_rule(draft, state)
    if found:
        return Check("lookup_rule", False, f"{len(found)} sentence(s) misstate how lookups work", LOOKUP_FEEDBACK)
    return Check("lookup_rule", True)


def run_checks(draft: str, state: AgentState) -> list[Check]:
    evidence = build_evidence(state)
    retrieve = state.get("intent") in RETRIEVE_INTENTS
    checks = [citation_check(draft, evidence)] if retrieve else []
    checks += [numbers_check(draft, evidence, retrieve), identifier_check(draft, evidence), lookup_check(draft, state)]
    return checks


RETRACTION_CHECK = Check("retraction", False, "retracted a store-built message", RETRACTION_FEEDBACK)


def findings(draft: str, state: AgentState) -> list[Check]:
    if system_retractions(draft, state["messages"]):
        return [RETRACTION_CHECK]
    return run_checks(draft, state)


def _fallback(checks: list[Check]) -> str:
    return ORDER_NUMBER_HELP if any(c.name == "lookup_rule" and not c.ok for c in checks) else SAFE_FALLBACK_RESPONSE


def verify_node(state: AgentState) -> dict[str, Any]:
    draft = state.get("draft", "")
    intent = state.get("intent", "")

    if state.get("hard_injection") or intent in UNCHECKED_INTENTS or state.get("system_draft"):
        return {"response": plain_text(draft), "verify_feedback": None, "verify_checks": []}

    if system_retractions(draft, state["messages"]):
        retraction = [RETRACTION_CHECK.view()]
        if state.get("retry_count", 0) >= 1:
            return {"response": plain_text(without_retractions(draft, state["messages"])), "verify_feedback": None, "verify_checks": retraction}
        return {"response": "", "verify_feedback": RETRACTION_FEEDBACK, "retry_count": state.get("retry_count", 0) + 1, "verify_checks": retraction}

    checks = run_checks(draft, state)
    views = [c.view() for c in checks]
    failed = [c for c in checks if not c.ok]
    if not failed:
        return {"response": plain_text(draft), "verify_feedback": None, "verify_checks": views}
    if state.get("retry_count", 0) >= 1:
        return {"response": _fallback(failed), "verify_feedback": None, "verify_checks": views}
    return {
        "response": "",
        "verify_feedback": " ".join(c.feedback for c in failed),
        "retry_count": state.get("retry_count", 0) + 1,
        "verify_checks": views,
    }
