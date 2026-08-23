"""
Held equity portfolio summary — SpaceX stock still owned after sales.

SSOT for inventory is ``build_lots_for_user``. Dashboard / hub KPIs should use
this so sold shares leave portfolio value.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.models.grant import Grant
from app.models.stock_sale import StockSale
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


def summarize_held_portfolio(
    user_id: int,
    *,
    live_price: Optional[float] = None,
    lots: Optional[List[dict]] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Return held equity metrics after subtracting recorded stock sales.

    Keys:
      live_price, held_shares, held_value, rsu_held, iso_held,
      iso_unexercised, iso_unexercised_value, sellable_value,
      grant_book_shares, grant_book_value (pre-sale full grant notionals)
    """
    as_of = as_of or date.today()
    price = float(live_price if live_price is not None else (get_latest_user_price(user_id) or 0.0))
    lots = lots if lots is not None else build_lots_for_user(user_id, as_of=as_of)

    rsu_held = 0.0
    iso_held = 0.0
    iso_unex = 0.0
    iso_unex_value = 0.0
    held_value = 0.0

    for lot in lots or []:
        avail = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        is_iso = bool(lot.get('is_iso'))
        strike = float(lot.get('strike_price') or lot.get('cost_basis_per_share') or 0)
        if is_iso:
            iso_held += avail
            held_value += avail * price
            iso_unex += unex
            iso_unex_value += unex * max(0.0, price - strike)
        else:
            rsu_held += avail
            held_value += avail * price

    held_shares = rsu_held + iso_held
    # Unexercised options aren't "shares held" but contribute intrinsic to portfolio
    portfolio_value = held_value + iso_unex_value

    grant_book_shares = 0.0
    grant_book_value = 0.0
    for g in Grant.query.filter_by(user_id=user_id).all():
        qty = float(g.share_quantity or 0)
        if g.share_type == 'cash':
            grant_book_shares += 0
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
        'held_shares': whole_shares(held_shares),
        'held_value': float(held_value),
        'portfolio_value': float(portfolio_value),
        'rsu_held': whole_shares(rsu_held),
        'iso_held': whole_shares(iso_held),
        'iso_unexercised': whole_shares(iso_unex),
        'iso_unexercised_value': float(iso_unex_value),
        'sellable_value': float(held_value),
        'shares_available': whole_shares(held_shares),
        'grant_book_shares': whole_shares(grant_book_shares),
        'grant_book_value': float(grant_book_value),
        'shares_sold_market': whole_shares(sold_total),
        'lots': lots,
    }
