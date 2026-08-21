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
│   │   ├── formatting.py        # shared prompt fragments
│   │   ├── audit_area_analyser.py  # relevance + risks, 1 call per area
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
2. **The audit area is the bounded unit of LLM reasoning (SPEC §6.1).** Exactly two calls per material audit
   area — one to analyse it (relevance + risks + likelihood/magnitude), one to select procedures for all its
   risks — plus one fact extraction per engagement. Calls scale with audit areas, not with assertions or
   risks: 5 calls for a cash + inventory run. Never one prompt for the whole audit, and never a prompt
   spanning two areas (SPEC §21).
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
   dependency, which is what lets the M13 evals and M4–M12's tests run it directly. The UI only calls into it.
8. **Risk rating is derived, never asked for (SPEC §11).** The LLM returns `likelihood` and `magnitude` only;
   `RiskAssessmentOutput` has no `risk_rating` field, so the model cannot supply one. `engine/risk_matrix.py`
   maps the pair to `system_rating` through `risk_matrix.json`. The matrix is config, not Python constants and
   not prompt text, so a methodology owner can change risk appetite without touching the engine.

---

## Milestones

Each milestone ends with `ruff check .` and `pytest` green. Milestones are ordered so the whole engine works
headlessly (M0–M12) before the UI (M14) is written. The one exception to strict linear order is M13a, which
sits right after the pipeline so live judgement feedback arrives while prompts are still being tuned.

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

### M5 — Deterministic risk matrix

**Adds:** `derive_rating(likelihood, magnitude)` — the likelihood × magnitude → rating mapping (SPEC §11).
Pure and config-driven; no LLM.

**Files:** `src/engine/risk_matrix.py`.

Sits before the analysis milestone because the analysis step derives ratings inside itself. The matrix lives in
`risk_matrix.json`, so changing risk appetite never touches code.

**Verify:** `tests/test_risk_matrix.py` — all 9 combinations map as configured; `high`/`high → high`,
`low`/`low → low`, `low`/`high → medium`; an unknown level raises rather than defaulting; **injecting an
inverted matrix flips every rating**, which is what proves the rating is configuration rather than constants.

**Depends on:** M1.

---

### M6 — Audit area analysis (assertion relevance + risks, one call)

**Adds:** `analyse_audit_area()` — **one** LLM call per audit area returning relevance verdicts for every
candidate assertion *and* the risks nested under each relevant one, plus deterministic rating derivation
(SPEC §6.1, §10, §11).

**Files:** `src/llm/audit_area_analyser.py`, `ANALYSE_AUDIT_AREA` prompt in `prompts.py`,
`AuditAreaAnalysisOutput` in `schemas.py`, `src/llm/formatting.py` for the shared prompt fragments.

The model receives only the bounded context SPEC §10 lists for **one** audit area — never the whole engagement,
never a second area. Post-processing is where the guarantees live:

- verdicts outside the candidate list are discarded; missing candidates default to `relevant=False` with an
  explicit "no verdict" rationale, so M10 can tell a considered rejection from an unexamined assertion;
- risks attached to a non-relevant assertion are discarded (SPEC §10);
- risks are capped at two per assertion — `maxItems` is not API-enforced, so this is applied in code;
- `system_rating = derive_rating(...)` from M5; `final_rating` starts equal, `is_overridden=False`;
- `supporting_fact_ids` are validated against the engagement's facts and dangling ones dropped.

**Verify:** `tests/test_audit_area_analyser.py` — five candidates produce five `AssertionAssessment` objects
with IDs, `line_item_id`, `isa_refs=["ISA315.29"]`; nested risks carry `assertion_id` and
`isa_refs=["ISA315.28b_31"]`; **exactly one call for the whole area** whatever the assertion/risk count;
`system_rating` follows an injected matrix rather than anything the model said; risks on a non-relevant
assertion are dropped; unknown assertions, unknown fact IDs and blank descriptions are rejected; immaterial or
non-audit-area line items make no call.

