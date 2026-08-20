# Audit Planning Engine — MVP Implementation Plan

> Status: planning complete, implementation not started. Per `CLAUDE.md`, milestones are implemented one at a
> time on explicit request.

---

## Context

`SPEC.md` describes a local prototype that turns Raiatea Ltd's financials plus free-text company context into a
proposed audit plan for cash and inventory, with deterministic materiality/scoping, LLM judgement for
assertion relevance, risk and procedure selection, explicit ISA traceability, auditor override, and capture of
overrides as candidate methodology rules.

The repository today contains only `SPEC.md`, `CLAUDE.md`, `AGENTS.md`, `README.md` and the two ISA PDFs. There
is no code, no `pyproject.toml`, and no test harness. This plan sequences the build into small milestones that
each end in a runnable check, so the engine is fully working headlessly before any UI exists.

The goal is a credible case-study MVP, not production audit software. Every milestone below is scoped to the
smallest thing that satisfies the spec section it implements.

### Environment findings (verified, and they gate M0)

| Finding | Consequence |
| --- | --- |
| `anthropic==0.66.0` installed; `client.messages.parse` / `output_format` absent | SPEC §21 requires native structured output. Pin `anthropic>=1.0.0` (current release) in a project venv. |
| `streamlit`, `pytest`, `ruff` not installed | All added as project dependencies in M0. |
| Python 3.11.10 | Fine — SDK 1.x needs ≥3.10. |
| ~~No `ANTHROPIC_API_KEY`~~ — **resolved**: key supplied and validated, stored in gitignored `.env` | Deterministic milestones and all unit tests still run without credentials (scripted fake). Live work (M3 smoke test, M13 evals, UI demo) is now unblocked. `claude-opus-5` confirmed available to this key. |

---

## Architecture

Follow SPEC §24 almost exactly. Objects hold state; functions perform logic; static config is JSON; runtime
audit state lives in Pydantic objects held in Streamlit session state. No database, no API layer, no framework.

```text
audit-engine/
├── pyproject.toml
├── src/
│   ├── models/
│   │   ├── engagement.py        # AuditEngagement, Materiality, FinancialLineItemAssessment, CompanyFact
│   │   ├── audit_objects.py     # AssertionAssessment, RiskAssessment, Procedure, enums
│   │   ├── isa.py               # ISARequirement
│   │   └── feedback.py          # AuditorFeedback, RuleProposal
│   ├── config/
│   │   └── loader.py            # loads + validates the five JSON files (cached)
│   ├── engine/
│   │   ├── materiality.py
│   │   ├── scoping.py           # derived metrics + material flag + is_audit_area
│   │   ├── risk_matrix.py       # likelihood x magnitude -> rating, from config
│   │   ├── catalogue.py         # deterministic catalogue filtering
│   │   ├── pipeline.py          # the SPEC §6 orchestration
│   │   ├── recompute.py         # override handling + downstream invalidation
│   │   ├── traceability.py
│   │   └── coverage.py
│   ├── llm/
│   │   ├── client.py            # SDK wrapper + per-task model config + LLMClient protocol
│   │   ├── schemas.py           # Pydantic output models for every LLM call
│   │   ├── prompts.py           # all system/user prompt text, no domain logic
│   │   ├── context_extractor.py
│   │   ├── assertion_assessor.py
│   │   ├── risk_assessor.py
│   │   ├── procedure_selector.py
│   │   └── feedback_generalizer.py
│   ├── data/
│   │   ├── raiatea.json
│   │   ├── audit_area_profiles.json
│   │   ├── procedure_catalogue.json
│   │   ├── risk_matrix.json
│   │   └── isa_requirements.json
│   └── ui/
│       └── app.py
├── tests/                       # pytest, fake LLM, no network
└── evals/                       # scenario contexts + runner (live model, opt-in)
```

### Design decisions taken here (simplest option consistent with the spec)

