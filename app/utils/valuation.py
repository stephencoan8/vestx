"""
Mark-to-market equity values.

One rule, used by the dashboard hero, the vesting timeline, holdings, and
grant/vest models:

- Unexercised ISOs/options (vested or still scheduled) are worth
  shares × max(0, price − strike). Underwater options are worth nothing.
- Exercised ISO shares still held, RSUs, and ESPP are worth shares × price.
- Cash grants are a dollar amount. They are not multiplied by the share price.

Taxes (withholding, estimated sale tax) are not applied here. Withholding and
market sales show up only because those shares are no longer in the held lot.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.utils.shares import whole_shares

OPTION_SHARE_TYPES = frozenset({'iso_5y', 'iso_6y'})
CASH_SHARE_TYPE = 'cash'


def is_option_share_type(share_type: Optional[str]) -> bool:
    return (share_type or '') in OPTION_SHARE_TYPES


def mark_to_market(
    shares: float,
    price: float,
    *,
    share_type: Optional[str] = None,
    strike: float = 0.0,
    unexercised: bool = False,
) -> float:
    """Dollar value of ``shares`` at ``price`` under the shared rule.

    Pass ``unexercised=True`` for vested or scheduled option grants. Exercised
    option shares that are still held are stock: leave ``unexercised`` false
    so they take the full price, same as RSU and ESPP.
    """
    qty = float(shares or 0)
    if qty == 0:
        return 0.0
    if (share_type or '') == CASH_SHARE_TYPE:
        return qty
    px = float(price or 0)
    if unexercised:
        return qty * max(0.0, px - float(strike or 0))
    return qty * px


def _lot_strike(lot: dict) -> float:
    raw = lot.get('strike_price')
    if raw is None or raw == '':
        raw = lot.get('cost_basis_per_share') or 0
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def split_lot(lot: dict) -> Tuple[float, float, bool, float, str]:
    """Return (stock shares, unexercised options, is_option, strike, share_type).

    Cash lots contribute neither stock nor options.
    """
    share_type = lot.get('share_type') or ''
    if share_type == CASH_SHARE_TYPE:
        return 0.0, 0.0, False, 0.0, CASH_SHARE_TYPE
    is_option = bool(lot.get('is_iso')) or is_option_share_type(share_type)
    available = float(lot.get('shares_available') or 0)
    unexercised = float(lot.get('shares_unexercised') or 0) if is_option else 0.0
    strike = _lot_strike(lot) if is_option else 0.0
    return available, unexercised, is_option, strike, share_type


def lot_market_value(lot: dict, price: float) -> float:
    """Held stock at full price, plus unexercised options at the floored spread."""
    available, unexercised, is_option, strike, share_type = split_lot(lot)
    if share_type == CASH_SHARE_TYPE:
        return mark_to_market(
            lot.get('shares_available') or 0,
            price,
            share_type=CASH_SHARE_TYPE,
        )
    value = mark_to_market(available, price, share_type=share_type, unexercised=False)
    if is_option and unexercised:
        value += mark_to_market(
            unexercised,
            price,
            share_type=share_type or 'iso_5y',
            strike=strike,
            unexercised=True,
        )
    return value


def held_position_from_lots(lots: Optional[Sequence[dict]], price: float) -> Dict[str, float]:
    """Current held position from lot inventory (the dashboard Vested figure).

    ``held_shares`` counts stock you can sell (RSU, ESPP, exercised ISO).
    Unexercised options are excluded from that count and included in
    ``available_value`` at the floored spread.
    """
    rsu_held = 0.0
    iso_held = 0.0
    iso_unex = 0.0
    stock_value = 0.0
    iso_unex_value = 0.0
    px = float(price or 0)
    for lot in lots or []:
        available, unexercised, is_option, strike, share_type = split_lot(lot)
        if share_type == CASH_SHARE_TYPE:
            continue
        if is_option:
            iso_held += available
            iso_unex += unexercised
            stock_value += mark_to_market(available, px, share_type=share_type, unexercised=False)
            iso_unex_value += mark_to_market(
                unexercised,
                px,
                share_type=share_type or 'iso_5y',
                strike=strike,
                unexercised=True,
            )
        else:
            rsu_held += available
            stock_value += mark_to_market(available, px, share_type=share_type, unexercised=False)
    return {
        'rsu_held': rsu_held,
        'iso_held': iso_held,
        'held_shares': rsu_held + iso_held,
        'iso_unexercised': iso_unex,
        'available_stock_value': stock_value,
        'iso_unexercised_value': iso_unex_value,
        'available_value': stock_value + iso_unex_value,
    }


def scheduled_vest_value(vest: Any, price: float) -> float:
    """Value of a scheduled vest at ``price`` (gross shares, no tax).

    Options use the floored spread. RSU and ESPP use the full price.
    Cash is the cash amount.
    """
    grant = getattr(vest, 'grant', None)
    share_type = getattr(grant, 'share_type', None) if grant is not None else None
    strike = getattr(grant, 'share_price_at_grant', 0) if grant is not None else 0
    shares = getattr(vest, 'shares_vested', 0)
    return mark_to_market(
        shares,
        price,
        share_type=share_type,
        strike=float(strike or 0),
        unexercised=is_option_share_type(share_type),
    )


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class _Tranche:
    """Shares that start contributing on ``start``."""

    def __init__(
        self,
        start: date,
        shares_for_count: float,
        qty: float,
        share_type: str,
        strike: float,
        unexercised: bool,
    ):
        self.start = start
        self.shares_for_count = float(shares_for_count or 0)
        self.qty = float(qty or 0)
        self.share_type = share_type
        self.strike = float(strike or 0)
        self.unexercised = bool(unexercised)

    def value(self, price: float) -> float:
        return mark_to_market(
            self.qty,
            price,
            share_type=self.share_type,
            strike=self.strike,
            unexercised=self.unexercised,
        )


def _tranches_from_lots(lots: Optional[Sequence[dict]], as_of: date) -> List[_Tranche]:
    """Currently held stock and vested unexercised options, dated at vest.

    Sold and withheld shares are already absent from the lot, so the line
    never adds them back.
    """
    out: List[_Tranche] = []
    for lot in lots or []:
        start = _as_date(lot.get('vest_date'))
        if start is None or start > as_of:
            continue
        available, unexercised, is_option, strike, share_type = split_lot(lot)
        if share_type == CASH_SHARE_TYPE:
            continue
        if available:
            out.append(_Tranche(start, available, available, share_type, 0.0, False))
        if is_option and unexercised:
            out.append(_Tranche(
                start,
                0.0,
                unexercised,
                share_type or 'iso_5y',
                strike,
                True,
            ))
    return out


def _tranches_from_future(events: Optional[Iterable[Any]], as_of: date) -> List[_Tranche]:
    """Scheduled vests after ``as_of``. Options count as shares and as spread."""
    out: List[_Tranche] = []
    for vest in events or []:
        start = _as_date(getattr(vest, 'vest_date', None))
        if start is None or start <= as_of:
            continue
        grant = getattr(vest, 'grant', None)
        share_type = getattr(grant, 'share_type', None) if grant is not None else None
        if not share_type or share_type == CASH_SHARE_TYPE:
            continue
        qty = float(getattr(vest, 'shares_vested', 0) or 0)
        if qty <= 0:
            continue
        if is_option_share_type(share_type):
            strike = float(getattr(grant, 'share_price_at_grant', 0) or 0)
            out.append(_Tranche(start, qty, qty, share_type, strike, True))
        else:
            out.append(_Tranche(start, qty, qty, share_type, 0.0, False))
    return out


def build_vesting_timeline(
    lots: Optional[Sequence[dict]],
    future_events: Optional[Iterable[Any]],
    price_points: Optional[Sequence[Any]],
    *,
    live_price: float,
    as_of: date,
) -> List[dict]:
    """Vesting timeline whose value on ``as_of`` is the held (Vested) figure.

    Past points mark shares still held, plus vested unexercised options at the
    floored spread, using the price on that date. The point on ``as_of`` uses
    ``live_price`` so it matches the dashboard hero. Later points add scheduled
    vests at that same live price (options at the floored spread), so the last
    point is Vested + Unvested.

    Share counts follow the hero: held stock only through today (unexercised
    options are value, not shares), then scheduled vest shares after today.
    """
    as_of = _as_date(as_of) or date.today()
    live = float(live_price or 0)
    tranches = _tranches_from_lots(lots, as_of) + _tranches_from_future(future_events, as_of)

    price_by_date: Dict[date, float] = {}
    for point in price_points or []:
        if isinstance(point, dict):
            raw_date = point.get('date', point.get('valuation_date'))
            raw_price = point.get('price', point.get('price_per_share'))
        else:
            raw_date, raw_price = point[0], point[1]
        d = _as_date(raw_date)
        if d is None or d > as_of:
            continue
        try:
            price_by_date[d] = float(raw_price)
        except (TypeError, ValueError):
            continue

    dates = set(price_by_date) | {t.start for t in tranches} | {as_of}
    running = 0.0
    timeline: List[dict] = []
    vested_value_today = 0.0
    vested_shares_today = 0

    for d in sorted(dates):
        if d <= as_of and d in price_by_date:
            running = price_by_date[d]
        # Today and everything after it use the hero's live price, not a
        # later print and not a stale close from before today.
        px = live if d >= as_of else running
        active = [t for t in tranches if t.start <= d]
        # Keep a point on as_of even when nothing has vested yet, so the
        # future series starts from the hero Vested figure (possibly zero).
        if not active and (d != as_of or not tranches):
            continue
        if d < as_of and px <= 0:
            continue
        held_now = [t for t in active if t.start <= as_of]
        scheduled_now = [t for t in active if t.start > as_of]
        total_value = sum(t.value(px) for t in active)
        # Share counts match the hero: whole held stock, plus whole scheduled
        # shares after today. Unexercised vested options are value only.
        held_share_count = whole_shares(sum(t.shares_for_count for t in held_now))
        scheduled_share_count = whole_shares(sum(t.shares_for_count for t in scheduled_now))
        total_shares = held_share_count + scheduled_share_count
        if d <= as_of:
            vested_value = total_value
            vested_shares = total_shares
            vested_value_today = vested_value
            vested_shares_today = vested_shares
        else:
            vested_value = vested_value_today
            vested_shares = vested_shares_today
        timeline.append({
            'date': d.isoformat(),
            'vested_shares': vested_shares,
            'total_shares': total_shares,
            'vested_value': float(vested_value),
            'total_value': float(total_value),
            'is_vested': d <= as_of,
            'price_at_date': float(px),
            'event_type': 'held' if d <= as_of else 'scheduled',
        })
    return timeline
