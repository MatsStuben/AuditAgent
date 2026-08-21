"""Print the Scenario A/B comparison side by side (SPEC 22).

The eval assertions say *whether* a check passed; this says what the model actually produced,
which is what prompt tuning needs to read. Ten live calls.

    uv run python -m evals.run_evals
"""

from evals.scenarios import (
    CONTEXT_A,
    CONTEXT_B,
    assertion_of,
    audit_area,
    highest_rating,
    procedures_for_assertion,
    relevant_assertions,
    run_scenario,
)
from src.config.loader import StaticConfig, load_config
from src.models.audit_objects import Assertion
from src.models.engagement import AuditEngagement

AREA = "inventory"
COLUMN = 46


def _wrap(text: str, width: int = COLUMN) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return lines


def _row(label: str, left: str, right: str) -> str:
    left_lines, right_lines = _wrap(left), _wrap(right)
    height = max(len(left_lines), len(right_lines))
    left_lines += [""] * (height - len(left_lines))
    right_lines += [""] * (height - len(right_lines))

    out = []
    for index in range(height):
        heading = label if index == 0 else ""
        out.append(f"{heading:<22}{left_lines[index]:<{COLUMN + 2}}{right_lines[index]}")
    return "\n".join(out)


def _valuation_summary(run: AuditEngagement) -> dict[str, str]:
    valuation = assertion_of(run, AREA, Assertion.VALUATION)
    risks = valuation.risks
    procedures = procedures_for_assertion(run, AREA, Assertion.VALUATION)
    rating = highest_rating(valuation)
    return {
        "relevant assertions": ", ".join(a.value for a in relevant_assertions(run, AREA)),
        "valuation relevant": str(valuation.relevant),
        "valuation rationale": valuation.rationale,
        "risks": " | ".join(
            f"{r.likelihood.value}/{r.magnitude.value} → {r.final_rating.value}: "
            f"{r.risk_description}"
            for r in risks
        )
        or "none",
        "highest rating": rating.value if rating else "none",
        "procedures": ", ".join(
            f"{p.procedure_id or 'AI suggestion'}"
            f"({p.evidence_strength.value if p.evidence_strength else 'unassessed'})"
            for p in procedures
        )
        or "none",
    }


def compare(config: StaticConfig | None = None) -> None:
    config = config or load_config()
    run_a = run_scenario(CONTEXT_A, config)
    run_b = run_scenario(CONTEXT_B, config)

    inventory = audit_area(run_a, AREA)
    print(
        f"\nSPEC 22 A/B — identical financials: {AREA} {inventory.cy:,.0f} "
        f"({inventory.metrics.amount_to_materiality_ratio:.1f}x materiality of "
        f"{run_a.materiality.amount:,.0f})\n"
    )
    print(f"{'':<22}{'A — stable industrial':<{COLUMN + 2}}B — seasonal fashion")
    print("-" * (22 + COLUMN * 2 + 2))

    left, right = _valuation_summary(run_a), _valuation_summary(run_b)
    for key in left:
        print(_row(key, left[key], right[key]))
        print()


if __name__ == "__main__":
    compare()