1. **Line item vs audit area (SPEC §2.1).** All eight supplied items become `FinancialLineItemAssessment`
   objects and are scoped against materiality. A line item continues past scoping iff it is material **and**
   `audit_area_profiles.json` has an entry for it — that is what makes it an *audit area*. This expresses
   "only cash and inventory continue" without a single `if item == "cash"` anywhere (SPEC §9), and adding a
   third audit area is a JSON edit. `material` and `is_audit_area` are independent flags.
2. **One LLM call per bounded judgement.** One call per material audit area for assertion relevance (returns
   the full candidate list with a relevance verdict each); one per relevant assertion for risk; one per risk
   for procedures. Never one prompt for the whole audit (SPEC §21).
3. **LLM services take an injected client.** Each service function signature ends with `client: LLMClient`
   (a `Protocol` with a single `parse(...)` method). Production passes the SDK wrapper; tests pass a scripted
   fake. This is what makes every LLM step independently testable without network.
4. **Model config lives in one module.** `llm/client.py` holds a `TASK_CONFIG` dict mapping each task to model
   id + `max_tokens` + effort, so it is explicit and changeable in one place (SPEC §21). Default
   `claude-opus-5`; fact extraction is the one task that starts at low effort.
5. **Every runtime object gets a stable string ID** (`fact_1`, `assertion_3`, `risk_2`, `proc_5`,
   `feedback_1`) from a per-engagement counter. Relationships are stored as IDs, never inferred from
   free text (SPEC §14). Child objects also store their parent ID, so traceability is a lookup, not a tree walk.
6. **LLM-returned `supporting_fact_ids` are validated against the engagement's actual facts** and unknown IDs
   are dropped with a warning rather than stored — otherwise traceability silently breaks.
7. **The pipeline is headless and importable.** `run_pipeline(engagement, client)` has no Streamlit
   dependency, which is what lets M13's evals and M4–M12's tests run it directly. The UI only calls into it.
8. **Risk rating is derived, never asked for (SPEC §11).** The LLM returns `likelihood` and `magnitude` only;
   `RiskAssessmentOutput` has no `risk_rating` field, so the model cannot supply one. `engine/risk_matrix.py`
   maps the pair to `system_rating` through `risk_matrix.json`. The matrix is config, not Python constants and
   not prompt text, so a methodology owner can change risk appetite without touching the engine.

---

## Milestones

Each milestone ends with `ruff check .` and `pytest` green. Milestones are ordered so the whole engine works
headlessly (M0–M11) before the UI (M13) is written.

### M0 — Project skeleton and tooling

**Adds:** a runnable, linted, testable project.

**Files:** `pyproject.toml` (deps: `anthropic>=1.0.0`, `pydantic>=2`, `python-dotenv`, `streamlit`, `pytest`,
`ruff`; ruff config; pytest config registering `llm` and `eval` markers so live tests are opt-in), `src/`
package layout with `__init__.py`, `tests/`, `README.md` run instructions, `.venv`.

Already done ahead of this milestone (needed to store the API key safely): `.gitignore`, `.env` (holds
`ANTHROPIC_API_KEY`, gitignored), `.env.example`. `src/llm/client.py` calls `load_dotenv()` once at import in
M3, so pytest, the eval runner and Streamlit all pick the key up identically — that is why `python-dotenv` is
in the dependency list.

**Verify:** `ruff check .` passes; `pytest` runs and collects zero tests without error;
`python -c "import anthropic; anthropic.Anthropic(api_key='x').messages.parse"` resolves (confirms the SDK
upgrade actually landed — the currently installed 0.66.0 fails this).

**Depends on:** nothing.

---

### M1 — Domain models and static data

**Adds:** the full Pydantic object model from SPEC §4 and the four static JSON files from SPEC §3.3, plus a
loader that validates them at import time.

**Files:** `src/models/{engagement,audit_objects,isa,feedback}.py`, `src/data/*.json` (five files, contents
copied verbatim from SPEC §3.3, including `risk_matrix.json`), `src/config/loader.py`.

