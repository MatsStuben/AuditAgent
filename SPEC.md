# Audit Planning Engine — MVP Specification

## 1. Objective

Build a local prototype that takes:

1. company financial numbers, and
2. a small amount of unstructured company context,

and generates a proposed audit plan for selected audit areas.

The prototype should demonstrate:

- deterministic materiality and line item scoping,
- company-specific assertion selection,
- assertion-level risk assessment,
- risk-responsive procedure selection,
- explicit ISA traceability,
- auditor review and override,
- capture of auditor feedback as candidate future methodology rules.

Core principle:

> Use deterministic logic where the decision is known and repeatable.  
> Use LLM reasoning where company-specific judgement is genuinely required.  
> Keep the auditor in control.

---

## 2. Scope

Implement deeply for:

- Cash
- Inventory

The engine architecture must be generic enough that additional audit areas can be added later without redesigning the core pipeline.

Do **not** implement:

- authentication,
- database,
- deployment,
- ERP/file parsing,
- full ISA 315/330 coverage,
- full audit workflow,
- automatic methodology updates from auditor feedback,
- every possible line item or assertion.

### 2.1 Terminology

**Financial line item** — any of the eight figures supplied in `raiatea.json`, including the P&L items
(`turnover`, `profit_before_tax`) and `net_assets`. All eight are loaded, receive derived metrics, and receive
a deterministic `material` flag.

**`FinancialLineItemAssessment`** — the runtime object holding that state. It is deliberately *not* called
`BalanceAssessment`: turnover is a P&L item, and `profit_before_tax` is simultaneously a supplied line item and
the materiality benchmark, so "balance" would be inaccurate for three of the eight.

**Audit area** — a financial line item for which implemented audit methodology exists, i.e. one that has an
entry in `audit_area_profiles.json`. In the MVP the audit areas are exactly `cash` and `inventory`. Only audit
areas continue through the assertion → risk → procedure pipeline.

A line item can therefore be **material without being an audit area**. That is a supported and explicitly
displayed state, not a gap — see Section 15.

---

## 3. Inputs

### 3.1 Financial data

Use the supplied Raiatea Ltd dataset.

```json
{
  "company": "Raiatea Ltd",
  "yearEnd": "2025-12-31",
  "turnover":           { "cy": 52400000, "py": 47100000 },
  "profitBeforeTax":    { "cy":  5240000, "py":  4850000 },
  "inventories":        { "cy":  8900000, "py":  6200000 },
  "tradeDebtors":       { "cy": 11300000, "py": 10900000 },
  "cash":               { "cy":  3120000, "py":  2890000 },
  "tradeCreditors":     { "cy":  7400000, "py":  7100000 },
  "propertyPlantEquip": { "cy":  4600000, "py":  4800000 },
  "netAssets":          { "cy": 21200000, "py": 17800000 }
}
```

For each financial line item derive at least:

- absolute YoY change,
- YoY percentage change,
- amount / materiality ratio.

### 3.2 Company context

Provide an editable free-text input.

Example:

> Raiatea is a fast-growing fashion retailer. Inventory is highly seasonal and a meaningful share of inventory is more than 12 months old.

Keep the raw context.

Optionally extract structured `CompanyFact` objects from it using an LLM.

Example:

```json
{
  "fact_type": "inventory_seasonality",
  "value": "high",
  "source": "company_context",
  "rationale": "The company describes its inventory as highly seasonal."
}
```

Auditors should be able to correct extracted facts.

### 3.3 Static files and concrete starting instances

Not every Python class gets its own JSON file.

Use this split:

```text
Python / Pydantic classes
→ define schemas and runtime state

JSON files
→ hold static input and methodology/configuration

Runtime-generated objects
→ live in memory / Streamlit session state
```

The MVP starts with exactly five static JSON files.

#### `raiatea.json`

Contains one engagement input with exactly the eight financial line items supplied in the case:

