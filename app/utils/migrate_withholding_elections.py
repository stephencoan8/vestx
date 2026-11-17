"""
Paystub date + elected federal supplemental rate.

Idempotent column adds. When federal and state YTD still match the seeded
7/31 stub, store that stub date so vests after it are not dropped. Does not
change withholding amounts or the supplemental rate (null stays 22% until
the user saves a rate).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import inspect, text

from app.utils.tax_constants import (
    STUB_FEDERAL_WITHHOLDING_YTD,
    STUB_STATE_WITHHOLDING_YTD,
    STUB_WITHHOLDING_AS_OF,
)

logger = logging.getLogger(__name__)

_PROFILE_COLUMNS = {
    'withholding_stub_date': 'DATE',
    'federal_supplemental_rate': 'FLOAT',
}
_YEAR_COLUMNS = {
    'withholding_stub_date': 'DATE',
    'federal_supplemental_rate': 'FLOAT',
}


def _add_columns(db, table: str, columns: dict) -> None:
    inspector = inspect(db.engine)
    if table not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns(table)}
    for name, ddl in columns.items():
        if name in existing:
            continue
        logger.info('Adding %s.%s', table, name)
        db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
        db.session.commit()
        existing.add(name)


def _matches_seed(fed, state) -> bool:
    try:
        fed_n = float(fed or 0)
        state_n = float(state or 0)
    except (TypeError, ValueError):
        return False
    return (
        abs(fed_n - STUB_FEDERAL_WITHHOLDING_YTD) < 0.02
        and abs(state_n - STUB_STATE_WITHHOLDING_YTD) < 0.02
    )


def migrate_withholding_elections(app) -> None:
    from app import db

    try:
        _add_columns(db, 'tax_profiles', _PROFILE_COLUMNS)
        _add_columns(db, 'tax_year_profiles', _YEAR_COLUMNS)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('withholding election columns skipped: %s', e)
        return

    stub = date.fromisoformat(STUB_WITHHOLDING_AS_OF)
    try:
        from app.models.tax_profile import TaxProfile
        from app.models.tax_year_profile import TaxYearProfile

        stamped = 0
        for profile in TaxProfile.query.all():
            if getattr(profile, 'withholding_stub_date', None):
                continue
            if not _matches_seed(
                getattr(profile, 'federal_withholding_ytd', None),
                getattr(profile, 'state_withholding_ytd', None),
            ):
                continue
            profile.withholding_stub_date = stub
            stamped += 1
            year = int(profile.tax_year or stub.year)
            row = TaxYearProfile.get_for(profile.user_id, year)
            if row is None or getattr(row, 'withholding_stub_date', None):
                continue
            row_fed = getattr(row, 'federal_withholding_ytd', None)
            row_state = getattr(row, 'state_withholding_ytd', None)
            # Blank year-row YTD still reads the profile stub. Matching YTD does too.
            if _matches_seed(row_fed, row_state) or (
                abs(float(row_fed or 0)) < 0.005 and abs(float(row_state or 0)) < 0.005
            ):
                row.withholding_stub_date = stub
        if stamped:
            db.session.commit()
            logger.info('Stamped %s profile(s) with stub date %s', stamped, stub.isoformat())
        else:
            db.session.rollback()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('withholding stub date stamp skipped: %s', e)
