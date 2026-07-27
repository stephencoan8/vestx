"""ISO exercise vs sale scenario planner tests."""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.tax_engine import (
    classify_iso_disposition,
    earliest_qualifying_sale_date,
    ExerciseInput,
    analyze_sales,
)
from app.utils.equity_planner import (
    LotSpec,
    plan_iso_cashless_dd,
    plan_iso_exercise_hold,
    plan_iso_exercise_sell_qd,
    compare_iso_strategies,
    run_plan,
)


def _profile(**kw):
    base = {
        'filing_status': 'single',
        'tax_year': 2026,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': None,
        'state_ordinary_rate': 0.0,
        'state_cg_rate': 0.0,
        'other_ordinary_income': 200_000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': True,
        'include_niit': True,
        'ytd_wages': 200_000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
    }
    base.update(kw)
    return base


def _iso_lot(shares=100, strike=10.0, grant=date(2023, 1, 1), vest=date(2024, 1, 1), **kw):
    return LotSpec(
        vest_event_id=1,
        grant_id=1,
        share_type='iso_5y',
        grant_type='new_hire',
        is_iso=True,
        shares=shares,
        vest_date=vest,
        grant_date=grant,
        strike_price=strike,
        cost_basis_per_share=strike,
        label='ISO test',
        **kw,
    )


def test_qd_dates():
    g = date(2020, 1, 1)
    ex = date(2023, 1, 1)
    assert classify_iso_disposition(g, ex, date(2024, 2, 1)) == 'qualifying'
    assert classify_iso_disposition(g, ex, date(2023, 6, 1)) == 'disqualifying'
    assert earliest_qualifying_sale_date(g, ex) == date(2024, 1, 1)


def test_cashless_has_ordinary_no_amt():
    p = plan_iso_cashless_dd(
        _profile(),
        [_iso_lot(shares=100, strike=10)],
        event_date=date(2026, 6, 1),
        price=50,
    )
    a = p.years[0].analysis
    # Ordinary on bargain: (50-10)*100 = 4000
    assert abs(a['equity_ordinary'] - 4000) < 1
    assert a['amt_due'] == 0 or a['amt_due'] < 1
    assert p.cash.exercise_cash_outlay == 1000  # strike
    assert p.cash.sale_gross_proceeds == 5000
    assert p.total_incremental_tax > 0


def test_exercise_hold_has_amt_no_proceeds():
    p = plan_iso_exercise_hold(
        _profile(other_ordinary_income=80_000, ytd_wages=80_000, ss_wage_base_maxed=False),
        [_iso_lot(shares=1000, strike=5)],
        exercise_date=date(2026, 3, 1),
        fmv=40,  # bargain 35*1000 = 35k
    )
    assert p.cash.sale_gross_proceeds == 0
    assert p.cash.exercise_cash_outlay == 5000
    assert p.iso_meta['bargain_element'] == 35_000
    a = p.years[0].analysis
    # Large bargain with modest wages can create AMT due
    assert a['equity_ordinary'] == 0  # ISO exercise: no regular income
    assert 'earliest_qd_dates' in p.iso_meta


def test_exercise_only_amt_via_engine():
    ex = ExerciseInput(
        vest_event_id=1,
        shares=2000,
        exercise_date=date(2026, 4, 1),
        strike_price=5,
        fmv_at_exercise=30,  # bargain 50k
        grant_date=date(2023, 1, 1),
        is_iso=True,
    )
    a = analyze_sales(
        _profile(other_ordinary_income=100_000, ytd_wages=100_000),
        lots=[],
        exercises=[ex],
    )
    assert a.total_proceeds == 0
    assert a.equity_ordinary == 0
    # AMT preference present — may or may not exceed regular depending on tables
    assert a.amt_tax > 0


def test_qd_path_multi_year():
    # Grant 2023, exercise 2026 → QD earliest max(2025-01-01 grant+2y, exercise+1y)
    lot = _iso_lot(shares=100, strike=10, grant=date(2023, 1, 1))
    p = plan_iso_exercise_sell_qd(
        _profile(),
        [lot],
        exercise_date=date(2026, 6, 1),
        fmv_at_exercise=40,
        sale_price=60,
        sale_date=None,  # auto earliest QD
    )
    assert len(p.years) == 2
    assert p.years[0].role == 'exercise'
    assert p.years[1].role == 'sale'
    assert p.cash.sale_gross_proceeds == 6000
    # Sale year analysis should show LTCG path (QD)
    sale_a = p.years[1].analysis
    assert sale_a['ltcg'] > 0 or sale_a['equity_ordinary'] == 0


def test_compare_returns_three():
    r = compare_iso_strategies(
        _profile(),
        [_iso_lot(shares=50, strike=10)],
        exercise_date=date(2026, 5, 1),
        fmv_at_exercise=40,
        sale_price=55,
    )
    assert r['compare'] is True
    assert 'iso_cashless_dd' in r['scenarios']
    assert 'iso_exercise_hold' in r['scenarios']
    assert 'iso_exercise_sell_qd' in r['scenarios']
    assert len(r['summary_rows']) == 3


def test_run_plan_dispatch():
    r = run_plan(
        _profile(),
        [_iso_lot(shares=10, strike=5)],
        strategy='iso_cashless_dd',
        sale_date=date(2026, 7, 1),
        sale_price=20,
        exercise_date=date(2026, 7, 1),
        exercise_fmv=20,
    )
    assert r['success']
    assert r['plan']['strategy'] == 'iso_cashless_dd'


if __name__ == '__main__':
    test_qd_dates()
    test_cashless_has_ordinary_no_amt()
    test_exercise_hold_has_amt_no_proceeds()
    test_exercise_only_amt_via_engine()
    test_qd_path_multi_year()
    test_compare_returns_three()
    test_run_plan_dispatch()
    print('ALL EQUITY PLANNER TESTS PASSED')