```json
{
  "company": "Raiatea Ltd",
  "year_end": "2025-12-31",
  "line_items": [
    {"type": "turnover", "cy": 52400000, "py": 47100000},
    {"type": "profit_before_tax", "cy": 5240000, "py": 4850000},
    {"type": "inventory", "cy": 8900000, "py": 6200000},
    {"type": "trade_debtors", "cy": 11300000, "py": 10900000},
    {"type": "cash", "cy": 3120000, "py": 2890000},
    {"type": "trade_creditors", "cy": 7400000, "py": 7100000},
    {"type": "property_plant_equipment", "cy": 4600000, "py": 4800000},
    {"type": "net_assets", "cy": 21200000, "py": 17800000}
  ]
}
```

All eight become runtime `FinancialLineItemAssessment` objects and receive derived metrics plus a deterministic `material: bool`. The materiality threshold is applied uniformly to all eight.

Only `cash` and `inventory` are audit areas and continue through the full assertion → risk → procedure pipeline in the MVP. The other six are still loaded, scoped and shown in the UI, labelled `material — audit logic not implemented in MVP` where they exceed materiality.

`profit_before_tax` is both an assessed line item and the benchmark used for the initial materiality calculation. This dual role is expected and is not special-cased.

On the Raiatea numbers every one of the eight items happens to exceed materiality, so the below-threshold branch of scoping is covered by test fixtures rather than by the case data.

#### `audit_area_profiles.json`

Contains the initial deterministic candidate-assertion knowledge for the two implemented audit areas:

```json
{
  "cash": {
    "candidate_assertions": [
      "existence",
      "completeness",
      "accuracy",
      "rights_and_obligations"
    ]
  },
  "inventory": {
    "candidate_assertions": [
      "existence",
      "completeness",
      "accuracy",
      "valuation",
      "rights_and_obligations"
    ]
  }
}
```

These are only candidates. The LLM makes the engagement-specific relevance decision.

Presence of an entry in this file is what makes a line item an audit area. No part of the engine tests for `cash` or `inventory` by name; adding a third audit area is a change to this file plus catalogue entries.

#### `isa_requirements.json`

Contains exactly three MVP requirement records:

```json
[
  {
    "id": "ISA315.29",
    "standard": "ISA 315",
    "paragraphs": ["29"],
    "purpose": "Determine relevant assertions for material balances.",
    "linked_object_type": "AssertionAssessment"
  },
  {
    "id": "ISA315.28b_31",
    "standard": "ISA 315",
    "paragraphs": ["28(b)", "31"],
    "purpose": "Identify and assess assertion-level risks using likelihood and magnitude.",
    "linked_object_type": "RiskAssessment"
  },
  {
    "id": "ISA330.6_7",
    "standard": "ISA 330",
    "paragraphs": ["6", "7"],
    "purpose": "Design procedures responsive to assessed risks and obtain more persuasive evidence as risk increases.",
    "linked_object_type": "Procedure"
  }
]
```

These records are intentionally small. Additional ISA requirements should be addable later through the same model.

#### `procedure_catalogue.json`

Start with a small, bounded catalogue for cash and inventory:

