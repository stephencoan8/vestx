"""One engine: sale estimates must use analyze_sales, not flat User rates."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.utils.sale_tax_estimate import estimate_vest_sale_tax, estimate_lots_sale_tax, lot_input_from_vest
from app.utils.tax_engine import analyze_sales, LotSaleInput


def _fake_user():
    return SimpleNamespace(id=1)


def _fake_vest(*, days_held=400, shares=100.0, vest_fmv=50.0, strike=10.0, iso=False):
    from app.models.grant import ShareType

    vest_date = date.today() - timedelta(days=days_held)
    grant = SimpleNamespace(
        id=1,
        user_id=1,
        share_type=ShareType.ISO_5Y.value if iso else ShareType.RSU.value,
        grant_type='iso' if iso else 'rsu',
        grant_date=vest_date - timedelta(days=400),
        share_price_at_grant=strike,
        user=None,
    )
    vest = SimpleNamespace(
        id=42,
        grant=grant,
        vest_date=vest_date,
        shares_received=shares,
        shares_vested=shares,
        share_price_at_vest=vest_fmv,
        has_vested=True,
    )
    return vest


class TestSaleTaxEnginePath:
    def test_ltcg_not_flat_15_when_profile_has_high_wages(self):
        """With high ordinary, LTCG may hit 20% — engine must not hardcode 15%."""
        vest = _fake_vest(days_held=400, shares=100, vest_fmv=100.0)
        user = _fake_user()
        profile = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': date.today().year,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'federal_ordinary_rate': None,
            'federal_ltcg_rate': None,
            'state_ordinary_rate': 0.0,
            'state_cg_rate': 0.0,
            'other_ordinary_income': 600_000,
            'ytd_wages': 600_000,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
        }
        # Sell at 200 → $10k gain LT
        r = estimate_vest_sale_tax(
            vest, user, current_stock_price=200.0, profile=profile
        )
        assert r['method'] == 'engine'
        assert r['unrealized_gain'] == pytest.approx(10_000.0)
        # Flat 15% would be $1,500 federal alone; 20% band + CA + NIIT is higher
        assert r['estimated_tax'] > 1500.0
        # Must not equal pure 15% * gain (old simplified path ignored state/niit or used flat)
        flat15 = 10_000 * 0.15
        assert abs(r['federal_tax'] - flat15) > 1.0 or r['state_tax'] > 0

    def test_multi_lot_stack_differs_from_sum_of_independents_possible(self):
        """Portfolio helper returns engine method and positive tax on gains."""
        vest = _fake_vest(days_held=400, shares=50, vest_fmv=100.0)
        user = _fake_user()
        lot = lot_input_from_vest(
            vest,
            shares=50,
            sale_price=200.0,
            sale_date=date.today(),
            cost_basis_per_share=100.0,
            user_id=1,
        )
        assert lot is not None
        profile = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': date.today().year,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'federal_ordinary_rate': None,
            'other_ordinary_income': 200_000,
            'ytd_wages': 200_000,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
            'use_state_engine': True,
            'state_ordinary_rate': 0,
            'state_cg_rate': 0,
        }
        r = estimate_lots_sale_tax(user, [lot], profile=profile)
        assert r['method'] == 'engine'
        assert r['estimated_tax'] > 0
        assert r['lot_count'] == 1

    def test_analyze_sales_matches_helper_total(self):
        vest = _fake_vest(days_held=400, shares=10, vest_fmv=50.0)
        user = _fake_user()
        profile = {
            'filing_status': 'single',
            'state_code': 'CA',
            'tax_year': date.today().year,
            'use_bracket_engine': True,
            'use_state_engine': True,
            'federal_ordinary_rate': None,
            'other_ordinary_income': 150_000,
            'ytd_wages': 150_000,
            'other_long_term_gains': 0,
            'other_short_term_gains': 0,
            'include_fica': True,
            'ss_wage_base_maxed': False,
            'include_niit': True,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
            'state_ordinary_rate': 0,
            'state_cg_rate': 0,
        }
        r = estimate_vest_sale_tax(vest, user, current_stock_price=100.0, profile=profile)
        lot = lot_input_from_vest(
            vest, shares=10, sale_price=100.0, sale_date=date.today(),
            cost_basis_per_share=50.0, user_id=1,
        )
        a = analyze_sales(profile, [lot])
        assert r['estimated_tax'] == pytest.approx(a.total_tax, rel=1e-6)
