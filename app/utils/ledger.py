"""
Single writer for inventory: a sale leaves the lot.

TaxLot.remaining_qty is SSOT. StockSale is still written so existing
Sold/Activity screens keep working until they read the journal.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app import db
from app.models.tax_lot import TaxLot, LedgerEntry
from app.models.stock_sale import StockSale
from app.utils.shares import whole_shares


class LedgerError(ValueError):
    pass


def lot_for_vest(user_id: int, vest_event_id: int, *, stock: bool = True) -> Optional[TaxLot]:
    q = TaxLot.query.filter_by(user_id=user_id, vest_event_id=vest_event_id)
    if stock:
        q = q.filter(TaxLot.kind.in_(('rsu', 'iso_stock', 'espp')))
    return q.order_by(TaxLot.id.desc()).first()


def record_sale(
    *,
    user_id: int,
    vest_event_id: int,
    qty: float,
    price: float,
    sale_date: date,
    fees: float = 0.0,
    notes: Optional[str] = None,
    commit: bool = True,
) -> StockSale:
    """
    SpecID sale against the open stock lot for this vest.
    Fails closed if qty > remaining (whole shares).
    """
    qty = float(whole_shares(qty))
    if qty <= 0:
        raise LedgerError('Sale quantity must be a positive whole share count')
    price = float(price or 0)
    if price <= 0:
        raise LedgerError('Sale price must be positive')

    lot = lot_for_vest(user_id, vest_event_id, stock=True)
    if lot is None:
        raise LedgerError('No open stock lot for that vest (record vest/exercise first)')
    remaining = float(whole_shares(lot.remaining_qty))
    if qty > remaining + 1e-9:
        raise LedgerError(
            f'Cannot sell {qty:.0f} sh — lot has {remaining:.0f} remaining'
        )

    proceeds = qty * price
    basis_ps = float(lot.cost_basis_per_share or 0)
    total_basis = qty * basis_ps
    gain = proceeds - total_basis - float(fees or 0)
    acquired = lot.acquired_date
    is_lt = bool(acquired and (sale_date - acquired).days > 365)

    sale = StockSale(
        user_id=user_id,
        vest_event_id=vest_event_id,
        sale_date=sale_date,
        shares_sold=qty,
        sale_price=price,
        total_proceeds=proceeds,
        cost_basis_per_share=basis_ps,
        total_cost_basis=total_basis,
        capital_gain=gain,
        is_long_term=is_lt,
        commission_fees=float(fees or 0),
        lot_selection_method='SpecID',
        notes=notes,
    )
    db.session.add(sale)
    db.session.flush()

    lot.remaining_qty = remaining - qty
    lot.close_if_empty()

    db.session.add(LedgerEntry(
        user_id=user_id,
        lot_id=lot.id,
        kind='sale',
        entry_date=sale_date,
        qty=-qty,
        price=price,
        fees=float(fees or 0),
        sale_id=sale.id,
        notes=notes,
    ))
    if lot.kind == 'iso_stock':
        _iso_reduce_still_held(user_id, vest_event_id, qty)
    if commit:
        db.session.commit()
    return sale


def _iso_reduce_still_held(user_id: int, vest_id: int, shares: float) -> None:
    from app.models.stock_sale import ISOExercise
    remaining = float(shares or 0)
    if remaining <= 0:
        return
    exs = (
        ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_id)
        .order_by(ISOExercise.exercise_date.asc())
        .all()
    )
    for ex in exs:
        if remaining <= 0:
            break
        held = ex.shares_still_held if ex.shares_still_held is not None else ex.shares_exercised
        take = min(float(held or 0), remaining)
        ex.shares_still_held = float(held or 0) - take
        remaining -= take


def _iso_restore_still_held(user_id: int, vest_id: int, shares: float) -> None:
    from app.models.stock_sale import ISOExercise
    remaining = float(shares or 0)
    if remaining <= 0:
        return
    exs = (
        ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_id)
        .order_by(ISOExercise.exercise_date.desc())
        .all()
    )
    for ex in exs:
        if remaining <= 0:
            break
        held = ex.shares_still_held if ex.shares_still_held is not None else ex.shares_exercised
        room = max(0.0, float(ex.shares_exercised or 0) - float(held or 0))
        take = min(room, remaining)
        ex.shares_still_held = float(held or 0) + take
        remaining -= take


def reverse_sale(*, user_id: int, sale: StockSale, commit: bool = True) -> None:
    """Put shares back on the lot and delete the sale row."""
    qty = float(whole_shares(sale.shares_sold or 0))
    lot = lot_for_vest(user_id, sale.vest_event_id, stock=True)
    if lot is not None and qty > 0:
        lot.remaining_qty = float(whole_shares(lot.remaining_qty)) + qty
        lot.status = 'open'
        db.session.add(LedgerEntry(
            user_id=user_id,
            lot_id=lot.id,
            kind='reverse',
            entry_date=sale.sale_date or date.today(),
            qty=qty,
            price=float(sale.sale_price or 0),
            sale_id=sale.id,
            notes='sale reversed',
        ))
        if lot.kind == 'iso_stock':
            _iso_restore_still_held(user_id, sale.vest_event_id, qty)
    db.session.delete(sale)
    if commit:
        db.session.commit()


def apply_sale_qty_delta(*, user_id: int, vest_event_id: int, delta: float) -> None:
    """delta > 0 means more shares sold (lot shrinks)."""
    if abs(float(delta or 0)) < 1e-9:
        return
    lot = lot_for_vest(user_id, vest_event_id, stock=True)
    if lot is None:
        raise LedgerError('No stock lot for that vest')
    remaining = float(whole_shares(lot.remaining_qty))
    d = float(whole_shares(delta))
    if d > remaining + 1e-9:
        raise LedgerError(
            f'Cannot sell {d:.0f} more sh — lot has {remaining:.0f} remaining'
        )
    lot.remaining_qty = remaining - d
    lot.close_if_empty()
    if lot.kind == 'iso_stock':
        if d > 0:
            _iso_reduce_still_held(user_id, vest_event_id, d)
        else:
            _iso_restore_still_held(user_id, vest_event_id, -d)


def record_exercise(
    *,
    user_id: int,
    vest_event_id: int,
    qty: float,
    exercise_date: date,
    fmv: float,
    strike: float,
    exercise_id: Optional[int] = None,
    commit: bool = True,
) -> TaxLot:
    """Convert ISO option remaining into an iso_stock lot."""
    qty = float(whole_shares(qty))
    if qty <= 0:
        raise LedgerError('Exercise quantity must be a positive whole share count')
    opt = (
        TaxLot.query.filter_by(
            user_id=user_id, vest_event_id=vest_event_id, kind='iso_option',
        )
        .order_by(TaxLot.id.desc())
        .first()
    )
    if opt is None:
        raise LedgerError('No ISO option lot for that vest')
    remaining = float(whole_shares(opt.remaining_qty))
    if qty > remaining + 1e-9:
        raise LedgerError(
            f'Cannot exercise {qty:.0f} — option lot has {remaining:.0f} remaining'
        )
    opt.remaining_qty = remaining - qty
    opt.close_if_empty()
    stock = TaxLot(
        user_id=user_id,
        grant_id=opt.grant_id,
        vest_event_id=vest_event_id,
        parent_lot_id=opt.id,
        kind='iso_stock',
        acquired_date=exercise_date,
        original_qty=qty,
        remaining_qty=qty,
        cost_basis_per_share=float(strike or 0),
        fmv_at_open=float(fmv or 0) or None,
        strike_price=float(strike or 0),
        status='open',
    )
    db.session.add(stock)
    db.session.flush()
    db.session.add(LedgerEntry(
        user_id=user_id, lot_id=opt.id, kind='exercise',
        entry_date=exercise_date, qty=-qty, price=fmv, exercise_id=exercise_id,
    ))
    db.session.add(LedgerEntry(
        user_id=user_id, lot_id=stock.id, kind='exercise',
        entry_date=exercise_date, qty=qty, price=fmv, exercise_id=exercise_id,
    ))
    if commit:
        db.session.commit()
    return stock


def sync_vest_lot_remaining(vest, *, commit: bool = False) -> dict:
    """
    Make inventory match shares received minus market sales.

    Shares received is vested minus shares sold for taxes. Market sales are
    StockSale rows on this vest. RSU and ESPP stock lots are set to that
    quantity (created when missing). ISO option lots are adjusted only when
    shares were actually withheld. ISO stock lots stay on the exercise/sale
    ledger so a sell-to-cover repair cannot inflate exercised shares.

    Idempotent: a second call with the same vest and sales changes nothing.
    """
    from app.models.grant import ShareType
    from app.models.stock_sale import StockSale, ISOExercise
    from app.utils.backfill_ledger import _kind_for_grant

    out = {'changed': False, 'remaining': None, 'previous': None, 'created': False}
    if vest is None:
        return out
    grant = getattr(vest, 'grant', None)
    if grant is None or not getattr(grant, 'user_id', None):
        return out
    if grant.share_type == ShareType.CASH.value:
        return out

    user_id = int(grant.user_id)
    kind = _kind_for_grant(grant)
    received = float(whole_shares(getattr(vest, 'shares_received', None) or 0))
    withheld = float(whole_shares(getattr(vest, 'shares_sold', None) or 0))
    vested_qty = float(whole_shares(getattr(vest, 'shares_vested', None) or 0))
    acquired = vest.vest_date

    if kind == 'iso_option':
        if withheld <= 0:
            return out
        exercised = 0.0
        for ex in ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest.id).all():
            exercised += float(whole_shares(ex.shares_exercised or 0))
        target = max(0.0, received - exercised)
        opt = (
            TaxLot.query.filter_by(user_id=user_id, vest_event_id=vest.id, kind='iso_option')
            .order_by(TaxLot.id.desc())
            .first()
        )
        if opt is None:
            return out
        return _set_lot_remaining(
            [opt], target, user_id=user_id, entry_date=acquired, commit=commit,
        )

    if kind not in ('rsu', 'espp'):
        return out

    sold = 0.0
    sales = StockSale.query.filter_by(user_id=user_id, vest_event_id=vest.id).all()
    for sale in sales:
        sold += float(whole_shares(sale.shares_sold or 0))
    target = max(0.0, received - sold)

    stock_lots = (
        TaxLot.query.filter_by(user_id=user_id, vest_event_id=vest.id)
        .filter(TaxLot.kind.in_(('rsu', 'espp')))
        .order_by(TaxLot.id.desc())
        .all()
    )
    if not stock_lots:
        try:
            fmv = float(getattr(vest, 'fmv_at_vest', None) or 0)
        except (TypeError, ValueError):
            fmv = 0.0
        lot = TaxLot(
            user_id=user_id,
            grant_id=grant.id,
            vest_event_id=vest.id,
            kind=kind,
            acquired_date=acquired,
            original_qty=vested_qty,
            remaining_qty=target,
            cost_basis_per_share=fmv,
            fmv_at_open=fmv or None,
            status='open' if target > 0 else 'closed',
        )
        lot.close_if_empty()
        db.session.add(lot)
        db.session.flush()
        db.session.add(LedgerEntry(
            user_id=user_id,
            lot_id=lot.id,
            kind='vest',
            entry_date=acquired,
            qty=vested_qty,
            price=fmv or None,
            notes='lot opened',
        ))
        if withheld > 0:
            db.session.add(LedgerEntry(
                user_id=user_id,
                lot_id=lot.id,
                kind='withhold',
                entry_date=acquired,
                qty=-withheld,
                price=fmv or None,
                notes='sell-to-cover',
            ))
        if sold > 0:
            # Sales already exist; remaining is net of them. Journal the outflow
            # so a brand-new lot's entries sum to remaining.
            for sale in sales:
                sq = float(whole_shares(sale.shares_sold or 0))
                if sq <= 0:
                    continue
                db.session.add(LedgerEntry(
                    user_id=user_id,
                    lot_id=lot.id,
                    kind='sale',
                    entry_date=sale.sale_date or acquired,
                    qty=-sq,
                    price=float(sale.sale_price or 0),
                    fees=float(sale.commission_fees or 0),
                    sale_id=sale.id,
                ))
        out = {'changed': True, 'remaining': target, 'previous': None, 'created': True}
        if commit:
            db.session.commit()
        return out

    return _set_lot_remaining(
        stock_lots, target, user_id=user_id, entry_date=acquired, commit=commit,
    )


def _set_lot_remaining(lots, target: float, *, user_id: int, entry_date, commit: bool) -> dict:
    """Set the sum of remaining_qty on these lots to ``target`` whole shares."""
    target = float(whole_shares(target))
    ordered = list(lots or [])
    if not ordered:
        return {'changed': False, 'remaining': None, 'previous': None, 'created': False}
    previous = sum(float(whole_shares(lot.remaining_qty)) for lot in ordered)
    delta = target - previous
    if abs(delta) < 0.5:
        for lot in ordered:
            if float(lot.remaining_qty or 0) > 0:
                lot.status = 'open'
            else:
                lot.close_if_empty()
        return {
            'changed': False,
            'remaining': previous,
            'previous': previous,
            'created': False,
        }

    if delta > 0:
        primary = ordered[0]
        primary.remaining_qty = float(whole_shares(primary.remaining_qty)) + delta
        primary.status = 'open'
    else:
        need = -delta
        for lot in sorted(ordered, key=lambda row: float(row.remaining_qty or 0), reverse=True):
            if need <= 0:
                break
            have = float(whole_shares(lot.remaining_qty))
            take = min(have, need)
            lot.remaining_qty = have - take
            lot.close_if_empty()
            need -= take
    db.session.add(LedgerEntry(
        user_id=user_id,
        lot_id=ordered[0].id,
        kind='withhold',
        entry_date=entry_date or date.today(),
        qty=delta,
        notes='sync remaining to shares received minus market sales',
    ))
    if commit:
        db.session.commit()
    return {
        'changed': True,
        'remaining': target,
        'previous': previous,
        'created': False,
    }


def repair_sell_to_cover_lots() -> dict:
    """
    Recompute TaxLot.remaining_qty from vest withholding and market sales.

    Safe to re-run. Lots that already match are not rewritten. ISO stock lots
    are not modified. Cash grants are skipped.
    """
    from app.models.vest_event import VestEvent

    checked = 0
    changed = 0
    errors = 0
    try:
        vests = VestEvent.query.order_by(VestEvent.id.asc()).all()
    except Exception:
        logger = __import__('logging').getLogger(__name__)
        logger.exception('sell-to-cover lot repair could not list vests')
        return {'checked': 0, 'changed': 0, 'errors': 1}

    logger = __import__('logging').getLogger(__name__)
    for vest in vests:
        checked += 1
        try:
            result = sync_vest_lot_remaining(vest, commit=False)
            if result.get('changed'):
                db.session.commit()
                changed += 1
        except Exception:
            errors += 1
            logger.exception('sell-to-cover lot repair failed for vest %s', getattr(vest, 'id', None))
            try:
                db.session.rollback()
            except Exception:
                pass
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    logger.info(
        'sell-to-cover lot repair checked=%s changed=%s errors=%s',
        checked, changed, errors,
    )
    return {'checked': checked, 'changed': changed, 'errors': errors}


def ensure_lots_for_user(user_id: int) -> None:
    """Backfill lots if this user has grants but no tax lots yet."""
    from app.models.grant import Grant
    if TaxLot.query.filter_by(user_id=user_id).count():
        return
    if not Grant.query.filter_by(user_id=user_id).count():
        return
    from app.utils.backfill_ledger import backfill_user
    backfill_user(user_id)