```json
[
  {
    "id": "CASH_BANK_CONFIRMATION",
    "name": "Obtain bank confirmation",
    "audit_areas": ["cash"],
    "assertions": ["existence", "rights_and_obligations"],
    "procedure_type": "external_confirmation",
    "evidence_strength": "high",
    "description": "Obtain independent confirmation of year-end bank balances and relevant account information."
  },
  {
    "id": "CASH_RECONCILIATION_REVIEW",
    "name": "Review bank reconciliation",
    "audit_areas": ["cash"],
    "assertions": ["existence", "completeness", "accuracy"],
    "procedure_type": "test_of_details",
    "evidence_strength": "medium",
    "description": "Inspect the year-end bank reconciliation and investigate material reconciling items."
  },
  {
    "id": "INV_PHYSICAL_COUNT",
    "name": "Observe and test physical inventory count",
    "audit_areas": ["inventory"],
    "assertions": ["existence", "completeness"],
    "procedure_type": "observation_test_of_details",
    "evidence_strength": "high",
    "description": "Observe inventory counting and perform selected test counts."
  },
  {
    "id": "INV_COST_TEST",
    "name": "Test recorded inventory cost",
    "audit_areas": ["inventory"],
    "assertions": ["accuracy", "valuation"],
    "procedure_type": "test_of_details",
    "evidence_strength": "medium",
    "description": "Test selected inventory costs to supporting purchase or production records."
  },
  {
    "id": "INV_AGED_STOCK_REVIEW",
    "name": "Review aged and slow-moving inventory",
    "audit_areas": ["inventory"],
    "assertions": ["valuation"],
    "procedure_type": "analytical_test_of_details",
    "evidence_strength": "medium",
    "description": "Review inventory ageing and investigate aged or slow-moving items for potential write-down."
  },
  {
    "id": "INV_SUBSEQUENT_SALES",
    "name": "Test post-year-end sales",
    "audit_areas": ["inventory"],
    "assertions": ["valuation"],
    "procedure_type": "test_of_details",
    "evidence_strength": "high",
    "description": "Inspect post-year-end sales evidence for selected inventory items to assess carrying value."
  },
  {
    "id": "INV_RIGHTS_REVIEW",
    "name": "Inspect evidence of inventory ownership",
    "audit_areas": ["inventory"],
    "assertions": ["rights_and_obligations"],
    "procedure_type": "inspection",
    "evidence_strength": "medium",
    "description": "Inspect relevant purchase, consignment or third-party documentation to assess ownership rights."
  }
]
```

These are prototype methodology/configuration records, not claims that these are the complete or universally required audit procedures.

#### `risk_matrix.json`

The deterministic likelihood × magnitude → risk rating mapping used in Section 11. It is methodology
configuration, not prompt logic and not Python constants, so a methodology owner can change the firm's risk
appetite without touching the engine or the prompts.

```json
{
  "label": "Prototype MVP risk matrix. Not an ISA-prescribed mapping.",
  "matrix": {
    "low":    { "low": "low",    "medium": "low",    "high": "medium" },
    "medium": { "low": "low",    "medium": "medium", "high": "high"   },
    "high":   { "low": "medium", "medium": "high",   "high": "high"   }
  }
}
```

The outer key is **likelihood**; the inner key is **magnitude**. The starting matrix is symmetric, but the
structure permits an asymmetric one (for example weighting magnitude more heavily) without a code change.

`CompanyFact`, `AssertionAssessment`, `RiskAssessment`, `Procedure` instances selected for an engagement, `AuditorFeedback`, and `RuleProposal` are generated at runtime and do not need predefined JSON instances.

---

## 4. Core architecture

Use Python with lightweight domain objects, preferably Pydantic models.

Objects hold state. Functions/services perform logic.

Do not build deep inheritance trees.

```text
AuditEngagement
├── CompanyFact[]
├── Materiality
└── FinancialLineItemAssessment[]        (all eight)
    └── AssertionAssessment[]            (audit areas only)
        └── RiskAssessment[]
            └── Procedure[]

ISARequirement[]
AuditorFeedback[]
RuleProposal[]
```

Suggested models:

### `AuditEngagement`

Contains:

- company,
- year end,
- raw financial data,
- company context,
- company facts,
- materiality,
- financial line item assessments.

### `FinancialLineItemAssessment`

Contains:

- line item type,
- CY amount,
- PY amount,
- derived metrics,
- material / non-material,
- is_audit_area (derived: does a profile exist?),
- assertion assessments (populated for audit areas only).

### `AssertionAssessment`

Contains:

- assertion,
- relevant: true / false,
- rationale,
- supporting company facts,
- ISA references,
- risk assessments.

### `RiskAssessment`

Contains:

- specific risk description,
- likelihood: low / medium / high — **LLM**,
- magnitude: low / medium / high — **LLM**,
- risk rating: low / medium / high — **derived deterministically** from likelihood × magnitude via `risk_matrix.json`,
- rationale,
- supporting company facts,
- ISA references,
- procedures,
- system rating (the derived rating, retained unchanged after any override),
- final rating (what all downstream logic reads),
- override metadata.

### `Procedure`

Contains:

