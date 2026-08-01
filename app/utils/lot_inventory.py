"""
Tax-lot inventory from grants / vests / sales / ISO exercises.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from app.models.grant import Grant, ShareType
from app.models.vest_event import VestEvent
from app.models.stock_sale import StockSale, ISOExercise
from app.utils.price_utils import get_latest_user_price
from app.utils.shares import whole_shares


def _iso_types():
    return {ShareType.ISO_5Y.value, ShareType.ISO_6Y.value}


def build_lots_for_user(user_id: int, as_of: Optional[date] = None) -> List[dict]:
    """
    Return list of lot dicts with available shares and tax metadata.

    Share counts are whole shares only (floor) — no fractional lots.
    """
    as_of = as_of or date.today()
    current_price = get_latest_user_price(user_id) or 0.0

    grants = Grant.query.filter_by(user_id=user_id).all()
    grant_map = {g.id: g for g in grants}

    vests = (
        VestEvent.query
        .join(Grant)
        .filter(Grant.user_id == user_id, VestEvent.vest_date <= as_of)
        .order_by(VestEvent.vest_date.asc())
        .all()
    )

    sales = StockSale.query.filter_by(user_id=user_id).all()
    sold_by_vest: Dict[int, float] = {}
    for s in sales:
        if s.vest_event_id:
            sold_by_vest[s.vest_event_id] = sold_by_vest.get(s.vest_event_id, 0.0) + float(
                s.shares_sold or 0
            )

    exercises = ISOExercise.query.filter_by(user_id=user_id).all()
    exercised_by_vest: Dict[int, float] = {}
    exercise_meta: Dict[int, list] = {}
    for e in exercises:
        exercised_by_vest[e.vest_event_id] = exercised_by_vest.get(e.vest_event_id, 0.0) + float(
            e.shares_exercised or 0
        )
        exercise_meta.setdefault(e.vest_event_id, []).append(e)

    lots = []
    for vest in vests:
        grant = grant_map.get(vest.grant_id) or vest.grant
        if not grant or grant.share_type == ShareType.CASH.value:
            continue

        is_iso = grant.share_type in _iso_types()
        received = whole_shares(vest.shares_received)  # after tax withholding
        sold = whole_shares(sold_by_vest.get(vest.id, 0.0))
        exercised = whole_shares(exercised_by_vest.get(vest.id, 0.0))
        vested = whole_shares(vest.shares_vested)

        if is_iso:
            # Available to sell = exercised still held - sold (approx)
            # Unexercised vested options shown separately
            still_held_exercised = 0
            latest_ex = None
            for e in exercise_meta.get(vest.id, []):
                if e.shares_still_held is not None:
                    still_held_exercised += whole_shares(e.shares_still_held)
                else:
                    still_held_exercised += whole_shares(e.shares_exercised)
                latest_ex = e
            # Fallback if shares_still_held not maintained
            if not exercise_meta.get(vest.id):
                available_to_sell = 0
                unexercised = max(0, received - sold)
            else:
                available_to_sell = max(0, still_held_exercised)
                unexercised = max(0, received - exercised)
        else:
            available_to_sell = max(0, received - sold)
            unexercised = 0
            latest_ex = None

        fmv_vest = vest.share_price_at_vest or 0.0
        strike = grant.share_price_at_grant if is_iso else 0.0
        if is_iso:
            basis = strike
        else:
            basis = fmv_vest

        holding_days = (as_of - vest.vest_date).days
        unrealized = (current_price - basis) * available_to_sell if available_to_sell else 0.0

        lots.append({
            'vest_event_id': vest.id,
            'grant_id': grant.id,
            'grant_type': grant.grant_type,
            'share_type': grant.share_type,
            'is_iso': is_iso,
            'vest_date': vest.vest_date.isoformat(),
            'grant_date': grant.grant_date.isoformat(),
            'shares_vested': vested,
            'shares_received': received,
            'shares_sold': sold,
            'shares_exercised': exercised,
            'shares_available': int(available_to_sell),
            'shares_unexercised': int(unexercised),
            'cost_basis_per_share': basis,
            'strike_price': strike if is_iso else None,
            'fmv_at_vest': fmv_vest,
            'current_price': current_price,
            'unrealized_gain': unrealized,
            'holding_days': holding_days,
            'is_long_term': holding_days >= 365,
            'exercise_date': latest_ex.exercise_date.isoformat() if latest_ex and latest_ex.exercise_date else None,
            'fmv_at_exercise': latest_ex.fmv_at_exercise if latest_ex else None,
            'label': f"{grant.grant_type} {grant.share_type.upper()} · {vest.vest_date.isoformat()}",
        })

    return lots
