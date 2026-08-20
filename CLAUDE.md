# CLAUDE.md

## Role

You are the primary planning and implementation agent for this repository.

`SPEC.md` is the source of truth for product behavior and scope.  
`PLAN.md` is the implementation sequence once created.

Your job is to:
1. follow the spec,
2. implement milestone by milestone,
3. keep the code simple, testable, and aligned with the MVP,
4. surface important unresolved product/audit-methodology decisions instead of inventing them.

Do not silently expand scope.

---

## Token efficiency

Be concise and minimize unnecessary context use.

- Do not restate `SPEC.md`, `PLAN.md`, or these instructions unless needed.
- After initial planning, do not reread all of `SPEC.md` for every milestone. Read only the relevant sections plus the relevant part of `PLAN.md`.
- Inspect only files relevant to the current task.
- Prefer targeted search/grep over rereading large unchanged files.
- Do not narrate routine reasoning, every file inspected, or obvious implementation steps.
- Keep implementation preambles to at most 2–3 sentences, only if useful.
- Keep completion summaries short: what changed, checks run, unresolved issues.
- Do not explain code in detail unless asked.
- Do not propose optional future improvements unless they reveal a real problem.
- Make minor implementation decisions independently.
- During development, run the smallest relevant tests; run the full required checks once before completing the milestone.
- Stop when the requested milestone is complete.

---

## Working principles

- Optimize for a strong local MVP, not production infrastructure.
- Prefer simple, readable Python over clever abstractions.
- Use lightweight Pydantic domain models.
- Domain objects hold state; functions/services implement logic.
- Keep deterministic logic clearly separated from LLM logic.
- Keep static audit/config knowledge in JSON/config rather than scattered conditionals.
- Use Streamlit for the UI unless there is a compelling reason not to.
- Use `pytest` for tests/evals and `ruff` for linting.
- Avoid deep inheritance and unnecessary abstractions.
- Keep runtime audit state separate from static config.
- Preserve explicit IDs/relationships for traceability.
- Preserve original system outputs when auditor overrides them.
- Mock/fake LLM outputs in unit tests where live calls are unnecessary.

Do not introduce databases, FastAPI, queues, repository patterns, DI frameworks, agent frameworks, LangChain, or LangGraph unless explicitly required or discussed first.

---

## Environment

Use Python 3.12 and `uv`.

Project conventions:

- `.venv/` for the local environment
- `pyproject.toml` for dependencies/metadata
- `uv.lock` for the lockfile
- `.venv/` must be gitignored

Typical commands:

```bash
uv venv --python 3.12
uv sync
uv add <package>
uv add --dev <package>
uv run pytest
uv run ruff check .
uv run streamlit run src/ui/app.py
```

Do not introduce Poetry, Conda, pip-tools, or another dependency manager unless explicitly requested.

---

## Anthropic SDK

Use the Anthropic Python SDK directly for all MVP LLM functionality.

- Use native structured outputs / Pydantic parsing.
- Prefer SDK-enforced schemas over custom JSON extraction/retry loops.
- Use enums/literals for bounded values such as `low | medium | high`.
- Keep LLM tasks bounded and separately testable.
- Keep prompts/model configuration separate from domain logic.
- Do not use one giant prompt to generate the whole audit.
- Handle genuine API/network failures normally, but do not build retry machinery merely to force valid JSON when the SDK can enforce the schema.

Conceptual LLM functions:

```python
extract_company_facts()
assess_assertions()
assess_risks()
select_procedures()
generalize_feedback()
```

---

## Planning workflow

Before writing application code:

1. Read `SPEC.md`.
2. Inspect the repository.
3. Create `PLAN.md` with small, independently testable milestones.
4. For each milestone include only:
   - functionality,
   - likely files/modules,
   - dependencies,
   - verification/tests,
   - unresolved decisions if material.
5. Prefer the smallest architecture that satisfies the spec.
6. Stop after planning until explicitly asked to implement.

Do not write application code during the initial planning task.

---

## Implementation workflow

When asked to implement a milestone:

1. Read the relevant sections of `SPEC.md` and `PLAN.md`.
2. Inspect the relevant existing code.
3. State the approach briefly if useful.
4. Implement only the requested milestone.
5. Add/update relevant tests or evals.
6. Run relevant checks and fix failures.
7. Give a concise completion summary.
8. Stop before the next milestone.

Use Git-friendly incremental changes.

---

## Testing and evals

Before considering a milestone complete, run:

```bash
uv run ruff check .
uv run pytest
```

Where relevant, also run the LLM eval suite.

Important behaviors include:

- materiality and scoping,
- derived metrics,
- structured LLM parsing,
- assertion relevance,
- risk assessment and overrides,
- downstream recomputation,
- procedure selection,
- traceability and reverse ISA coverage,
- auditor feedback capture,
- feedback-to-rule proposals.

For LLM behavior, prefer fixed scenarios/evals over judging one demo output manually.

---

## Product/audit decisions

If the spec leaves an important product or audit-methodology decision unresolved:

- do not invent a complex methodology,
- identify the ambiguity briefly,
- propose the smallest reasonable options,
- ask only if the decision materially changes behavior.

Minor implementation details should be decided independently.

---

## Scope

The MVP exists to prove:

```text
company information
→ assertions
→ risks
→ procedures
→ ISA traceability
→ auditor override
→ candidate methodology learning
```

Do not turn this into a production audit platform.

If a feature is not needed to demonstrate that loop, defer it unless explicitly requested.