- procedure ID,
- description,
- procedure type,
- evidence strength,
- risk(s) addressed,
- rationale,
- source: approved catalogue / AI suggestion,
- ISA traceability.

### `ISARequirement`

Contains at least:

- ID,
- standard,
- paragraph,
- short description,
- object type(s) it relates to.

Design this so more ISA requirements can be added later without changing the architecture.

---

## 5. ISA subset for MVP

Only implement the following concepts.

### ISA 315.29 — Relevant assertions

Use for the decision about which assertions are relevant for a material audit area.

Linked to:

`AssertionAssessment`

### ISA 315.28(b) + 315.31 — Assertion-level risk identification and assessment

Use for:

- identifying the specific risk of material misstatement at assertion level,
- assessing likelihood and magnitude,
- assigning an inherent risk level.

Linked to:

`RiskAssessment`

### ISA 330.6 + 330.7 — Responses to assessed risks

Use for:

- selecting procedures that respond to the identified risk,
- making the response stronger / more persuasive as risk increases.

Linked to:

`Procedure`

Do not attempt full ISA applicability logic in the MVP.

---

## 6. Core sequential pipeline

```text
Input financials + context
        ↓
context extraction → CompanyFact[]
        ↓
calculate materiality                 [deterministic]
        ↓
scope line items                      [deterministic]
        ↓
candidate assertions                  [deterministic config]
        ↓
assess assertion relevance            [LLM]
        ↓
identify risk + likelihood/magnitude  [LLM]
        ↓
derive risk rating from matrix        [deterministic config]
        ↓
select procedures                     [LLM from bounded catalogue]
        ↓
build traceability / ISA coverage     [deterministic]
        ↓
auditor review + overrides
```

Core logic should be implemented as simple functions/services rather than hidden inside domain objects.

Example:

```python
engagement = load_engagement(input_data)

engagement.materiality = calculate_materiality(engagement)
scope_line_items(engagement)

for item in engagement.line_items:
    # material alone is not enough: an implemented profile is what
    # makes a line item an audit area
    if not item.material or not item.is_audit_area:
        continue

    item.assertions = assess_assertions(item, engagement)

    for assertion in item.assertions:
        if not assertion.relevant:
            continue

        assertion.risks = assess_risks(assertion, item, engagement)

        for risk in assertion.risks:
            risk.procedures = select_procedures(
                risk=risk,
                item=item,
                engagement=engagement,
            )

coverage = check_isa_coverage(engagement)
```

---

## 7. Step 1 — Materiality

Deterministic.

Use this prototype methodology rule:

```text
if profit_before_tax / turnover > 0.05:
    materiality = 0.05 * profit_before_tax
else:
    materiality = 0.005 * turnover
```

For Raiatea:

```text
profit_before_tax / turnover
= £5.24m / £52.4m
= 10%

10% > 5%
→ use 5% of profit before tax

materiality
= 5% × £5.24m
= £262k
```

Clearly label this as prototype methodology, not an ISA-prescribed formula.

---

## 8. Step 2 — Line item scoping

Deterministic. Applied uniformly to all eight supplied line items, using the CY amount.

```text
if line item CY amount > materiality:
    line item is material
else:
    line item is not material
```

Materiality and audit-area status are two independent flags:

```text
material            → does it exceed the threshold?          (all eight evaluated)
is_audit_area       → is there a profile for it?             (cash, inventory)

pipeline continues  → material AND is_audit_area
```

A material line item that is not an audit area is displayed as
`material — audit logic not implemented in MVP`. It is not an ISA gap (Section 15).

The architecture should allow future qualitative or risk overrides, but these do not need to be implemented now.

---

## 9. Step 3 — Candidate assertions

Load the deterministic candidate assertions from `audit_area_profiles.json` defined in Section 3.3.

For the MVP, only `cash` and `inventory` have profiles, and that is precisely what makes them the audit areas.

These are candidate assertions, not final engagement-specific conclusions. The same generic engine loads the profile and passes the candidates to the assertion-relevance LLM.