Notes: `Assertion`, `RiskLevel` (`low|medium|high`), `EvidenceStrength`, and `ProcedureSource`
(`catalogue|ai_suggestion`) are `Literal`/`StrEnum` types defined once in `audit_objects.py` and reused by both
the domain models and the LLM output schemas — that is what gives the LLM bounded values for free.
`RiskAssessment` carries `system_rating`, `final_rating`, `is_overridden`, `override_reason` from the start.

**Verify:** `tests/test_config_loading.py` — all five files load and validate; exactly 8 line items,
2 audit area profiles, 3 ISA requirements, 7 catalogue procedures; every assertion named in the profiles and
catalogue is a member of the `Assertion` enum; every `audit_areas` value in the catalogue has a profile; the
risk matrix is complete (all 9 likelihood × magnitude combinations present, every value a valid `RiskLevel`).
Those last two checks are what catch config drift when audit areas are added later.

**Depends on:** M0.

---

### M2 — Materiality, derived metrics, scoping

**Adds:** the deterministic front half of the pipeline (SPEC §7, §8, §3.1).

**Files:** `src/engine/materiality.py` (`calculate_materiality`), `src/engine/scoping.py`
(`derive_metrics`, `scope_line_items`).

Materiality returns a `Materiality` object carrying benchmark used, rate, amount, and a
`"prototype methodology, not an ISA-prescribed formula"` label so the UI can display it (SPEC §7).
Derived metrics per line item: `yoy_change`, `yoy_change_pct`, `amount_to_materiality_ratio`. Scoping sets two
independent flags — `material` (CY amount > materiality, applied to all eight) and `is_audit_area` (a profile
exists). Pure functions, no mutation of inputs beyond the assignment the caller makes.

**Verify:** `tests/test_materiality.py` and `tests/test_scoping.py`:
- Raiatea materiality is exactly `262_000` (5.24m/52.4m = 10% > 5% → 5% × PBT).
- The other branch fires: a synthetic engagement with PBT/turnover ≤ 5% uses 0.5% × turnover.
- Boundary: ratio exactly 0.05 takes the turnover branch (`>` is strict).
- Inventory metrics: `+2_700_000`, `≈43.55%`, ratio `≈33.97`. Cash: `+230_000`, `≈7.96%`, ratio `≈11.91`.
  PPE change is negative (`-200_000`, `≈-4.17%`) — confirms sign handling.
- Divide-by-zero guard when a PY amount is 0.
- All 8 Raiatea line items come out material; exactly 2 (`cash`, `inventory`) have `is_audit_area=True`.
- **The below-threshold branch is covered by a fixture**, since no Raiatea item exercises it: a synthetic
  line item below materiality gets `material=False`. Also a fixture for the inverse case that the
  line-item/audit-area distinction exists to express — material **and** not an audit area.

**Depends on:** M1.

---

### M3 — LLM client, output schemas, and the test fake

**Adds:** the single seam through which every LLM call passes, and the fake that makes M4–M8 testable offline.

**Files:** `src/llm/client.py` (`LLMClient` protocol, `AnthropicLLMClient` wrapping
`client.messages.parse(model=..., output_format=<PydanticModel>)` → returns `response.parsed_output`,
`TASK_CONFIG`), `src/llm/schemas.py` (all five output models), `src/llm/prompts.py` (empty shell + system
prompt constants), `tests/fakes.py` (`ScriptedLLMClient` returning queued objects keyed by task name and
asserting it was called with the expected task).

**Verify:** `tests/test_llm_client.py` — schemas accept a valid payload and reject an out-of-range literal
(e.g. `likelihood="severe"`); the fake satisfies the protocol; `TASK_CONFIG` covers all five tasks. One live
smoke test marked `@pytest.mark.llm` (deselected by default) that does a trivial `messages.parse` round trip,
so credentials can be verified on demand without coupling the suite to the network.

**Depends on:** M1.

---

### M4 — Company fact extraction

