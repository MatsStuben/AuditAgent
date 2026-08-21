"""Rollback for the operations that make several LLM calls in sequence (SPEC 17).

Any run that spends more than one call can fail part-way, and a half-applied run is worse than
a failed one: it puts an engagement in front of the auditor holding new facts above old
assessments, or one audit area analysed and the next untouched, with nothing on screen to say
so. `capture` before, `restore` on failure, and the auditor keeps the file they already had.

Shallow by design. It records *which objects* the engagement points at, not their contents,
which is enough because these paths replace lists and rebind fields rather than editing
existing objects in place. It also means a rollback restores the original objects themselves,
so a UI still holding a reference is not left pointing at an orphaned copy.

What is deliberately not restored is the ID counter. Reusing an ID a failed run consumed is
exactly what SPEC 14 forbids: a retained reference would silently resolve to different
evidence, where a never-reused ID leaves it visibly unresolved.

Lives in its own module because both `pipeline` and `recompute` need it and `recompute`
imports `pipeline`.
"""

from src.models.engagement import AuditEngagement

State = tuple


def capture(engagement: AuditEngagement) -> State:
    """Everything a multi-call operation may replace."""
    return (
        engagement.company_context,
        engagement.company_facts,
        engagement.materiality,
        [
            (item, item.cy, item.metrics, item.material, item.is_audit_area,
             item.assertions, item.procedures)
            for item in engagement.line_items
        ],
    )


def restore(engagement: AuditEngagement, state: State) -> None:
    context, facts, materiality, items = state
    engagement.company_context = context
    engagement.company_facts = facts
    engagement.materiality = materiality
    for item, cy, metrics, material, is_audit_area, assertions, procedures in items:
        item.cy = cy
        item.metrics = metrics
        item.material = material
        item.is_audit_area = is_audit_area
        item.assertions = assertions
        item.procedures = procedures