Do not create special `if item == "cash"` / `if item == "inventory"` logic throughout the application. Area-specific knowledge belongs in configuration.

---

## 10. Step 4 — Assertion relevance

This is a bounded LLM judgement.

For each material audit area give the model:

- line item type,
- amount,
- materiality,
- derived metrics,
- company context,
- relevant structured company facts,
- candidate assertions.

Require structured output.

Example:

```json
{
  "assertions": [
    {
      "assertion": "valuation",
      "relevant": true,
      "rationale": "Seasonal inventory creates a meaningful risk of obsolescence.",
      "supporting_fact_ids": ["fact_12"]
    }
  ]
}
```

Requirements:

- use schema-constrained output,
- no unstructured model output should become system state,
- store rationale,
- store supporting fact IDs where available,
- link the resulting assessment to ISA 315.29.

Auditor can override:

- relevant / not relevant,
- rationale.

---

## 11. Step 5 — Risk assessment

For every relevant assertion, ask the LLM to identify and assess the specific risk.

Inputs should include:

- line item / audit area,
- assertion,
- amount,
- materiality,
- amount/materiality ratio,
- YoY movement,
- relevant company facts/context.

The model returns **likelihood and magnitude, but not the rating**:

```json
{
  "risk_description": "Inventory may be carried above recoverable value because of aged seasonal stock.",
  "likelihood": "high",
  "magnitude": "high",
  "rationale": "...",
  "supporting_fact_ids": ["fact_12", "fact_13"]
}
```

The model may return up to **two** distinct risks for one assertion. The prompt asks for the single most
significant risk, and a second only where a genuinely distinct risk exists.

For MVP use:

- low,
- medium,
- high.

### Deriving the rating

The risk rating is **not** an LLM output. It is computed deterministically from likelihood × magnitude using
`risk_matrix.json` (Section 3.3):

```text
system_rating = risk_matrix[likelihood][magnitude]
```

This keeps a known, repeatable, firm-level methodology decision out of the model (Section 20), makes the
rating reproducible for identical likelihood/magnitude pairs, and makes an incoherent combination such as
`low`/`low → high` structurally impossible.

Keep `significant risk` separate and deferred.

The auditor must be able to override the final risk rating and rationale.

Store both:

```text
system_rating      # the matrix-derived rating, never mutated by an override
final_rating       # starts equal to system_rating; the auditor changes this
is_overridden
override_reason
```

Downstream logic always uses `final_rating`.

An override changes `final_rating` only. `likelihood`, `magnitude` and `system_rating` are preserved, so the
original system conclusion and the auditor's departure from it both remain visible.

Link the risk assessment to ISA 315.28(b) and ISA 315.31.

---

## 12. Procedure catalogue

Use the concrete starting `procedure_catalogue.json` defined in Section 3.3.

The catalogue is static methodology/configuration. Runtime `Procedure` objects are created when the selector chooses a catalogue entry for a specific risk.

The catalogue must remain data-driven rather than becoming area-specific branching in Python.

The initial catalogue is intentionally small. It only needs enough alternatives to demonstrate that procedure selection responds sensibly to:

- the audit area,
- the assertion,
- the specific risk,
- the risk level,
- the company context.

Additional catalogue entries can be added later without changing the selection engine.

---

## 13. Step 6 — Procedure selection

Use an LLM to choose from the bounded procedure catalogue.

For each risk provide:

- audit area,
- assertion,
- risk description,
- final risk rating,
- rationale,
- company facts/context,
- relevant catalogue procedures.

Structured output:

```json
{
  "selected_procedures": [
    {
      "procedure_id": "INV_SUBSEQUENT_SALES",
      "rationale": "This directly addresses the risk that aged inventory is overstated."
    }
  ]
}
```

The model may optionally return:

```json
{
  "suggested_new_procedure": {
    "description": "...",
    "rationale": "..."
  }
}
```

Any non-catalogue procedure must be clearly marked:

```text
AI SUGGESTION — AUDITOR APPROVAL REQUIRED
```

Procedure selection should respond to the reason and severity of the risk.

Link procedures to ISA 330.6 / 330.7.