**Adds:** `extract_company_facts()` — free-text context → `CompanyFact[]` with assigned IDs (SPEC §3.2).

**Files:** `src/llm/context_extractor.py`, prompt text in `prompts.py`, `CompanyFactOutput` in `schemas.py`.

Facts carry `fact_type`, `value`, `source="company_context"`, `rationale`, and an engagement-assigned `id`.
IDs are assigned by the service, not the model, so they are guaranteed unique and referenceable.

**Verify:** `tests/test_context_extractor.py` with the scripted fake — two facts returned get IDs `fact_1`,
`fact_2` and land on the engagement; empty context returns `[]` without calling the model; re-extraction after
a context edit replaces the previous set. Live behaviour is checked in M12.

**Depends on:** M3.

---

### M5 — Assertion relevance

**Adds:** `assess_assertions()` — candidate assertions from the profile → per-assertion relevance verdict with
rationale, supporting fact IDs, and ISA 315.29 linkage (SPEC §10).

**Files:** `src/llm/assertion_assessor.py`, prompt in `prompts.py`, `AssertionRelevanceOutput` in `schemas.py`.

The model receives only the bounded context SPEC §10 lists (line item type, amount, materiality, derived metrics,
raw context, facts, candidate assertions) — not the whole engagement. Returned assertions not in the candidate
list are rejected; missing candidates default to `relevant=False` with a "no verdict returned" rationale rather
than silently vanishing.

**Verify:** `tests/test_assertion_assessor.py` — fake returns verdicts for inventory's five candidates →
five `AssertionAssessment` objects, each with an ID, `line_item_id`, `isa_refs=["ISA315.29"]`, rationale, and
validated fact IDs; an unknown assertion in the model output is dropped; an unknown fact ID is dropped.

**Depends on:** M2, M4.

---

### M6 — Risk assessment

**Adds:** `assess_risks()` — up to two risks per relevant assertion, plus deterministic rating derivation
(SPEC §11).

**Files:** `src/engine/risk_matrix.py` (`derive_rating(likelihood, magnitude)` — pure, config-driven),
`src/llm/risk_assessor.py`, prompt, `RiskAssessmentOutput` in `schemas.py`.

`RiskAssessmentOutput` deliberately has **no** `risk_rating` field — the model returns `likelihood` and
`magnitude` only. `system_rating = derive_rating(...)`, and `final_rating` is initialised to the same value
with `is_overridden=False`. Nothing downstream ever reads `system_rating` (SPEC §11).

**Verify:** `tests/test_risk_matrix.py` (pure, no LLM) — all 9 combinations map as configured;
`high`/`high → high`, `low`/`low → low`, `low`/`high → medium`; an unknown level raises rather than defaulting;
swapping in a synthetic matrix changes the result with no code change (proves it is config, not constants).
`tests/test_risk_assessor.py` — fake output produces a `RiskAssessment` whose `system_rating` matches the
matrix rather than anything the model said, `system_rating == final_rating`, `is_overridden is False`,
`assertion_id` set, `isa_refs` containing `ISA315.28b_31`; two returned risks produce two objects; irrelevant
assertions are skipped without a call to the model.

**Depends on:** M5.

---

### M7 — Procedure catalogue filtering and selection

**Adds:** deterministic catalogue filtering plus `select_procedures()` (SPEC §12, §13).

**Files:** `src/engine/catalogue.py` (`filter_catalogue(audit_area, assertion)` — pure, no LLM),
`src/llm/procedure_selector.py`, prompt, `ProcedureSelectionOutput` in `schemas.py`.

The LLM only ever sees the filtered subset, so it cannot select a procedure that is wrong for the
audit area/assertion. A returned `procedure_id` outside that subset is rejected. An optional
`suggested_new_procedure` becomes a `Procedure` with `source="ai_suggestion"`, `approved=False`, and the
`AI SUGGESTION — AUDITOR APPROVAL REQUIRED` label (SPEC §13).