**Depends on:** M2, M4, M5.

---

### M7 — Procedure catalogue filtering and per-area selection

**Adds:** deterministic catalogue filtering plus `select_procedures()` — **one** LLM call per audit area
covering every risk in that area (SPEC §6.1, §12, §13).

**Files:** `src/engine/catalogue.py` (`filter_catalogue(audit_area, assertion)` — pure, no LLM),
`src/llm/procedure_selector.py`, `SELECT_PROCEDURES` prompt, `ProcedureSelectionOutput` in `schemas.py`.

The call receives every assessed risk in the area — each with its **risk ID**, assertion, description and
**`final_rating`** — plus the catalogue subset for that area. Using `final_rating` is what lets a risk-rating
override be answered by re-running this call alone (SPEC §17).

Each returned procedure names the `risk_ids` it addresses, and becomes **one** runtime `Procedure` carrying
that list — never one copy per risk. Procedures are attached to `FinancialLineItemAssessment.procedures`;
`procedures_for(risk_id)` resolves the relationship in the other direction for traceability and coverage.

Rejected rather than stored: a `procedure_id` outside the area's catalogue subset; a `risk_id` that is not one
of the area's risks; a `risk_id` whose **assertion is not one the catalogue entry addresses** (an
existence-only procedure cannot answer a valuation risk — storing that link would make M10 report the risk as
covered while approved methodology says otherwise); and a procedure left with no valid risk IDs
(`Procedure.risk_ids` has `min_length=1`, so an empty one cannot be constructed). Unusable IDs are dropped
from an otherwise-valid procedure; it is discarded only if that empties the list. AI suggestions are not
assertion-constrained — they have no catalogue mapping to contradict.

**Partial catalogue coverage.** When an area has risks but *no* catalogue procedure covers their assertions,
the call is still made with an explicit "no approved procedures match" catalogue section, and only AI
suggestions can survive it. SPEC §13 permits a suggestion precisely in that situation, so short-circuiting
would make the feature unreachable in the one case it exists for. The shipped config happens to cover every
candidate assertion, but validation only requires one procedure per *area*, so this is reachable on extension.

`suggested_new_procedures` become `Procedure` objects with `source="ai_suggestion"`, `approved=False` and the
`AI SUGGESTION — AUDITOR APPROVAL REQUIRED` label (SPEC §13).

**Re-selection clears first.** Attaching must replace the area's procedures wholesale, never append, or a
rerun would leave procedures pointing at risks from the previous run. `dangling_risk_ids()` asserts this in
tests.

**Verify:** `tests/test_catalogue.py` (pure) — `("inventory", "valuation")` returns `INV_COST_TEST`,
`INV_AGED_STOCK_REVIEW` and `INV_SUBSEQUENT_SALES` (three, not two: `INV_COST_TEST` covers accuracy *and*
valuation); `("cash", "valuation")` returns `[]`; filtering is data-driven (a synthetic catalogue yields the
right subset with zero code change).
`tests/test_procedure_selector.py` — **exactly one call per area** whatever the risk count; a procedure naming
two `risk_ids` produces **one** `Procedure` reachable from both risks via `procedures_for`; each has
`isa_refs=["ISA330.6_7"]` and `source="catalogue"`; out-of-subset procedure IDs and unknown risk IDs are
rejected; an AI suggestion is flagged unapproved; re-selection replaces rather than appends and leaves
`dangling_risk_ids()` empty; the prompt carries `final_rating`, not `system_rating`.

**Depends on:** M6.

---

### M8 — Pipeline orchestration

**Adds:** `run_pipeline()` — the exact SPEC §6 sequence, headless and importable.

**Files:** `src/engine/pipeline.py` (`load_engagement`, `run_pipeline`, `run_area`, `clear_area`),
`src/demo.py`.

`run_area` is the unit M11 re-runs: it **clears the area's procedures before re-analysing**, because
re-analysis replaces the risks those procedures name. Without that, a failure between the two calls would
leave procedures pointing at risks that no longer exist.

