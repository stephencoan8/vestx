"""IRC §423 ESPP — QD vs DD, lookback, loss cap, not RSU FMV-basis."""

from datetime import date

import pytest

from app.utils.espp_423 import (
    analyze_espp_sale,
    offering_start_for,
    lookback_purchase_price,
)
from app.utils.tax_engine import LotSaleInput, analyze_lot
from app.utils.equity_planner import LotSpec, plan_rsu_sell
from app.utils.goal_optimizer import _lot_rank_score


def test_lookback_purchase_is_85pct_of_min_fmv():
    # Grant $50, purchase $80, 15% → pay 85% of $50 = $42.50
    assert lookback_purchase_price(50, 80, 0.15) == pytest.approx(42.50)
    assert lookback_purchase_price(80, 50, 0.15) == pytest.approx(42.50)


def test_offering_inferred_six_months_when_grant_equals_purchase():
    p = date(2026, 4, 15)
    assert offering_start_for(grant_date=p, purchase_date=p) == date(2025, 10, 15)


def test_offering_uses_grant_when_grant_is_before_purchase():
    assert offering_start_for(
        grant_date=date(2025, 10, 15), purchase_date=date(2026, 4, 15)
    ) == date(2025, 10, 15)


def test_dd_bargain_ordinary_not_rsu_ltcg():
    """Sale 10 months after purchase: DD. Ordinary = purchase FMV − purchase price."""
    r = analyze_espp_sale(
        shares=100,
        sale_price=100.0,
        sale_date=date(2026, 6, 1),
        purchase_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        grant_fmv=50.0,
        purchase_fmv=80.0,
        discount=0.15,
    )
    assert r.disposition_code == 'DD'
    assert r.purchase_price_per_share == pytest.approx(42.50)
    # Bargain at purchase $80 − $42.50 = $37.50/sh
    assert r.ordinary_income == pytest.approx(3750.0)
    # Residual CG = $100 − $80 = $20/sh
    assert r.capital_gain == pytest.approx(2000.0)
    assert r.fica_ordinary == 0.0
    assert r.is_long_term is False


def test_dd_ordinary_capped_at_actual_gain():
    r = analyze_espp_sale(
        shares=100,
        sale_price=60.0,
        sale_date=date(2026, 6, 1),
        purchase_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        grant_fmv=50.0,
        purchase_fmv=80.0,
        discount=0.15,
    )
    assert r.disposition_code == 'DD'
    # Actual gain $60 − $42.50 = $17.50/sh < $37.50 bargain
    assert r.ordinary_income == pytest.approx(1750.0)
    assert r.capital_gain == pytest.approx(0.0)


def test_dd_sale_below_purchase_is_capital_loss_no_ordinary():
    r = analyze_espp_sale(
        shares=100,
        sale_price=40.0,
        sale_date=date(2026, 6, 1),
        purchase_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        grant_fmv=50.0,
        purchase_fmv=80.0,
        discount=0.15,
    )
    assert r.ordinary_income == pytest.approx(0.0)
    assert r.capital_gain == pytest.approx(-250.0)  # 40 − 42.50


def test_qd_ordinary_is_grant_date_discount_not_purchase_bargain():
    r = analyze_espp_sale(
        shares=100,
        sale_price=100.0,
        sale_date=date(2027, 11, 1),
        purchase_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        offering_start=date(2025, 10, 15),
        grant_fmv=50.0,
        purchase_fmv=80.0,
        discount=0.15,
    )
    assert r.disposition_code == 'QD'
    # Grant discount $50 − $42.50 = $7.50/sh — NOT the $37.50 purchase bargain
    assert r.ordinary_income == pytest.approx(750.0)
    assert r.capital_gain == pytest.approx(100 * (100 - 42.50) - 750)
    assert r.fica_ordinary == 0.0
    assert r.is_long_term is True


def test_analyze_lot_never_falls_through_to_rsu_for_espp():
    lot = LotSaleInput(
        vest_event_id=90,
        grant_id=1,
        share_type='espp',
        grant_type='espp',
        shares=100,
        sale_price=100.0,
        sale_date=date(2026, 6, 1),
        vest_date=date(2026, 4, 15),
        grant_date=date(2026, 4, 15),
        cost_basis_per_share=80.0,  # what an RSU model would use (purchase FMV)
        espp_discount=0.0,  # stored 0 must still be ESPP
        fmv_at_grant=50.0,
        fmv_at_purchase=80.0,
    )
    r = analyze_lot(lot)
    assert r.disposition == 'DD'
    assert r.ordinary_income == pytest.approx(3750.0)
    assert r.ordinary_income != 0
    # RSU model would be CG vs $80 = $20/sh, ordinary $0
    assert r.capital_gain == pytest.approx(2000.0)
    assert 'RSU' not in ' '.join(r.notes)


def test_plan_espp_sale_copy_is_not_rsu():
    spec = LotSpec(
        vest_event_id=90,
        grant_id=1,
        share_type='espp',
        grant_type='espp',
        is_iso=False,
        shares=100,
        vest_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        strike_price=0,
        cost_basis_per_share=80,
        espp_discount=0.15,
        fmv_at_grant=50,
        fmv_at_purchase=80,
        label='ESPP',
    )
    p = plan_rsu_sell(
        {
            'filing_status': 'single',
            'tax_year': 2026,
            'state_code': 'CA',
            'use_bracket_engine': True,
            'use_state_engine': True,
            'other_ordinary_income': 136_000,
            'include_fica': False,
            'include_niit': True,
        },
        [spec],
        sale_date=date(2026, 6, 1),
        sale_price=100,
    )
    assert p.name == 'Sell ESPP'
    assert p.timeline[0].title == 'ESPP sale'
    a = p.years[0].analysis
    assert a['equity_ordinary'] == pytest.approx(3750.0)
    assert a['lots'][0]['disposition'] == 'DD'


def test_optimizer_espp_reason_is_not_rsu_ltcg():
    spec = LotSpec(
        vest_event_id=1,
        grant_id=1,
        share_type='espp',
        grant_type='espp',
        is_iso=False,
        shares=100,
        vest_date=date(2026, 4, 15),
        grant_date=date(2025, 10, 15),
        strike_price=0,
        cost_basis_per_share=80,
        espp_discount=0.15,
        fmv_at_grant=50,
        fmv_at_purchase=80,
        shares_available=100,
    )
    score, reason, is_lt, disp = _lot_rank_score(
        spec, price=100, sale_date=date(2026, 6, 1), mode='sell_held'
    )
    assert disp == 'disqualifying'
    assert 'RSU long-term' not in reason
    assert 'ESPP' in reason
    assert 'disqualifying' in reason.lower() or 'bargain' in reason.lower()
