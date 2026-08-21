"""A scripted `LLMClient` so every LLM-dependent test runs offline and deterministically.

Usage:

    client = ScriptedLLMClient(assess_risks=[risk_output_1, risk_output_2])
    ...
    assert client.call_count(LLMTask.ASSESS_RISKS) == 2

A queued `Exception` is raised rather than returned, which is how a test makes the *second* of
several calls fail:

    ScriptedLLMClient(analyse_audit_area=[good_output, RuntimeError("api down")])

The fake is strict on purpose. It fails loudly when a task is called that the test did not
script, when a queue is exhausted, or when a queued object is the wrong type for the
requested `output_format` — all of which mean the test is not exercising what it claims.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from src.llm.client import LLMTask


@dataclass
class RecordedCall:
    task: LLMTask
    system: str
    user: str
    output_format: type[BaseModel]


class ScriptedLLMClient:
    """Returns queued responses per task and records how it was called."""

    def __init__(self, **responses: Any) -> None:
        self._queues: dict[LLMTask, list[Any]] = {}
        for name, value in responses.items():
            # Raises on a typo'd task name rather than silently never matching.
            task = LLMTask(name)
            self._queues[task] = list(value) if isinstance(value, list) else [value]
        self.calls: list[RecordedCall] = []

    def parse(self, *, task: LLMTask, system: str, user: str, output_format: type) -> Any:
        self.calls.append(RecordedCall(task, system, user, output_format))

        if task not in self._queues:
            raise AssertionError(
                f"unscripted LLM call for {task}; scripted tasks: {sorted(self._queues)}"
            )
        queue = self._queues[task]
        if not queue:
            raise AssertionError(
                f"{task} was called more times than scripted "
                f"({self.call_count(task)} calls, {len(self._responses_for(task))} queued)"
            )

        response = queue.pop(0)
        if isinstance(response, Exception):
            # A queued exception is how a test scripts an API failure part-way through a
            # multi-call recompute, so the rollback path can be exercised at any point.
            raise response
        if not isinstance(response, output_format):
            raise AssertionError(
                f"{task} scripted with {type(response).__name__} but the service asked for "
                f"{output_format.__name__}"
            )
        return response

    def _responses_for(self, task: LLMTask) -> list[Any]:
        return self._queues.get(task, [])

    # --- inspection helpers ----------------------------------------------------------

    def call_count(self, task: LLMTask | None = None) -> int:
        if task is None:
            return len(self.calls)
        return sum(1 for call in self.calls if call.task == task)

    def calls_for(self, task: LLMTask) -> list[RecordedCall]:
        return [call for call in self.calls if call.task == task]

    def last_user_message(self, task: LLMTask) -> str:
        calls = self.calls_for(task)
        if not calls:
            raise AssertionError(f"{task} was never called")
        return calls[-1].user

    def assert_all_consumed(self) -> None:
        """Fail if any scripted response went unused — usually a miscounted expectation."""
        leftover = {task: len(queue) for task, queue in self._queues.items() if queue}
        if leftover:
            raise AssertionError(f"scripted responses never consumed: {leftover}")


@dataclass
class FailingLLMClient:
    """An `LLMClient` that always raises, for testing error propagation."""

    error: Exception = field(default_factory=lambda: RuntimeError("boom"))

    def parse(self, *, task: LLMTask, system: str, user: str, output_format: type) -> Any:
        raise self.error