**Verify:** `tests/test_catalogue.py` (pure) — `("inventory", "valuation")` returns exactly
`INV_AGED_STOCK_REVIEW` and `INV_SUBSEQUENT_SALES`; `("cash", "valuation")` returns `[]`; filtering is
data-driven (test passes a synthetic catalogue and gets the right subset with zero code change).
`tests/test_procedure_selector.py` — fake selection creates `Procedure` objects with `risk_id`,
`isa_refs=["ISA330.6_7"]`, `source="catalogue"`; an out-of-subset ID is rejected; an AI suggestion is flagged
unapproved.

**Depends on:** M6.

---

### M8 — Pipeline orchestration

**Adds:** `run_pipeline()` — the exact SPEC §6 sequence, headless and importable.

**Files:** `src/engine/pipeline.py` (`load_engagement`, `run_pipeline`).

**Verify:** `tests/test_pipeline.py` with a fully scripted fake covering every call:
- All 8 line items get metrics and a material flag; only cash and inventory acquire assertion assessments
  (because only those two have profiles).
- The resulting object graph is fully linked: every `Procedure.risk_id` resolves to a risk, every
  `RiskAssessment.assertion_id` to an assertion, every assertion to a line item.
- Irrelevant assertions produce no risks; every relevant assertion produces at least one.
- The fake records call counts, proving one call per audit area / per relevant assertion / per risk — i.e. no
  giant single prompt.

This test's fixture becomes the shared engagement fixture for M9–M11.

**Depends on:** M2–M7.

---

### M9 — Traceability

**Adds:** the explicit forward chain of SPEC §14.

**Files:** `src/engine/traceability.py` — `trace_procedure(procedure, engagement)` returning a structured
`TraceChain` (procedure → risk → assertion → line item → supporting facts + metrics → ISA requirement IDs).

**Verify:** `tests/test_traceability.py` on the M8 fixture — tracing the inventory subsequent-sales procedure
yields the valuation assertion, the inventory line item, the seasonality fact, and the ISA chain
`ISA315.29 → ISA315.28b_31 → ISA330.6_7`. A procedure whose `risk_id` is dangling raises rather than returning
a partial chain. Deterministic, no LLM.

**Depends on:** M8.

---

### M10 — Reverse ISA coverage

**Adds:** `check_isa_coverage()` implementing the three MVP rules of SPEC §15 with `GAP` reporting.

**Files:** `src/engine/coverage.py`.

Rules, evaluated **only over audit areas** (SPEC §15 Coverage scope): material audit areas have assertion
assessments (ISA315.29); relevant assertions have risk assessments (ISA315.28b_31); risks have at least one
procedure (ISA330.6_7). Returns, per requirement, the list of addressing object IDs plus any gaps, and
separately a list of material non-audit-area line items labelled
`material — audit logic not implemented in MVP`.

**Verify:** `tests/test_coverage.py` — the M8 fixture reports **zero** gaps; the six non-audit-area line items
appear in the "not implemented" list and **not** as ISA315.29 gaps (this is the test that encodes the
decision — without it the panel would show six false gaps and bury the real one); deleting a risk's procedures
produces exactly one `ISA330.6_7` gap naming that risk ID and leaves the other two requirements clean.

**Depends on:** M8, M9.

---

### M11 — Overrides, downstream recomputation, auditor feedback

**Adds:** the auditor-control half of the product (SPEC §17, §18) — the milestone that makes the MVP more than
a one-shot generator.

**Files:** `src/engine/recompute.py`, `src/models/feedback.py` (already defined in M1, used here).

Explicit functions, one per override type, each recording an `AuditorFeedback` (`object_type`, `object_id`,
`before`, `after`, `reason`) before mutating, and each recomputing only its own subtree:

| Override | Recomputes |
| --- | --- |
| `override_risk_rating` | that risk's procedures, then coverage |
| `override_assertion_relevance` | true→false drops that assertion's risks/procedures; false→true generates them |
| `override_procedures` (add/remove/approve) | coverage only |
| `update_company_context` | re-extract facts, rerun cash + inventory pipeline |
| `update_financials` | materiality → scoping → full pipeline rerun |

