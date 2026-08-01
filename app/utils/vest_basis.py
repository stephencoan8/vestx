"""
Canonical RSU cost basis = FMV on vest date.

Priority:
  1. VestEvent.fmv_at_vest (snapshot stored on the vest row)
  2. Price history on/before vest date (private pre-IPO + public post-IPO)
  3. Earliest price after vest date (forward-fill) — better than $0 for planning
  4. 0.0 if nothing available (caller should surface basis_missing)

ISO basis is strike (share_price_at_grant), not this module.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional, Tuple

from app import db

logger = logging.getLogger(__name__)


def _as_date(d) -> Optional[date]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    return d


def lookup_price_on_or_near_date(
    user_id: int,
    on_date: date,
    *,
    allow_forward_fill: bool = True,
) -> Tuple[float, str]:
    """
    Return (price, source) where source is:
      as_of | forward_fill | missing
    """
    from app.utils.price_utils import get_price_on_or_near_date

    on_date = _as_date(on_date)
    if not on_date or not user_id:
        return 0.0, 'missing'
    price, source = get_price_on_or_near_date(
        user_id, on_date, allow_forward_fill=allow_forward_fill
    )
    return float(price or 0.0), source


def get_stored_fmv_at_vest(vest) -> float:
    raw = getattr(vest, 'fmv_at_vest', None)
    try:
        v = float(raw or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else 0.0


def _raw_share_price_attr(vest) -> float:
    """Numeric share_price_at_vest attribute only (never invoke the ORM property)."""
    try:
        raw = object.__getattribute__(vest, 'share_price_at_vest')
    except Exception:
        return 0.0
    if callable(raw):
        return 0.0
    try:
        v = float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def resolve_vest_fmv(
    vest,
    *,
    user_id: Optional[int] = None,
    persist: bool = True,
    allow_forward_fill: bool = True,
) -> Tuple[float, str]:
    """
    Resolve FMV for a vest event. Optionally write snapshot to vest.fmv_at_vest.

    Returns (fmv, source) with source in:
      stored | as_of | forward_fill | missing
    """
    if vest is None:
        return 0.0, 'missing'

    stored = get_stored_fmv_at_vest(vest)
    if stored > 0:
        return stored, 'stored'

    # Tests / denormalized objects may set share_price_at_vest as a plain float
    raw_attr = _raw_share_price_attr(vest)
    if raw_attr > 0:
        return raw_attr, 'stored'

    uid = user_id
    if uid is None:
        try:
            uid = vest.grant.user_id if vest.grant else None
        except Exception:
            uid = None
    if not uid:
        return 0.0, 'missing'

    vest_date = _as_date(getattr(vest, 'vest_date', None))
    if not vest_date:
        return 0.0, 'missing'

    # Future vest: use latest available as estimate (not a locked basis yet)
    if vest_date > date.today():
        from app.utils.price_utils import get_latest_user_price
        live = float(get_latest_user_price(uid) or 0.0)
        return (live, 'as_of') if live > 0 else (0.0, 'missing')

    price, source = lookup_price_on_or_near_date(
        uid, vest_date, allow_forward_fill=allow_forward_fill
    )
    if price <= 0:
        return 0.0, 'missing'

    if persist:
        try:
            # Only lock in a snapshot for past/present vest dates
            vest.fmv_at_vest = float(price)
            db.session.add(vest)
            # Flush only — outer request/backfill owns the commit
            db.session.flush()
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.warning(
                'Could not persist fmv_at_vest for vest %s: %s',
                getattr(vest, 'id', '?'), e,
            )

    return float(price), source


def rsu_cost_basis_per_share(
    vest,
    *,
    user_id: Optional[int] = None,
    persist: bool = True,
) -> Tuple[float, str]:
    """RSU/RSA cost basis = FMV at vest. Returns (basis, source)."""
    return resolve_vest_fmv(vest, user_id=user_id, persist=persist)


def iso_cost_basis_per_share(vest) -> float:
    """ISO tax basis for held shares starts at strike (option price)."""
    try:
        return float(vest.grant.share_price_at_grant or 0.0)
    except Exception:
        return 0.0


def ensure_vest_fmv_snapshot(vest, *, user_id: Optional[int] = None) -> float:
    """Force resolve+persist; return FMV (0 if unknown)."""
    fmv, _ = resolve_vest_fmv(vest, user_id=user_id, persist=True)
    return fmv


def backfill_user_vest_fmv(user_id: int) -> dict:
    """
    Persist fmv_at_vest for all of a user's vested lots missing a snapshot.
    Safe to call from HTTP or background jobs.
    """
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent

    vests = (
        VestEvent.query
        .join(Grant)
        .filter(Grant.user_id == user_id)
        .order_by(VestEvent.vest_date.asc())
        .all()
    )
    filled = 0
    missing = 0
    already = 0
    for ve in vests:
        if get_stored_fmv_at_vest(ve) > 0:
            already += 1
            continue
        if not ve.has_vested:
            continue
        fmv, src = resolve_vest_fmv(ve, user_id=user_id, persist=True)
        if fmv > 0:
            filled += 1
        else:
            missing += 1
            logger.warning(
                'Vest %s (%s) still has no FMV after backfill for user %s (src=%s)',
                ve.id, ve.vest_date, user_id, src,
            )
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning('vest FMV backfill commit failed for user %s: %s', user_id, e)
    return {
        'user_id': user_id,
        'already_set': already,
        'filled': filled,
        'still_missing': missing,
        'total': len(vests),
    }