`clear_area` is the other half of SPEC §17's rescoping rule. A line item that is no longer *both* material and
an audit area has its assertions, risks and procedures dropped rather than merely skipped — otherwise a rerun
after a PBT change would leave out-of-scope work in place for traceability and coverage to render as live.
Metrics and `is_audit_area` are kept, since the line item is still displayed. Deterministic, no LLM call.

`src/demo.py` (`python -m src.demo`) runs the whole pipeline against Raiatea and prints the audit plan —
materiality, the scoped line items, extracted facts, and per area the assertions, risks with
likelihood/magnitude/derived rating, and every procedure with its risk links. It is how the engine is
inspected end to end before the UI exists, and the base M13a's `run_evals.py` builds on. Presentation only:
no domain logic.

**Verify:** `tests/test_pipeline.py` with a fully scripted fake covering every call:
- All 8 line items get metrics and a material flag; only cash and inventory acquire assertion assessments
  (because only those two have profiles).
- The resulting object graph is fully linked: every `Procedure.risk_id` resolves to a risk, every
  `RiskAssessment.assertion_id` to an assertion, every assertion to a line item.
- Irrelevant assertions produce no risks; every relevant assertion produces at least one.
- **The fake records exactly 5 calls** for the Raiatea run: 1 fact extraction + 2 area analyses + 2 procedure
  selections. This is the SPEC §6.1 budget, and the test is what stops it regressing toward per-assertion or
  per-risk calls as prompts are tuned.
- No call's user message mentions more than one audit area.
- **Rescoping clears what it descopes**: after a rerun that pushes cash below materiality, its assertions and
  procedures are gone, no call was spent on it, and inventory is still analysed.

This test's fixture becomes the shared engagement fixture for M9–M11.

**Depends on:** M2–M7.

---

### M9 — Traceability

**Adds:** the explicit forward chain of SPEC §14.

**Files:** `src/engine/traceability.py` — `trace_procedure(procedure, engagement)` returning **one
`TraceChain` per entry in `procedure.risk_ids`** (procedure → risk → assertion → line item → supporting facts
+ metrics → ISA requirement IDs). A procedure addressing several risks fans out into several complete chains;
that is a property of the audit, not a gap (SPEC §14).

The chain holds the runtime objects, not copies of their fields, so a UI reading
`chain.risk.final_rating` after an M11 override sees the override rather than a stale snapshot. The
`procedure` argument only *identifies* what to trace: every link is read from the engagement's own copy, so a
deserialised or edited object with the same ID cannot report links the audit file does not contain.

**Verify:** `tests/test_traceability.py` on the M8 fixture — tracing the inventory subsequent-sales procedure
yields the valuation assertion, the inventory line item, the aged-stock fact, the area's metrics, and the ISA
chain `ISA315.29 → ISA315.28b_31 → ISA330.6_7`. A procedure naming two risks yields two chains, each complete
and differing only from the risk upward. A dangling `risk_id`, `assertion_id` or `fact_id` raises rather than
returning a partial chain — including when the *other* risks on the same procedure resolve fine, since
returning the chains that happen to work would hide the broken one. Identity assertions confirm the chain
aliases live state. Deterministic, no LLM.

The M8 scripted run moves from `test_pipeline.py` into `conftest.py` here, as the `engagement` fixture: M9–M11
all need a completed, fully linked engagement and none of them should be scripting LLM output to get one.

**Depends on:** M8.

---

### M10 — Reverse ISA coverage

**Adds:** `check_isa_coverage()` implementing the three MVP rules of SPEC §15 with `GAP` reporting.

**Files:** `src/engine/coverage.py`.

Rules, evaluated **only over audit areas** (SPEC §15 Coverage scope): material audit areas have assertion
assessments (ISA315.29); relevant assertions have risk assessments (ISA315.28b_31); risks have at least one
procedure — resolved via `procedures_for(risk.id)`, since procedures live on the area (ISA330.6_7). Returns,
per requirement, the list of addressing object IDs plus any gaps, and separately a list of material
non-audit-area line items labelled `material — audit logic not implemented in MVP`.