---

## 14. Traceability

All important relationships must be explicit.

Forward traceability:

```text
Procedure
→ Risk
→ Assertion
→ Financial line item (audit area)
→ Company facts / financial metrics
→ ISA requirement
```

Example:

```text
Test post-year-end inventory sales
↓
Addresses inventory obsolescence risk
↓
Valuation assertion
↓
Inventory £8.9m
↓
Seasonal inventory + large YoY increase
↓
ISA 315.29 → ISA 315.28/31 → ISA 330.6/7
```

Do not rely only on free-text rationales for relationships.

Use IDs / references.

---

## 15. Reverse ISA coverage

Support querying:

```text
ISA requirement
→ which audit objects address it?
```

For MVP, simple coverage rules are sufficient.

```text
ISA 315.29
→ material audit areas should have assertion assessments

ISA 315.28/31
→ relevant assertions should have risk assessments

ISA 330.6/7
→ assessed risks should have responsive procedures
```

If expected linked content is missing, display a potential:

```text
GAP
```

### Coverage scope

Coverage is evaluated **only over audit areas** — line items for which methodology is implemented. A `GAP`
means missing work *inside the scope the MVP claims to support*, which is what makes the signal meaningful.

Material line items that are not audit areas are reported in a separate list as:

```text
material — audit logic not implemented in MVP
```

They are an acknowledged scope boundary, not an ISA gap. Reporting the six non-implemented Raiatea items as
gaps would bury the one real gap this feature exists to surface.

This is not intended to prove full ISA compliance.

It demonstrates that the architecture can support progressive requirement coverage.

---

## 16. UI

Use Streamlit unless there is a compelling reason not to.

No separate frontend/backend architecture is required.

Use:

```text
Streamlit UI
    ↕
Python domain objects / session state
    ↕
deterministic functions + LLM services
```

The auditor should be able to:

- inspect company data,
- edit company context,
- inspect extracted company facts,
- inspect assertion decisions,
- change assertion relevance,
- inspect risk reasoning,
- change risk ratings,
- inspect procedures,
- add/remove procedures,
- inspect traceability,
- inspect ISA coverage.

Everything should be pre-populated.

Avoid blank-form workflows.

---

## 17. Downstream recomputation

When the auditor changes something, only recompute downstream dependencies.

Examples:

```text
risk rating changes
→ rerun procedure selection
→ rerun ISA coverage

assertion relevant changes
→ rerun/remove downstream risks
→ rerun/remove procedures
→ rerun ISA coverage

company context changes
→ assertions may change
→ risks may change
→ procedures may change

PBT changes
→ materiality changes
→ line item scope may change
→ downstream pipeline may need rerun
```

For the MVP, this can be implemented with normal Streamlit reruns and explicit recomputation functions.

No complex reactive framework is required.

---

## 18. Auditor feedback

Every meaningful override should create an `AuditorFeedback` record.

Example:

```json
{
  "id": "feedback_17",
  "object_type": "risk_assessment",
  "object_id": "risk_3",
  "before": {
    "final_rating": "high"
  },
  "after": {
    "final_rating": "low"
  },
  "reason": "The inventory is contractually pre-sold and has very low obsolescence exposure.",
  "engagement_context": {}
}
```

Do not simply overwrite the original system output and lose it.

---

## 19. Feedback → methodology learning

This is a separate pipeline from audit generation.

Flow:

```text
original system proposal
+
auditor change
+
auditor explanation
+
relevant engagement context
        ↓
LLM feedback analyser
```

The LLM must classify the feedback as either:

### A. Engagement-specific judgement

```json
{
  "type": "engagement_specific",
  "reason": "..."
}
```

or:

### B. Candidate methodology rule

```json
{
  "type": "methodology_rule_proposal",
  "condition": "...",
  "action": "...",
  "reason": "...",
  "source_feedback_id": "feedback_17",
  "status": "pending_review"
}
```

Example:

```text
IF revenue < £10m
AND inventory valuation risk != high
THEN do not require procedure X
```

The LLM must not automatically alter production methodology.

