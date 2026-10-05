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
