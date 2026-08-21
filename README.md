# Audit planning engine — MVP

A local prototype that turns a set of financial figures plus a paragraph of free-text company
context into a **proposed audit plan** for cash and inventory, with explicit ISA traceability and
auditor override.

It demonstrates one loop end to end:

```
company information → assertions → risks → procedures → ISA traceability
                            → auditor override → candidate methodology rule
```

`SPEC.md` is the source of truth for behaviour; `PLAN.md` records how it was built and why.

---

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- An Anthropic API key (only for the LLM steps — the test suite needs none)

## Setup

```bash
uv venv --python 3.12
uv sync --extra dev

cp .env.example .env        # then paste your key into ANTHROPIC_API_KEY
```

## Run the app

```bash
uv run streamlit run src/ui/app.py
```

The screen arrives **pre-populated** with the Raiatea Ltd case — no blank forms. Nothing is
persisted: reloading the browser starts a fresh engagement.

A full pipeline run is **five API calls**: one to extract company facts, then two per audit area
(analysis, then procedure selection). Calls scale with audit areas, not with assertions or risks.

### The demo path

1. **Run the pipeline** from the sidebar. Materiality is £262,000 — 5% of profit before tax,
   labelled as prototype methodology rather than an ISA-prescribed formula.
2. **Line items** — all eight are scoped. Six are material but show
   `material — audit logic not implemented in MVP`: a stated scope boundary, deliberately not
   reported as an ISA gap.
3. **Audit areas** — per assertion, a relevance verdict with its rationale; per risk, the
   likelihood × magnitude the model returned and the `system_rating` the configured matrix derived
   from them.
4. **Override an inventory risk** from high to low with a reason. The system rating stays visible
   beside the new one, procedures are re-selected **for that risk alone**, and cash is untouched.
5. **Traceability** — pick a procedure and read the chain back to its risk, assertion, line item,
   supporting facts and ISA requirements.
6. **ISA coverage** — the same links read in reverse: what addresses each requirement, and where
   work is missing.
7. **Auditor feedback** — every override is logged with what it replaced. Analyse one for a
   methodology rule; a generalisable reason produces a `pending_review` proposal. Nothing is ever
   written back to the approved methodology.

### Headless

```bash
uv run python -m src.demo        # run the pipeline and print the plan (5 calls)
```

## Tests

```bash
uv run pytest                    # ~400 tests, no network, no API key
uv run ruff check .
```

Live-model work is opt-in and deselected by default:

```bash
uv run pytest -m llm             # SDK plumbing: schemas round-trip, enums enforced
uv run pytest -m eval            # scenario evals: is the judgement sensible? (~25 calls, ~6 min)
uv run python -m evals.run_evals # prints the A/B comparison table
```

The two markers answer different questions. A green `llm` suite with a red `eval` suite means the
prompts are wrong, not the code. **Evals are advisory, not a gate** — they call a non-deterministic
model, so a failure means reading the printed table and judging whether the prompt regressed.

---

## How it fits together

```
ui/app.py      presentation only — no domain logic
    ↓
engine/        deterministic: materiality, scoping, risk matrix, catalogue,
               pipeline, recompute, traceability, coverage
    ↓
llm/           four bounded judgements, each behind an injected client
    ↓
models/        Pydantic runtime state
    ↑
config/        five JSON files, validated at load
```

Four decisions shape the rest:

**The deterministic/LLM line is drawn on purpose.** The model supplies judgement; the engine owns
anything known and repeatable. It returns `likelihood` and `magnitude` but *cannot* return a rating
— the schema has no such field — and `engine/risk_matrix.py` derives `system_rating` from config. It
only ever sees a catalogue subset the engine filtered, so it cannot select a procedure that is wrong
for the assertion.

**Methodology is data.** Candidate assertions, the procedure catalogue, the risk matrix and the ISA
requirements are JSON. Adding an audit area or changing risk appetite is a config edit; there is no
`if area == "cash"` anywhere in the code.

**Relationships are explicit IDs.** Every object carries its parent's ID, and procedures hold a
plural `risk_ids` because one procedure genuinely answers several risks. IDs are allocated by the
engine, never by the model, and are never reused — a recycled ID would let a retained reference
resolve silently to different evidence.

**The auditor stays in control.** `system_rating` is never overwritten, so the engine's original
conclusion survives disagreement. Every override records what it replaced, recomputes only its own
subtree, and is all-or-nothing: if a call fails, the engagement is restored rather than left
half-updated.

## Deliberately not built

Auth, a database, deployment, ERP/file ingestion, full ISA 315/330 coverage, control-risk modelling,
significant-risk classification, and any automatic update of methodology from auditor feedback. A
rule proposal is advisory by design — a firm's methodology should not change because one auditor
disagreed once, and there is a test asserting the config files are byte-identical after a
classification run.
