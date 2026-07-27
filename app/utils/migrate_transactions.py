"""
Idempotent startup migrations for columns added after initial deploy.
Safe on Postgres (Railway) and SQLite (local). Never drops data.
"""

from app import db
from sqlalchemy import text, inspect
import logging

logger = logging.getLogger(__name__)


def _column_names(table: str) -> set:
    inspector = inspect(db.engine)
    if table not in inspector.get_table_names():
        return set()
    return {col['name'] for col in inspector.get_columns(table)}


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> bool:
    """Add a column if missing. Returns True if added."""
    cols = _column_names(table)
    if not cols:
        return False
    if column in cols:
        return False
    db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))
    return True


def migrate_transactions(app):
    """Add missing columns used by the app if they don't exist."""
    try:
        with app.app_context():
            try:
                db.create_all()
            except Exception as e:
                logger.error("Error in db.create_all(): %s", e)

            # vest_events.tax_year
            try:
                if _add_column_if_missing('vest_events', 'tax_year', 'INTEGER'):
                    db.session.commit()
                    logger.info("Added tax_year column to vest_events")
            except Exception as e:
                db.session.rollback()
                logger.warning("tax_year migration skipped: %s", e)

            # vest_events.notes
            try:
                if _add_column_if_missing('vest_events', 'notes', 'TEXT'):
                    db.session.commit()
                    logger.info("Added notes column to vest_events")
            except Exception as e:
                db.session.rollback()
                logger.warning("notes migration skipped: %s", e)

            # stock_sales actual tax columns
            try:
                added = False
                for col in ('actual_federal_tax', 'actual_state_tax', 'actual_total_tax'):
                    if _add_column_if_missing('stock_sales', col, 'FLOAT'):
                        added = True
                if added:
                    db.session.commit()
                    logger.info("Added actual tax columns to stock_sales")
            except Exception as e:
                db.session.rollback()
                logger.warning("stock_sales tax columns migration skipped: %s", e)

            # users tax preference columns
            try:
                added = False
                for col, ddl in (
                    ('federal_tax_rate', 'FLOAT'),
                    ('state_tax_rate', 'FLOAT'),
                    ('include_fica', 'BOOLEAN'),
                ):
                    if _add_column_if_missing('users', col, ddl):
                        added = True
                if added:
                    db.session.execute(text(
                        'UPDATE users SET federal_tax_rate = 0.22 WHERE federal_tax_rate IS NULL'
                    ))
                    db.session.execute(text(
                        'UPDATE users SET state_tax_rate = 0.0 WHERE state_tax_rate IS NULL'
                    ))
                    # Boolean default: SQLite/Postgres both accept TRUE-ish via 1 or TRUE
                    dialect = db.engine.dialect.name
                    if dialect == 'sqlite':
                        db.session.execute(text(
                            'UPDATE users SET include_fica = 1 WHERE include_fica IS NULL'
                        ))
                    else:
                        db.session.execute(text(
                            'UPDATE users SET include_fica = TRUE WHERE include_fica IS NULL'
                        ))
                    db.session.commit()
                    logger.info("Added tax preference columns to users")
            except Exception as e:
                db.session.rollback()
                logger.warning("users tax prefs migration skipped: %s", e)

    except Exception as e:
        logger.error("Migration failed but continuing: %s", e)


if __name__ == '__main__':
    from app import create_app
    app = create_app()
    migrate_transactions(app)
    print("Migration complete!")
