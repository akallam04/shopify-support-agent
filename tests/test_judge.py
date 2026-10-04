import asyncio
from types import SimpleNamespace

from evals.graders import JUDGE_ATTEMPTS, run_judge


def reply(text: str | None, stop: str = "end_turn") -> SimpleNamespace:
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(
        content=content, stop_reason=stop, usage=SimpleNamespace(input_tokens=100, output_tokens=10)
    )


class FakeClient:
    def __init__(self, replies: list[SimpleNamespace]) -> None:
        self.replies = list(replies)
        self.messages = self

    async def create(self, **_: object) -> SimpleNamespace:
        return self.replies.pop(0)


def grade(replies: list[SimpleNamespace]) -> tuple[bool, str, dict[str, int]]:
    messages = [{"role": "user", "content": "Do you have a rain jacket?"}]
    return asyncio.run(run_judge(FakeClient(replies), "judge", messages, "Yes, the Stormline.", "Recommends it."))


def test_an_empty_judge_reply_is_regraded_and_its_tokens_counted() -> None:
    verdict, reason, usage = grade([reply(None), reply('{"pass": true, "reason": "accurate"}')])
    assert verdict is True
    assert reason == "accurate [judge retried 1x]"
    assert usage == {"input_tokens": 200, "output_tokens": 20}


def test_a_judge_that_never_answers_fails_with_the_cause() -> None:
    verdict, reason, _ = grade([reply(None, stop="max_tokens")] * JUDGE_ATTEMPTS)
    assert verdict is False
    assert reason.startswith(f"JUDGE_ERROR unparseable after {JUDGE_ATTEMPTS} tries")
    assert "stop=max_tokens" in reason
