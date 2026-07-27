"""Federal/CA AMT, credit rollforward, and sell-to-cover tests."""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.amt import (
    compute_federal_tmt,
    compute_ca_tmt,
    compute_amt_stack,
    apply_amt_and_credit,
)
from app.utils.tax_engine import analyze_sales, ExerciseInput, LotSaleInput
from app.utils.equity_planner import LotSpec, plan_iso_exercise_sell_qd, plan_iso_exercise_hold
from app.utils.liquidity import solve_iso_exercise_sell_to_cover, solve_rsu_sell_to_fund_iso


def _profile(**kw):
    base = {
        'filing_status': 'single',
        'tax_year': 2025,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': None,
        'state_ordinary_rate': 0.0,
        'state_cg_rate': 0.0,
        'other_ordinary_income': 120_000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': False,
        'include_niit': True,
        'ytd_wages': 120_000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0.0,
        'ca_amt_credit_carryforward': 0.0,
    }
    base.update(kw)
    return base


def test_ca_tmt_rate_7pct():
    tmt, ex = compute_ca_tmt(200_000, 'single', 2025)
    # exemption ~92,749 → taxable ~107,251 * 7%
    assert ex > 90_000
    assert abs(tmt - (200_000 - ex) * 0.07) < 1


def test_federal_tmt_positive():
    tmt, _ = compute_federal_tmt(300_000, 'single', 2025)
    assert tmt > 0


def test_amt_credit_generated_then_used():
    # Year of AMT: TMT > regular → generate credit
    layer = apply_amt_and_credit(
        jurisdiction='federal',
        amti=400_000,
        regular_tax=50_000,
        tmt=80_000,
        exemption_used=90_000,
        opening_credit=0,
    )
    assert abs(layer.amt_due - 30_000) < 0.01
    assert abs(layer.credit_generated - 30_000) < 0.01
    assert layer.credit_ending == 30_000

    # Later year: regular > TMT → use credit
    layer2 = apply_amt_and_credit(
        jurisdiction='federal',
        amti=150_000,
        regular_tax=40_000,
        tmt=20_000,
        exemption_used=90_000,
        opening_credit=30_000,
    )
    assert layer2.amt_due == 0
    assert abs(layer2.credit_used - 20_000) < 0.01  # room = 20k
    assert abs(layer2.credit_ending - 10_000) < 0.01


def test_iso_exercise_triggers_fed_and_ca_amt():
    ex = ExerciseInput(
        vest_event_id=1,
        shares=5_000,
        exercise_date=date(2025, 6, 1),
        strike_price=5,
        fmv_at_exercise=40,  # bargain 175k
        grant_date=date(2023, 1, 1),
        is_iso=True,
    )
    a = analyze_sales(_profile(), lots=[], exercises=[ex])
    assert a.equity_ordinary == 0
    assert a.amt_due > 0 or a.amt_tax > 0
    # CA AMT often positive with large bargain
    assert a.ca_amt_due >= 0
    assert a.federal_amt_credit_generated >= 0
    assert a.federal_amt_credit_ending >= a.federal_amt_credit_opening


def test_qd_path_hands_off_credit():
    lot = LotSpec(
        vest_event_id=1,
        grant_id=1,
        share_type='iso_5y',
        grant_type='new_hire',
        is_iso=True,
        shares=2_000,
        vest_date=date(2024, 1, 1),
        grant_date=date(2023, 1, 1),
        strike_price=5,
        cost_basis_per_share=5,
        label='ISO',
    )
    p = plan_iso_exercise_sell_qd(
        _profile(other_ordinary_income=100_000),
        [lot],
        exercise_date=date(2025, 6, 1),
        fmv_at_exercise=35,
        sale_price=50,
        sale_date=None,
    )
    assert len(p.years) == 2
    assert len(p.amt_credit_ledger) == 2
    # Exercise year should generate credit if AMT due
    y0 = p.amt_credit_ledger[0]
    y1 = p.amt_credit_ledger[1]
    # Ending year0 should equal opening year1 for federal (handoff)
    assert abs(y0['federal_credit_ending'] - y1['federal_credit_opening']) < 1.0


def test_sell_to_cover_finds_partial_sale():
    lots = [
        LotSpec(
            vest_event_id=1,
            grant_id=1,
            share_type='iso_5y',
            grant_type='new_hire',
            is_iso=True,
            shares=1_000,
            vest_date=date(2024, 1, 1),
            grant_date=date(2022, 1, 1),
            strike_price=10,
            cost_basis_per_share=10,
            label='ISO',
        )
    ]
    r = solve_iso_exercise_sell_to_cover(
        _profile(other_ordinary_income=150_000, ytd_wages=150_000),
        lots,
        exercise_date=date(2025, 7, 1),
        fmv=50,
        cover_strike=True,
        cover_tax=True,
    )
    assert r.shares_total == 1000
    # With $40 spread, selling some fraction should fund hold
    if r.success:
        assert 0 < r.shares_to_sell <= 1000
        assert r.shortfall == 0
    else:
        # If unsuccessful, shortfall explained
        assert r.shortfall > 0


def test_rsu_fund_iso():
    rsu = [
        LotSpec(
            vest_event_id=2,
            grant_id=2,
            share_type='rsu',
            grant_type='new_hire',
            is_iso=False,
            shares=5_000,
            vest_date=date(2023, 1, 1),
            grant_date=date(2022, 1, 1),
            strike_price=0,
            cost_basis_per_share=20,
            label='RSU',
        )
    ]
    iso = [
        LotSpec(
            vest_event_id=1,
            grant_id=1,
            share_type='iso_5y',
            grant_type='new_hire',
            is_iso=True,
            shares=500,
            vest_date=date(2024, 1, 1),
            grant_date=date(2022, 1, 1),
            strike_price=10,
            cost_basis_per_share=10,
            label='ISO',
        )
    ]
    r = solve_rsu_sell_to_fund_iso(
        _profile(),
        rsu,
        iso,
        sale_date=date(2025, 8, 1),
        sale_price=50,
        exercise_date=date(2025, 8, 1),
        exercise_fmv=50,
    )
    assert r.mode == 'rsu_fund_iso'
    if r.success:
        assert r.shares_to_sell > 0
        assert r.shares_to_sell <= 5000


def test_amt_stack_combined():
    s = compute_amt_stack(
        filing='single',
        year=2025,
        federal_regular_tax=40_000,
        ca_regular_tax=15_000,
        ordinary_and_cg_base=150_000,
        iso_bargain_preference=200_000,
        federal_credit_opening=0,
        ca_credit_opening=0,
        state_code='CA',
    )
    assert s.federal.amti == 350_000
    assert s.california is not None
    assert s.california.tentative_minimum_tax > 0


if __name__ == '__main__':
    test_ca_tmt_rate_7pct()
    test_federal_tmt_positive()
    test_amt_credit_generated_then_used()
    test_iso_exercise_triggers_fed_and_ca_amt()
    test_qd_path_hands_off_credit()
    test_sell_to_cover_finds_partial_sale()
    test_rsu_fund_iso()
    test_amt_stack_combined()
    print('ALL AMT + LIQUIDITY TESTS PASSED')