A human methodology owner later:

- approves,
- rejects,
- or edits

the candidate rule.

For the MVP, it is enough to display the generated rule proposal and its status.

---

## 20. Deterministic vs LLM responsibilities

### Deterministic

- financial calculations,
- materiality,
- line item scoping,
- derived metrics,
- **risk rating derivation from the likelihood × magnitude matrix**,
- candidate assertion profiles,
- schemas,
- object relationships,
- approved procedure catalogue,
- traceability,
- reverse coverage checks,
- explicit methodology rules,
- downstream dependency logic.

### LLM

- extraction of structured facts from company context,
- deciding assertion relevance from bounded candidate assertions,
- identifying company-specific risks and assessing their likelihood and magnitude
  (but **not** the resulting rating — see Section 11),
- selecting procedures from a bounded catalogue,
- suggesting unusual procedures,
- interpreting auditor feedback,
- proposing candidate methodology rules.

Principle:

> If a decision can reliably be represented as approved deterministic methodology, do not ask an LLM to make it again.

---

## 21. LLM implementation requirements

Use the Anthropic Python SDK directly for all MVP LLM calls.

Prefer native SDK capabilities over custom infrastructure. In particular, use Anthropic's native structured-output support with Pydantic models / schema-constrained outputs rather than asking for free-form JSON and building manual extraction/retry logic.

Keep separate functions/services:

```python
extract_company_facts()
assess_assertions()
assess_risks()
select_procedures()
generalize_feedback()
```

Each LLM function should define an explicit Pydantic output model.

Example:

```python
class RiskAssessmentOutput(BaseModel):
    risk_description: str
    likelihood: Literal["low", "medium", "high"]
    magnitude: Literal["low", "medium", "high"]
    rationale: str
    supporting_fact_ids: list[str]
```

Note the absence of `risk_rating`: it is derived from `risk_matrix.json` after the call, not requested from
the model.

Use the SDK's structured parsing/output features so application code receives a validated object directly.

Conceptually:

```python
response = client.messages.parse(
    model=MODEL,
    max_tokens=...,
    messages=[...],
    output_format=RiskAssessmentOutput,
)

result = response.parsed_output
```

Exact SDK syntax should follow the currently installed Anthropic SDK version.

Do NOT build a custom loop like:

```text
ask model for JSON
→ extract JSON from text
→ parse
→ validation failure
→ reprompt
→ parse again
```

Retries may still be used for genuine API/network failures or application-level recovery, but not merely to force the model into the requested schema when the SDK can enforce that schema natively.

All production-facing LLM calls must:

- use native structured/schema-constrained output where possible,
- use explicit Pydantic response models,
- use enum / literal constraints for bounded values such as low / medium / high,
- have explicit system instructions,
- receive only the context relevant to the current bounded judgement,
- return rationale,
- reference supporting company facts where possible,
- be independently testable,
- separate prompt/model configuration from domain/business logic.

Use SDK features such as strict tool schemas if tools are later introduced, rather than manually validating tool payloads where native enforcement is available.

Model configuration should be explicit and easy to change. Do not assume every task requires maximum reasoning effort or the same model settings.

Do not use one giant prompt to generate the whole audit.

Do not introduce LangChain, LangGraph, an agent framework, or custom orchestration unless a concrete need appears.

For the MVP, direct Anthropic SDK calls are preferred.

---

## 22. Evaluation fixtures

Create fixed scenarios before aggressively tuning prompts.

### Scenario A — lower-risk inventory

Same financial data, but context describes:

- stable industrial company,
- non-perishable inventory,
- low obsolescence,
- stable demand.

Expected:

- valuation remains relevant,
- valuation risk should be lower than Scenario B,
- procedure set should be proportionate.

### Scenario B — higher-risk inventory

Same financial data, but context describes:

- seasonal fashion retailer,
- rapidly changing product range,
- meaningful aged inventory.

Expected:

- valuation relevant,
- valuation risk materially higher than Scenario A,
- stronger/more persuasive valuation procedures.

### Scenario C — same numbers, different context

Use identical financial numbers for A and B.

