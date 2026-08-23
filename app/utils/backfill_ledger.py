"""
Idempotent backfill: VestEvent + StockSale + ISOExercise → TaxLot + LedgerEntry.

Does not drop old columns. Safe to re-run (skips users who already have lots).
"""

from __future__ import annotations

import logging
from app import db
from app.models.grant import Grant, ShareType
from app.models.vest_event import VestEvent
from app.models.stock_sale import StockSale, ISOExercise
from app.models.tax_lot import TaxLot, LedgerEntry
from app.models.user import User
from app.utils.shares import whole_shares

logger = logging.getLogger(__name__)


def _kind_for_grant(grant: Grant) -> str:
    st = grant.share_type
    if st in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value):
        return 'iso_option'
    if st == ShareType.CASH.value:
        return 'cash'
    if (grant.grant_type or '') in ('espp', 'nqespp'):
        return 'espp'
    return 'rsu'


def backfill_user(user_id: int, *, force: bool = False) -> dict:
    existing = TaxLot.query.filter_by(user_id=user_id).count()
    if existing and not force:
        return {'user_id': user_id, 'skipped': True, 'lots': existing}

    grants = {g.id: g for g in Grant.query.filter_by(user_id=user_id).all()}
    vests = (
        VestEvent.query.join(Grant)
        .filter(Grant.user_id == user_id)
        .order_by(VestEvent.vest_date.asc())
        .all()
    )
    sales_by_vest: dict[int, list] = {}
    for s in StockSale.query.filter_by(user_id=user_id).all():
        if s.vest_event_id:
            sales_by_vest.setdefault(s.vest_event_id, []).append(s)
    exercises_by_vest: dict[int, list] = {}
    for e in ISOExercise.query.filter_by(user_id=user_id).all():
        exercises_by_vest.setdefault(e.vest_event_id, []).append(e)

    created = 0
    for vest in vests:
        grant = grants.get(vest.grant_id) or vest.grant
        if not grant:
            continue
        kind = _kind_for_grant(grant)
        if kind == 'cash':
            continue
        vested = float(whole_shares(vest.shares_vested))
        withheld = float(whole_shares(vest.shares_sold or 0))
        received = max(0.0, vested - withheld)
        acquired = vest.vest_date
        is_iso = kind == 'iso_option'
        if is_iso:
            basis = float(grant.share_price_at_grant or 0)
            fmv = float(getattr(vest, 'fmv_at_vest', None) or 0)
        else:
            try:
                fmv = float(vest.share_price_at_vest or 0)
            except Exception:
                fmv = float(vest.fmv_at_vest or 0)
            basis = fmv

        option_lot = TaxLot(
            user_id=user_id,
            grant_id=grant.id,
            vest_event_id=vest.id,
            kind=kind,
            acquired_date=acquired,
            original_qty=vested,
            remaining_qty=0.0 if is_iso else received,
            cost_basis_per_share=basis,
            fmv_at_open=fmv or None,
            strike_price=float(grant.share_price_at_grant or 0) if is_iso else None,
            status='open',
        )
        if is_iso:
            # Unexercised options remain on the option lot
            exercised = sum(float(e.shares_exercised or 0) for e in exercises_by_vest.get(vest.id, []))
            option_lot.remaining_qty = max(0.0, received - float(whole_shares(exercised)))
        else:
            sold = sum(float(s.shares_sold or 0) for s in sales_by_vest.get(vest.id, []))
            option_lot.remaining_qty = max(0.0, received - float(whole_shares(sold)))
        option_lot.close_if_empty()
        db.session.add(option_lot)
        db.session.flush()
        created += 1

        db.session.add(LedgerEntry(
            user_id=user_id,
            lot_id=option_lot.id,
            kind='vest',
            entry_date=acquired,
            qty=vested,
            price=fmv or basis,
        ))
        if withheld > 0:
            db.session.add(LedgerEntry(
                user_id=user_id,
                lot_id=option_lot.id,
                kind='withhold',
                entry_date=acquired,
                qty=-withheld,
                price=fmv or basis,
            ))
            if not is_iso:
                # remaining already net of withhold via received
                pass

        if is_iso:
            for e in exercises_by_vest.get(vest.id, []):
                eqty = float(whole_shares(e.shares_exercised or 0))
                if eqty <= 0:
                    continue
                still = float(whole_shares(
                    e.shares_still_held if e.shares_still_held is not None else eqty
                ))
                stock = TaxLot(
                    user_id=user_id,
                    grant_id=grant.id,
                    vest_event_id=vest.id,
                    parent_lot_id=option_lot.id,
                    kind='iso_stock',
                    acquired_date=e.exercise_date,
                    original_qty=eqty,
                    remaining_qty=still,
                    cost_basis_per_share=float(e.strike_price or grant.share_price_at_grant or 0),
                    fmv_at_open=float(e.fmv_at_exercise or 0) or None,
                    strike_price=float(e.strike_price or grant.share_price_at_grant or 0),
                    status='open',
                )
                stock.close_if_empty()
                db.session.add(stock)
                db.session.flush()
                created += 1
                db.session.add(LedgerEntry(
                    user_id=user_id,
                    lot_id=option_lot.id,
                    kind='exercise',
                    entry_date=e.exercise_date,
                    qty=-eqty,
                    price=float(e.fmv_at_exercise or 0),
                    exercise_id=e.id,
                ))
                db.session.add(LedgerEntry(
                    user_id=user_id,
                    lot_id=stock.id,
                    kind='exercise',
                    entry_date=e.exercise_date,
                    qty=eqty,
                    price=float(e.fmv_at_exercise or 0),
                    exercise_id=e.id,
                ))
                for s in sales_by_vest.get(vest.id, []):
                    sq = float(whole_shares(s.shares_sold or 0))
                    db.session.add(LedgerEntry(
                        user_id=user_id,
                        lot_id=stock.id,
                        kind='sale',
                        entry_date=s.sale_date,
                        qty=-sq,
                        price=float(s.sale_price or 0),
                        fees=float(s.commission_fees or 0),
                        sale_id=s.id,
                    ))
        else:
            for s in sales_by_vest.get(vest.id, []):
                sq = float(whole_shares(s.shares_sold or 0))
                db.session.add(LedgerEntry(
                    user_id=user_id,
                    lot_id=option_lot.id,
                    kind='sale',
                    entry_date=s.sale_date,
                    qty=-sq,
                    price=float(s.sale_price or 0),
                    fees=float(s.commission_fees or 0),
                    sale_id=s.id,
                ))

    db.session.commit()
    lots = TaxLot.query.filter_by(user_id=user_id).all()
    stock = sum(float(l.remaining_qty or 0) for l in lots if l.is_stock())
    opt = sum(float(l.remaining_qty or 0) for l in lots if l.is_option())
    return {
        'user_id': user_id,
        'skipped': False,
        'lots': len(lots),
        'stock_remaining': stock,
        'option_remaining': opt,
        'created': created,
    }


def backfill_all(*, force: bool = False) -> list:
    out = []
    for u in User.query.order_by(User.id).all():
        try:
            out.append(backfill_user(u.id, force=force))
        except Exception as e:
            logger.exception('backfill user %s failed', u.id)
            db.session.rollback()
            out.append({'user_id': u.id, 'error': str(e)})
    return out
