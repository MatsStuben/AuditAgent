"""The single seam through which every LLM call passes (SPEC 21).

Design points this file exists to enforce:

* **Model configuration is separate from domain logic.** `TASK_CONFIG` is the one place
  model, token budget and reasoning effort are chosen, per task.
* **Every service takes an injected `LLMClient`.** Production passes `AnthropicLLMClient`;
  tests pass a scripted fake, so no unit test needs credentials or a network.
* **Native structured output only.** Calls go through `messages.parse` with a Pydantic
  `output_format`; there is no JSON-extraction or reprompt-until-valid loop.
"""

from enum import StrEnum
from typing import Literal, Protocol, TypeVar

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMTask(StrEnum):
    """The four bounded judgements delegated to the model (SPEC 21).

    `ANALYSE_AUDIT_AREA` and `SELECT_PROCEDURES` each run once per audit area, so total calls
    scale with audit areas rather than with assertions and risks (SPEC 6.1).
    """

    EXTRACT_COMPANY_FACTS = "extract_company_facts"
    ANALYSE_AUDIT_AREA = "analyse_audit_area"
    SELECT_PROCEDURES = "select_procedures"
    GENERALIZE_FEEDBACK = "generalize_feedback"


class TaskConfig(BaseModel):
    model: str
    max_tokens: int
    effort: Effort


DEFAULT_MODEL = "claude-opus-5"

#: Per-task model configuration. Effort is matched to how much judgement the task needs
#: rather than applied uniformly (SPEC 21: "not every task requires maximum reasoning
#: effort"). Extraction is near-mechanical; analysing an audit area is the core judgement.
#:
#: Token budgets reflect batching: one analysis response covers every assertion and risk for
#: an area, and one selection response covers every risk, so both need far more room than the
#: per-assertion calls they replaced.
TASK_CONFIG: dict[LLMTask, TaskConfig] = {
    LLMTask.EXTRACT_COMPANY_FACTS: TaskConfig(model=DEFAULT_MODEL, max_tokens=2_000, effort="low"),
    LLMTask.ANALYSE_AUDIT_AREA: TaskConfig(model=DEFAULT_MODEL, max_tokens=16_000, effort="high"),
    LLMTask.SELECT_PROCEDURES: TaskConfig(model=DEFAULT_MODEL, max_tokens=8_000, effort="medium"),
    LLMTask.GENERALIZE_FEEDBACK: TaskConfig(model=DEFAULT_MODEL, max_tokens=2_000, effort="medium"),
}


class LLMError(RuntimeError):
    """Any failure to obtain a validated result from the model."""


class LLMClient(Protocol):
    """The narrow interface every LLM service depends on."""

    def parse(self, *, task: LLMTask, system: str, user: str, output_format: type[T]) -> T:
        """Return a validated `output_format` instance, or raise `LLMError`."""
        ...


class AnthropicLLMClient:
    """`LLMClient` backed by the Anthropic SDK."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        config: dict[LLMTask, TaskConfig] | None = None,
    ) -> None:
        if client is None:
            # Loaded here rather than at import so that importing this module has no
            # filesystem side effect and tests using the fake never touch .env.
            load_dotenv()
            client = anthropic.Anthropic()
        self._client = client
        self._config = config or TASK_CONFIG

    def parse(self, *, task: LLMTask, system: str, user: str, output_format: type[T]) -> T:
        config = self._config[task]
        try:
            response = self._client.messages.parse(
                model=config.model,
                max_tokens=config.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
                # The SDK merges output_format in as output_config["format"], so effort
                # and the schema coexist here.
                output_config={"effort": config.effort},
            )
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"{task}: could not reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"{task}: API returned {exc.status_code}: {exc}") from exc

        # Checked before reading content: a refusal is a 200 response with no parsed output.
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMError(f"{task}: the model declined this request (category={category})")

        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"{task}: response hit the {config.max_tokens} token cap before completing"
            )

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(f"{task}: no parsed output (stop_reason={response.stop_reason})")
        return parsed
