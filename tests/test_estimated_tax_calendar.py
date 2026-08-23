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


def test_remaining_set_aside_front_loads_next_due():
    """August sales: Q1/Q2 past → full still-to-save on Q3 (Sep 15), not split with Q4."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from app.utils.estimated_tax_calendar import build_estimated_tax_calendar

    profile = SimpleNamespace(
        prior_year_total_tax=0,
        prior_year_agi=None,
        federal_withholding_ytd=0,
        state_withholding_ytd=0,
        estimated_payments_ytd=0,
    )
    with patch(
        'app.utils.estimated_tax_calendar.stacked_tax_on_sales',
        return_value={'total_tax': 10_000, 'federal_tax': 8000, 'state_tax': 1500, 'niit': 500,
                      'fica': 0, 'amt_due': 0, 'lot_count': 1, 'method': 'test'},
    ):
        cal = build_estimated_tax_calendar(
            user=SimpleNamespace(id=1),
            tax_year=2026,
            sales=[],
            profile=profile,
            as_of=date(2026, 8, 23),
        )
    qs = {q['label']: q for q in cal['quarters']}
    assert qs['Q1']['suggested_payment'] == 0
    assert qs['Q2']['suggested_payment'] == 0
    assert qs['Q3']['is_next'] is True
    assert qs['Q3']['suggested_payment'] == 10_000
    assert qs['Q4']['suggested_payment'] == 0
    assert qs['Q3']['due'] == date(2026, 9, 15)
    assert qs['Q4']['due'] == date(2027, 1, 15)
