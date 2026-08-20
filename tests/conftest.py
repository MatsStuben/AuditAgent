"""Shared fixtures.

`raiatea_engagement` builds an unscoped engagement straight from static config. M8 introduces
the real `load_engagement`; this fixture is expected to delegate to it at that point.
"""

import pytest

from src.config.loader import StaticConfig, load_config
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment


@pytest.fixture
def static_config() -> StaticConfig:
    return load_config()


def build_engagement(config: StaticConfig) -> AuditEngagement:
    """An engagement with line items loaded but nothing assessed yet."""
    return AuditEngagement(
        company=config.engagement_input.company,
        year_end=config.engagement_input.year_end,
        line_items=[
            FinancialLineItemAssessment(
                id=f"li_{li.type}", line_item_type=li.type, cy=li.cy, py=li.py
            )
            for li in config.engagement_input.line_items
        ],
    )


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