Expected:

- audit output changes because company context changes.

### Scenario D — risk override

Auditor changes a risk rating from high to low.

Expected:

- original system rating retained,
- final rating updated,
- downstream procedure selection reruns,
- unrelated assertions/audit areas remain unchanged.

### Scenario E — auditor feedback

Auditor removes or changes a procedure and gives a generalizable reason.

Expected:

- feedback record created,
- LLM classifies it,
- if generalizable, candidate methodology rule is produced,
- production methodology remains unchanged.

---

## 23. Testing

Use:

- `pytest` for deterministic logic,
- lightweight eval runner or pytest-based evals for LLM behaviour,
- `ruff` for linting/format checks.

At minimum test:

- materiality calculation,
- line item scoping,
- derived metrics,
- risk matrix derivation,
- object relationships,
- risk override behavior,
- downstream recomputation,
- traceability,
- reverse coverage,
- structured LLM parsing,
- procedure catalogue filtering.

Mock or fake LLM responses where deterministic unit tests should not depend on a live model.

---

## 24. Suggested project structure

```text
audit-engine/
├── SPEC.md
├── PLAN.md
├── CLAUDE.md
├── AGENTS.md
├── README.md
├── pyproject.toml
├── src/
│   ├── models/
│   │   ├── engagement.py
│   │   ├── audit_objects.py
│   │   ├── isa.py
│   │   └── feedback.py
│   ├── engine/
│   │   ├── materiality.py
│   │   ├── scoping.py
│   │   ├── pipeline.py
│   │   ├── recompute.py
│   │   ├── traceability.py
│   │   └── coverage.py
│   ├── llm/
│   │   ├── client.py
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
├── tests/
└── evals/
```

The five files under `src/data/` are the concrete static input/config files defined in Section 3.3. Runtime audit objects should not be serialized into separate files for the MVP; keep them in Python/Pydantic objects and Streamlit session state.

This structure is a suggestion, not a requirement. Keep it simpler if there is a cleaner implementation, but preserve the separation between static configuration and runtime-generated audit state.

---

## 25. Definition of MVP success

The prototype is successful if:

1. Raiatea numbers load correctly.
2. Materiality is calculated deterministically.
3. All eight line items are scoped deterministically against materiality.
4. Cash and inventory pass through the same generic pipeline, selected by profile rather than by name.
5. Material line items that are not audit areas are shown as such, and are not reported as ISA gaps.
6. Company context affects assertion/risk decisions.
7. Risk ratings are reproducible from likelihood × magnitude via the configured matrix.
8. Risk level affects procedure selection.
9. Procedures visibly trace to risk → assertion → line item → selected ISA requirement.
10. ISA requirements can be queried in reverse.
11. Auditor can override assertion/risk/procedure decisions.
12. Downstream work updates after an override.
13. Original system output is preserved alongside auditor overrides.
14. At least one auditor override can be turned into a pending candidate methodology rule.
15. Tests and eval scenarios pass.
16. The code can support additional audit areas, procedures and ISA requirements without redesigning the engine.

---

## 26. Deliberately deferred

Only consider these if the MVP is already strong:

- ISA 315.32 significant-risk classification,
- ISA 330.18 material-balance substantive-work requirement,
- ISA 330.21 significant-risk response requirement,
- richer ISA applicability rules,
- control-risk modelling,
- historical precedent retrieval,
- automatic clustering of repeated auditor feedback,
- methodology rule approval/versioning engine,
- confidence/uncertainty handling,
- additional audit areas,
- richer company ontology,
- persistent database,
- production deployment.

---

## 27. Product / engineering rule

When implementation requires a choice not resolved by this specification:

1. prefer the simplest architecture that preserves the intended product behavior,
2. do not expand scope automatically,
3. document the assumption,
4. ask before making a product-level or audit-methodology decision.

The goal is not to build production audit software.

The goal is to demonstrate a credible architecture for:

```text
company-specific information
→ assertion judgement
→ risk assessment
→ responsive audit work
→ ISA traceability
→ auditor correction
→ methodology learning
```