**Verify:** `tests/test_recompute.py` — this is SPEC §22 Scenario D as a test:
- Override inventory valuation risk high → low: `system_rating` still `"high"`, `final_rating` `"low"`,
  `is_overridden` True, `override_reason` stored, procedure selection called again for that risk **only**.
- **Unrelated branches are untouched** — assert the cash subtree objects are the *same instances*
  (identity, not equality) before and after. This is the check that catches over-eager recomputation.
- An `AuditorFeedback` record exists with the correct before/after, and the original system output is
  recoverable from it.
- Assertion relevance true→false removes exactly that assertion's risks and procedures; flipping back
  regenerates them.

**Depends on:** M8, M10.

---

### M12 — Feedback → candidate methodology rule

**Adds:** `generalize_feedback()` — the separate learning pipeline of SPEC §19.

**Files:** `src/llm/feedback_generalizer.py`, prompt, `FeedbackClassificationOutput` in `schemas.py`
(a discriminated union over `engagement_specific` | `methodology_rule_proposal`).

Produces either nothing (engagement-specific) or a `RuleProposal` with `condition`, `action`, `reason`,
`source_feedback_id`, `status="pending_review"`. **It never mutates any static JSON file** — proposals are
runtime objects only, which is the point of SPEC §19.

**Verify:** `tests/test_feedback_generalizer.py` — both branches with the fake; the methodology branch yields a
`RuleProposal` with `status="pending_review"` linked to its feedback ID; an explicit assertion that
`procedure_catalogue.json`, `audit_area_profiles.json` and `risk_matrix.json` are byte-identical before and
after (proves no auto-update of methodology).

**Depends on:** M11.

---

### M13 — Eval scenarios

**Adds:** the SPEC §22 fixed scenarios, run against the live model, opt-in.

**Files:** `evals/scenarios.py` (contexts A/B, identical financials), `evals/run_evals.py`,
`evals/test_evals.py` marked `@pytest.mark.eval` (deselected by default; run with `pytest -m eval`).

Assertions are **comparative and ordinal**, never exact-string — that is what makes them stable against model
variation:
- A and B: valuation relevant in both.
- C: `rank(risk_B) > rank(risk_A)` for inventory valuation on identical numbers — the core claim of the product.
- B selects at least as many valuation procedures as A, and includes at least one `evidence_strength="high"`
  one (SPEC §22, ISA 330.7 responsiveness).
- D and E are already covered deterministically by M11/M12 tests; the eval runner re-runs them end to end.

The runner prints a side-by-side A/B table so prompt tuning has a fixed target (SPEC §22: fixtures before
tuning).

