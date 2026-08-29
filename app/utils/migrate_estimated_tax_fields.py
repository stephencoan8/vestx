"""Add TaxProfile estimated-tax / safe-harbor columns. Idempotent."""

from __future__ import annotations

import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)

_COLUMNS = {
    'prior_year_total_tax': 'FLOAT DEFAULT 0',
    'prior_year_agi': 'FLOAT',
    'federal_withholding_ytd': 'FLOAT DEFAULT 0',
    'state_withholding_ytd': 'FLOAT DEFAULT 0',
    'estimated_payments_ytd': 'FLOAT DEFAULT 0',
    'itemize_salt': 'FLOAT DEFAULT 0',
    'itemize_mortgage': 'FLOAT DEFAULT 0',
    'itemize_charity': 'FLOAT DEFAULT 0',
}


def migrate_estimated_tax_fields(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'tax_profiles' not in inspector.get_table_names():
            return
        existing = {c['name'] for c in inspector.get_columns('tax_profiles')}
        for name, ddl in _COLUMNS.items():
            if name in existing:
                continue
            logger.info('Adding tax_profiles.%s …', name)
            db.session.execute(text(f'ALTER TABLE tax_profiles ADD COLUMN {name} {ddl}'))
            db.session.commit()
            existing.add(name)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('estimated tax fields migration skipped: %s', e)
