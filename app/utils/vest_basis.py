"""
Canonical RSU cost basis = FMV on vest date (Price at Vest).

Not a strike. Strike is ISO-only (grant option price).

Resolution order for past/present vests:
  1. Recomputed from price history (regime-aware) — preferred when force_recompute
  2. Stored vest.fmv_at_vest only if trusted (not IPO-polluted pre-IPO snapshot)
  3. get_vest_date_fmv:
       pre-IPO  → private Prices on/before vest only (never public SPCX)
       post-IPO → public close on/before vest
  4. Pre-IPO fallback: grant.share_price_at_grant (e.g. $16.20 on grant/vest day)
  5. missing → 0.0 (UI should flag basis_missing)

Poisoned snapshots (~$160 IPO on 2023 vests) are overwritten by recompute_user_vest_fmv.
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


def get_stored_fmv_at_vest(vest) -> float:
    """Read fmv_at_vest from instance state only (avoid expired-load storms)."""
    try:
        d = object.__getattribute__(vest, '__dict__')
        if isinstance(d, dict) and 'fmv_at_vest' in d:
            raw = d['fmv_at_vest']
            if raw is None:
                return 0.0
            v = float(raw)
            return v if v > 0 else 0.0
    except Exception:
        pass
    return 0.0


def _raw_share_price_attr(vest) -> float:
    """
    Plain instance attribute only (tests / SimpleNamespace).

    Never use object.__getattribute__(vest, 'share_price_at_vest') on ORM
    models — that invokes the @property and re-enters resolve_vest_fmv.
    """
    try:
        d = object.__getattribute__(vest, '__dict__')
        if not isinstance(d, dict) or 'share_price_at_vest' not in d:
            return 0.0
        raw = d['share_price_at_vest']
    except Exception:
        return 0.0
    if callable(raw):
        return 0.0
    try:
        v = float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _grant_price(vest) -> float:
    try:
        g = vest.grant
        if not g:
            return 0.0
        return float(g.share_price_at_grant or 0.0)
    except Exception:
        return 0.0


def _is_pre_ipo(vest_date: date) -> bool:
    from app.utils.price_utils import _public_start
    return vest_date < _public_start()


def _stored_looks_ipo_polluted(vest, stored: float, user_id: int) -> bool:
    """
    True if a pre-IPO vest's stored FMV looks like public IPO (~$160) pollution
    rather than private/grant FMV.

    Heuristic only (no nested price queries — those caused resolve recursion).
    """
    vest_date = _as_date(getattr(vest, 'vest_date', None))
    if not vest_date or not _is_pre_ipo(vest_date):
        return False
    if stored <= 0:
        return False

    grant_px = _grant_price(vest)
    # Clear mismatch: grant ~$16, stored ~$160
    if grant_px > 0 and stored >= max(grant_px * 3.0, grant_px + 20.0) and stored >= 40.0:
        return True
    # Pre-IPO vest stamped at typical IPO band with no usable grant price
    if grant_px <= 0 and stored >= 80.0:
        return True
    return False


def _compute_fmv_from_history(
    vest,
    *,
    user_id: int,
    vest_date: date,
) -> Tuple[float, str]:
    """Regime-aware compute without reading stored snapshot."""
    from app.utils.price_utils import get_vest_date_fmv, get_latest_user_price

    if vest_date > date.today():
        live = float(get_latest_user_price(user_id) or 0.0)
        return (live, 'estimate_live') if live > 0 else (0.0, 'missing')

    price, source = get_vest_date_fmv(user_id, vest_date)
    if price and float(price) > 0:
        return float(price), str(source or 'as_of')

    # Pre-IPO: grant price is the standard planning fallback (same-day vest = grant FMV)
    if _is_pre_ipo(vest_date):
        gp = _grant_price(vest)
        if gp > 0:
            return gp, 'grant_price'

    return 0.0, 'missing'


def resolve_vest_fmv(
    vest,
    *,
    user_id: Optional[int] = None,
    persist: bool = True,
    force_recompute: bool = False,
    allow_forward_fill: bool = False,  # ignored; kept for call-site compat
) -> Tuple[float, str]:
    """
    Resolve FMV for a vest event (RSU Price at Vest / cost basis).

    Returns (fmv, source).
    """
    if vest is None:
        return 0.0, 'missing'

    uid = user_id
    if uid is None:
        try:
            uid = vest.grant.user_id if vest.grant else None
        except Exception:
            uid = None

    vest_date = _as_date(getattr(vest, 'vest_date', None))
    if not vest_date:
        return 0.0, 'missing'

    # Tests / denormalized objects with plain float attribute
    if not force_recompute:
        raw_attr = _raw_share_price_attr(vest)
        if raw_attr > 0 and not hasattr(type(vest), 'fmv_at_vest'):
            # SimpleNamespace-style test objects without ORM column
            return raw_attr, 'stored'

    stored = get_stored_fmv_at_vest(vest)
    if stored > 0 and not force_recompute:
        if uid and _stored_looks_ipo_polluted(vest, stored, uid):
            logger.warning(
                'Rejecting IPO-polluted fmv_at_vest=$%.2f for vest %s (%s); recomputing',
                stored, getattr(vest, 'id', '?'), vest_date,
            )
            force_recompute = True
            # Clear poisoned snapshot via ORM so SQLAlchemy tracks the change
            try:
                vest.fmv_at_vest = None
            except Exception:
                pass
        else:
            return stored, 'stored'

    if not uid:
        gp = _grant_price(vest)
        return (gp, 'grant_price') if gp > 0 else (0.0, 'missing')

    price, source = _compute_fmv_from_history(vest, user_id=uid, vest_date=vest_date)
    if price <= 0:
        return 0.0, 'missing'

    if persist and vest_date <= date.today():
        try:
            # Must use ORM attribute set so flush actually UPDATEs the row
            vest.fmv_at_vest = float(price)
            db.session.add(vest)
            db.session.flush()
        except Exception as e:
            logger.warning(
                'Could not persist fmv_at_vest for vest %s: %s (returning computed $%.2f)',
                getattr(vest, 'id', '?'), e, price,
            )

    return float(price), source


def rsu_cost_basis_per_share(
    vest,
    *,
    user_id: Optional[int] = None,
    persist: bool = True,
    force_recompute: bool = False,
) -> Tuple[float, str]:
    """RSU/RSA cost basis = FMV at vest. Returns (basis, source)."""
    return resolve_vest_fmv(
        vest,
        user_id=user_id,
        persist=persist,
        force_recompute=force_recompute,
    )


def iso_cost_basis_per_share(vest) -> float:
    """ISO tax basis for held shares starts at strike (option price)."""
    return _grant_price(vest)


def ensure_vest_fmv_snapshot(vest, *, user_id: Optional[int] = None) -> float:
    """Force resolve+persist; return FMV (0 if unknown)."""
    fmv, _ = resolve_vest_fmv(vest, user_id=user_id, persist=True, force_recompute=True)
    return fmv


def backfill_user_vest_fmv(user_id: int, *, force: bool = False) -> dict:
    """
    Persist fmv_at_vest for a user's vested lots.

    force=True: recompute and overwrite every vested lot (repairs IPO pollution).
    force=False: fill missing, and overwrite only IPO-polluted stored values.
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
    repaired = 0
    for ve in vests:
        if not ve.has_vested:
            continue
        stored = get_stored_fmv_at_vest(ve)
        do_force = force
        if not do_force and stored > 0 and _stored_looks_ipo_polluted(ve, stored, user_id):
            do_force = True
        if stored > 0 and not do_force:
            already += 1
            continue
        fmv, src = resolve_vest_fmv(
            ve, user_id=user_id, persist=True, force_recompute=do_force or stored <= 0
        )
        if fmv > 0:
            if stored > 0 and do_force:
                repaired += 1
            else:
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
        'repaired': repaired,
        'still_missing': missing,
        'total': len(vests),
        'force': force,
    }


def recompute_user_vest_fmv(user_id: int) -> dict:
    """Force recompute all vested FMV snapshots (fixes $160 IPO stamp)."""
    return backfill_user_vest_fmv(user_id, force=True)
