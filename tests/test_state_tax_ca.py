"""Unit tests for California state tax engine."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from app.utils.state_tax import compute_state_tax
from app.utils.state_tax.california import (
    compute_california_tax,
    CA_MHST_THRESHOLD,
    CA_MHST_RATE,
    CA_BRACKETS,
)
from app.utils.state_tax.base import progressive_tax_from_floors
from app.utils.tax_engine import LotSaleInput, analyze_sales


def test_ca_zero_income():
    r = compute_california_tax(
        ordinary_income=0, capital_gains=0, filing_status='single', tax_year=2025
    )
    assert r.total_tax == 0
    assert r.engine == 'CA'
    assert r.surtax == 0


def test_ca_cg_taxed_as_ordinary():
    """CA has no LTCG preference — same tax whether income is ordinary or CG."""
    as_ord = compute_california_tax(
        ordinary_income=200_000, capital_gains=0, filing_status='single', tax_year=2025
    )
    as_cg = compute_california_tax(
        ordinary_income=0, capital_gains=200_000, filing_status='single', tax_year=2025
    )
    assert abs(as_ord.total_tax - as_cg.total_tax) < 0.01
    assert as_cg.breakdown['cg_preferential'] is False


def test_ca_progressive_single_2025():
    # Spot-check progressive math against bracket helper
    income = 100_000
    brackets = CA_BRACKETS[2025]['single']
    expected = progressive_tax_from_floors(income, brackets)
    r = compute_california_tax(
        ordinary_income=income, capital_gains=0, filing_status='single', tax_year=2025
    )
    assert abs(r.regular_tax - expected) < 0.01
    assert r.surtax == 0  # under $1M
    assert r.total_tax == r.regular_tax
    assert r.marginal_rate == 0.093  # 9.3% band for 100k single


def test_ca_mhst_over_1m():
    income = 1_500_000
    r = compute_california_tax(
        ordinary_income=income, capital_gains=0, filing_status='single', tax_year=2025
    )
    expected_mhst = (income - CA_MHST_THRESHOLD) * CA_MHST_RATE
    assert abs(r.surtax - expected_mhst) < 0.01
    assert abs(r.surtax - 5_000) < 0.01  # 1% of 500k
    assert r.total_tax == r.regular_tax + r.surtax
    # Top bracket 12.3% + MHST 1% = 13.3%
    assert abs(r.marginal_rate - 0.133) < 0.001


def test_ca_mfj_brackets_wider():
    income = 200_000
    single = compute_california_tax(
        ordinary_income=income, capital_gains=0, filing_status='single', tax_year=2025
    )
    mfj = compute_california_tax(
        ordinary_income=income, capital_gains=0, filing_status='mfj', tax_year=2025
    )
    # Same income should cost less (or equal) MFJ due to wider brackets
    assert mfj.regular_tax <= single.regular_tax


def test_dispatch_ca_vs_flat():
    ca = compute_state_tax(
        state_code='CA',
        filing_status='single',
        tax_year=2025,
        ordinary_income=300_000,
        capital_gains=50_000,
        use_state_engine=True,
        state_ordinary_rate=0.10,
        state_cg_rate=0.10,
    )
    assert ca.engine == 'CA'
    assert ca.total_tax > 0

    flat = compute_state_tax(
        state_code='CA',
        filing_status='single',
        tax_year=2025,
        ordinary_income=300_000,
        capital_gains=50_000,
        use_state_engine=False,
        state_ordinary_rate=0.10,
        state_cg_rate=0.10,
    )
    assert flat.engine == 'flat'
    assert abs(flat.total_tax - 35_000) < 0.01  # 350k * 10%


def test_dispatch_non_ca_flat_note():
    r = compute_state_tax(
        state_code='TX',
        filing_status='single',
        tax_year=2025,
        ordinary_income=100_000,
        capital_gains=0,
        use_state_engine=True,
        state_ordinary_rate=0.0,
        state_cg_rate=0.0,
    )
    assert r.engine == 'flat'
    assert r.total_tax == 0
    assert any('CA' in n or 'California' in n for n in r.notes)


def test_analyze_sales_uses_ca_engine():
    profile = {
        'filing_status': 'single',
        'tax_year': 2025,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': 0.15,
        'state_ordinary_rate': 0.0,  # ignored when CA engine on
        'state_cg_rate': 0.0,
        'other_ordinary_income': 250_000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': False,
        'include_niit': True,
        'ytd_wages': 250_000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
    }
    # LT RSU sale: $100k gain
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='new_hire',
        shares=1000,
        sale_price=150,
        sale_date=date(2025, 7, 1),
        vest_date=date(2023, 1, 1),
        grant_date=date(2022, 1, 1),
        cost_basis_per_share=50,
        is_iso=False,
    )
    a = analyze_sales(profile, [lot])
    assert a.state_engine == 'CA'
    assert a.state_tax > 0
    assert a.state_regular_tax > 0
    # CA taxable = wages + CG after CA standard deduction (~$5.7k single 2025)
    assert a.state_taxable_income >= 250_000 + 100_000 - 10_000
    assert a.state_taxable_income < 250_000 + 100_000  # std ded applied
    assert a.rates_used.get('state_marginal', 0) > 0
    assert a.state_breakdown.get('cg_preferential') is False


def test_analyze_sales_ca_mhst_path():
    """Large income + equity should trigger MHST surtax."""
    profile = {
        'filing_status': 'single',
        'tax_year': 2025,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'federal_ordinary_rate': 0.37,
        'federal_ltcg_rate': 0.20,
        'state_ordinary_rate': 0.0,
        'state_cg_rate': 0.0,
        'other_ordinary_income': 900_000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': False,
        'include_niit': True,
        'ytd_wages': 900_000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
    }
    lot = LotSaleInput(
        vest_event_id=1,
        grant_id=1,
        share_type='rsu',
        grant_type='new_hire',
        shares=5000,
        sale_price=100,
        sale_date=date(2025, 8, 1),
        vest_date=date(2023, 1, 1),
        grant_date=date(2022, 1, 1),
        cost_basis_per_share=20,  # 80 * 5000 = 400k gain → total state TI 1.3M
        is_iso=False,
    )
    a = analyze_sales(profile, [lot])
    assert a.state_engine == 'CA'
    assert a.state_surtax > 0
    assert a.state_taxable_income > CA_MHST_THRESHOLD


if __name__ == '__main__':
    test_ca_zero_income()
    test_ca_cg_taxed_as_ordinary()
    test_ca_progressive_single_2025()
    test_ca_mhst_over_1m()
    test_ca_mfj_brackets_wider()
    test_dispatch_ca_vs_flat()
    test_dispatch_non_ca_flat_note()
    test_analyze_sales_uses_ca_engine()
    test_analyze_sales_ca_mhst_path()
    print('ALL CA STATE TAX TESTS PASSED')
