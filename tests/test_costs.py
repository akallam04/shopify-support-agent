import pytest

from evals.run_evals import usage_cost


def test_input_output_and_cache_tokens_are_priced() -> None:
    usage = [{
        "model": "claude-sonnet-5-5",
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }]
    assert usage_cost(usage) == pytest.approx(2.00 + 1.00 + 2.50 + 0.20)


def test_records_without_cache_fields_still_price() -> None:
    assert usage_cost([{"model": "claude-haiku-4-5", "input_tokens": 1000, "output_tokens": 200}]) == pytest.approx(0.002)


def test_an_unknown_model_fails_instead_of_costing_nothing() -> None:
    with pytest.raises(KeyError):
        usage_cost([{"model": "claude-new-model", "input_tokens": 10, "output_tokens": 10}])


def test_opus_cache_reads_cost_half_the_usual_rate() -> None:
    usage = [{"model": "claude-opus-5-5", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000}]
    assert usage_cost(usage) == pytest.approx(0.20)
