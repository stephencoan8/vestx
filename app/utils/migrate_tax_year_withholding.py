"""Year-scope withholding + itemize on tax_year_profiles. Idempotent."""

from __future__ import annotations

import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)

_COLUMNS = {
    'federal_withholding_ytd': 'FLOAT DEFAULT 0',
    'state_withholding_ytd': 'FLOAT DEFAULT 0',
    'estimated_payments_ytd': 'FLOAT DEFAULT 0',
    'itemize_salt': 'FLOAT DEFAULT 0',
    'itemize_mortgage': 'FLOAT DEFAULT 0',
    'itemize_charity': 'FLOAT DEFAULT 0',
}


def migrate_tax_year_withholding(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'tax_year_profiles' not in inspector.get_table_names():
            return
        existing = {c['name'] for c in inspector.get_columns('tax_year_profiles')}
        for name, ddl in _COLUMNS.items():
            if name in existing:
                continue
            logger.info('Adding tax_year_profiles.%s …', name)
            db.session.execute(text(f'ALTER TABLE tax_year_profiles ADD COLUMN {name} {ddl}'))
            db.session.commit()
            existing.add(name)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('tax_year_profiles withholding migration skipped: %s', e)
        return

    # Copy main TaxProfile stub/itemize onto the active year row (don't overwrite non-zero).
    try:
        if 'tax_profiles' not in inspector.get_table_names():
            return
        dialect = db.engine.dialect.name
        if dialect == 'postgresql':
            db.session.execute(text(
                """
                UPDATE tax_year_profiles y
                SET
                  federal_withholding_ytd = COALESCE(NULLIF(y.federal_withholding_ytd, 0), p.federal_withholding_ytd, 0),
                  state_withholding_ytd = COALESCE(NULLIF(y.state_withholding_ytd, 0), p.state_withholding_ytd, 0),
                  estimated_payments_ytd = COALESCE(NULLIF(y.estimated_payments_ytd, 0), p.estimated_payments_ytd, 0),
                  itemize_salt = COALESCE(NULLIF(y.itemize_salt, 0), p.itemize_salt, 0),
                  itemize_mortgage = COALESCE(NULLIF(y.itemize_mortgage, 0), p.itemize_mortgage, 0),
                  itemize_charity = COALESCE(NULLIF(y.itemize_charity, 0), p.itemize_charity, 0)
                FROM tax_profiles p
                WHERE y.user_id = p.user_id
                  AND y.tax_year = p.tax_year
                """
            ))
            db.session.commit()
        else:
            from app.models.tax_profile import TaxProfile
            from app.models.tax_year_profile import TaxYearProfile
            from app.utils.withholding import entered_amount
            for p in TaxProfile.query.all():
                y = TaxYearProfile.get_for(p.user_id, int(p.tax_year or 0))
                if not y:
                    continue
                for name in _COLUMNS:
                    if entered_amount(getattr(y, name, None)) is not None:
                        continue
                    src = getattr(p, name, None)
                    if entered_amount(src) is not None or name.startswith('itemize'):
                        setattr(y, name, float(src or 0))
            db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('tax_year_profiles withholding copy skipped: %s', e)
