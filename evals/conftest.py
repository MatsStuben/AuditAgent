"""Shared scenario runs for the eval suite.

Session-scoped because each run is five live API calls (SPEC 6.1) and several modules ask
questions of the same run. Anything that mutates a run must take a `fresh()` copy first — an
override left on a shared engagement would silently become the next eval's starting state.
"""

import pytest

from evals.scenarios import (
    CONTEXT_A,
    CONTEXT_B,
    MINIMAL_CONTEXT,
    rich_context,
    run_scenario,
)
from src.config.loader import load_config


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def minimal_run(config):
    """The short context: nothing whatsoever is supplied about cash (SPEC 22 F)."""
    return run_scenario(MINIMAL_CONTEXT, config)


@pytest.fixture(scope="session")
def rich_run(config):
    """The demo default, which supplies both areas explicitly."""
    return run_scenario(rich_context(config), config)


@pytest.fixture(scope="session")
def run_a(config):
    """Scenario A — stable industrial company, non-perishable stock (SPEC 22)."""
    return run_scenario(CONTEXT_A, config)


@pytest.fixture(scope="session")
def run_b(config):
    """Scenario B — seasonal fashion retailer with aged stock (SPEC 22).

    Identical financials to A, and an identical cash description. The inventory narrative is
    the only variable, which is what makes any difference in the output attributable.
    """
    return run_scenario(CONTEXT_B, config)
