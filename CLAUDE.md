# CLAUDE.md

## Role

You are the primary implementation agent for this repository.

Your job is to:
1. read and follow `SPEC.md`,
2. plan the MVP before implementation,
3. implement the plan milestone by milestone,
4. keep the code simple, testable, and aligned with the product design,
5. stop and surface unresolved product/audit-methodology decisions instead of inventing major assumptions.

`SPEC.md` is the source of truth for product behavior and scope.

---

## Working principles

- Optimize for a strong local MVP, not production infrastructure.
- Prefer simple, readable Python over clever abstractions.
- Use lightweight domain modelling with Pydantic.
- Domain objects hold state; functions/services implement logic.
- Keep deterministic logic clearly separated from LLM logic.
- Keep static audit/config knowledge in JSON/config rather than scattered `if/else` branches.
- Use Streamlit for the UI unless there is a compelling reason not to.
- Use `pytest` for deterministic tests and lightweight evals for LLM behavior.
- Use `ruff` for linting/format checks.
- Do not add a database, FastAPI layer, message queue, repository pattern, dependency-injection framework, agent framework, or other infrastructure unless `SPEC.md` requires it.
- Do not use LangChain or LangGraph unless a concrete need appears and is discussed first.
- Do not silently expand scope.

---

## Environment

Use Python 3.12 and `uv` for virtual environment and dependency management.

The project should use:

- `.venv/` as the local virtual environment,
- `pyproject.toml` for project dependencies and metadata,
- `uv.lock` as the dependency lockfile.

Expected setup:

```bash
uv venv --python 3.12
uv sync
```

Run project commands through `uv`, for example:

```bash
uv run pytest
uv run ruff check .
uv run streamlit run src/ui/app.py
```

Add dependencies with:

```bash
uv add <package>
```

Add development dependencies with:

```bash
uv add --dev <package>
```

Do not introduce Poetry, Conda, pip-tools, or another dependency/environment manager unless explicitly requested.

Ensure `.venv/` is included in `.gitignore`.

---

## Anthropic SDK

Use the Anthropic Python SDK directly for all LLM functionality.

- Use native structured outputs / Pydantic parsing.
- Prefer SDK-enforced schemas over custom JSON extraction and retry loops.
- Use enums / literals for bounded outputs such as `low | medium | high`.
- Keep each LLM task bounded and separately testable.
- Keep prompts and model configuration separate from domain/business logic.
- Do not use one giant prompt to generate the whole audit.
- Handle genuine API/network failures appropriately, but do not build retry machinery merely to force valid JSON when the SDK can enforce the output schema.

Expected LLM functions are conceptually:

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

1. Read `SPEC.md` fully.
2. Inspect the repository.
3. Create `PLAN.md`.
4. Break implementation into small, independently testable milestones.
5. For each milestone include:
   - functionality added,
   - likely files/modules,
   - dependencies,
   - verification/tests,
   - unresolved decisions if any.
6. Prefer the smallest architecture that satisfies the spec.
7. Stop after producing the plan until explicitly asked to implement.

Do not write application code during the initial planning task.

---

## Implementation workflow

When asked to implement a milestone:

1. Read the relevant part of `SPEC.md` and `PLAN.md`.
2. Inspect existing code before editing.
3. Briefly state the implementation approach.
4. Implement only the requested milestone.
5. Add or update tests/evals.
6. Run relevant checks.
7. Fix failures.
8. Summarize:
   - what changed,
   - tests/evals run,
   - assumptions made,
   - unresolved issues.
9. Do not automatically continue to the next milestone.

Use Git-friendly, incremental changes.

---

## Code-quality rules

- Prefer explicit code over unnecessary abstraction.
- Avoid deep inheritance.
- Avoid hidden side effects.
- Keep pure deterministic functions pure where practical.
- Keep LLM calls behind narrow service functions.
- Use typed Pydantic models for structured state.
- Preserve explicit IDs/relationships for traceability.
- Avoid duplicating balance-specific logic in Python when it belongs in configuration.
- Mock/fake LLM outputs in unit tests where live model calls are unnecessary.
- Keep runtime audit state separate from static config.
- Preserve original system outputs when auditor overrides them.

---

## Testing and evals

Before considering a milestone complete:

```bash
ruff check .
pytest
```

Where relevant, also run the LLM eval suite.

Important behavior to test includes:

- materiality,
- balance scoping,
- derived metrics,
- structured model parsing,
- assertion relevance,
- risk overrides,
- downstream recomputation,
- procedure selection,
- traceability,
- reverse ISA coverage,
- auditor feedback capture,
- feedback-to-rule proposal.

For LLM behavior, prefer scenario/eval tests over judging one demo output manually.

---

## Product/audit decisions

If implementation requires an important decision not resolved by `SPEC.md`:

- do not invent a complex methodology,
- document the ambiguity,
- propose the smallest reasonable options,
- ask for a decision before proceeding if it materially changes product behavior.

Minor implementation details may be decided independently.

---

## Scope discipline

The MVP is intended to prove:

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

If a feature is not necessary to demonstrate that loop, defer it unless explicitly requested.
