"""Sell-to-cover lots, post-stub withholding credits, supplemental rate."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_supplemental_rate_default_22_and_elected_37():
    from app.utils.withholding import (
        federal_supplemental_withholding,
        resolve_federal_supplemental_rate,
        resolve_withholding_stub_date,
        split_withholding_by_rates,
    )

    assert federal_supplemental_withholding(100_000) == pytest.approx(22_000)
    assert federal_supplemental_withholding(100_000, rate=0.37) == pytest.approx(37_000)
    assert federal_supplemental_withholding(100_000, rate=37) == pytest.approx(37_000)
    # Elected 25% until $1M, then at least 37% on the excess.
    mixed = federal_supplemental_withholding(
        250_000, ytd_supplemental_before=800_000, rate=0.25,
    )
    assert mixed == pytest.approx(200_000 * 0.25 + 50_000 * 0.37)

    class Blank:
        federal_supplemental_rate = None

    assert resolve_federal_supplemental_rate(Blank(), None) == pytest.approx(0.22)

    class Elected:
        federal_supplemental_rate = 0.37

    assert resolve_federal_supplemental_rate(Elected(), Blank()) == pytest.approx(0.37)

    class OtherYtd:
        federal_withholding_ytd = 5_000
        state_withholding_ytd = 2_000
        withholding_stub_date = None

    class Seeded:
        federal_withholding_ytd = 42_110.89
        state_withholding_ytd = 19_257.25
        withholding_stub_date = date(2026, 7, 31)

    # Another user's own YTD must not inherit the seeded stub date.
    assert resolve_withholding_stub_date(OtherYtd(), Seeded()) is None
    assert resolve_withholding_stub_date(None, OtherYtd()) is None
    assert resolve_withholding_stub_date(None, Seeded()) == date(2026, 7, 31)

    class SeedAmountsOnly:
        federal_withholding_ytd = 42_110.89
        state_withholding_ytd = 19_257.25
        withholding_stub_date = None

    assert resolve_withholding_stub_date(None, SeedAmountsOnly()) == date(2026, 7, 31)

    split = split_withholding_by_rates(10_000, {
        'federal': 0.37,
        'state': 0.1023,
        'social_security': 0.0,
        'medicare': 0.0145,
        'additional_medicare': 0.009,
        'sdi': 0.011,
    })
    weight = 0.37 + 0.1023 + 0.0145 + 0.009 + 0.011
    assert split['federal'] == pytest.approx(10_000 * 0.37 / weight)
    assert split['state'] == pytest.approx(10_000 * 0.1023 / weight)
    assert split['payroll'] == pytest.approx(10_000 * (0.0145 + 0.009 + 0.011) / weight)
    assert split['federal'] + split['state'] < split['actual']
    assert split['federal'] + split['state'] + split['payroll'] == pytest.approx(10_000)


def test_future_vest_withholding_uses_elected_rate_not_35_percent():
    from app import create_app, db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent

    app = create_app()
    with app.app_context():
        user = User(username='supp-rate-user', email='supp-rate-user@example.com')
        user.set_password('Test-password-1!')
        db.session.add(user)
        db.session.flush()
        grant = Grant(
            user_id=user.id,
            grant_date=date(2024, 1, 1),
            grant_type='new_hire',
            share_type='rsu',
            share_quantity=100,
            share_price_at_grant=10.0,
            vest_years=1,
            cliff_years=0,
        )
        db.session.add(grant)
        db.session.flush()
        vest = VestEvent(
            grant_id=grant.id,
            vest_date=date.today() + timedelta(days=40),
            shares_vested=100,
            fmv_at_vest=50.0,
            shares_sold=0,
            cash_covered_all=True,
            tax_year=date.today().year,
        )
        db.session.add(vest)
        db.session.commit()
        try:
            est = vest.estimate_tax_withholding(
                current_stock_price=50.0,
                user=user,
                _tax_profile={
                    'filing_status': 'single',
                    'state_code': 'CA',
                    'federal_supplemental_rate': 0.37,
                    'include_fica': False,
                    'ss_wage_base_maxed': True,
                    'other_ordinary_income_raw': 500_000,
                },
            )
            gross = 100 * 50.0
            assert est['federal_withholding'] == pytest.approx(gross * 0.37)
            assert est['federal_rate'] == pytest.approx(0.37)
            # Not the old 22% projection and not a flat 35% bracket.
            assert est['federal_withholding'] != pytest.approx(gross * 0.22)
            assert est['federal_withholding'] != pytest.approx(gross * 0.35)
        finally:
            db.session.delete(vest)
            db.session.delete(grant)
            db.session.delete(user)
            db.session.commit()


def test_sell_to_cover_syncs_lot_and_leaves_other_users():
    from app import create_app, db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.tax_lot import TaxLot
    from app.models.stock_sale import StockSale
    from app.utils.ledger import sync_vest_lot_remaining
    from app.utils.lot_inventory import build_lots_for_user

    app = create_app()
    with app.app_context():
        owner = User(username='lot-owner-rsu', email='lot-owner-rsu@example.com')
        owner.set_password('Test-password-1!')
        other = User(username='lot-other-rsu', email='lot-other-rsu@example.com')
        other.set_password('Test-password-1!')
        db.session.add_all([owner, other])
        db.session.flush()

        def _grant(user, qty):
            g = Grant(
                user_id=user.id,
                grant_date=date(2024, 5, 15),
                grant_type='new_hire',
                share_type='rsu',
                share_quantity=qty,
                share_price_at_grant=20.0,
                vest_years=4,
                cliff_years=1,
            )
            db.session.add(g)
            db.session.flush()
            return g

        g_owner = _grant(owner, 109)
        g_other = _grant(other, 80)
        vest = VestEvent(
            grant_id=g_owner.id,
            vest_date=date(2026, 11, 15),
            shares_vested=109,
            shares_sold=54,
            cash_covered_all=False,
            cash_paid=0,
            fmv_at_vest=100.0,
            tax_year=2026,
        )
        other_vest = VestEvent(
            grant_id=g_other.id,
            vest_date=date(2026, 11, 15),
            shares_vested=80,
            shares_sold=0,
            cash_covered_all=True,
            fmv_at_vest=100.0,
            tax_year=2026,
        )
        db.session.add_all([vest, other_vest])
        db.session.flush()
        lot = TaxLot(
            user_id=owner.id,
            grant_id=g_owner.id,
            vest_event_id=vest.id,
            kind='rsu',
            acquired_date=vest.vest_date,
            original_qty=109,
            remaining_qty=109,  # stale: ignores 54 shares sold for taxes
            cost_basis_per_share=100.0,
            status='open',
        )
        other_lot = TaxLot(
            user_id=other.id,
            grant_id=g_other.id,
            vest_event_id=other_vest.id,
            kind='rsu',
            acquired_date=other_vest.vest_date,
            original_qty=80,
            remaining_qty=80,
            cost_basis_per_share=100.0,
            status='open',
        )
        iso_lot = TaxLot(
            user_id=other.id,
            grant_id=g_other.id,
            vest_event_id=other_vest.id,
            kind='iso_stock',
            acquired_date=other_vest.vest_date,
            original_qty=12,
            remaining_qty=12,
            cost_basis_per_share=5.0,
            status='open',
        )
        db.session.add_all([lot, other_lot, iso_lot])
        db.session.commit()
        try:
            first = sync_vest_lot_remaining(vest, commit=True)
            assert first['changed'] is True
            assert first['remaining'] == 55
            db.session.refresh(lot)
            assert lot.remaining_qty == 55

            built = {row['vest_event_id']: row for row in build_lots_for_user(owner.id)}
            assert built[vest.id]['shares_available'] == 55
            assert built[vest.id]['shares_received'] == 55

            # Market sale after the lot is correct: remaining falls by the sale only.
            db.session.add(StockSale(
                user_id=owner.id,
                vest_event_id=vest.id,
                sale_date=date(2026, 11, 16),
                shares_sold=10,
                sale_price=110.0,
                total_proceeds=1100.0,
                cost_basis_per_share=100.0,
                total_cost_basis=1000.0,
                capital_gain=100.0,
                is_long_term=False,
            ))
            db.session.commit()
            second = sync_vest_lot_remaining(vest, commit=True)
            assert second['remaining'] == 45
            again = sync_vest_lot_remaining(vest, commit=True)
            assert again['changed'] is False
            assert again['remaining'] == 45

            # Editing shares sold back to zero restores shares that were not sold on the market.
            vest.shares_sold = 0
            vest.cash_covered_all = True
            db.session.commit()
            restored = sync_vest_lot_remaining(vest, commit=True)
            assert restored['remaining'] == 99  # 109 received − 10 market sale

            db.session.refresh(other_lot)
            db.session.refresh(iso_lot)
            assert other_lot.remaining_qty == 80
            assert iso_lot.remaining_qty == 12
            sync_vest_lot_remaining(other_vest, commit=True)
            db.session.refresh(other_lot)
            assert other_lot.remaining_qty == 80
        finally:
            StockSale.query.filter_by(user_id=owner.id).delete()
            TaxLot.query.filter(TaxLot.user_id.in_([owner.id, other.id])).delete(synchronize_session=False)
            VestEvent.query.filter(VestEvent.id.in_([vest.id, other_vest.id])).delete(synchronize_session=False)
            Grant.query.filter(Grant.id.in_([g_owner.id, g_other.id])).delete(synchronize_session=False)
            User.query.filter(User.id.in_([owner.id, other.id])).delete(synchronize_session=False)
            db.session.commit()


def test_post_stub_vest_withholding_is_an_income_tax_credit():
    from unittest.mock import patch

    from app import create_app, db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.tax_profile import TaxProfile
    from app.models.vest_event import VestEvent
    from app.utils.cash_vs_tax import build_cash_vs_tax
    from app.utils.tax_constants import (
        STUB_FEDERAL_WITHHOLDING_YTD,
        STUB_STATE_WITHHOLDING_YTD,
    )

    app = create_app()
    with app.app_context():
        user = User(username='stub-vest-user', email='stub-vest-user@example.com')
        user.set_password('Test-password-1!')
        user.ss_wage_base_maxed = True
        db.session.add(user)
        db.session.flush()
        profile = TaxProfile(
            user_id=user.id,
            filing_status='single',
            state_code='CA',
            other_ordinary_income=0,
            include_fica=True,
            ss_wage_base_maxed=True,
            use_bracket_engine=True,
            use_state_engine=True,
            tax_year=2026,
            federal_withholding_ytd=STUB_FEDERAL_WITHHOLDING_YTD,
            state_withholding_ytd=STUB_STATE_WITHHOLDING_YTD,
            withholding_stub_date=None,  # seeded amounts imply 2026-07-31
            federal_supplemental_rate=0.37,
        )
        db.session.add(profile)
        grant = Grant(
            user_id=user.id,
            grant_date=date(2024, 5, 15),
            grant_type='annual_performance',
            share_type='rsu',
            share_quantity=400,
            share_price_at_grant=16.0,
            vest_years=4,
            cliff_years=0,
        )
        db.session.add(grant)
        db.session.flush()

        def _vest(on, shares, sold):
            row = VestEvent(
                grant_id=grant.id,
                vest_date=on,
                shares_vested=shares,
                shares_sold=sold,
                cash_covered_all=sold == 0,
                cash_paid=0,
                fmv_at_vest=100.0,
                tax_year=2026,
            )
            db.session.add(row)
            return row

        pre = _vest(date(2026, 6, 1), 80, 40)       # inside the stub
        on_stub = _vest(date(2026, 7, 31), 20, 10)  # on the stub date
        post = _vest(date(2026, 11, 15), 100, 40)   # after the stub
        future = _vest(date(2026, 12, 15), 200, 0)
        db.session.commit()
        try:
            with patch('app.utils.price_utils.get_latest_user_price', return_value=10.0):
                report = build_cash_vs_tax(user, tax_year=2026, profile=profile, as_of=date(2026, 11, 17))
            wh = report['withholding']
            assert wh['stub_date'] == '2026-07-31'
            assert wh['federal_supplemental_rate'] == pytest.approx(0.37)
            assert wh['post_stub_vests'] == 1
            # June and 7/31 withholding must not be added again.
            assert wh['post_stub_federal'] > 0
            assert wh['post_stub_federal'] < 40 * 100  # payroll slice excluded
            assert wh['post_stub_state'] > 0
            assert wh['post_stub_payroll'] > 0
            assert wh['federal_locked'] == pytest.approx(
                STUB_FEDERAL_WITHHOLDING_YTD + wh['post_stub_federal']
            )
            assert wh['state_locked'] == pytest.approx(
                STUB_STATE_WITHHOLDING_YTD + wh['post_stub_state']
            )
            # Future December vest at the live price, elected 37% — not 22% and not 35%.
            assert wh['federal_coming'] == pytest.approx(200 * 10 * 0.37)
            assert report['still_coming']['withholding'] > 0
            # The $4,000 sell-to-cover is fully allocated, and payroll is not an income-tax credit.
            assert (
                wh['post_stub_federal'] + wh['post_stub_state'] + wh['post_stub_payroll']
            ) == pytest.approx(40 * 100)
            assert report['expected_withholding'] == pytest.approx(
                wh['federal_locked'] + wh['state_locked']
                + wh['federal_coming'] + wh['state_coming']
                + wh['estimated_payments']
            )
            assert pre.shares_sold == 40 and on_stub.shares_sold == 10 and post.id and future.id
        finally:
            VestEvent.query.filter_by(grant_id=grant.id).delete(synchronize_session=False)
            db.session.delete(grant)
            db.session.delete(profile)
            db.session.delete(user)
            db.session.commit()