**Verify:** `pytest -m eval`. Uses the key in `.env` (Decision log #4).

**Depends on:** M8–M12.

---

### M14 — Streamlit UI

**Adds:** the auditor-facing surface (SPEC §16). Everything it shows already works and is already tested.

**Files:** `src/ui/app.py` — single file, sections rather than multipage unless it gets unwieldy.

Sections mirroring SPEC §16: company data + materiality (with the prototype-methodology label); scoped line
item table (all 8, showing metrics, material flag, and whether it is an audit area — with the
`material — audit logic not implemented in MVP` label where applicable); editable context with re-extract;
editable fact list; per-audit-area assertion cards with a relevance toggle and rationale; risk cards showing
likelihood, magnitude and the matrix-derived `system_rating` alongside `final_rating`, with an override
control and reason box;
procedure lists with add-from-catalogue / remove / approve-AI-suggestion; a traceability view for a selected
procedure; an ISA coverage panel; a feedback log with an "analyse for methodology rule" action and the
resulting pending proposals.

**Hard rule (AGENTS.md review criterion):** no domain logic in `app.py`. It reads session state and calls
`engine`/`llm` functions. Anything that looks like a calculation or a decision belongs in `engine/`.

**Verify:** `streamlit run src/ui/app.py` and walk the demo path manually: load → inspect materiality →
edit context → observe assertion/risk change → override an inventory risk high→low → see procedures update and
cash left untouched → inspect traceability → inspect coverage → generate a rule proposal. Everything
pre-populated, no blank forms (SPEC §16). Automated coverage stays in M11–M13; the UI is verified by walkthrough.

**Depends on:** M11, M12 (M13 optional).

---

## Decision log

All open questions were resolved on 2026-08-21 and folded into `SPEC.md`, which remains the source of truth.
This log records what was decided and why, so the reasoning is not lost.

**1. Terminology — "balance" replaced by line item / audit area.** *(SPEC §2.1, §8)*
The materiality threshold applies to all eight supplied items, as the case implies. But they are not all
balances: turnover is a P&L item, and `profit_before_tax` is simultaneously a supplied item and the
materiality benchmark. The runtime object is therefore `FinancialLineItemAssessment`, and an **audit area** is
a line item with an implemented profile. `material` and `is_audit_area` are independent flags; the pipeline
requires both. Below-threshold behaviour is covered by fixtures, since no Raiatea item exercises it.

**2. ISA coverage is evaluated only over audit areas.** *(SPEC §15)*
A `GAP` means missing work inside the scope the MVP claims to support. Material line items without
implemented methodology are reported separately as `material — audit logic not implemented in MVP`. Reporting
the six non-implemented Raiatea items as gaps would bury the one real gap the feature exists to surface.

**3. Risk rating is derived deterministically, not returned by the model.** *(SPEC §11, §3.3)*
The LLM returns `likelihood` and `magnitude`; `system_rating` comes from an explicit 3×3 matrix in
`risk_matrix.json`. Kept as methodology config rather than prompt logic or Python constants, so a methodology
owner can change risk appetite without touching the engine. This also makes an incoherent `low`/`low → high`
structurally impossible and makes ratings reproducible for identical inputs.

**4. LLM credentials.** Key supplied, validated against the Models API, stored in a gitignored `.env`;
`claude-opus-5` confirmed available. The scripted fake stays test-only — no offline canned-response mode for
the UI.

**5. Minor decisions, all approved:**
- **Up to two distinct risks per assertion** — the prompt asks for the single most significant risk and a
  second only where genuinely distinct.
- **CY amount is used for scoping.**
- **`CompanyFact` extraction is implemented**, not skipped. SPEC §3.2 calls it optional, but
  `supporting_fact_ids` appear in every downstream schema and in the SPEC §14 traceability chain.
- **Overrides recompute on an explicit apply/regenerate action**, not on every widget change — Streamlit
  reruns on every interaction and LLM calls are paid. Stale downstream sections are badged until recomputed.
- **No persistence.** Session state only (SPEC §2 excludes a database).
- **Auditor-added procedures come from the filtered catalogue picker**, so `procedure_id` always resolves.

---

## End-to-end verification

Per milestone:

```bash
ruff check .
pytest                 # deterministic suite; no network, no API key
```

Opt-in suites:

```bash
pytest -m llm          # live SDK smoke test
pytest -m eval         # SPEC §22 scenarios A–E against the live model
python evals/run_evals.py   # prints the A/B comparison table
```

Full demo:

```bash
streamlit run src/ui/app.py
```

The MVP is done when the SPEC §25 checklist passes: Raiatea loads, materiality is £262k, cash and inventory
traverse one generic pipeline, context change moves risk (M13 Scenario C), risk level moves procedure choice,
procedures trace back to ISA requirements and forward from them, overrides preserve system output and
recompute only downstream, and one override becomes a pending `RuleProposal`.

---

## Explicitly out of scope

Everything in SPEC §2 and §26: auth, database, deployment, ERP/file parsing, full ISA coverage, significant-risk
classification, control-risk modelling, automatic methodology updates, additional balances, FastAPI, LangChain
or any agent framework, and any production infrastructure. New capability beyond this plan gets raised before
it gets built.