Three rules follow from reading SPEC §15 against the rest of the spec, all recorded in SPEC §15:

- **An object counts only where it records the requirement in its own `isa_refs`** — the same links M9 walks
  forward. The object type selects the rule; it does not decide whether the requirement is addressed. Without
  this, adding a requirement to `isa_requirements.json` would mark it covered by work that never referenced
  it, and work that lost its reference would still read as coverage. A new requirement is picked up by
  re-running the area, not by editing config.

- **Coverage follows pipeline scope**, so material-but-not-implemented and implemented-but-immaterial are both
  outside it. A descoped area is cleared (SPEC §17), so evaluating it would report gaps for work the
  engagement deliberately dropped.
- **An unapproved AI suggestion does not close an ISA330.6_7 gap.** SPEC §13 says a suggestion will not be used
  without approval, so counting one would report a risk as answered by work not yet in the plan. It still
  appears in `addressed_by`, so the panel can show a proposed response waiting on a decision.

**Verify:** `tests/test_coverage.py` — the M8 fixture reports **zero** gaps; the six non-audit-area line items
appear in the "not implemented" list and **not** as ISA315.29 gaps (this is the test that encodes the
decision — without it the panel would show six false gaps and bury the real one); removing a risk ID from the
procedures that cover it produces exactly one `ISA330.6_7` gap naming that risk and leaves the other two
requirements clean, **including for the other risks the same procedure still covers**; a shared procedure is
listed once, not once per risk; an unapproved suggestion gaps while an approved one does not; a non-relevant
assertion with no risks is not a gap; stripping `isa_refs` at any one level gaps that requirement and leaves
the other two clean; adding a fourth requirement leaves an existing engagement *uncovered* until the pipeline
is re-run, at which point it is covered with no code change; and an object type with no dispatch entry raises
instead of silently reporting full coverage.

The `two_risk_engagement` fixture moves from `test_traceability.py` into `conftest.py` here — it is the fixture
for anything that must not treat a procedure as answering exactly one risk, which is true of coverage as well
as traceability.

**Depends on:** M8, M9.

---

### M11 — Overrides, downstream recomputation, auditor feedback

**Adds:** the auditor-control half of the product (SPEC §17, §18) — the milestone that makes the MVP more than
a one-shot generator.

**Files:** `src/engine/recompute.py`, `src/models/feedback.py` (already defined in M1, used here).

Explicit functions, one per override type, each recording an `AuditorFeedback` (`object_type`, `object_id`,
`before`, `after`, `reason`) before mutating, and each recomputing only its own dependency subtree.

**Call scope and mutation scope are deliberately different.** Audit-area analysis remains an area-level LLM
call, but procedure updates must be as narrow as the changed risk relationship permits. A scoped procedure
selection call accepts the changed `risk_id` (or a set of changed IDs), then merges its result into the existing
area procedure list. Procedures whose `risk_ids` are disjoint from that set retain their existing objects and
links. A procedure that also addresses a changed risk is in the affected closure: it may be updated, but its
still-valid links to other risks must be preserved. This prevents an override in one assertion from silently
replacing procedure work for unrelated assertions in the same audit area.

| Override | Recomputes | LLM calls |
| --- | --- | --- |
| `override_risk_rating` | scoped procedure selection for that risk's affected procedure closure, merged into the area list, then coverage. Never re-analyses: `likelihood`, `magnitude` and `system_rating` are the original conclusion and must survive | 1 |
| `override_assertion_relevance` → not relevant | drops that assertion's risks; detaches only those risk IDs from procedures, retaining a procedure while it still covers other risks; then coverage | 0 |
| `override_assertion_relevance` → relevant | re-analyses that area, then reselects its procedures. **Replaces every assertion and risk in the area**, discarding overrides held on them — the UI must warn first | 2 |
| `override_procedures` (add/remove/approve) | coverage only. "Remove" means detaching one `risk_id`; the procedure survives if it still covers others, and is dropped only when its last reference goes | 0 |
| `update_company_context` | re-extract facts, then both calls for every audit area | 1 + 2n |
| `update_financials` | materiality → scoping → both calls for any area entering or leaving scope | ≤ 2n |

