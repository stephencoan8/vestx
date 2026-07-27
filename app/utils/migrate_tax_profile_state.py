"""
Idempotent migration: ensure tax_profiles has use_state_engine (and related) columns.
Safe on Postgres (Railway) and SQLite (local).
"""

from sqlalchemy import text, inspect
import logging

logger = logging.getLogger(__name__)


def migrate_tax_profile_state(app):
    """Add state-engine columns to tax_profiles if missing."""
    from app import db

    try:
        inspector = inspect(db.engine)
        if 'tax_profiles' not in inspector.get_table_names():
            return

        columns = {col['name'] for col in inspector.get_columns('tax_profiles')}
        dialect = db.engine.dialect.name

        def _add(col_sql: str, name: str):
            if name in columns:
                return
            logger.info("Adding %s to tax_profiles...", name)
            db.session.execute(text(f"ALTER TABLE tax_profiles ADD COLUMN {col_sql}"))
            db.session.commit()
            columns.add(name)
            logger.info("%s migration successful", name)

        if dialect == 'sqlite':
            _add('use_state_engine BOOLEAN DEFAULT 1', 'use_state_engine')
            _add('state_code VARCHAR(2)', 'state_code')
            _add('state_cg_rate FLOAT DEFAULT 0', 'state_cg_rate')
            _add('ca_amt_credit_carryforward FLOAT DEFAULT 0', 'ca_amt_credit_carryforward')
            _add('amt_credit_carryforward FLOAT DEFAULT 0', 'amt_credit_carryforward')
        else:
            # Postgres
            _add('use_state_engine BOOLEAN DEFAULT TRUE', 'use_state_engine')
            _add('state_code VARCHAR(2)', 'state_code')
            _add('state_cg_rate DOUBLE PRECISION DEFAULT 0', 'state_cg_rate')
            _add('ca_amt_credit_carryforward DOUBLE PRECISION DEFAULT 0', 'ca_amt_credit_carryforward')
            _add('amt_credit_carryforward DOUBLE PRECISION DEFAULT 0', 'amt_credit_carryforward')

        # Default existing null CA users who have no state set — leave null alone;
        # for_user() seeds CA on create only.

    except Exception as e:
        db.session.rollback()
        logger.warning("tax_profile state migration skipped: %s", e)
