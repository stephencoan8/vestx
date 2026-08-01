"""
Safely sync a grant's vest events to a newly calculated schedule.

Preserves user-entered tax fields and never deletes vest rows that have
tax data, notes, stock sales, ISO exercises, or sale plans.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Any

from app import db
from app.models.vest_event import VestEvent
from app.models.stock_sale import StockSale, ISOExercise
from app.models.sale_plan import SalePlan

logger = logging.getLogger(__name__)


def _has_user_data(vest: VestEvent) -> bool:
    """True if this vest holds user-entered or dependent data that must not be dropped."""
    if (vest.cash_paid or 0) != 0:
        return True
    if (vest.shares_sold or 0) != 0:
        return True
    if vest.notes and str(vest.notes).strip():
        return True
    # Explicit "not fully covered" is a user choice even with zero cash/shares
    if vest.cash_covered_all is False:
        return True
    return False


def _has_dependents(vest_id: int) -> bool:
    """True if sales, exercises, or sale plans reference this vest."""
    if StockSale.query.filter_by(vest_event_id=vest_id).limit(1).first():
        return True
    if ISOExercise.query.filter_by(vest_event_id=vest_id).limit(1).first():
        return True
    if SalePlan.query.filter_by(vest_event_id=vest_id).limit(1).first():
        return True
    return False


def sync_vest_events_for_grant(grant, vest_schedule: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Align vest_events for ``grant`` with ``vest_schedule`` without data loss.

    Matching is by exact ``vest_date``. Existing rows keep tax fields, notes,
    and IDs (so stock_sales / iso_exercises / sale_plans stay linked).

    Args:
        grant: Grant model instance (must have id)
        vest_schedule: list of dicts with keys ``vest_date`` and ``shares``
            (as produced by ``calculate_vest_schedule``)

    Returns:
        counts: created, updated, deleted, preserved
    """
    existing = (
        VestEvent.query
        .filter_by(grant_id=grant.id)
        .order_by(VestEvent.vest_date)
        .all()
    )

    # Group existing by date (multiple on same date is rare; keep a list)
    by_date: Dict[Any, List[VestEvent]] = {}
    for ve in existing:
        by_date.setdefault(ve.vest_date, []).append(ve)

    matched_ids = set()
    created = 0
    updated = 0

    for entry in vest_schedule:
        vest_date = entry['vest_date']
        shares = float(entry['shares'])
        candidates = by_date.get(vest_date) or []

        if candidates:
            # Prefer an unmatched candidate on this date
            ve = None
            for candidate in candidates:
                if candidate.id not in matched_ids:
                    ve = candidate
                    break
            if ve is None:
                ve = candidates[0]

            matched_ids.add(ve.id)
            # Update schedule math only; never overwrite tax / notes
            if ve.shares_vested != shares or ve.tax_year != vest_date.year:
                ve.shares_vested = shares
                ve.tax_year = vest_date.year
                updated += 1
            else:
                # Still count as matched/updated path for clarity if only touch needed
                pass
        else:
            ve = VestEvent(
                grant_id=grant.id,
                vest_date=vest_date,
                shares_vested=shares,
                tax_year=vest_date.year,
            )
            db.session.add(ve)
            db.session.flush()
            try:
                from app.utils.vest_basis import ensure_vest_fmv_snapshot
                if ve.has_vested:
                    ensure_vest_fmv_snapshot(ve, user_id=grant.user_id)
            except Exception:
                pass
            created += 1

    deleted = 0
    preserved = 0

    for ve in existing:
        if ve.id in matched_ids:
            continue

        # Not in new schedule — only remove if empty of user/dependent data
        if _has_user_data(ve) or _has_dependents(ve.id):
            preserved += 1
            logger.info(
                "Preserving vest_event %s (date=%s) for grant %s: has user data or dependents",
                ve.id, ve.vest_date, grant.id,
            )
            continue

        db.session.delete(ve)
        deleted += 1

    return {
        'created': created,
        'updated': updated,
        'deleted': deleted,
        'preserved': preserved,
    }
