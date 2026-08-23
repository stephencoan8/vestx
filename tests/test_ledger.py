"""A sale must leave remaining_qty. Oversell is rejected."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_record_sale_decrements_lot_and_rejects_oversell():
    from app import create_app, db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.tax_lot import TaxLot
    from app.utils.ledger import record_sale, LedgerError

    app = create_app()
    with app.app_context():
        u = User.query.first()
        if not u:
            return
        g = Grant(
            user_id=u.id,
            grant_date=date(2024, 1, 1),
            grant_type='new_hire',
            share_type='rsu',
            share_quantity=100,
            share_price_at_grant=20.0,
            vest_years=1,
            cliff_years=0,
        )
        db.session.add(g)
        db.session.flush()
        vest = VestEvent(
            grant_id=g.id,
            vest_date=date.today() - timedelta(days=400),
            shares_vested=100,
            fmv_at_vest=25.0,
            shares_sold=0,
        )
        db.session.add(vest)
        db.session.flush()
        lot = TaxLot(
            user_id=u.id,
            grant_id=g.id,
            vest_event_id=vest.id,
            kind='rsu',
            acquired_date=vest.vest_date,
            original_qty=100,
            remaining_qty=100,
            cost_basis_per_share=25.0,
            fmv_at_open=25.0,
            status='open',
        )
        db.session.add(lot)
        db.session.commit()
        vid = vest.id
        lid = lot.id

        sale = record_sale(
            user_id=u.id,
            vest_event_id=vid,
            qty=40,
            price=140.0,
            sale_date=date.today(),
        )
        assert sale.shares_sold == 40
        db.session.expire_all()
        lot = TaxLot.query.get(lid)
        assert lot.remaining_qty == 60
        assert lot.status == 'open'

        try:
            record_sale(
                user_id=u.id,
                vest_event_id=vid,
                qty=61,
                price=140.0,
                sale_date=date.today(),
            )
            raise AssertionError('oversell should fail')
        except LedgerError as e:
            assert 'remaining' in str(e).lower()

        db.session.expire_all()
        lot = TaxLot.query.get(lid)
        assert lot.remaining_qty == 60

        record_sale(
            user_id=u.id,
            vest_event_id=vid,
            qty=60,
            price=141.0,
            sale_date=date.today(),
        )
        db.session.expire_all()
        lot = TaxLot.query.get(lid)
        assert lot.remaining_qty == 0
        assert lot.status == 'closed'

        db.session.delete(lot)
        VestEvent.query.filter_by(id=vid).delete()
        Grant.query.filter_by(id=g.id).delete()
        db.session.commit()


def test_build_lots_uses_tax_lot_remaining():
    from app import create_app, db
    from app.models.user import User
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.tax_lot import TaxLot
    from app.utils.lot_inventory import build_lots_for_user

    app = create_app()
    with app.app_context():
        u = User.query.first()
        if not u:
            return
        g = Grant(
            user_id=u.id,
            grant_date=date(2023, 5, 1),
            grant_type='new_hire',
            share_type='rsu',
            share_quantity=50,
            share_price_at_grant=10.0,
            vest_years=1,
            cliff_years=0,
        )
        db.session.add(g)
        db.session.flush()
        vest = VestEvent(
            grant_id=g.id,
            vest_date=date.today() - timedelta(days=400),
            shares_vested=50,
            fmv_at_vest=12.0,
            shares_sold=0,
        )
        db.session.add(vest)
        db.session.flush()
        lot = TaxLot(
            user_id=u.id,
            grant_id=g.id,
            vest_event_id=vest.id,
            kind='rsu',
            acquired_date=vest.vest_date,
            original_qty=50,
            remaining_qty=7,
            cost_basis_per_share=12.0,
            status='open',
        )
        db.session.add(lot)
        db.session.commit()
        built = {l['vest_event_id']: l for l in build_lots_for_user(u.id)}
        assert vest.id in built
        assert built[vest.id]['shares_available'] == 7
        db.session.delete(lot)
        VestEvent.query.filter_by(id=vest.id).delete()
        Grant.query.filter_by(id=g.id).delete()
        db.session.commit()


if __name__ == '__main__':
    test_record_sale_decrements_lot_and_rejects_oversell()
    test_build_lots_uses_tax_lot_remaining()
    print('LEDGER TESTS PASSED')