The risk-rating row is the one that matters most: it is the common override, it is Scenario D, and routing it
around re-analysis is what keeps the original system output intact. It must also preserve procedure objects
whose risk links are unrelated to the override; the test suite checks identity, not merely equality, for those
objects.

**Verify:** `tests/test_recompute.py` — this is SPEC §22 Scenario D as a test:
- Override inventory valuation risk high → low: `system_rating` still `"high"`, `final_rating` `"low"`,
  `is_overridden` True, `override_reason` stored, one scoped procedure-selection call made for that risk.
- **Unrelated procedure work is untouched** — assert procedures whose `risk_ids` are disjoint from the changed
  risk are the *same instances* (identity, not equality) before and after, including where they sit in the same
  audit area. A shared procedure retains its still-valid links to other risks.
- **Unrelated audit areas are untouched** — assert the cash subtree objects are the same instances before and
  after. These identity checks catch over-eager recomputation at both levels.
- An `AuditorFeedback` record exists with the correct before/after, and the original system output is
  recoverable from it.
- Assertion relevance true→false removes exactly that assertion's risks and procedures; flipping back
  regenerates them.

**As built.** `select_procedures` gained a `risk_ids` argument: the scoped call shows the model only
those risks and narrows the catalogue subset to their assertions, so it can neither answer nor
re-link work the override never touched. `_merge_procedures` folds the result back — a procedure
loses only the changed risk IDs, survives on its remaining ones, and is dropped only when the
changed risks were its whole reason to exist; a fresh selection naming a catalogue entry the area
already holds re-links that same object rather than creating a second.

Every LLM-backed override is all-or-nothing: `run_area` restores the area if either of its calls
fails, `override_risk_rating` puts the rating back if selection fails, and the two engagement-wide
recomputes snapshot and restore via `_capture`/`_restore`. Feedback is appended only after the
recompute succeeds. The ID counter is deliberately not rolled back (SPEC 14).

`AuditEngagement.feedback` holds the append-only log. An override that changes nothing returns
`None`, records nothing and makes no call. `is_overridden` is derived from
`final_rating != system_rating`, so reverting to the system rating clears the marker while both
moves stay in the log. Coverage is deliberately **not** stored or returned by
these functions: `check_isa_coverage` is a pure read over live state, so there is nothing to
invalidate and callers ask for a current report when they want one.

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

**As built.** Eligibility is deterministic: `is_analysable` admits only assertion, risk and procedure
overrides, and the two engagement-input records raise instead — revised source data is new input,
not a judgement, and what follows from it is M11's dependency logic. The engagement context rung of
the prompt is read from `AuditorFeedback.engagement_context`, snapshotted by `record_feedback` when
the override was made, so a later re-extraction cannot reach a proposal attributed to an earlier
judgement. The record is resolved from `engagement.feedback` by ID, so a same-ID copy cannot have a
proposal filed against the real record while the model was shown something else.

`AuditEngagement.rule_proposals` holds the filed proposals; unlike the pipeline's
outputs they accumulate rather than replace, since each refers to a different override. Re-analysing
a feedback record that already has a proposal returns it without spending a call. A returned rule
with a blank condition or action is dropped — one applies always, the other asks for nothing, and a
reviewer has nothing to approve either way. The prompt carries only the four SPEC 19 inputs, so no
rule can be drawn from work the auditor never commented on, and a record whose object has since been
replaced still generalises from its `before`/`after` snapshot (SPEC 18).

**Depends on:** M11.

---

### Two kinds of live test

Both are opt-in and deselected by default; they answer different questions and fail for different reasons.

