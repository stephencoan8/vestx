"""Shared mark-to-market rule and vesting-timeline invariant.

The dashboard hero Vested figure is held stock at the full price plus
unexercised vested options at max(0, price - strike). The timeline point on
today must match that figure, and today plus every future vest must match
Vested + Unvested.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.valuation import (  # noqa: E402
    build_vesting_timeline,
    held_position_from_lots,
    lot_market_value,
    mark_to_market,
    scheduled_vest_value,
)
from app.utils.portfolio_summary import value_unvested_events  # noqa: E402

PRICE = 139.75


def test_mark_to_market_rules():
    assert mark_to_market(10, PRICE, share_type='rsu') == pytest.approx(10 * PRICE)
    assert mark_to_market(10, PRICE, share_type='espp') == pytest.approx(10 * PRICE)
    # Exercised ISO stock still held is worth the full price.
    assert mark_to_market(
        10, PRICE, share_type='iso_6y', strike=20, unexercised=False,
    ) == pytest.approx(10 * PRICE)
    # Unexercised options, vested or scheduled, use the floored spread.
    assert mark_to_market(
        120, PRICE, share_type='iso_6y', strike=20, unexercised=True,
    ) == pytest.approx(120 * (PRICE - 20))
    assert mark_to_market(
        120, PRICE, share_type='iso_6y', strike=20, unexercised=True,
    ) != pytest.approx(120 * PRICE)
    assert mark_to_market(
        100, 50, share_type='iso_5y', strike=80, unexercised=True,
    ) == 0
    # Cash is not multiplied by the share price.
    assert mark_to_market(5000, PRICE, share_type='cash') == 5000


def test_scheduled_iso_tranche_is_not_full_price():
    """120-share ISO vest at $139.75 is $16,770 only if strike is ignored."""
    vest = SimpleNamespace(
        shares_vested=120,
        grant=SimpleNamespace(share_type='iso_6y', share_price_at_grant=20.0),
    )
    assert 120 * PRICE == pytest.approx(16770)
    assert scheduled_vest_value(vest, PRICE) == pytest.approx(120 * (PRICE - 20))


def test_lot_value_includes_unexercised_iso_and_floors():
    unexercised = {
        'shares_available': 0,
        'shares_unexercised': 1305,
        'is_iso': True,
        'share_type': 'iso_6y',
        'strike_price': 20.0,
    }
    assert lot_market_value(unexercised, PRICE) == pytest.approx(1305 * (PRICE - 20))
    assert lot_market_value(unexercised, 10) == 0

    exercised_stock = {
        'shares_available': 40,
        'shares_unexercised': 0,
        'is_iso': True,
        'share_type': 'iso_5y',
        'strike_price': 20.0,
    }
    assert lot_market_value(exercised_stock, PRICE) == pytest.approx(40 * PRICE)


def test_grant_and_vest_models_floor_iso_spread():
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent

    grant = SimpleNamespace(
        share_type='iso_6y',
        share_quantity=120,
        share_price_at_grant=200.0,
        current_share_price=PRICE,
        user_id=1,
    )
    assert Grant.current_value.fget(grant) == 0

    grant.share_price_at_grant = 20.0
    assert Grant.current_value.fget(grant) == pytest.approx(120 * (PRICE - 20))

    rsu = SimpleNamespace(
        share_type='rsu',
        share_quantity=10,
        share_price_at_grant=0,
        current_share_price=PRICE,
        user_id=1,
    )
    assert Grant.current_value.fget(rsu) == pytest.approx(10 * PRICE)

    cash = SimpleNamespace(
        share_type='cash',
        share_quantity=5000,
        share_price_at_grant=0,
        current_share_price=PRICE,
        user_id=1,
    )
    assert Grant.current_value.fget(cash) == 5000

    underwater = SimpleNamespace(
        grant=SimpleNamespace(share_type='iso_5y', share_price_at_grant=200.0),
        shares_vested=50,
        shares_received=50,
        share_price_at_vest=PRICE,
    )
    assert VestEvent.value_at_vest.fget(underwater) == 0
    assert VestEvent.net_value.fget(underwater) == 0

    in_the_money = SimpleNamespace(
        grant=SimpleNamespace(share_type='iso_6y', share_price_at_grant=20.0),
        shares_vested=120,
        shares_received=120,
        share_price_at_vest=PRICE,
    )
    assert VestEvent.value_at_vest.fget(in_the_money) == pytest.approx(120 * (PRICE - 20))


def _timeline_case():
    as_of = date(2026, 11, 11)
    lots = [
        {
            'vest_date': '2024-05-15',
            'shares_available': 850,
            'shares_unexercised': 0,
            'is_iso': False,
            'share_type': 'rsu',
            'strike_price': None,
        },
        {
            'vest_date': '2025-06-15',
            'shares_available': 0,
            'shares_unexercised': 1305,
            'is_iso': True,
            'share_type': 'iso_6y',
            'strike_price': 20.0,
        },
        {
            'vest_date': '2025-08-01',
            'shares_available': 0,
            'shares_unexercised': 100,
            'is_iso': True,
            'share_type': 'iso_5y',
            'strike_price': 200.0,
        },
    ]
    future = [
        SimpleNamespace(
            vest_date=date(2026, 12, 15),
            shares_vested=200,
            grant=SimpleNamespace(share_type='rsu', share_price_at_grant=0),
        ),
        SimpleNamespace(
            vest_date=date(2027, 1, 15),
            shares_vested=5000,
            grant=SimpleNamespace(share_type='cash', share_price_at_grant=0),
        ),
        SimpleNamespace(
            vest_date=date(2027, 2, 15),
            shares_vested=120,
            grant=SimpleNamespace(share_type='iso_6y', share_price_at_grant=20.0),
        ),
    ]
    prices = [(date(2024, 1, 1), 80.0), (date(2026, 11, 10), 100.0)]
    return as_of, lots, future, prices


def test_timeline_today_matches_held_value_and_future_adds_schedule():
    as_of, lots, future, prices = _timeline_case()
    timeline = build_vesting_timeline(
        lots, future, prices, live_price=PRICE, as_of=as_of,
    )
    held = held_position_from_lots(lots, PRICE)
    unvested = value_unvested_events(future, PRICE)

    today_points = [p for p in timeline if p['date'] == as_of.isoformat()]
    assert today_points
    today = today_points[-1]
    assert today['is_vested'] is True
    assert today['total_value'] == pytest.approx(held['available_value'])
    assert today['total_shares'] == int(held['held_shares'])
    # Gross vested shares would still include the 150 RSU withheld or sold
    # and would price the 1,305 options at the full $139.75.
    assert today['total_shares'] == 850
    assert today['total_value'] < 850 * PRICE + 1305 * PRICE

    assert timeline[-1]['total_value'] == pytest.approx(
        held['available_value'] + unvested['unavailable_value']
    )
    assert timeline[-1]['total_shares'] == int(held['held_shares']) + int(
        unvested['unavailable_shares']
    )
    # Cash grant is not added, and the ISO tranche is not full price.
    assert unvested['unavailable_value'] == pytest.approx(
        200 * PRICE + 120 * (PRICE - 20)
    )

    nov10 = [p for p in timeline if p['date'] == '2026-11-10']
    assert nov10
    assert nov10[-1]['total_value'] == pytest.approx(
        850 * 100 + 1305 * (100 - 20)
    )
    assert nov10[-1]['total_value'] != pytest.approx(today['total_value'])


@pytest.fixture()
def app_ctx(monkeypatch):
    monkeypatch.setenv('FLASK_ENV', 'testing')
    monkeypatch.setattr(
        'app.utils.market_data.sync_market_prices',
        lambda force=False: {'skipped': True},
    )
    monkeypatch.setattr(
        'app.utils.market_data.load_public_price_history',
        lambda as_of=None, ensure_sync=True: [],
    )
    from sqlalchemy.pool import StaticPool
    from app.config import TestingConfig
    TestingConfig.SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool,
        'connect_args': {'check_same_thread': False},
    }
    from app import create_app, db
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_timeline_value_at_today_equals_hero_vested(app_ctx):
    """Held lots (after withholding and sales) drive both numbers."""
    from app import db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.stock_sale import StockSale
    from app.utils.portfolio_summary import summarize_held_portfolio
    from sqlalchemy.orm import joinedload

    today = date.today()
    user = User(username='valuation_hero', email='valuation_hero@example.com')
    user.set_password('test-password-long')
    db.session.add(user)
    db.session.flush()

    rsu = Grant(
        user_id=user.id,
        grant_date=today - timedelta(days=800),
        grant_type='new_hire',
        share_type='rsu',
        share_quantity=1000,
        share_price_at_grant=20.0,
        vest_years=1,
        cliff_years=0,
    )
    iso = Grant(
        user_id=user.id,
        grant_date=today - timedelta(days=400),
        grant_type='annual_performance',
        share_type='iso_6y',
        share_quantity=1405,
        share_price_at_grant=20.0,
        vest_years=4,
        cliff_years=0,
    )
    underwater = Grant(
        user_id=user.id,
        grant_date=today - timedelta(days=300),
        grant_type='annual_performance',
        share_type='iso_5y',
        share_quantity=100,
        share_price_at_grant=200.0,
        vest_years=4,
        cliff_years=0,
    )
    cash = Grant(
        user_id=user.id,
        grant_date=today - timedelta(days=10),
        grant_type='annual_performance',
        share_type='cash',
        share_quantity=5000,
        share_price_at_grant=0,
        vest_years=1,
        cliff_years=0,
    )
    db.session.add_all([rsu, iso, underwater, cash])
    db.session.flush()

    rsu_vest = VestEvent(
        grant_id=rsu.id,
        vest_date=today - timedelta(days=500),
        shares_vested=1000,
        shares_sold=100,  # withheld at vest
        cash_covered_all=False,
        fmv_at_vest=20.0,
        tax_year=(today - timedelta(days=500)).year,
    )
    iso_vest = VestEvent(
        grant_id=iso.id,
        vest_date=today - timedelta(days=200),
        shares_vested=1305,
        shares_sold=0,
        fmv_at_vest=PRICE,
        tax_year=(today - timedelta(days=200)).year,
    )
    dead_iso = VestEvent(
        grant_id=underwater.id,
        vest_date=today - timedelta(days=100),
        shares_vested=100,
        shares_sold=0,
        fmv_at_vest=PRICE,
        tax_year=(today - timedelta(days=100)).year,
    )
    future_rsu = VestEvent(
        grant_id=rsu.id,
        vest_date=today + timedelta(days=30),
        shares_vested=200,
        shares_sold=0,
        tax_year=(today + timedelta(days=30)).year,
    )
    future_iso = VestEvent(
        grant_id=iso.id,
        vest_date=today + timedelta(days=60),
        shares_vested=120,
        shares_sold=0,
        tax_year=(today + timedelta(days=60)).year,
    )
    future_cash = VestEvent(
        grant_id=cash.id,
        vest_date=today + timedelta(days=45),
        shares_vested=5000,
        shares_sold=0,
        tax_year=(today + timedelta(days=45)).year,
    )
    db.session.add_all([rsu_vest, iso_vest, dead_iso, future_rsu, future_iso, future_cash])
    db.session.flush()
    db.session.add(StockSale(
        user_id=user.id,
        vest_event_id=rsu_vest.id,
        sale_date=today - timedelta(days=20),
        shares_sold=50,
        sale_price=PRICE,
        total_proceeds=50 * PRICE,
        cost_basis_per_share=20.0,
        total_cost_basis=1000.0,
        capital_gain=50 * PRICE - 1000.0,
        is_long_term=True,
    ))
    db.session.commit()

    held = summarize_held_portfolio(user.id, live_price=PRICE, as_of=today)
    events = (
        VestEvent.query.options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == user.id)
        .all()
    )
    timeline = build_vesting_timeline(
        held['lots'],
        [v for v in events if v.vest_date > today],
        [(today - timedelta(days=900), 50.0), (today - timedelta(days=1), 100.0)],
        live_price=PRICE,
        as_of=today,
    )

    # 1,000 vested − 100 withheld − 50 sold. Options are not in the share count.
    assert held['held_shares'] == 850
    assert held['iso_unexercised'] == 1405
    assert held['available_value'] == pytest.approx(850 * PRICE + 1305 * (PRICE - 20))
    assert held['unavailable_value'] == pytest.approx(200 * PRICE + 120 * (PRICE - 20))

    today_points = [p for p in timeline if p['date'] == today.isoformat() and p['is_vested']]
    assert today_points
    point = today_points[-1]
    assert point['total_value'] == pytest.approx(held['available_value'])
    assert point['total_shares'] == held['held_shares']
    assert timeline[-1]['total_value'] == pytest.approx(
        held['available_value'] + held['unavailable_value']
    )
    assert timeline[-1]['total_shares'] == held['held_shares'] + held['unavailable_shares']

    iso_lot = next(l for l in held['lots'] if l['shares_unexercised'] == 1305)
    assert iso_lot['shares_available'] == 0
    assert lot_market_value(iso_lot, PRICE) == pytest.approx(1305 * (PRICE - 20))
    assert lot_market_value(iso_lot, PRICE) != 0
