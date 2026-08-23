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


def value_unvested_events(events, price: float) -> Dict[str, float]:
    """Mark unvested vest rows at live FMV (RSU) or intrinsic (ISO)."""
    unavail_shares_rsu = 0.0
    unavail_shares_iso = 0.0
    unavailable_value = 0.0
    for ve in events or []:
        grant = getattr(ve, 'grant', None)
        if not grant or getattr(grant, 'share_type', None) == ShareType.CASH.value:
            continue
        sh = float(getattr(ve, 'shares_vested', 0) or 0)
        if sh <= 0:
            continue
        st = grant.share_type
        if st in _iso_types():
            unavail_shares_iso += sh
            strike = float(getattr(grant, 'share_price_at_grant', 0) or 0)
            unavailable_value += sh * max(0.0, float(price) - strike)
        else:
            unavail_shares_rsu += sh
            unavailable_value += sh * float(price)
    return {
        'unavailable_value': float(unavailable_value),
        'unavailable_shares_rsu': float(unavail_shares_rsu),
        'unavailable_shares_iso': float(unavail_shares_iso),
        'unavailable_shares': float(unavail_shares_rsu + unavail_shares_iso),
    }


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
        VestEvent.query
        .join(Grant)
        .filter(Grant.user_id == user_id, VestEvent.vest_date > as_of)
        .all()
    )
    unavail = value_unvested_events(future_vests, price)
    unavailable_value = unavail['unavailable_value']
    unavail_shares_rsu = unavail['unavailable_shares_rsu']
    unavail_shares_iso = unavail['unavailable_shares_iso']
    unavail_shares = unavail['unavailable_shares']

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

    # ——— Shareworks-style buckets (for reconciliation) ———
    # Common / RSU stock held (excl. ESPP) · ESPP held · ISO stock held · ISO unexercised
    sw_common_sh = 0.0
    sw_espp_sh = 0.0
    sw_iso_stock_sh = 0.0
    for lot in lots or []:
        avail = float(lot.get('shares_available') or 0)
        if avail <= 0:
            continue
        gt = (lot.get('grant_type') or '').lower()
        if lot.get('is_iso'):
            sw_iso_stock_sh += avail  # exercised → shows as Common in Shareworks
        elif gt in ('espp', 'nqespp'):
            sw_espp_sh += avail
        else:
            sw_common_sh += avail  # RSU/RSA released stock (Common + Non-Transferable lumped)

    buckets = {
        'available': {
            'common_stock': {
                'label': 'Common / released RSU (incl. Non-Transferable)',
                'shares': whole_shares(sw_common_sh + sw_iso_stock_sh),
                'shares_rsu_only': whole_shares(sw_common_sh),
                'shares_from_iso_exercise': whole_shares(sw_iso_stock_sh),
                'value': float((sw_common_sh + sw_iso_stock_sh) * price),
                'note': 'VestX cannot split Shareworks “Common” vs “Common – Non-Transferable” yet',
            },
            'espp': {
                'label': 'ESPP / nqESPP',
                'shares': whole_shares(sw_espp_sh),
                'value': float(sw_espp_sh * price),
            },
            'iso_unexercised': {
                'label': 'Options (ISO) — vested unexercised',
                'shares': whole_shares(iso_unex),
                'value': float(iso_unex_value),
                'note': 'Intrinsic (FMV − strike), same as Shareworks Options line',
            },
        },
        'unavailable': {
            'rsu': {
                'label': 'Stock Awards (RSU) — unvested',
                'shares': whole_shares(unavail_shares_rsu),
                'value': float(unavail_shares_rsu * price),
            },
            'iso': {
                'label': 'Options (ISO) — unvested',
                'shares': whole_shares(unavail_shares_iso),
                'value': float(
                    # recompute from value_unvested already aggregated; use unavailable iso portion
                    # approximate: if only ISO contrib to unavailable besides RSU:
                    max(0.0, unavailable_value - unavail_shares_rsu * price)
                ),
                'note': 'Intrinsic on unvested options',
            },
        },
        'shareworks_reference': {
            # User-reported Shareworks snapshot (for on-page delta); price ~$136.97
            'as_of_note': 'Shareworks snapshot you shared (FMV ≈ $136.97)',
            'available': {
                'common': {'shares': 4005, 'value': 548564.85},
                'common_nt': {'shares': 2500, 'value': 342425.00},
                'espp': {'shares': 4400, 'value': 602668.00},
                'iso': {'shares': 1055, 'value': 124036.35},
            },
            'unavailable': {
                'rsu': {'shares': 8371, 'value': 1146575.87},
                'iso': {'shares': 4565, 'value': 536707.05},
            },
        },
    }

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
        'buckets': buckets,
        'lots': lots,
    }
