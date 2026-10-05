"""Recognizing when the model API cannot serve requests, as opposed to a bug in the agent."""

import anthropic

BILLING_MARKERS = ("credit balance", "usage limit")
UNAVAILABLE = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.APIConnectionError,
)


def billing_text(text: str) -> bool:
    return any(m in text.lower() for m in BILLING_MARKERS)


def is_billing_error(e: Exception) -> bool:
    return isinstance(e, anthropic.BadRequestError) and billing_text(str(e))


def model_unavailable(e: Exception) -> bool:
    return isinstance(e, UNAVAILABLE) or is_billing_error(e)