| Marker | Question | Where |
| --- | --- | --- |
| `llm` | Does the **plumbing** work — structured output round-trips, enums enforced, IDs referenceable? | Alongside each service's unit tests, added as that service is built (M3–M7) |
| `eval` | Is the model's **judgement** sensible and responsive to company context? | `evals/`, M13a and M13b |

A green `llm` suite with a red `eval` suite means the prompts are wrong, not the code.

---

### M8a — Grounding the LLM layer (done, out of sequence)

**Adds:** the SPEC §21.1 epistemic rules and the SPEC §22 Scenario F eval that enforces them. Prompted by the
first end-to-end Raiatea run, which produced audit-plausible fiction: store floats, card settlements in transit,
third-party logistics providers and stock controls that had "not kept pace with expansion", none of them
supplied. Extraction was leaking too — `growth_profile = fast-growing` came back with the rationale *"which may
strain controls and complicate comparability"*, turning a judgement into a cited fact.

Prompt, input-context and eval work only. No change to the four services, the 5-call budget or the domain model.

**Files:** `src/llm/prompts.py` (all five prompt constants), `src/llm/schemas.py` (`CompanyFactOutput` field
descriptions), `src/data/raiatea.json` (richer context), `evals/scenarios.py`,
`evals/test_unsupported_inference.py`, `tests/test_eval_helpers.py`.

Two halves that only work together:

- **Stricter rules.** The shared preamble names the four kinds of information (SPEC §21.1); extraction takes
  literal statements only; analysis may use a generic mechanism but not invent the company-specific cause, and
  may not rule an assertion out from silence; selection adds no facts; the generalizer does not reconstruct
  reasoning behind a thin reason.
- **Richer context.** Forbidding inference-from-silence with a two-sentence context would produce bland
  everything-is-relevant output. The shipped context now describes both areas, including explicit negatives
  ("no restricted cash balances"), which is what makes ruling an assertion out legitimate again. Its two
  control facts — a year-end count, monthly reconciliations — are supplied because they bear on procedure
  feasibility, and the preamble's inherent-before-controls rule keeps them from lowering an assessed risk.

**Verify:** `tests/test_eval_helpers.py` tests the scanners offline — an eval is only informative if the thing
doing the checking is known to work, and a false negative would make Scenario F pass while asserting nothing.
`evals/test_unsupported_inference.py` runs two live scenarios (the short context, kept as a fixture for being
the highest-pressure case, and the shipped one): no unsupplied circumstances at any stage, no ruling out from
silence, no cross-area fact citation, and inventory valuation still relevant, still ≥ medium, still drawing
procedures.

**Depends on:** M8.

---

### M13a — Scenario fixtures and context-sensitivity evals

**Adds:** the SPEC §22 A/B/C scenarios against the live model. Deliberately placed straight after the
pipeline, because SPEC §22 opens with *"create fixed scenarios before aggressively tuning prompts"* — prompts
written in M4–M7 otherwise get no judgement feedback until the end of the build.

**Files:** `evals/scenarios.py` (already exists from M8a — contexts A and B over identical financials are
added to it), `evals/run_evals.py`, `evals/test_context_sensitivity.py` marked `@pytest.mark.eval`.

Assertions are **comparative and ordinal** wherever possible — exact-string matching on model prose would be
brittle — but comparative alone is not enough, so each is paired with an absolute bound:

| Check | Assertion |
| --- | --- |
| Context moves risk (SPEC §22 C) | `rank(risk_B) > rank(risk_A)` for inventory valuation on identical numbers — the core product claim |
| Scale has not collapsed | B's inventory valuation risk is `medium` or `high`; A's is not `high` |
| Assertion relevance is sensible | valuation relevant in both; **and** A's count of relevant assertions ≤ B's |
| Procedures respond to context | B selects at least as many valuation procedures as A, including at least one `evidence_strength="high"` (ISA 330.7) |

