"""
Thorough: vest FMV must be stored and drive RSU cost basis / sale tax gain.

Covers:
  - resolve + persist snapshot on VestEvent.fmv_at_vest
  - lot inventory basis matches stored FMV (~$40), not $0
  - analyze_sales gain = (sale − vest FMV) × shares
  - background job path (no request / no current_user) still decrypts prices
"""

from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app, db
from app.models.user import User
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
from app.models.user_price import UserPrice
from app.utils.encryption import generate_user_key, encrypt_with_master, encrypt_for_user


@pytest.fixture()
def app_ctx(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('VESTX_MASTER_KEY', Fernet.generate_key().decode())
    # Reset cached master fernet if any
    try:
        from app.utils import encryption as enc
        if hasattr(enc, '_master_fernet'):
            enc._master_fernet = None
    except Exception:
        pass
    app = create_app()
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['TESTING'] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _make_user_with_key(username='basis_tester'):
    user = User(username=username, email=f'{username}@example.com')
    user.set_password('test-password-long')
    user_key = generate_user_key()
    user.encrypted_user_key = encrypt_with_master(user_key)
    db.session.add(user)
    db.session.commit()
    return user, user_key


def _add_price(user, user_key, on: date, price: float):
    row = UserPrice(
        user_id=user.id,
        valuation_date=on,
        encrypted_price=encrypt_for_user(user_key, f'{price:.4f}'),
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_resolve_persists_fmv_and_inventory_basis(app_ctx):
    from app.utils.vest_basis import resolve_vest_fmv, backfill_user_vest_fmv
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.market_data import public_market_start

    user, key = _make_user_with_key()
    cutover = public_market_start()
    # Vest well before public cutover so private prices apply
    vest_day = cutover - timedelta(days=400)
    _add_price(user, key, vest_day - timedelta(days=5), 40.0)

    grant = Grant(
        user_id=user.id,
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        grant_date=vest_day - timedelta(days=365),
        share_quantity=1000,
        vest_years=1,
        cliff_years=0,
        share_price_at_grant=40.0,
    )
    db.session.add(grant)
    db.session.flush()
    vest = VestEvent(
        grant_id=grant.id,
        vest_date=vest_day,
        shares_vested=1000,
        shares_sold=0,
        cash_paid=0,
        tax_year=vest_day.year,
        fmv_at_vest=None,  # force resolve from history
    )
    db.session.add(vest)
    db.session.commit()

    # Background path: no request context (already app_context only)
    with patch('app.utils.price_utils.has_request_context', return_value=False):
        fmv, src = resolve_vest_fmv(vest, user_id=user.id, persist=True)
        assert fmv == pytest.approx(40.0, abs=0.01), (fmv, src)
        assert src in ('as_of', 'stored', 'private_as_of', 'public_as_of', 'grant_price')
        db.session.refresh(vest)
        assert float(vest.fmv_at_vest) == pytest.approx(40.0, abs=0.01)

        stats = backfill_user_vest_fmv(user.id)
        assert stats['still_missing'] == 0

        lots = build_lots_for_user(user.id)
        assert lots, 'expected inventory lots'
        lot = next(l for l in lots if l['vest_event_id'] == vest.id)
        assert lot['cost_basis_per_share'] == pytest.approx(40.0, abs=0.01)
        assert lot['fmv_at_vest'] == pytest.approx(40.0, abs=0.01)
        assert not lot.get('basis_missing')


def test_sale_tax_gain_uses_vest_fmv_not_zero(app_ctx):
    from app.utils.tax_engine import LotSaleInput, analyze_sales
    from app.utils.vest_basis import resolve_vest_fmv
    from app.utils.market_data import public_market_start

    user, key = _make_user_with_key('tax_basis')
    cutover = public_market_start()
    vest_day = cutover - timedelta(days=500)
    sale_day = vest_day + timedelta(days=400)  # LT
    _add_price(user, key, vest_day, 40.0)

    grant = Grant(
        user_id=user.id,
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        grant_date=vest_day - timedelta(days=30),
        share_quantity=100,
        vest_years=1,
        cliff_years=0,
        share_price_at_grant=40.0,
    )
    db.session.add(grant)
    db.session.flush()
    vest = VestEvent(
        grant_id=grant.id,
        vest_date=vest_day,
        shares_vested=100,
        tax_year=vest_day.year,
    )
    db.session.add(vest)
    db.session.commit()

    with patch('app.utils.price_utils.has_request_context', return_value=False):
        basis, _ = resolve_vest_fmv(vest, user_id=user.id, persist=True)
    assert basis == pytest.approx(40.0)

    sale_price = 108.37
    shares = 100.0
    lot = LotSaleInput(
        vest_event_id=vest.id,
        grant_id=grant.id,
        share_type='rsu',
        grant_type='new_hire',
        shares=shares,
        sale_price=sale_price,
        sale_date=sale_day,
        vest_date=vest_day,
        grant_date=grant.grant_date,
        cost_basis_per_share=basis,
        is_iso=False,
        label='test',
    )
    profile = {
        'filing_status': 'single',
        'tax_year': sale_day.year,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'other_ordinary_income': 200_000,
        'ytd_wages': 200_000,
        'include_fica': False,
        'include_niit': True,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
        'ca_amt_credit_carryforward': 0,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': None,
        'state_ordinary_rate': 0,
        'state_cg_rate': 0,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
    }
    a = analyze_sales(profile, [lot])
    expected_gain = (sale_price - 40.0) * shares
    wrong_gain_if_zero_basis = sale_price * shares
    assert a.ltcg == pytest.approx(expected_gain, abs=1.0)
    assert a.ltcg < wrong_gain_if_zero_basis - 1000
    # Tax on ~$68 gain/sh must be less than tax on full $108
    lot_zero = LotSaleInput(
        vest_event_id=vest.id,
        grant_id=grant.id,
        share_type='rsu',
        grant_type='new_hire',
        shares=shares,
        sale_price=sale_price,
        sale_date=sale_day,
        vest_date=vest_day,
        grant_date=grant.grant_date,
        cost_basis_per_share=0.0,
        is_iso=False,
        label='bad',
    )
    a0 = analyze_sales(profile, [lot_zero])
    assert a.total_tax < a0.total_tax - 100, (
        f'correct basis tax {a.total_tax} should be meaningfully below zero-basis {a0.total_tax}'
    )


def test_goal_optimizer_pick_basis_not_zero(app_ctx):
    from app.utils.goal_optimizer import GoalRequest, optimize_goal, inventory_to_specs
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.market_data import public_market_start

    user, key = _make_user_with_key('goal_basis')
    cutover = public_market_start()
    vest_day = cutover - timedelta(days=450)
    _add_price(user, key, vest_day, 40.0)
    # Live-ish price after for sale
    _add_price(user, key, cutover - timedelta(days=1), 108.37)

    grant = Grant(
        user_id=user.id,
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        grant_date=vest_day - timedelta(days=30),
        share_quantity=5000,
        vest_years=1,
        cliff_years=0,
        share_price_at_grant=40.0,
    )
    db.session.add(grant)
    db.session.flush()
    vest = VestEvent(
        grant_id=grant.id,
        vest_date=vest_day,
        shares_vested=5000,
        tax_year=vest_day.year,
    )
    db.session.add(vest)
    db.session.commit()

    with patch('app.utils.price_utils.has_request_context', return_value=False):
        lots = build_lots_for_user(user.id)
        assert lots[0]['cost_basis_per_share'] == pytest.approx(40.0, abs=0.05)

        goal = GoalRequest(
            target_net_cash=50_000,
            objective='min_tax',
            sale_price=108.37,
            sale_date=date.today(),
            allow_rsu=True,
            allow_iso_cashless=False,
            allow_iso_sell_held=False,
        )
        prof = {
            'filing_status': 'single',
            'tax_year': date.today().year,
            'state_code': 'CA',
            'use_bracket_engine': True,
            'use_state_engine': True,
            'other_ordinary_income': 200_000,
            'ytd_wages': 200_000,
            'include_fica': False,
            'include_niit': True,
            'ss_wage_base_maxed': True,
            'amt_credit_carryforward': 0,
            'ca_amt_credit_carryforward': 0,
            'federal_ordinary_rate': None,
            'federal_ltcg_rate': None,
            'state_ordinary_rate': 0,
            'state_cg_rate': 0,
        }
        r = optimize_goal(prof, lots, goal)
        assert r.picks
        for p in r.picks:
            if p.action == 'sell_rsu':
                assert p.basis_or_strike == pytest.approx(40.0, abs=0.05), p


def test_pre_ipo_vest_never_uses_public_ipo_price(app_ctx):
    """
    Screenshot regression: RSU grant/vest 2023-10-15 grant price $16.20 must NOT
    show Price at Vest = public IPO ~$160.95.
    """
    from app.utils.vest_basis import resolve_vest_fmv, recompute_user_vest_fmv
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.market_data import public_market_start
    from app.models.market_price import MarketPrice

    user, key = _make_user_with_key('ipo_pollute')
    cutover = public_market_start()
    vest_day = date(2023, 10, 15)
    assert vest_day < cutover

    # Poison: public IPO-level price only (no private history)
    db.session.add(MarketPrice(
        ticker='SPCX',
        valuation_date=cutover,
        price_per_share=160.95,
        source='test',
    ))
    db.session.commit()

    grant = Grant(
        user_id=user.id,
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        grant_date=vest_day,
        share_quantity=630,
        vest_years=0,
        cliff_years=0,
        share_price_at_grant=16.20,
    )
    db.session.add(grant)
    db.session.flush()
    vest = VestEvent(
        grant_id=grant.id,
        vest_date=vest_day,
        shares_vested=630,
        tax_year=2023,
        fmv_at_vest=160.95,  # poisoned snapshot as on production
    )
    db.session.add(vest)
    db.session.commit()

    with patch('app.utils.price_utils.has_request_context', return_value=False):
        # Auto-reject polluted stored + fall back to grant price
        fmv, src = resolve_vest_fmv(vest, user_id=user.id, persist=True)
        assert fmv == pytest.approx(16.20, abs=0.01), (fmv, src)
        assert src in ('grant_price', 'private_as_of')
        assert fmv != pytest.approx(160.95, abs=0.5)

        stats = recompute_user_vest_fmv(user.id)
        assert stats.get('still_missing', 1) == 0 or fmv > 0
        db.session.refresh(vest)
        assert float(vest.fmv_at_vest) == pytest.approx(16.20, abs=0.01)

        lots = build_lots_for_user(user.id)
        lot = next(l for l in lots if l['vest_event_id'] == vest.id)
        assert lot['cost_basis_per_share'] == pytest.approx(16.20, abs=0.01)
        assert lot['fmv_at_vest'] == pytest.approx(16.20, abs=0.01)


def test_pre_ipo_uses_private_price_not_grant_when_available(app_ctx):
    from app.utils.vest_basis import resolve_vest_fmv
    from app.utils.market_data import public_market_start
    from app.models.market_price import MarketPrice

    user, key = _make_user_with_key('private_ok')
    cutover = public_market_start()
    vest_day = date(2024, 3, 1)
    _add_price(user, key, date(2024, 2, 15), 42.50)
    db.session.add(MarketPrice(
        ticker='SPCX', valuation_date=cutover, price_per_share=160.95, source='test',
    ))
    db.session.commit()

    grant = Grant(
        user_id=user.id,
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        grant_date=date(2023, 1, 1),
        share_quantity=100,
        vest_years=4,
        cliff_years=1,
        share_price_at_grant=16.20,
    )
    db.session.add(grant)
    db.session.flush()
    vest = VestEvent(
        grant_id=grant.id,
        vest_date=vest_day,
        shares_vested=100,
        tax_year=2024,
        fmv_at_vest=None,
    )
    db.session.add(vest)
    db.session.commit()

    with patch('app.utils.price_utils.has_request_context', return_value=False):
        fmv, src = resolve_vest_fmv(vest, user_id=user.id, persist=True, force_recompute=True)
    assert fmv == pytest.approx(42.50, abs=0.01), (fmv, src)
    assert src == 'private_as_of'
