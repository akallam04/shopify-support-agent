import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("sync_lambda_env", Path("deploy/sync_lambda_env.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

ENV = {"SHOPIFY_ADMIN_TOKEN": "read-token", "SHOPIFY_WRITE_TOKEN": "write-token"}


def test_the_write_token_is_refused_by_name() -> None:
    wanted, _ = sync.requested_values(["SHOPIFY_WRITE_TOKEN"], ENV)
    assert sync.refusals(wanted, ENV)


def test_the_write_token_is_refused_under_another_name() -> None:
    swapped = dict(ENV, SHOPIFY_ADMIN_TOKEN="write-token")
    wanted, _ = sync.requested_values(["SHOPIFY_ADMIN_TOKEN"], swapped)
    assert sync.refusals(wanted, swapped)
    literal, _ = sync.requested_values(["SOMETHING=write-token"], ENV)
    assert sync.refusals(literal, ENV)


def test_ordinary_keys_and_literals_pass() -> None:
    wanted, missing = sync.requested_values(["SHOPIFY_ADMIN_TOKEN", "CORS_ORIGINS=https://example.com"], ENV)
    assert missing == []
    assert wanted == {"SHOPIFY_ADMIN_TOKEN": "read-token", "CORS_ORIGINS": "https://example.com"}
    assert sync.refusals(wanted, ENV) == []


def test_missing_keys_are_reported() -> None:
    _, missing = sync.requested_values(["NOT_SET"], ENV)
    assert missing == ["NOT_SET"]
