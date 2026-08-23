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


def ensure_lots_for_user(user_id: int) -> None:
    """Backfill lots if this user has grants but no tax lots yet."""
    from app.models.grant import Grant
    if TaxLot.query.filter_by(user_id=user_id).count():
        return
    if not Grant.query.filter_by(user_id=user_id).count():
        return
    from app.utils.backfill_ledger import backfill_user
    backfill_user(user_id)
