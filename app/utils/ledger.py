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
    db.session.commit()
    return sale
