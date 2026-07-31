"""Ensure tax_year_profiles table exists (create_all usually handles it)."""

import logging
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def migrate_tax_year_profiles(app) -> None:
    from app import db
    from app.models.tax_year_profile import TaxYearProfile  # noqa: F401

    try:
        inspector = inspect(db.engine)
        if 'tax_year_profiles' in (inspector.get_table_names() or []):
            return
        TaxYearProfile.__table__.create(db.engine, checkfirst=True)
        logger.info('Created tax_year_profiles table')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('tax_year_profiles migration skipped: %s', e)
