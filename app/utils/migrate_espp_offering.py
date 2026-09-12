"""Add grants.espp_offering_start for the §423 2-year offering clock."""

from __future__ import annotations

import logging

from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_espp_offering(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'grants' not in set(inspector.get_table_names() or []):
            return
        cols = {c['name'] for c in inspector.get_columns('grants')}
        if 'espp_offering_start' in cols:
            return
        db.session.execute(text('ALTER TABLE grants ADD COLUMN espp_offering_start DATE'))
        db.session.commit()
        logger.info('Added grants.espp_offering_start')
    except Exception as e:
        db.session.rollback()
        logger.warning('espp_offering_start migration skipped: %s', e)
