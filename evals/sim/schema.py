"""Task schema, mirroring tau2's tasks with a few additions for this store."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = (
    "order_status",
    "cancel",
    "address_change",
    "return",
    "product_policy",
    "multi_intent",
    "identity",
    "other_person",
    "injection",
    "difficult_customer",
    "confirmation",
    "handoff",
    "which_order",
)
WRITE_TOOLS = frozenset({"cancel_order", "update_shipping_address", "request_return"})
RewardType = Literal["DB", "COMMUNICATE", "NL_ASSERTION", "ENV_ASSERTION"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Instructions(Strict):
    reason_for_call: str
    known_info: str
    unknown_info: str = ""
    task_instructions: str


class UserScenario(Strict):
    persona: str
    instructions: Instructions
    must_not_reveal: list[str] = []


class Action(Strict):
    name: str
    arguments: dict = {}


class ForbiddenAction(Strict):
    name: str
    order_number: str | None = None


class EnvAssertion(Strict):
    type: Literal["handoff_recorded", "no_handoff"]


class EvaluationCriteria(Strict):
    actions: list[Action] = []
    communicate_info: list[str | list[str]] = []
    nl_assertions: list[str] = []
    env_assertions: list[EnvAssertion] = []
    forbidden_actions: list[ForbiddenAction] = []
    reward_basis: list[RewardType] = Field(default_factory=lambda: ["DB", "COMMUNICATE"])

    def write_actions(self) -> list[Action]:
        return [a for a in self.actions if a.name in WRITE_TOOLS]


class InitialState(Strict):
    place: dict[str, str] = {}
    deliver: dict[str, str] = {}
    tag_products: dict[str, str] = {}


class Task(Strict):
    id: str
    category: Literal[CATEGORIES]
    description: str
    user_scenario: UserScenario
    initial_state: InitialState = InitialState()
    evaluation_criteria: EvaluationCriteria


def load_tasks(path: str | Path = "evals/sim/tasks.json") -> list[Task]:
    return [Task.model_validate(t) for t in json.loads(Path(path).read_text())]
