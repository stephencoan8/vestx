"""
Held equity portfolio summary — aligned with Shareworks-style Available / Unavailable.

Available  = vested inventory still held (after market sales), marked at live FMV
Unavailable = unvested schedule shares (future vest events) at live FMV / ISO intrinsic
Total      = available + unavailable (+ vested unexercised ISO intrinsic in available)

SSOT for vested inventory: ``build_lots_for_user``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import joinedload

from app.models.grant import Grant, ShareType
from app.models.stock_sale import StockSale
from app.models.vest_event import VestEvent
from app.utils.lot_inventory import build_lots_for_user
from app.utils.price_utils import get_latest_user_price
from app.utils.shares import whole_shares


def sold_shares_by_vest(user_id: int) -> Dict[int, float]:
    """Market shares sold per vest_event_id (not vest-withholding)."""
    out: Dict[int, float] = {}
    for s in StockSale.query.filter_by(user_id=user_id).all():
        if not s.vest_event_id:
            continue
        out[s.vest_event_id] = out.get(s.vest_event_id, 0.0) + float(s.shares_sold or 0)
    return out


def _iso_types():
    return {ShareType.ISO_5Y.value, ShareType.ISO_6Y.value}


def summarize_held_portfolio(
    user_id: int,
    *,
    live_price: Optional[float] = None,
    lots: Optional[List[dict]] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Shareworks-aligned portfolio breakdown.

    available_value: vested RSU/ISO-held stock still owned × live FMV
    unexercised_iso_value: vested but unexercised options × intrinsic
    unavailable_value: unvested future shares × FMV (RSU) or intrinsic (ISO)
    portfolio_value / total_value: available + unexercised ISO + unavailable
      (matches Shareworks Available + Unavailable when ISO intrinsic sits in Available)
    """
    as_of = as_of or date.today()
    price = float(live_price if live_price is not None else (get_latest_user_price(user_id) or 0.0))
    lots = lots if lots is not None else build_lots_for_user(user_id, as_of=as_of)

    rsu_held = 0.0
    iso_held = 0.0
    iso_unex = 0.0
    iso_unex_value = 0.0
    available_stock_value = 0.0  # shares you actually hold (can sell once settled)

    for lot in lots or []:
        avail = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        is_iso = bool(lot.get('is_iso'))
        strike = float(lot.get('strike_price') or lot.get('cost_basis_per_share') or 0)
        if is_iso:
            iso_held += avail
            # Exercised ISO stock is real shares — full FMV (same as Shareworks stock)
            available_stock_value += avail * price
            iso_unex += unex
            iso_unex_value += unex * max(0.0, price - strike)
        else:
            rsu_held += avail
            available_stock_value += avail * price

    held_shares = rsu_held + iso_held

    # ——— Unavailable: future (unvested) schedule ———
    future_vests = (
        VestEvent.query.options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == user_id, VestEvent.vest_date > as_of)
        .all()
    )
    unavail_shares_rsu = 0.0
    unavail_shares_iso = 0.0
    unavailable_value = 0.0
    for ve in future_vests:
        grant = ve.grant
        if not grant or grant.share_type == ShareType.CASH.value:
            continue
        sh = float(ve.shares_vested or 0)
        if sh <= 0:
            continue
        if grant.share_type in _iso_types():
            unavail_shares_iso += sh
            strike = float(grant.share_price_at_grant or 0)
            # Options: intrinsic only (not full FMV × unvested options)
            unavailable_value += sh * max(0.0, price - strike)
        else:
            unavail_shares_rsu += sh
            unavailable_value += sh * price

    unavail_shares = unavail_shares_rsu + unavail_shares_iso

    # Shareworks-style Available ≈ stock you can sell + exercisable option intrinsic
    available_value = available_stock_value + iso_unex_value
    # Total equity position value (available + unavailable)
    total_value = available_value + unavailable_value

    # Legacy aliases
    held_value = available_stock_value
    portfolio_value = total_value

    grant_book_shares = 0.0
    grant_book_value = 0.0
    for g in Grant.query.filter_by(user_id=user_id).all():
        qty = float(g.share_quantity or 0)
        if g.share_type == 'cash':
            grant_book_value += qty
        elif g.share_type in ('iso_5y', 'iso_6y'):
            grant_book_shares += qty
            grant_book_value += qty * max(0.0, price - float(g.share_price_at_grant or 0))
        else:
            grant_book_shares += qty
            grant_book_value += qty * price

    sold_total = sum(float(s.shares_sold or 0) for s in StockSale.query.filter_by(user_id=user_id).all())

    return {
        'live_price': price,
        'as_of': as_of.isoformat(),
        # Shareworks-aligned
        'available_value': float(available_value),
        'available_stock_value': float(available_stock_value),
        'unavailable_value': float(unavailable_value),
        'unavailable_shares': whole_shares(unavail_shares),
        'unavailable_shares_rsu': whole_shares(unavail_shares_rsu),
        'unavailable_shares_iso': whole_shares(unavail_shares_iso),
        'total_value': float(total_value),
        # Held stock detail
        'held_shares': whole_shares(held_shares),
        'held_value': float(held_value),
        'rsu_held': whole_shares(rsu_held),
        'iso_held': whole_shares(iso_held),
        'iso_unexercised': whole_shares(iso_unex),
        'iso_unexercised_value': float(iso_unex_value),
        'sellable_value': float(available_stock_value),
        'shares_available': whole_shares(held_shares),
        # Legacy / book
        'portfolio_value': float(portfolio_value),
        'grant_book_shares': whole_shares(grant_book_shares),
        'grant_book_value': float(grant_book_value),
        'shares_sold_market': whole_shares(sold_total),
        'lots': lots,
    }
