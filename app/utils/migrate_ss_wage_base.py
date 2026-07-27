"""
Migration utility to add ss_wage_base_maxed column to users table.
Idempotent; safe on Postgres (Railway) and SQLite (local).
"""

from sqlalchemy import text, inspect
import logging

logger = logging.getLogger(__name__)


def migrate_ss_wage_base(app):
    """Add ss_wage_base_maxed column to users table if it doesn't exist."""
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'users' not in inspector.get_table_names():
            return

        columns = {col['name'] for col in inspector.get_columns('users')}
        if 'ss_wage_base_maxed' in columns:
            logger.debug("ss_wage_base_maxed column already exists")
            return

        logger.info("Adding ss_wage_base_maxed column to users table...")
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN ss_wage_base_maxed BOOLEAN DEFAULT FALSE"
        ))
        db.session.commit()
        logger.info("ss_wage_base_maxed migration successful")

    except Exception as e:
        db.session.rollback()
        logger.warning("ss_wage_base_maxed migration skipped: %s", e)