The absolute bounds exist because ordering alone is satisfiable by a degenerate model: `A=low, B=medium`
passes `rank(B) > rank(A)`, but so would a collapsed scale where aged seasonal inventory is rated `low`. The
relevant-assertion count catches the opposite failure — a model that marks everything relevant.

`run_evals.py` prints a side-by-side A/B table so prompt tuning has a fixed target to read.

**As built.** A and B share an identical cash paragraph as well as identical financials. SPEC §22
describes only the inventory narrative, but leaving cash unsaid in both would put every run under
the Scenario F pressure and make any difference in the cash area noise rather than signal. The
scenario runs moved into `evals/conftest.py` as **session** fixtures — four runs, twenty calls,
shared by every eval module — and `scenarios.fresh()` hands out deep copies to anything that
mutates, so one eval's override cannot become another's starting state.

**Verify:** `pytest -m eval`. Uses the key in `.env` (Decision log #4).

**Depends on:** M8.

---

### M13b — Override and feedback evals

**Adds:** SPEC §22 D/E end to end, plus the one claim no cross-scenario comparison can evidence.

**Files:** `evals/test_risk_response.py`, `evals/test_feedback_evals.py`, both marked `@pytest.mark.eval`.

- **Risk level drives procedure selection, isolated from context** (SPEC §25.8). Same scenario, same context;
  override inventory valuation risk `high → low` and re-run selection. The procedure set must weaken — fewer
  procedures, or losing its `evidence_strength="high"` member. M13a varies context and risk together, so it
  cannot separate the two; this holds context fixed and moves only the rating.
- **D**: the override path end to end — `system_rating` retained, `final_rating` updated, procedures reselected,
  unrelated areas untouched.
- **E**: a generalizable override is classified and produces a `pending_review` `RuleProposal`, with the static
  JSON files unchanged.

D and E are already covered deterministically in M11/M12 against the fake; these run the same paths against the
live model.

**As built.** E is checked in both directions. A classifier that always proposes a rule would pass the
generalisable case, so a second call puts a deliberately one-off reason through the same path and asserts
nothing is proposed — that pair is the evidence the classifier discriminates. D also checks the *shape* of
what a live re-selection returned: every procedure traces, no risk ID dangles, and coverage is clean, which
is the failure a schema-valid but half-linked response would otherwise pass.

**Verify:** `pytest -m eval`.

**Depends on:** M11, M12.

---

### Eval flakiness policy

Evals call a non-deterministic model and are **advisory, not a gate**. They are excluded from the default
`pytest` run and are not a CI blocker. A failure means read the printed A/B table and judge whether the prompt
regressed or the model simply phrased things differently — never auto-retry until green, which would tune the
suite rather than the prompts. If a check proves genuinely unstable across runs, loosen that check to an
ordinal comparison or drop it; do not add retries.

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

**Depends on:** M11, M12 (the M13 evals are independent of the UI).

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
pytest -m llm          # plumbing: the seam works against the live API
pytest -m eval         # judgement: SPEC §22 scenarios A–E against the live model (advisory)
python evals/run_evals.py   # prints the A/B comparison table for prompt tuning
```

Neither runs by default. `llm` failing means the integration broke; `eval` failing means the prompts regressed
— read the A/B table rather than retrying.

Full demo:

```bash
streamlit run src/ui/app.py
```

The MVP is done when the SPEC §25 checklist passes: Raiatea loads, materiality is £262k, cash and inventory
traverse one generic pipeline, context change moves risk (M13a Scenario C), risk level moves procedure choice
independently of context (M13b),
procedures trace back to ISA requirements and forward from them, overrides preserve system output and
recompute only downstream, and one override becomes a pending `RuleProposal`.

---

## Explicitly out of scope

Everything in SPEC §2 and §26: auth, database, deployment, ERP/file parsing, full ISA coverage, significant-risk
classification, control-risk modelling, automatic methodology updates, additional balances, FastAPI, LangChain
or any agent framework, and any production infrastructure. New capability beyond this plan gets raised before
it gets built.
