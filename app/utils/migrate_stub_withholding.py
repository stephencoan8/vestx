"""
Seed 7/31 stub income-tax withholding when YTD fields are blank.

Modeled withholding is not a guess once these land. Does not overwrite
values the user already typed. Skips sqlite (tests / local).
"""

from __future__ import annotations

import logging

from app.utils.tax_constants import (
    STUB_FEDERAL_WITHHOLDING_YTD,
    STUB_STATE_WITHHOLDING_YTD,
    STUB_WITHHOLDING_AS_OF,
)
from app.utils.withholding import entered_amount

logger = logging.getLogger(__name__)


def migrate_stub_withholding(app):
    uri = (app.config.get('SQLALCHEMY_DATABASE_URI') or '').lower()
    if 'sqlite' in uri or app.config.get('TESTING'):
        return
    from app import db
    from app.models.tax_profile import TaxProfile

    rows = TaxProfile.query.all()
    changed = 0
    for p in rows:
        fed = entered_amount(getattr(p, 'federal_withholding_ytd', None))
        st = entered_amount(getattr(p, 'state_withholding_ytd', None))
        if fed is not None and st is not None:
            continue
        if fed is None:
            p.federal_withholding_ytd = STUB_FEDERAL_WITHHOLDING_YTD
        if st is None:
            p.state_withholding_ytd = STUB_STATE_WITHHOLDING_YTD
        changed += 1
        logger.info(
            'Seeded %s stub withholding user_id=%s fed=%s ca=%s',
            STUB_WITHHOLDING_AS_OF,
            p.user_id,
            p.federal_withholding_ytd,
            p.state_withholding_ytd,
        )
    if changed:
        db.session.commit()
    # Keep the active year's TaxYearProfile in sync so 2026 reads the stub, not 2025.
    try:
        from app.models.tax_year_profile import TaxYearProfile
        from datetime import date
        ynow = date.today().year
        for p in TaxProfile.query.all():
            yr = int(p.tax_year or ynow)
            row = TaxYearProfile.get_for(p.user_id, yr)
            if not row:
                continue
            if entered_amount(getattr(row, 'federal_withholding_ytd', None)) is None:
                row.federal_withholding_ytd = float(p.federal_withholding_ytd or 0)
            if entered_amount(getattr(row, 'state_withholding_ytd', None)) is None:
                row.state_withholding_ytd = float(p.state_withholding_ytd or 0)
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('stub withholding year-row copy skipped: %s', e)
