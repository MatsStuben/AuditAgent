"""Shared fixtures."""

import pytest

from src.config.loader import StaticConfig, load_config
from src.engine.pipeline import load_engagement
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment


@pytest.fixture
def static_config() -> StaticConfig:
    return load_config()


def build_engagement(config: StaticConfig) -> AuditEngagement:
    """An engagement with line items loaded but nothing assessed yet.

    Delegates to the real `load_engagement` so fixtures cannot drift from production, then
    blanks the seeded context — most tests set their own, and those that do not should see
    the no-context path rather than a silently inherited one.
    """
    engagement = load_engagement(config)
    engagement.company_context = ""
    return engagement


@pytest.fixture
def raiatea_engagement(static_config) -> AuditEngagement:
    return build_engagement(static_config)


def make_engagement(*line_items: tuple[str, float, float]) -> AuditEngagement:
    """A synthetic engagement from (type, cy, py) triples, for edge cases the case data
    does not exercise."""
    return AuditEngagement(
        company="Synthetic Ltd",
        year_end="2025-12-31",
        line_items=[
            FinancialLineItemAssessment(id=f"li_{t}", line_item_type=t, cy=cy, py=py)
            for t, cy, py in line_items
        ],
    )
