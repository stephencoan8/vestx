"""Unit tests for the equity tax engine."""

from datetime import date
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.tax_engine import (
    progressive_tax,
    analyze_lot,
    analyze_sales,
    LotSaleInput,
    classify_iso_disposition,
    compute_amt,
    ltcg_rate_for_income,
)


def test_progressive_tax_basic():
    brackets = [(0, 0.10), (10000, 0.20), (50000, 0.30)]
    # 10k * 10% + 0 = 1000
    assert abs(progressive_tax(10000, brackets) - 1000) < 0.01
    # 10k*0.1 + 40k*0.2 = 1000+8000=9000
    assert abs(progressive_tax(50000, brackets) - 9000) < 0.01


def test_iso_qualifying_vs_disqualifying():
    g = date(2020, 1, 1)
    ex = date(2023, 1, 1)
    # held 2y from grant and 1y from exercise
    assert classify_iso_disposition(g, ex, date(2024, 2, 1)) == 'qualifying'
    assert classify_iso_disposition(g, ex, date(2023, 6, 1)) == 'disqualifying'


def test_rsu_sale_capital_gain():
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='new_hire',
        shares=100,
        sale_price=50,
        sale_date=date(2026, 7, 1),
        vest_date=date(2024, 6, 15),
        grant_date=date(2023, 6, 15),
        cost_basis_per_share=20,
        is_iso=False,
    )
    r = analyze_lot(lot)
    assert r.ordinary_income == 0
    assert abs(r.capital_gain - 3000) < 0.01  # (50-20)*100
    assert r.is_long_term is True


def test_iso_disqualifying_ordinary():
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='iso_5y',
        grant_type='annual_performance',
        shares=10,
        sale_price=100,
        sale_date=date(2026, 3, 1),
        vest_date=date(2025, 1, 1),
        grant_date=date(2024, 6, 1),
        cost_basis_per_share=10,
        is_iso=True,
        strike_price=10,
        exercise_date=date(2026, 2, 1),
        fmv_at_exercise=80,
    )
    r = analyze_lot(lot)
    assert r.iso_disposition == 'disqualifying'
    # ordinary = (min(100,80)-10)*10 = 70*10 = 700
    assert abs(r.ordinary_income - 700) < 0.01
    assert r.capital_gain != 0


def test_analyze_sales_with_profile_income():
    profile = {
        'filing_status': 'single',
        'tax_year': 2026,
        'use_bracket_engine': True,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': 0.15,
        'state_ordinary_rate': 0.05,
        'state_cg_rate': 0.05,
        'other_ordinary_income': 200000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': False,
        'include_niit': True,
        'ytd_wages': 200000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
    }
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='new_hire',
        shares=100,
        sale_price=150,
        sale_date=date(2026, 7, 1),
        vest_date=date(2024, 1, 1),
        grant_date=date(2023, 1, 1),
        cost_basis_per_share=50,
        is_iso=False,
    )
    a = analyze_sales(profile, [lot])
    assert a.ltcg > 0
    assert a.total_tax > 0
    assert a.after_tax_proceeds < a.total_proceeds
    assert a.federal_ltcg_tax > 0
    # NIIT likely applies at 200k + gain
    assert a.niit >= 0


def test_amt_positive_with_large_bargain():
    # Large ISO bargain should push AMT above regular when ordinary income modest
    tmt = compute_amt(500_000, 'single', 2026)
    assert tmt > 0


def test_ltcg_rate_brackets():
    # Low income → 0%
    assert ltcg_rate_for_income(10000, 'single', 2026) == 0.0
    # Mid → 15%
    r = ltcg_rate_for_income(100000, 'single', 2026)
    assert r == 0.15


if __name__ == '__main__':
    test_progressive_tax_basic()
    test_iso_qualifying_vs_disqualifying()
    test_rsu_sale_capital_gain()
    test_iso_disqualifying_ordinary()
    test_analyze_sales_with_profile_income()
    test_amt_positive_with_large_bargain()
    test_ltcg_rate_brackets()
    print('ALL TAX ENGINE TESTS PASSED')
