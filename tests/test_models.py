from app.agent.models import call_options

SCHEMA = {"type": "json_schema", "schema": {"type": "object"}}


def test_haiku_calls_are_unchanged() -> None:
    assert call_options("claude-haiku-4-5", 300) == {"model": "claude-haiku-4-5", "max_tokens": 300}
    assert call_options("claude-haiku-4-5", 300, SCHEMA)["output_config"] == {"format": SCHEMA}


def test_sonnet_5_5_runs_at_low_effort_with_room_for_thinking() -> None:
    options = call_options("claude-sonnet-5-5", 50, SCHEMA)
    assert options["output_config"] == {"format": SCHEMA, "effort": "low"}
    assert options["max_tokens"] >= 4000
    assert "thinking" not in options


def test_haiku_5_5_runs_at_low_effort_with_room_for_thinking() -> None:
    options = call_options("claude-haiku-5-5", 50, SCHEMA)
    assert options["output_config"] == {"format": SCHEMA, "effort": "low"}
    assert options["max_tokens"] >= 4000
    assert not {"thinking", "temperature", "top_p", "top_k"} & set(options)


def test_haiku_5_5_prices_follow_the_prompt_length_tier() -> None:
    from app.costs import usage_cost

    short = {"model": "claude-haiku-5-5", "input_tokens": 50_000, "output_tokens": 10_000, "cache_read_input_tokens": 40_000}
    assert round(usage_cost([short]), 6) == round(0.05 * 0.10 + 0.01 * 0.50 + 0.04 * 0.01, 6)
    long = {"model": "claude-haiku-5-5", "input_tokens": 150_000, "output_tokens": 0}
    assert round(usage_cost([long]), 6) == round(0.15 * 0.50, 6)


def test_a_refusal_has_no_text_to_parse() -> None:
    from types import SimpleNamespace

    from app.agent.models import response_text

    text = SimpleNamespace(type="text", text='{"label": "confirm"}')
    assert response_text(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"), text])) == '{"label": "confirm"}'
    assert response_text(SimpleNamespace(stop_reason="refusal", content=[text])) is None
    assert response_text(SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="thinking")])) is None


def test_thinking_blocks_in_the_loop_are_detected() -> None:
    from types import SimpleNamespace

    from app.agent.models import carries_thinking

    assert carries_thinking([{"role": "user", "content": "hi"}, {"role": "assistant", "content": [SimpleNamespace(type="thinking")]}])
    assert not carries_thinking([{"role": "user", "content": "hi"}, {"role": "assistant", "content": [SimpleNamespace(type="tool_use")]}])
