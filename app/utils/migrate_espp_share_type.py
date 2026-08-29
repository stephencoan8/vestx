"""Set grant.share_type = espp for ESPP grants that were stored as rsu."""

from __future__ import annotations

import logging

from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def migrate_espp_share_type(app):
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'grants' not in inspector.get_table_names():
            return
        cols = {c['name'] for c in inspector.get_columns('grants')}
        if 'share_type' not in cols or 'grant_type' not in cols:
            return
        result = db.session.execute(text(
            "UPDATE grants SET share_type = 'espp' "
            "WHERE lower(grant_type) IN ('espp', 'nqespp') "
            "AND lower(coalesce(share_type, '')) <> 'espp'"
        ))
        n = result.rowcount if result.rowcount is not None else 0
        db.session.commit()
        if n:
            logger.info('Set share_type=espp on %s ESPP grant(s)', n)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning('espp share_type migration skipped: %s', e)
