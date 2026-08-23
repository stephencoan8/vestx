"""Safe harbor + quarterly due dates for Sold tab tax reserve."""

from datetime import date

import pytest

from app.utils.estimated_tax_calendar import (
    federal_estimated_due_dates,
    safe_harbor_targets,
)


def test_federal_quarters_2026():
    qs = federal_estimated_due_dates(2026)
    assert len(qs) == 4
    assert qs[0]['due'] == date(2026, 4, 15)
    assert qs[3]['due'] == date(2027, 1, 15)


def test_safe_harbor_110_when_high_agi():
    h = safe_harbor_targets(
        prior_year_total_tax=100_000,
        prior_year_agi=200_000,
        current_year_estimated_tax=50_000,
    )
    assert h['high_agi_110'] is True
    assert h['prior_year_safe_harbor'] == pytest.approx(110_000)
    assert h['current_year_90pct'] == pytest.approx(45_000)
    assert h['required_annual'] == pytest.approx(45_000)  # min of 110k and 45k
    assert h['binding_rule'] == 'current_90'


def test_safe_harbor_100_when_normal_agi():
    h = safe_harbor_targets(
        prior_year_total_tax=40_000,
        prior_year_agi=120_000,
        current_year_estimated_tax=80_000,
    )
    assert h['prior_year_safe_harbor'] == 40_000
    assert h['required_annual'] == 40_000
    assert h['binding_rule'] == 'prior_year'
