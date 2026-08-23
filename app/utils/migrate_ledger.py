"""Idempotent tax_lots + ledger_entries tables."""

from __future__ import annotations

import logging

from sqlalchemy import inspect

logger = logging.getLogger(__name__)


def migrate_ledger(app) -> None:
    from app import db

    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names() or [])
        dialect = db.engine.dialect.name
        if 'tax_lots' in tables and 'ledger_entries' in tables:
            logger.debug('ledger tables already exist')
            return
        logger.info('Creating ledger tables (dialect=%s)', dialect)
        db.create_all()
        db.session.commit()
    except Exception:
        logger.exception('migrate_ledger failed')
        try:
            db.session.rollback()
        except Exception:
            pass
